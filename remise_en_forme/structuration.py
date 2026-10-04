"""Étape 3 — Structuration : étiquetage de chaque page selon le profil (JSON).

Entrée  : <sortie>/02_transcription/transcription.json
Sortie  : <sortie>/03_structuration/<page>.json   éléments d'une page
          <sortie>/03_structuration/structure.json toutes les pages, dans l'ordre

Le modèle reçoit les lignes numérotées et ne renvoie que des plages de lignes
étiquetées (« lignes 3 à 5 : réplique d'ANTOINE »). Le texte des éléments est
découpé ici, dans le code, à partir de la transcription : le modèle ne peut
donc rien modifier, et sa réponse est courte (donc peu coûteuse).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import claude, profil

SYSTEME = """Tu étiquettes le texte d'UNE page de livre selon un profil.
Le texte t'est donné ligne par ligne, chaque ligne précédée de son numéro.
Tu renvoies la liste ordonnée des éléments de la page ; chaque élément est \
une plage de lignes consécutives [debut, fin] (numéros inclus) et un type.
Règles :
- Ne recopie pas le texte : seulement les numéros de lignes et les étiquettes.
- Chaque ligne non vide appartient à exactement un élément ; les plages se \
suivent dans l'ordre, sans chevauchement. Les lignes vides peuvent être \
incluses ou non dans une plage, peu importe.
- Un élément commence toujours en début de ligne.
- En cas de doute sur le type, utilise « autre ».

"""


def numeroter(texte: str) -> tuple[list[str], str]:
    lignes = texte.split("\n")
    return lignes, "\n".join(f"{i:>3}| {l}" for i, l in enumerate(lignes, 1))


def decouper(lignes: list[str], etiquettes: list[dict], prof: dict) -> tuple[list[dict], list[str]]:
    """Construit les éléments à partir des plages ; retourne (éléments, anomalies)."""
    anomalies, elements, couvertes = [], [], set()
    n = len(lignes)
    for e in etiquettes:
        d, f = int(e.pop("debut")), int(e.pop("fin"))
        if not (1 <= d <= f <= n):
            anomalies.append(f"plage invalide {d}-{f} ({e['type']})")
            continue
        if couvertes & set(range(d, f + 1)):
            anomalies.append(f"lignes {d}-{f} étiquetées deux fois")
        couvertes |= set(range(d, f + 1))
        texte = "\n".join(lignes[d - 1:f]).strip("\n")
        nom = e.get("personnage")
        if nom:
            # le nom imprimé en tête (« ANTOINE. ») passe dans `personnage`
            m = re.match(rf"\s*{re.escape(nom)}\s*\.\s*", texte)
            if m:
                texte = texte[m.end():]
            else:
                anomalies.append(f"ligne {d} : le nom « {nom} » n'est pas en tête de l'élément")
        elements.append({**e, "texte": texte, "lignes": [d, f]})
    oubliees = [i for i in range(1, n + 1) if lignes[i - 1].strip() and i not in couvertes]
    if oubliees:
        anomalies.append(f"lignes non étiquetées : {oubliees} — ajoutées comme « autre »")
        for i in oubliees:
            elements.append({"type": "autre", **{c: (None if prof["champs"][c]["type"] == "texte" else False)
                                                   for c in prof.get("champs", {})},
                             "texte": lignes[i - 1], "lignes": [i, i]})
        elements.sort(key=lambda e: e["lignes"][0])
    return elements, anomalies


def _pages(sortie: Path) -> list[dict]:
    return json.loads((sortie / "02_transcription" / "transcription.json").read_text(encoding="utf-8"))


def preparer(sortie: Path, prof: dict, force: bool = False, modele: str = claude.MODELE,
             effort: str = "low") -> list[dict]:
    """Requêtes pour les pages non blanches pas encore structurées (toutes avec `force`)."""
    dossier = sortie / "03_structuration"
    systeme = SYSTEME + profil.description(prof)
    schema = profil.schema_elements(prof)
    requetes = []
    for pg in _pages(sortie):
        if not pg["texte"].strip():
            continue
        if (dossier / f"{Path(pg['image']).stem}.json").exists() and not force:
            continue
        _, numerote = numeroter(pg["texte"])
        requetes.append(claude.requete(
            pg["image"], systeme,
            [{"type": "text", "text": f"Lignes de la page :\n<page>\n{numerote}\n</page>"}],
            schema, modele, effort, max_tokens=8000))
    return requetes


def terminer(sortie: Path, prof: dict, reponses: dict[str, dict]) -> list[dict]:
    """Découpe le texte selon les plages reçues (cle = image de la page) ; écrit structure.json."""
    sys.stdout.reconfigure(encoding="utf-8")
    dossier = sortie / "03_structuration"
    dossier.mkdir(parents=True, exist_ok=True)
    pages = _pages(sortie)
    resultats = []
    for i, pg in enumerate(pages, 1):
        cible = dossier / f"{Path(pg['image']).stem}.json"
        if not pg["texte"].strip():
            res, etat = {"image": pg["image"], "numero_page": pg["numero_page"], "elements": [],
                         "anomalies": []}, "page blanche"
        elif pg["image"] in reponses:
            lignes, _ = numeroter(pg["texte"])
            elements, anomalies = decouper(lignes, reponses[pg["image"]]["elements"], prof)
            res = {"image": pg["image"], "numero_page": pg["numero_page"],
                   "elements": elements, "anomalies": anomalies}
            cible.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            etat = "structurée"
        else:
            res, etat = json.loads(cible.read_text(encoding="utf-8")), "déjà fait"
        compte = {}
        for e in res["elements"]:
            compte[e["type"]] = compte.get(e["type"], 0) + 1
        print(f"[{i}/{len(pages)}] p. {pg['numero_page'] or '?'} — {etat} — "
              + ", ".join(f"{n} {t}" for t, n in compte.items())
              + "".join(f"\n      ⚠ {a}" for a in res.get("anomalies", [])))
        resultats.append(res)
    (dossier / "structure.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultats


def executer(sortie: Path, prof: dict, force: bool = False, modele: str = claude.MODELE,
             effort: str = "low") -> list[dict]:
    debut_journal = len(claude.JOURNAL)
    reponses = claude.envoyer(preparer(sortie, prof, force, modele, effort))
    resultats = terminer(sortie, prof, reponses)
    claude.bilan(sortie / "03_structuration", debut_journal)
    return resultats
