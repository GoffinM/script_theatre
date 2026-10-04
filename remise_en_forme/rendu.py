"""Étape 5 — Rendu DOCX (et PDF en option) selon la mise en page du profil.

Entrée  : <sortie>/04_controles/document.json
Sortie  : <sortie>/05_rendu/<nom>.docx, <nom>_a_annoter.docx et <nom>.pdf (voir pdf.py)

Les styles sont créés d'après `mise_en_page.styles` du profil. Si le profil
désigne un `docx_reference`, le document part de ce fichier : ses styles de
même nom sont conservés tels quels (on peut donc tout régler dans Word).

Chaque paragraphe porte un signet invisible « rf_<id> » : le DOCX peut ainsi
servir de version de référence pour les mises à jour (voir reference.py).

Un élément peut aussi porter, pour le mode mise à jour :
  segments      : par paragraphe, liste de (texte, mode, auteur), mode = None | "ins" | "del"
  mode, auteur  : élément entier inséré ("ins") ou supprimé ("del") en suivi des modifications
  notes         : liste de {"auteur", "texte"} → commentaires Word
"""
from __future__ import annotations

import itertools
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ITALIQUE = re.compile(r"_(.+?)_")
STYLES_INTERNES = {  # styles de caractère utilisés par l'outil, créés s'ils manquent
    "Personnage": {"caractere": True},
    "Référence page": {"caractere": True, "taille": 8, "couleur": "999999"},
    "Numéro": {"caractere": True, "taille": 7, "couleur": "999999"},
}
_ids = itertools.count(1)


def _definir_styles(doc: Document, mep: dict, existants: set[str]) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = mep.get("police", "Garamond")
    normal.font.size = Pt(mep.get("taille", 12))
    styles = {**STYLES_INTERNES, **mep.get("styles", {})}
    for nom, s in styles.items():
        if nom in existants:  # défini par le docx de référence : on n'y touche pas
            continue
        type_ = WD_STYLE_TYPE.CHARACTER if s.get("caractere") else WD_STYLE_TYPE.PARAGRAPH
        st = doc.styles.add_style(nom, type_)
        if type_ == WD_STYLE_TYPE.PARAGRAPH:
            st.base_style = normal
            pf = st.paragraph_format
            if s.get("centre"):
                pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if "espace_avant" in s:
                pf.space_before = Pt(s["espace_avant"])
            if "espace_apres" in s:
                pf.space_after = Pt(s["espace_apres"])
            if s.get("retrait_gauche"):
                pf.left_indent = Cm(s["retrait_gauche"])
            if s.get("retrait_negatif"):
                pf.left_indent = Cm(s["retrait_negatif"])
                pf.first_line_indent = Cm(-s["retrait_negatif"])
        f = st.font
        if "taille" in s:
            f.size = Pt(s["taille"])
        if "gras" in s:
            f.bold = s["gras"]
        if "italique" in s:
            f.italic = s["italique"]
        if s.get("majuscules"):
            f.all_caps = True
        if s.get("petites_capitales"):
            f.small_caps = True
        if s.get("couleur"):
            f.color.rgb = RGBColor.from_string(s["couleur"])


# --------------------------------------------------------------------------
# Briques XML : signets, suivi des modifications
# --------------------------------------------------------------------------

def _date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signet(par, nom: str) -> None:
    bid = str(next(_ids))
    debut = OxmlElement("w:bookmarkStart")
    debut.set(qn("w:id"), bid)
    debut.set(qn("w:name"), nom)
    fin = OxmlElement("w:bookmarkEnd")
    fin.set(qn("w:id"), bid)
    p = par._p
    if p.pPr is not None:
        p.pPr.addnext(debut)
    else:
        p.insert(0, debut)
    p.append(fin)


def _suivi(runs: list, mode: str, auteur: str) -> None:
    """Place des runs dans une insertion ou une suppression suivie (w:ins / w:del)."""
    if not runs or mode not in ("ins", "del"):
        return
    env = OxmlElement(f"w:{mode}")
    env.set(qn("w:id"), str(next(_ids)))
    env.set(qn("w:author"), auteur or "?")
    env.set(qn("w:date"), _date())
    runs[0]._r.addprevious(env)
    for r in runs:
        env.append(r._r)
        if mode == "del":
            for t in r._r.findall(qn("w:t")):
                t.tag = qn("w:delText")


def _marque_paragraphe(par, mode: str, auteur: str) -> None:
    """Marque de fin de paragraphe insérée/supprimée (paragraphe entier ajouté/retiré)."""
    pPr = par._p.get_or_add_pPr()
    rPr = pPr.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        pPr.append(rPr)
    m = OxmlElement(f"w:{mode}")
    m.set(qn("w:id"), str(next(_ids)))
    m.set(qn("w:author"), auteur or "?")
    m.set(qn("w:date"), _date())
    rPr.append(m)


def _texte_riche(par, texte: str, tout_italique: bool = False, sans_italique: bool = False) -> list:
    """Ajoute le texte au paragraphe ; _x_ devient de l'italique. Retourne les runs créés."""
    runs, pos = [], 0
    for m in ITALIQUE.finditer(texte):
        if m.start() > pos:
            runs.append(par.add_run(texte[pos:m.start()]))
        r = par.add_run(m.group(1))
        if not sans_italique:
            r.italic = not tout_italique  # dans une didascalie déjà italique : romain
        runs.append(r)
        pos = m.end()
    if pos < len(texte):
        runs.append(par.add_run(texte[pos:]))
    return runs


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------

def construire(elements: list[dict], prof: dict, numeros: bool = False) -> Document:
    """`numeros` : petit numéro gris devant chaque élément (copie à annoter)."""
    mep = prof.get("mise_en_page", {})
    ref = mep.get("docx_reference")
    if ref:
        doc = Document(str(Path(prof["_dossier"]) / ref))
        for p in list(doc.paragraphs):  # on garde les styles, pas le contenu
            p._element.getparent().remove(p._element)
        existants = {s.name for s in doc.styles}
    else:
        doc, existants = Document(), set()
    _definir_styles(doc, mep, existants)
    for sec in doc.sections:
        m = Cm(mep.get("marges_cm", 2.5))
        sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = m

    styles_elt = {t: e.get("style", "Normal") for t, e in prof["elements"].items()}
    ref_page = mep.get("reference_page", "fin")
    dernier_liste = max((i for i, e in enumerate(elements) if e["type"] == "personnage_liste"), default=None)

    for i, e in enumerate(elements):
        style = styles_elt.get(e["type"], "Normal")
        mode_elt, auteur_elt = e.get("mode"), e.get("auteur", "")
        segments = e.get("segments") or [[(t, None, "")] for t in e["texte"].split("\n")]
        tous_runs = []
        for k, segs in enumerate(segments):
            par = doc.add_paragraph(style=style)
            runs = []
            if numeros and k == 0 and e.get("id"):
                runs.append(par.add_run(f"{e['id'][1:].lstrip('0')} ", style="Numéro"))
            if e["type"] == "replique" and e.get("personnage") and k == 0:
                runs.append(par.add_run(f"{e['personnage']}.", style="Personnage"))
                runs.append(par.add_run(" "))
            for texte, mode, auteur in segs:
                if e["type"] == "didascalie":
                    rs = _texte_riche(par, texte.strip().strip("_") if k == 0 and len(segs) == 1 else texte,
                                      tout_italique=True)
                elif prof["elements"].get(e["type"], {}).get("italique_source", True):
                    rs = _texte_riche(par, texte)
                else:  # mise en forme imposée par le style : on ignore l'italique lu sur la photo
                    rs = _texte_riche(par, texte, sans_italique=True)
                _suivi(rs, mode, auteur)
                runs += rs
            if e["type"] == "replique" and ref_page == "fin" and k == len(segments) - 1 and e.get("pages_txt"):
                runs.append(par.add_run(f"  [p. {e['pages_txt']}]", style="Référence page"))
            if mode_elt:
                _suivi([r for r in runs if r._r.getparent() is par._p], mode_elt, auteur_elt)
                _marque_paragraphe(par, mode_elt, auteur_elt)
            if e.get("id"):
                _signet(par, f"rf_{e['id']}" + (f"_{k + 1}" if k else ""))
            tous_runs += runs
        for n in e.get("notes", []):
            if tous_runs:
                doc.add_comment(tous_runs, text=n["texte"], author=n.get("auteur", "?"),
                                initials="".join(w[0] for w in n.get("auteur", "?").split())[:3])
        if i == dernier_liste and mep.get("saut_apres_liste"):
            par.add_run().add_break(WD_BREAK.PAGE)
    return doc


def executer(sortie: Path, prof: dict, nom: str = "document") -> Path:
    sys.stdout.reconfigure(encoding="utf-8")
    elements = json.loads((sortie / "04_controles" / "document.json").read_text(encoding="utf-8"))
    dossier = sortie / "05_rendu"
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / f"{nom}.docx"
    construire(elements, prof).save(str(cible))
    print(f"DOCX : {cible}")
    # copie à annoter : même document, avec un numéro discret devant chaque élément
    annoter = dossier / f"{nom}_a_annoter.docx"
    construire(elements, prof, numeros=True).save(str(annoter))
    print(f"DOCX à imprimer pour les annotations : {annoter}")
    # PDF pour lire et partager (le partage de Chrome sur Android refuse les .docx)
    from . import pdf
    cible_pdf = dossier / f"{nom}.pdf"
    cible_pdf.write_bytes(pdf.construire(elements, prof, nom))
    print(f"PDF  : {cible_pdf}")
    return cible
