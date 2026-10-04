"""Interface en ligne de commande : remise-en-forme <étape> ...

Étapes (chacune relit la sortie de la précédente dans le dossier --sortie) :
  pretraiter  photos -> pages redressées            (01_pretraitement)
  transcrire  pages -> texte littéral, API Claude   (02_transcription)
  structurer  texte -> éléments JSON selon profil   (03_structuration)
  controler   contrôles + assemblage, sans API      (04_controles)
  rendre      DOCX (+ PDF)                          (05_rendu)
  tout        les cinq à la suite
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _rotations(valeurs: list[str]) -> dict[str, int]:
    out = {}
    for v in valeurs or []:
        nom, _, angle = v.rpartition("=")
        out[nom] = int(angle)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="remise-en-forme",
                                 description="Photos de pages imprimées -> document mis en forme.")
    from . import __version__
    ap.add_argument("--version", action="version",
                    version=f"remise-en-forme {__version__} — © 2026 M&M's productions")
    sub = ap.add_subparsers(dest="etape", required=True)

    def commun(p, profil=False, force=False):
        p.add_argument("-o", "--sortie", type=Path, default=Path("sortie"),
                       help="dossier des sorties intermédiaires (défaut : sortie)")
        if profil:
            p.add_argument("-p", "--profil", default="theatre",
                           help="nom (profils/<nom>.yaml) ou chemin d'un profil YAML")
        if force:
            p.add_argument("--force", action="store_true", help="refait les appels API déjà faits")
            p.add_argument("--modele", default="claude-opus-5-5",
                           help="modèle Claude (claude-opus-5-5, claude-sonnet-5-5, claude-haiku-4-5)")
            p.add_argument("--effort", default="low", choices=["low", "medium", "high"],
                           help="profondeur de réflexion (Opus/Sonnet seulement)")

    def entree(p):
        p.add_argument("entree", type=Path, help="dossier de photos JPG/PNG")
        p.add_argument("--rotation", action="append", metavar="PHOTO=ANGLE",
                       help="force la rotation (0/90/180/270, sens horaire) d'une photo")

    def rendu(p):
        p.add_argument("--nom", default="document", help="nom du fichier produit")
        p.add_argument("--pdf", action="store_true", help="produit aussi un PDF (LibreOffice ou Word)")

    p = sub.add_parser("pretraiter", help="Étape 1 : orientation, doubles pages, redressement, contraste")
    entree(p); commun(p)
    p = sub.add_parser("transcrire", help="Étape 2 : transcription littérale (API Claude, une page par appel)")
    commun(p, force=True)
    p.add_argument("--une-image", action="store_true",
                   help="n'envoie que la page contrastée (pas la version couleur) : moins cher")
    p = sub.add_parser("structurer", help="Étape 3 : étiquetage JSON selon le profil")
    commun(p, profil=True, force=True)
    p = sub.add_parser("controler", help="Étape 4 : contrôles et assemblage (sans API)")
    commun(p, profil=True)
    p = sub.add_parser("rendre", help="Étape 5 : DOCX (et PDF)")
    commun(p, profil=True); rendu(p)
    p = sub.add_parser("tout", help="Les cinq étapes à la suite")
    entree(p); commun(p, profil=True, force=True); rendu(p)
    p.add_argument("--une-image", action="store_true")
    p = sub.add_parser("mettre-a-jour",
                       help="Intègre des pages annotées à la main (un sous-dossier par personne) "
                            "dans une version de référence : DOCX en suivi des modifications")
    p.add_argument("reference", type=Path, help="DOCX de référence (produit par l'outil, éventuellement retouché)")
    p.add_argument("annotations", type=Path, help="dossier des photos annotées, un sous-dossier par personne")
    commun(p, profil=True, force=True)
    p.add_argument("--nom", default=None, help="nom du DOCX produit (défaut : <référence>_maj)")

    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    a = ap.parse_args(argv)
    from . import profil as profils
    prof = profils.charger(a.profil) if hasattr(a, "profil") else None

    if a.etape in ("pretraiter", "tout"):
        from . import pretraitement
        print("— Étape 1 : prétraitement")
        pretraitement.executer(a.entree, a.sortie, _rotations(a.rotation))
    if a.etape in ("transcrire", "tout"):
        from . import transcription
        print("— Étape 2 : transcription")
        transcription.executer(a.sortie, a.force, a.modele, a.effort, not a.une_image)
    if a.etape in ("structurer", "tout"):
        from . import structuration
        print("— Étape 3 : structuration")
        structuration.executer(a.sortie, prof, a.force, a.modele, a.effort)
    if a.etape in ("controler", "tout"):
        from . import controles
        print("— Étape 4 : contrôles")
        controles.executer(a.sortie, prof)
    if a.etape in ("rendre", "tout"):
        from . import rendu as r
        print("— Étape 5 : rendu")
        r.executer(a.sortie, prof, a.nom, a.pdf)
    if a.etape == "mettre-a-jour":
        from . import mise_a_jour
        sortie = a.sortie if a.sortie != Path("sortie") else Path("sortie_maj")
        mise_a_jour.executer(a.reference, a.annotations, prof, sortie,
                             a.nom or f"{a.reference.stem}_maj", a.modele, a.effort, a.force)
