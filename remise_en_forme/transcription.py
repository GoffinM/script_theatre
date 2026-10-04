"""Étape 2 — Transcription littérale, une page par appel à Claude (vision).

Entrée  : <sortie>/01_pretraitement/manifest.json + pages/*.png
Sortie  : <sortie>/02_transcription/<page>.json  {numero_page, texte, remarques}
          <sortie>/02_transcription/<page>.txt   (texte seul, pour relecture)
          <sortie>/02_transcription/transcription.json (toutes les pages, dans l'ordre)
Les pages déjà transcrites ne sont pas renvoyées à l'API (sauf --force).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import claude

SYSTEME = """Tu es un transcripteur. Tu reçois UNE page imprimée, en deux versions :
image 1 = page redressée et contrastée (version principale) ; image 2, si présente = la même page en couleur, simplement recadrée (plus fidèle pour les traits pâles ; peut montrer un bout de la page voisine, à ignorer).
Transcris-la de façon STRICTEMENT LITTÉRALE :
- Recopie exactement ce qui est imprimé : orthographe, ponctuation, majuscules, \
espaces avant « ? ! ; : », points de suspension « … », apostrophes, tirets. \
Ne corrige rien, ne modernise rien, ne reformule rien, même si cela semble \
être une faute.
- Respecte les retours à la ligne de la page : une ligne imprimée = une ligne \
transcrite. Garde les mots coupés en fin de ligne tels quels, avec leur tiret \
(ex. « répa-» puis « rer » à la ligne suivante).
- Sépare les paragraphes / répliques par une ligne vide quand l'espacement \
de la page l'indique.
- Le texte en italique est entouré de _soulignés_ (ex. _Un temps._). C'est le \
seul balisage autorisé.
- Si un mot ou un passage est illisible, écris [illisible] à sa place. Ne \
devine jamais ; ne complète jamais un mot coupé par le bord de l'image.
- Ignore ce qui n'appartient pas à la page imprimée (doigts, annotations \
manuscrites, texte transparaissant du verso, page voisine partiellement visible).
- Le numéro de page imprimé (en haut ou en bas) va dans `numero_page` et \
n'apparaît PAS dans `texte`. S'il n'y en a pas, `numero_page` vaut null.
- Si la page ne contient AUCUN texte imprimé (page blanche où seul le verso transparaît), `texte` est une chaîne vide.
- `remarques` : vide, sauf observation utile pour la relecture (passage \
douteux, page coupée, etc.)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "numero_page": {"type": ["string", "null"]},
        "texte": {"type": "string"},
        "remarques": {"type": "string"},
    },
    "required": ["numero_page", "texte", "remarques"],
    "additionalProperties": False,
}


def images(src: Path, fichier: str, couleur: bool = True) -> list[dict]:
    blocs = [claude.image(src / "pages" / fichier)]
    chemin = src / "pages_couleur" / fichier.replace(".png", ".jpg")
    if couleur and chemin.exists():
        blocs.append(claude.image(chemin))
    return blocs


def _pages(sortie: Path) -> list[tuple[str, str]]:
    manifest = json.loads((sortie / "01_pretraitement" / "manifest.json").read_text(encoding="utf-8"))
    return [(ph["photo"], pg["fichier"]) for ph in manifest for pg in ph["pages"]]


def preparer(sortie: Path, force: bool = False, modele: str = claude.MODELE,
             effort: str = "low", couleur: bool = True) -> list[dict]:
    """Requêtes pour les pages pas encore transcrites (toutes avec `force`)."""
    src, dossier = sortie / "01_pretraitement", sortie / "02_transcription"
    requetes = []
    for _, fichier in _pages(sortie):
        if (dossier / f"{Path(fichier).stem}.json").exists() and not force:
            continue
        requetes.append(claude.requete(
            fichier, SYSTEME,
            images(src, fichier, couleur) + [{"type": "text", "text": "Transcris cette page."}],
            SCHEMA, modele, effort))
    return requetes


def terminer(sortie: Path, reponses: dict[str, dict]) -> list[dict]:
    """Enregistre les réponses (cle = fichier de la page) et assemble transcription.json."""
    sys.stdout.reconfigure(encoding="utf-8")
    dossier = sortie / "02_transcription"
    dossier.mkdir(parents=True, exist_ok=True)
    pages = _pages(sortie)
    resultats = []
    for i, (photo, fichier) in enumerate(pages, 1):
        nom = Path(fichier).stem
        cible = dossier / f"{nom}.json"
        if fichier in reponses:
            res = {"image": fichier, "photo": photo, **reponses[fichier]}
            cible.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            (dossier / f"{nom}.txt").write_text(res["texte"], encoding="utf-8")
            etat = "transcrite"
        else:
            res = json.loads(cible.read_text(encoding="utf-8"))
            etat = "déjà fait"
        n_ill = res["texte"].count("[illisible]")
        if not res["texte"].strip():
            etat += " — PAGE BLANCHE, ignorée par la suite"
        print(f"[{i}/{len(pages)}] {fichier} — p. {res['numero_page'] or '?'} — {etat}"
              + (f" — {n_ill} [illisible]" if n_ill else "")
              + (f" — remarque : {res['remarques']}" if res.get("remarques") else ""))
        resultats.append(res)
    (dossier / "transcription.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultats


def executer(sortie: Path, force: bool = False, modele: str = claude.MODELE,
             effort: str = "low", couleur: bool = True) -> list[dict]:
    debut_journal = len(claude.JOURNAL)
    reponses = claude.envoyer(preparer(sortie, force, modele, effort, couleur))
    resultats = terminer(sortie, reponses)
    claude.bilan(sortie / "02_transcription", debut_journal)
    return resultats
