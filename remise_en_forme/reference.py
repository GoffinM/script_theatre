"""Version de référence : relecture d'un DOCX produit par l'outil (puis éventuellement
corrigé à la main dans Word) en une liste d'éléments identifiés.

Chaque paragraphe produit par l'outil porte un signet « rf_<id> » (ou « rf_<id>_<n> »
pour le n-ième paragraphe d'un même élément). Un paragraphe ajouté à la main dans
Word, sans signet, devient un nouvel élément (identifiant dérivé du précédent :
E0012a, E0012b…). Les commentaires Word (notes de jeu) sont conservés.
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

SIGNET = re.compile(r"^rf_(E\d{4}[a-z]*)(?:_(\d+))?$")
STYLES_IGNORES = {"Référence page", "Numéro"}


class ModificationsEnAttente(RuntimeError):
    pass


def _texte_paragraphe(par) -> tuple[str | None, str]:
    """(personnage, texte avec _italique_) d'un paragraphe."""
    personnage, morceaux = None, []
    for r in par.runs:
        style = r.style.name if r.style is not None else ""
        if style in STYLES_IGNORES:
            continue
        if style == "Personnage":
            personnage = r.text.strip().rstrip(".")
            continue
        morceaux.append(f"_{r.text}_" if r.italic and r.text.strip() else r.text)
    texte = "".join(morceaux).strip()
    return personnage, re.sub(r"_(\s*)_", r"\1", texte)  # recolle deux italiques adjacents


def importer(chemin: Path, prof: dict) -> list[dict]:
    doc = Document(str(chemin))
    corps = doc.element.body
    if corps.find(".//" + qn("w:ins")) is not None or corps.find(".//" + qn("w:del")) is not None:
        raise ModificationsEnAttente(
            f"{chemin.name} contient encore des modifications suivies : dans Word, "
            "Révision > Accepter (ou Refuser) toutes les modifications, puis enregistrer.")

    type_de_style = {}
    for t, e in prof["elements"].items():
        type_de_style.setdefault(e.get("style", "Normal"), t)

    notes_par_id = {}
    for c in doc.comments:
        notes_par_id[str(c.comment_id)] = {"auteur": c.author, "texte": c.text}

    elements: list[dict] = []
    dernier_id = "E0000"
    for par in doc.paragraphs:
        personnage, texte = _texte_paragraphe(par)
        if not texte and not personnage:
            continue
        signets = [b.get(qn("w:name")) for b in par._p.iter(qn("w:bookmarkStart"))]
        m = next((SIGNET.match(s) for s in signets if s and SIGNET.match(s)), None)
        notes = [notes_par_id[c.get(qn("w:id"))] for c in par._p.iter(qn("w:commentRangeStart"))
                 if c.get(qn("w:id")) in notes_par_id]
        type_ = type_de_style.get(par.style.name, "autre")
        if m and m.group(2) and elements and elements[-1]["id"] == m.group(1):
            # paragraphe suivant d'un même élément
            elements[-1]["texte"] += "\n" + texte
            elements[-1]["notes"] += [n for n in notes if n not in elements[-1]["notes"]]
            continue
        if m:
            ident = m.group(1)
        else:  # paragraphe ajouté à la main : nouvel identifiant après le précédent
            base = re.match(r"E\d{4}", dernier_id).group(0)
            suffixe = dernier_id[5:]
            ident = base + (chr(ord(suffixe[-1]) + 1) if suffixe else "a")
            while any(e["id"] == ident for e in elements):
                ident = ident[:-1] + chr(ord(ident[-1]) + 1)
        dernier_id = ident
        elements.append({"id": ident, "type": type_, "personnage": personnage,
                         "texte": texte, "notes": notes})
    return elements


def resume(elements: list[dict]) -> str:
    """Texte numéroté de la référence, tel qu'on le montre au modèle."""
    lignes = []
    for e in elements:
        tete = f"{e['personnage']}. " if e.get("personnage") else ""
        texte = e["texte"].replace("\n", " ¶ ")
        lignes.append(f"[{e['id']}] ({e['type']}) {tete}{texte}")
    return "\n".join(lignes)
