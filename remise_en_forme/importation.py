"""Import d'un document déjà « propre » (TXT, DOCX, PDF) à la place des photos.

Remplace les étapes 1 et 2 (prétraitement, transcription) : le texte est lu
directement, puis la suite est la même (étiquetage selon le profil, contrôles,
rendu). Sortie : <sortie>/02_transcription/transcription.json, au même format
que la transcription des photos.

- TXT, DOCX : le texte est découpé en « pages » d'environ CARACTERES_PAR_PAGE
  caractères, toujours entre deux paragraphes, pour l'étiquetage. L'italique
  d'un DOCX est conservé (_ainsi_), ce qui aide à repérer les didascalies.
- PDF : chaque page est lue directement si elle contient du texte (PDF exporté
  depuis un traitement de texte) ; une page sans texte (PDF scanné) est
  transcrite par Claude, comme une photo — c'est la seule source de coût.
- DOC (Word 97-2003) : non pris en charge — l'enregistrer en DOCX ou PDF.

Comme les autres étapes qui interrogent Claude : preparer() → requêtes, terminer().
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
from pathlib import Path

from . import claude, transcription

EXTENSIONS = {".txt", ".docx", ".pdf"}
CARACTERES_PAR_PAGE = 2500
NUMERO_SEUL = re.compile(r"^\s*[-–—]?\s*(\d{1,4})\s*[-–—]?\s*$")


class FormatNonPrisEnCharge(ValueError):
    pass


# --------------------------------------------------------------------------
# Lecture des formats
# --------------------------------------------------------------------------

def _paragraphes_docx(chemin: Path) -> list[str]:
    from docx import Document
    ignores = {"Numéro", "Référence page"}  # repères ajoutés par l'outil lui-même
    out = []
    for par in Document(str(chemin)).paragraphs:
        morceaux = []
        for r in par.runs:
            if r.style is not None and r.style.name in ignores:
                continue
            t = r.text
            morceaux.append(f"_{t}_" if r.italic and t.strip() else t)
        texte = re.sub(r"_(\s*)_", r"\1", "".join(morceaux)).strip()
        out.append(texte)
    return out


def _lire_txt(chemin: Path) -> str:
    octets = chemin.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return octets.decode(enc)
        except UnicodeDecodeError:
            continue
    return octets.decode("utf-8", errors="replace")


def _lignes_coupees(lignes: list[str]) -> bool:
    """Texte « coupé » comme une page imprimée (retours à la ligne au milieu des phrases) ?"""
    pleines = [l for l in lignes if l.strip()]
    if len(pleines) < 5:
        return False
    coupures = sum(1 for a, b in zip(pleines, pleines[1:])
                   if not re.search(r"[.!?…:»)]\s*$", a) and b[:1].islower())
    return coupures / (len(pleines) - 1) > 0.25


def _en_pages(paragraphes: list[str]) -> list[str]:
    """Regroupe des paragraphes en pages d'environ CARACTERES_PAR_PAGE caractères."""
    pages, courante, taille = [], [], 0
    for p in paragraphes:
        if courante and taille + len(p) > CARACTERES_PAR_PAGE and p.strip():
            pages.append("\n".join(courante).strip("\n"))
            courante, taille = [], 0
        courante.append(p)
        taille += len(p) + 1
    if any(l.strip() for l in courante):
        pages.append("\n".join(courante).strip("\n"))
    return pages


def _pages_pdf(chemin: Path) -> list[dict]:
    """Pages d'un PDF : texte (s'il y en a) et, pour les pages scannées, la page seule en PDF."""
    from pypdf import PdfReader, PdfWriter
    lecteur = PdfReader(str(chemin))
    brutes = [[l.rstrip() for l in (p.extract_text() or "").replace("\r", "").split("\n") if l.strip()]
              for p in lecteur.pages]
    # en-têtes / pieds de page répétés (titre courant, « Titre — 12 ») : retirés
    forme = lambda l: re.sub(r"\d+", "#", l.strip())  # noqa: E731
    repetes = set()
    avec_texte = [l for l in brutes if l]
    if len(avec_texte) >= 3:
        for bord in (0, -1):
            compte: dict[str, int] = {}
            for l in avec_texte:
                compte[forme(l[bord])] = compte.get(forme(l[bord]), 0) + 1
            repetes |= {f for f, n in compte.items() if n >= max(3, len(avec_texte) // 2) and f != "#"}
    pages = []
    for i, page in enumerate(lecteur.pages):
        lignes = list(brutes[i])
        while lignes and forme(lignes[0]) in repetes:
            lignes.pop(0)
        while lignes and forme(lignes[-1]) in repetes:
            lignes.pop()
        numero = None
        # numéro de page imprimé seul sur la première ou la dernière ligne
        for j in (0, -1):
            while lignes and not lignes[j].strip():
                lignes.pop(j)
            if lignes and NUMERO_SEUL.match(lignes[j]):
                numero = NUMERO_SEUL.match(lignes[j]).group(1)
                lignes.pop(j)
        if len("".join(lignes).strip()) >= 20:
            pages.append({"texte": "\n".join(lignes), "numero": numero, "pdf": None})
        else:
            w = PdfWriter()
            w.add_page(page)
            tampon = io.BytesIO()
            w.write(tampon)
            pages.append({"texte": None, "numero": None, "pdf": tampon.getvalue()})
    return pages


def extraire(fichier: Path) -> list[dict]:
    """Pages du document : {texte | None, numero, pdf (page scannée) | None, par_ligne}."""
    ext = fichier.suffix.lower()
    if ext == ".doc":
        raise FormatNonPrisEnCharge(
            "Format .doc (Word 97-2003) non pris en charge : dans Word, « Enregistrer sous » DOCX ou PDF.")
    if ext not in EXTENSIONS:
        raise FormatNonPrisEnCharge(f"Format {ext or 'inconnu'} non pris en charge (TXT, DOCX ou PDF).")
    if ext == ".pdf":
        return [{**p, "par_ligne": False} for p in _pages_pdf(fichier)]
    if ext == ".docx":
        paragraphes, par_ligne = _paragraphes_docx(fichier), True
    else:
        texte = _lire_txt(fichier).replace("\r\n", "\n").replace("\r", "\n")
        lignes = texte.split("\n")
        par_ligne = not _lignes_coupees(lignes)
        paragraphes = lignes
    # deux lignes vides de suite au plus
    propres = []
    for p in paragraphes:
        if p.strip() or (propres and propres[-1].strip()):
            propres.append(p)
    return [{"texte": t, "numero": None, "pdf": None, "par_ligne": par_ligne} for t in _en_pages(propres)]


# --------------------------------------------------------------------------
# Étape (préparer / terminer)
# --------------------------------------------------------------------------

CONSIGNE_PDF = ("Cette page est fournie sous forme de PDF (page scannée) au lieu de photos : "
                "applique exactement les mêmes règles de transcription.")


def preparer(fichier: Path, sortie: Path, modele: str = claude.MODELE, effort: str = "low") -> list[dict]:
    """Lit le document, enregistre ses pages ; retourne les requêtes des pages scannées."""
    dossier = sortie / "02_import"
    dossier.mkdir(parents=True, exist_ok=True)
    pages = extraire(fichier)
    if not pages:
        raise ValueError(f"{fichier.name} : aucun texte trouvé.")
    requetes, index = [], []
    for i, p in enumerate(pages, 1):
        cle = f"{i:03d}_{fichier.stem.replace(' ', '_')[:40]}"
        index.append({"cle": cle, "texte": p["texte"], "numero": p["numero"], "par_ligne": p["par_ligne"]})
        if p["pdf"] is not None:
            donnees = base64.standard_b64encode(p["pdf"]).decode()
            requetes.append(claude.requete(
                cle, transcription.SYSTEME,
                [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": donnees}},
                 {"type": "text", "text": CONSIGNE_PDF + " Transcris cette page."}],
                transcription.SCHEMA, modele, effort))
    (dossier / "pages.json").write_text(json.dumps({"fichier": fichier.name, "pages": index},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    return requetes


def terminer(sortie: Path, reponses: dict[str, dict]) -> list[dict]:
    """Assemble 02_transcription/transcription.json (format de l'étape 2)."""
    sys.stdout.reconfigure(encoding="utf-8")
    info = json.loads((sortie / "02_import" / "pages.json").read_text(encoding="utf-8"))
    dossier = sortie / "02_transcription"
    dossier.mkdir(parents=True, exist_ok=True)
    resultats = []
    for p in info["pages"]:
        if p["texte"] is not None:
            res = {"numero_page": p["numero"], "texte": p["texte"], "remarques": ""}
            origine = "lue directement"
        else:
            res = reponses[p["cle"]]
            origine = "page scannée, transcrite"
        resultats.append({"image": p["cle"], "photo": info["fichier"], "par_ligne": p["par_ligne"], **res})
        print(f"{p['cle']} — {origine} — {len(res['texte'])} caractères")
    (dossier / "transcription.json").write_text(json.dumps(resultats, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    print(f"{info['fichier']} : {len(resultats)} page(s)")
    return resultats


def executer(fichier: Path, sortie: Path, modele: str = claude.MODELE, effort: str = "low") -> list[dict]:
    debut = len(claude.JOURNAL)
    resultats = terminer(sortie, claude.envoyer(preparer(fichier, sortie, modele, effort)))
    claude.bilan(sortie / "02_transcription", debut)
    return resultats
