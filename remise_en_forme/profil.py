"""Chargement d'un profil déclaratif (YAML) et dérivation du schéma JSON."""
from __future__ import annotations

from pathlib import Path

import yaml

TYPES_CHAMPS = {"texte": {"type": ["string", "null"]}, "booleen": {"type": "boolean"}}


def charger(nom_ou_chemin: str) -> dict:
    p = Path(nom_ou_chemin)
    if not p.suffix:
        p = Path(__file__).resolve().parent.parent / "profils" / f"{nom_ou_chemin}.yaml"
    return charger_texte(p.read_text(encoding="utf-8"), p.parent, p.name)


def charger_texte(texte: str, dossier: Path | str = ".", nom: str = "profil") -> dict:
    """Profil depuis son texte YAML (utilisé aussi par l'application web)."""
    prof = yaml.safe_load(texte)
    if not isinstance(prof, dict) or "elements" not in prof:
        raise ValueError(f"Profil {nom} : section `elements` manquante")
    prof["_dossier"] = str(dossier)
    for t, e in prof["elements"].items():
        for c in e.get("champs", []):
            if c not in prof.get("champs", {}):
                raise ValueError(f"Profil {nom} : champ « {c} » de « {t} » non déclaré dans `champs`")
    return prof


def schema_elements(prof: dict) -> dict:
    """Schéma de sortie de l'étape 3 : plages de lignes étiquetées, dans l'ordre."""
    props = {
        "type": {"type": "string", "enum": list(prof["elements"])},
        "debut": {"type": "integer"},
        "fin": {"type": "integer"},
    }
    for nom, c in prof.get("champs", {}).items():
        props[nom] = dict(TYPES_CHAMPS[c["type"]])
    return {
        "type": "object",
        "properties": {
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["elements"],
        "additionalProperties": False,
    }


def description(prof: dict) -> str:
    """Description textuelle du profil pour la consigne de l'étape 3."""
    lignes = [f"Profil : {prof['nom']} — {prof.get('description', '')}", "",
              "Types d'éléments autorisés :"]
    for t, e in prof["elements"].items():
        ch = ", ".join(e.get("champs", []))
        lignes.append(f"- {t}{f' (champs : {ch})' if ch else ''} : {' '.join(e['description'].split())}")
    if prof.get("champs"):
        lignes += ["", "Champs :"]
        for nom, c in prof["champs"].items():
            lignes.append(f"- {nom} ({c['type']}) : {c['description']} "
                          f"Pour les types qui n'utilisent pas ce champ : "
                          f"{'null' if c['type'] == 'texte' else 'false'}.")
    if prof.get("personnages"):
        lignes += ["", "Personnages attendus (pour information — étiquette le nom TEL QU'IMPRIMÉ, "
                   "même s'il diffère) : " + ", ".join(prof["personnages"])]
    if prof.get("conventions"):
        lignes += ["", "Conventions du texte source :", prof["conventions"].strip()]
    return "\n".join(lignes)
