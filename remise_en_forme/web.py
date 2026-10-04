"""Façade pour l'application web (Python exécuté dans le navigateur par Pyodide).

Le JavaScript dépose les fichiers (photos, DOCX de référence) dans le système de
fichiers virtuel, appelle ces fonctions, et se charge lui-même des appels à
Claude : `preparer_*` renvoie les requêtes (JSON : cle + paramètres de l'API),
le JavaScript les envoie, puis `terminer_*` reçoit les réponses brutes de l'API.
Toutes les fonctions échangent du texte JSON, plus simple à passer entre les
deux langages.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if not hasattr(sys.stdout, "reconfigure"):  # sortie de Pyodide : pas de reconfigure()
    class _Sortie:
        def __init__(self, f):
            self._f = f

        def __getattr__(self, nom):
            return getattr(self._f, nom)

        def reconfigure(self, **_):
            pass

    sys.stdout = _Sortie(sys.stdout)

from . import (claude, controles, mise_a_jour, pretraitement, profil, reference, rendu,
               structuration, transcription)

_EN_ATTENTE: dict[str, dict] = {}   # cle → requête, en attente de sa réponse
_PROFIL: dict | None = None
_MAJ: dict = {}                      # état d'une mise à jour en cours


def _vers_js(requetes: list[dict], prefixe: str = "") -> str:
    out = []
    for r in requetes:
        cle = prefixe + r["cle"]
        _EN_ATTENTE[cle] = r
        out.append({"cle": cle, "params": claude.parametres(r)})
    return json.dumps(out, ensure_ascii=False)


def _depuis_js(reponses_json: str, prefixe: str = "") -> dict[str, dict]:
    """Réponses brutes de l'API (cle → message) → JSON produits, coût journalisé."""
    out = {}
    for cle, rep in json.loads(reponses_json).items():
        req = _EN_ATTENTE.pop(cle)
        out[cle[len(prefixe):]] = claude.lire_reponse(req, rep)
    return out


def definir_profil(yaml_texte: str) -> str:
    """Appelée au début de chaque traitement : fixe le profil et remet le compteur de coût à zéro."""
    global _PROFIL
    _PROFIL = profil.charger_texte(yaml_texte, nom="profil")
    claude.JOURNAL.clear()
    return _PROFIL.get("nom", "profil")


def cout_total() -> float:
    return round(sum(a["cout_usd"] for a in claude.JOURNAL), 4)


# --------------------------------------------------------------------------
# Nouveau document
# --------------------------------------------------------------------------

def pretraiter(photos: str, sortie: str) -> str:
    res = pretraitement.executer(Path(photos), Path(sortie))
    return json.dumps({"photos": len(res), "pages": sum(len(r.pages) for r in res),
                       "incertaines": [r.photo for r in res if r.orientation_incertaine]})


def preparer_transcription(sortie: str, modele: str, effort: str = "low") -> str:
    return _vers_js(transcription.preparer(Path(sortie), True, modele, effort))


def terminer_transcription(sortie: str, reponses_json: str) -> str:
    pages = transcription.terminer(Path(sortie), _depuis_js(reponses_json))
    return json.dumps({"pages": len(pages), "blanches": sum(1 for p in pages if not p["texte"].strip())})


def preparer_structuration(sortie: str, modele: str, effort: str = "low") -> str:
    return _vers_js(structuration.preparer(Path(sortie), _PROFIL, True, modele, effort))


def terminer_structuration(sortie: str, reponses_json: str) -> str:
    structuration.terminer(Path(sortie), _PROFIL, _depuis_js(reponses_json))
    return "ok"


def controler_et_rendre(sortie: str, nom: str) -> str:
    """Étapes 4 et 5 ; retourne les chemins des fichiers produits."""
    s = Path(sortie)
    controles.executer(s, _PROFIL)
    rendu.executer(s, _PROFIL, nom)
    return json.dumps({"docx": str(s / "05_rendu" / f"{nom}.docx"),
                       "a_annoter": str(s / "05_rendu" / f"{nom}_a_annoter.docx"),
                       "rapport": str(s / "04_controles" / "rapport.md")})


# --------------------------------------------------------------------------
# Mise à jour
# --------------------------------------------------------------------------

def maj_commencer(reference_docx: str, nom_reference: str = "") -> str:
    """Lit la référence ; refuse un fichier où des modifications suivies restent en attente."""
    _MAJ.clear()
    _MAJ["nom_reference"] = nom_reference or Path(reference_docx).name
    _MAJ["elements"] = reference.importer(Path(reference_docx), _PROFIL)
    _MAJ["lectures"] = {}
    return json.dumps({"elements": len(_MAJ["elements"])})


def maj_preparer_transcription(photos: str, travail: str, modele: str, effort: str = "low") -> str:
    pretraitement.executer(Path(photos), Path(travail))
    return preparer_transcription(travail, modele, effort)


def maj_preparer_annotations(travail: str, reponses_transcription_json: str, modele: str,
                             effort: str = "low") -> str:
    transcription.terminer(Path(travail), _depuis_js(reponses_transcription_json))
    return _vers_js(mise_a_jour.preparer_annotations(Path(travail), _MAJ["elements"], _PROFIL,
                                                     modele, effort, force=True), prefixe=travail + "|")


def maj_terminer_auteur(auteur: str, travail: str, reponses_json: str) -> str:
    ops, msgs = mise_a_jour.terminer_annotations(Path(travail), _MAJ["elements"],
                                                 _depuis_js(reponses_json, prefixe=travail + "|"), force=True)
    _MAJ["lectures"][auteur] = (ops, msgs)
    return json.dumps({"operations": len(ops)})


def maj_finaliser(sortie: str, nom: str) -> str:
    cible = mise_a_jour.finaliser(_MAJ["nom_reference"], _MAJ["elements"], _MAJ["lectures"],
                                  _PROFIL, Path(sortie), nom)
    return json.dumps({"docx": str(cible), "rapport": str(Path(sortie) / "rapport_maj.md")})
