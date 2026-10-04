"""Modèle de mise en page (DOCX de styles) d'un projet.

Créer un modèle :
  1. creer() produit un DOCX contenant un exemple de chaque type d'élément du
     profil, chacun dans son style (« Réplique », « Didascalie », « Titre pièce »…) ;
  2. on l'ouvre dans Word et on modifie les STYLES (clic droit sur le style >
     Modifier : police, taille, retraits, espacements…), sans les renommer ;
  3. on l'enregistre et on le dépose dans le projet.
Ensuite, appliquer() fait utiliser ces styles : le DOCX repart du modèle
(rendu.construire, via `docx_reference`) et le PDF reprend tailles, gras,
italique, centrage, retraits et espacements (mais garde sa police embarquée).
"""
from __future__ import annotations

import copy
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

EXEMPLES = {  # texte d'exemple par type d'élément (sinon : nom du type)
    "titre": "TITRE DE LA PIÈCE",
    "dedicace": "pour …",
    "titre_personnages": "PERSONNAGES",
    "personnage_liste": "Prénom Nom",
    "replique": "Texte de la réplique, qui peut s'étendre sur plusieurs lignes pour voir "
                "l'effet des retraits et de l'interlignage dans le document final.",
    "didascalie": "Un temps.",
    "titre_chapitre": "Chapitre premier",
    "epigraphe": "« Une citation placée en tête de chapitre. »",
    "paragraphe": "Un paragraphe de narration, assez long pour occuper plusieurs lignes et "
                  "montrer l'alignement, les retraits et l'espacement entre paragraphes.",
    "dialogue": "« Une réplique de dialogue. »",
    "note": "1. Une note de bas de page.",
    "autre": "Texte non classé.",
}


def creer(prof: dict, chemin: Path) -> Path:
    """Modèle de départ : un exemple de chaque élément du profil, dans son style."""
    from . import rendu
    elements = []
    personnages = prof.get("personnages") or ["PERSONNAGE"]
    for t in prof["elements"]:
        e = {"type": t, "texte": EXEMPLES.get(t, t), "personnage": None}
        if t == "replique":
            e["personnage"] = personnages[0]
        elements.append(e)
    doc = rendu.construire(elements, prof)
    # consigne en tête (style Normal : elle disparaît quand le modèle est utilisé)
    consigne = doc.paragraphs[0].insert_paragraph_before(
        "MODÈLE DE MISE EN PAGE — Modifiez les STYLES de ce document dans Word (Accueil > Styles : "
        "clic droit sur un style > Modifier), sans les renommer, puis enregistrez et déposez ce "
        "fichier dans le projet. Le texte lui-même n'est pas utilisé.", style="Normal")
    consigne.runs[0].italic = True
    doc.save(str(chemin))
    return chemin


def _resoudre(style, attribut, sous=None):
    """Valeur effective d'un attribut de style, en remontant les styles de base."""
    s = style
    while s is not None:
        objet = getattr(s, sous) if sous else s
        v = getattr(objet, attribut, None)
        if v is not None:
            return v
        s = s.base_style
    return None


def styles(chemin: Path, prof: dict) -> dict:
    """Styles du modèle, au format `mise_en_page.styles` du profil (pour le PDF)."""
    doc = Document(str(chemin))
    noms = {s.name: s for s in doc.styles}
    taille_normale = _resoudre(noms.get("Normal"), "size", "font")
    out = {}
    for nom, defaut in prof.get("mise_en_page", {}).get("styles", {}).items():
        st = noms.get(nom)
        if st is None:
            out[nom] = defaut
            continue
        d = dict(defaut)
        taille = _resoudre(st, "size", "font")
        if taille is not None:
            d["taille"] = round(taille.pt, 1)
        for attr, cle in [("bold", "gras"), ("italic", "italique"), ("all_caps", "majuscules"),
                          ("small_caps", "petites_capitales")]:
            v = _resoudre(st, attr, "font")
            if v is not None:
                d[cle] = bool(v)
        couleur = st.font.color.rgb if st.font.color is not None and st.font.color.type is not None else None
        if couleur is not None:
            d["couleur"] = str(couleur)
        if hasattr(st, "paragraph_format"):
            pf = st.paragraph_format
            if pf.alignment is not None:
                d["centre"] = pf.alignment == WD_ALIGN_PARAGRAPH.CENTER
            if pf.space_before is not None:
                d["espace_avant"] = round(pf.space_before.pt, 1)
            if pf.space_after is not None:
                d["espace_apres"] = round(pf.space_after.pt, 1)
            if pf.left_indent is not None:
                d["retrait_gauche"] = round(pf.left_indent.cm, 2)
        out[nom] = d
    if taille_normale is not None:
        out["_taille_normale"] = round(taille_normale.pt, 1)
    return out


def appliquer(prof: dict, chemin: Path | None) -> dict:
    """Profil qui utilise le modèle : DOCX construit à partir du modèle, PDF avec ses styles."""
    if not chemin:
        return prof
    p = copy.deepcopy(prof)
    mep = p.setdefault("mise_en_page", {})
    st = styles(Path(chemin), p)
    taille = st.pop("_taille_normale", None)
    if taille:
        mep["taille"] = taille
    mep["styles"] = st
    mep["docx_reference"] = str(Path(chemin).resolve())
    return p
