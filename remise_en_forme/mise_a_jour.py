"""Mise à jour d'une version de référence à partir de pages annotées à la main.

  remise-en-forme mettre-a-jour REFERENCE.docx DOSSIER_ANNOTATIONS -p theatre

DOSSIER_ANNOTATIONS contient un sous-dossier par personne (le nom du
sous-dossier = l'auteur des modifications), avec les photos de ses pages
annotées — sur le livre d'origine ou sur la version imprimée par l'outil.

Pour chaque personne et chaque page :
  1. prétraitement + transcription du texte IMPRIMÉ (étapes 1 et 2) ;
  2. recalage : on retrouve dans la référence les éléments présents sur la page ;
  3. lecture des annotations manuscrites (un appel) → opérations :
     remplacer / supprimer / inserer_apres / note ;
  4. vérification : chaque opération doit viser un texte présent dans la référence.
Puis fusion de toutes les personnes :
  - opérations compatibles → suivi des modifications Word, au nom de l'auteur ;
  - même modification proposée par plusieurs → une seule, auteurs réunis ;
  - désaccord sur un même passage → rien d'appliqué, commentaire « Conflit » ;
  - notes de jeu → commentaires Word au nom de l'auteur.
Sorties : <sortie>/<nom>.docx (à valider dans Word), <sortie>/<nom>.pdf (lecture,
          modifications en couleur) et <sortie>/rapport_maj.md
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

from . import claude, pretraitement, reference, rendu, transcription

OUTIL = "remise-en-forme"

SYSTEME = """Tu lis les ANNOTATIONS MANUSCRITES portées sur une page imprimée \
d'une pièce ou d'un livre (photo d'une page annotée). On te donne aussi le \
texte de référence des éléments présents sur cette page, chacun avec son \
identifiant entre crochets [E0012].
Tu traduis chaque annotation en opération :
- remplacer : un passage barré et remplacé par un texte manuscrit. `ancien` = \
le passage barré, `nouveau` = le texte écrit à la main.
- supprimer : un passage barré sans remplacement. `ancien` = le passage ; \
`ancien` = null si tout l'élément est supprimé (élément entièrement barré, ou \
pris dans une coupe : crochets, accolade, grand trait sur plusieurs \
éléments → une opération « supprimer » par élément concerné).
- inserer_apres : texte manuscrit ajouté (marge, interligne, flèche) qui \
forme un nouvel élément. `id` = l'élément APRÈS lequel il s'insère, `type` = \
son type, `personnage` = le nom s'il s'agit d'une réplique, `nouveau` = le \
texte ajouté (sans le nom). Un ajout de quelques mots à l'intérieur d'un \
élément est un « remplacer » : `ancien` = les mots autour du point \
d'insertion, `nouveau` = ces mêmes mots avec l'ajout.
- note : indication de jeu ou de mise en scène (déplacement, intention, \
ton…) qui ne modifie pas le texte. `id` = l'élément concerné, `nouveau` = la \
note telle qu'écrite.
Règles absolues :
- `ancien` est recopié EXACTEMENT depuis le texte de référence (mêmes \
caractères), aussi court que possible mais sans ambiguïté dans l'élément.
- Le texte manuscrit est transcrit tel quel. Mot illisible → [illisible]. \
Ne devine jamais ; en cas de doute sur le sens d'une marque, `certitude` = \
« douteuse » et explique dans `remarques`.
- Ignore le texte imprimé non annoté, les soulignements simples, les coches, \
les numéros, les surlignages sans autre indication.
- S'il n'y a aucune annotation, renvoie une liste vide."""


def schema(types: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "operations": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["remplacer", "supprimer", "inserer_apres", "note"]},
                    "id": {"type": "string"},
                    "ancien": {"type": ["string", "null"]},
                    "nouveau": {"type": ["string", "null"]},
                    "type": {"anyOf": [{"type": "string", "enum": types}, {"type": "null"}]},
                    "personnage": {"type": ["string", "null"]},
                    "certitude": {"type": "string", "enum": ["certaine", "douteuse"]},
                },
                "required": ["action", "id", "ancien", "nouveau", "type", "personnage", "certitude"],
                "additionalProperties": False,
            }},
            "remarques": {"type": "string"},
        },
        "required": ["operations", "remarques"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# Recalage page ↔ référence
# --------------------------------------------------------------------------

def _mots(texte: str) -> list[str]:
    return [m for m in re.findall(r"[^\W\d_]+", texte.lower())]


def fenetre(texte_page: str, elements: list[dict], marge: int = 1) -> tuple[int, int] | None:
    """Indices [debut, fin] des éléments de la référence présents sur la page."""
    ref_mots, ref_elt = [], []
    for i, e in enumerate(elements):
        for m in _mots(f"{e.get('personnage') or ''} {e['texte']}"):
            ref_mots.append(m)
            ref_elt.append(i)
    page = _mots(texte_page)
    if not page or not ref_mots:
        return None
    sm = difflib.SequenceMatcher(None, ref_mots, page, autojunk=False)
    touches = set()
    for b in sm.get_matching_blocks():
        if b.size >= 4:  # au moins 4 mots consécutifs : évite les « Oui. » isolés
            touches.update(ref_elt[b.a:b.a + b.size])
    if not touches:
        return None
    return max(0, min(touches) - marge), min(len(elements) - 1, max(touches) + marge)


# --------------------------------------------------------------------------
# Lecture des annotations (préparer / terminer, comme les autres étapes)
# --------------------------------------------------------------------------

def _pages_fenetres(travail: Path, elements: list[dict]):
    """(page transcrite, fenêtre d'éléments ou None) pour chaque page non blanche."""
    pages = json.loads((travail / "02_transcription" / "transcription.json").read_text(encoding="utf-8"))
    for pg in pages:
        if pg["texte"].strip():
            f = fenetre(pg["texte"], elements)
            yield pg, (elements[f[0]:f[1] + 1] if f else None)


def _cache(travail: Path, force: bool) -> dict:
    cible = travail / "annotations.json"
    return json.loads(cible.read_text(encoding="utf-8")) if cible.exists() and not force else {}


def preparer_annotations(travail: Path, elements: list[dict], prof: dict, modele: str = claude.MODELE,
                         effort: str = "low", force: bool = False) -> list[dict]:
    """Une requête par page annotée (après transcription du texte imprimé)."""
    src, cache, types = travail / "01_pretraitement", _cache(travail, force), list(prof["elements"])
    requetes = []
    for pg, fen in _pages_fenetres(travail, elements):
        if fen is None or pg["image"] in cache:
            continue
        requetes.append(claude.requete(
            pg["image"], SYSTEME + "\n\nTypes d'éléments : " + ", ".join(types),
            transcription.images(src, pg["image"])
            + [{"type": "text", "text": "Texte de référence des éléments de cette page :\n<reference>\n"
                + reference.resume(fen) + "\n</reference>\nListe les annotations manuscrites."}],
            schema(types), modele, effort))
    return requetes


def terminer_annotations(travail: Path, elements: list[dict], reponses: dict[str, dict],
                         force: bool = False) -> tuple[list[dict], list[str]]:
    """Opérations proposées par une personne (toutes ses pages) + messages pour le rapport."""
    cache = _cache(travail, force)
    cache.update(reponses)
    (travail / "annotations.json").write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    ops, msgs = [], []
    for pg, fen in _pages_fenetres(travail, elements):
        if fen is None:
            msgs.append(f"{pg['image']} : page introuvable dans la référence (autre pièce ? page non transcrite ?)")
            continue
        res = cache.get(pg["image"])
        if res is None:
            continue
        if res.get("remarques"):
            msgs.append(f"{pg['image']} : {res['remarques']}")
        ids_fen = {e["id"] for e in fen}
        for op in res["operations"]:
            if op["id"] not in ids_fen:
                msgs.append(f"{pg['image']} : opération sur [{op['id']}] hors de la page, ignorée : {op}")
                continue
            ops.append({**op, "image": pg["image"]})
    return ops, msgs


def lire_annotations(dossier_auteur: Path, travail: Path, elements: list[dict], prof: dict,
                     modele: str, effort: str, force: bool) -> tuple[list[dict], list[str]]:
    """Ligne de commande : prétraitement, transcription, puis lecture des annotations."""
    if force or not (travail / "01_pretraitement" / "manifest.json").exists():
        pretraitement.executer(dossier_auteur, travail)
    transcription.executer(travail, force, modele, effort)
    reponses = claude.envoyer(preparer_annotations(travail, elements, prof, modele, effort, force))
    return terminer_annotations(travail, elements, reponses, force)


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------

def _localiser(texte: str, ancien: str) -> tuple[int, int] | None:
    """Position de `ancien` dans `texte` (exacte, sinon aux apostrophes/espaces/italique près)."""
    i = texte.find(ancien)
    if i >= 0:
        return i, i + len(ancien)
    # tolérance : ' ↔ ’, espaces multiples, balisage _italique_
    def variante(s):
        return re.escape(s.replace("’", "'").replace("_", "")).replace("'", "['’]").replace(r"\ ", r"_?\s+_?")
    m = re.search(variante(ancien), texte)
    return (m.start(), m.end()) if m else None


def _cle(op: dict) -> tuple:
    return (op["action"], op.get("ancien"), (op.get("nouveau") or "").strip(), op.get("personnage"), op.get("type"))


def fusionner(elements: list[dict], ops: list[dict], rap: dict) -> list[dict]:
    """Applique les opérations de toutes les personnes ; retourne les éléments à rendre."""
    par_id: dict[str, list[dict]] = {}
    for op in ops:
        par_id.setdefault(op["id"], []).append(op)

    # regroupe les propositions identiques de plusieurs personnes
    def regrouper(liste):
        groupes: dict[tuple, dict] = {}
        for op in liste:
            g = groupes.setdefault(_cle(op), {**op, "auteurs": []})
            if op["auteur"] not in g["auteurs"]:
                g["auteurs"].append(op["auteur"])
            if op["certitude"] == "douteuse":
                g["certitude"] = "douteuse"
        for g in groupes.values():
            g["auteur"] = ", ".join(g["auteurs"])
        return list(groupes.values())

    sortie: list[dict] = []
    pris = {e["id"] for e in elements}

    def nouvel_id(apres: str) -> str:
        """E0012 → E0012a, E0012b… (premier libre) ; reste un nom de signet Word valide."""
        lettre = "a"
        while f"{apres}{lettre}" in pris:
            lettre = chr(ord(lettre) + 1)
        pris.add(f"{apres}{lettre}")
        return f"{apres}{lettre}"

    for e in elements:
        e = {**e, "notes": list(e.get("notes", []))}
        liste = regrouper(par_id.get(e["id"], []))
        notes = [o for o in liste if o["action"] == "note"]
        inserts = [o for o in liste if o["action"] == "inserer_apres"]
        modifs = [o for o in liste if o["action"] in ("remplacer", "supprimer")]
        for o in notes:
            e["notes"].append({"auteur": o["auteur"], "texte": o["nouveau"] or ""})
            rap["Notes de jeu"].append(f"[{e['id']}] {o['auteur']} : {o['nouveau']}")

        totales = [o for o in modifs if o["action"] == "supprimer" and o["ancien"] is None]
        partielles = [o for o in modifs if o not in totales]
        if totales and (partielles or len(totales) > 1):
            _conflit(e, totales + partielles, rap)
        elif totales:
            o = totales[0]
            e["mode"], e["auteur"] = "del", o["auteur"]
            rap["Appliqué"].append(f"[{e['id']}] supprimé en entier — {o['auteur']}" + _doute(o, rap, e))
        elif partielles:
            _appliquer_partielles(e, partielles, rap)
        sortie.append(e)

        # insertions après cet élément
        for k, o in enumerate(inserts):
            nouveau = {"id": nouvel_id(e["id"]), "type": o.get("type") or "autre",
                       "personnage": o.get("personnage"), "texte": o.get("nouveau") or "",
                       "mode": "ins", "auteur": o["auteur"], "notes": []}
            if len(inserts) > 1:
                nouveau["notes"].append({"auteur": OUTIL, "texte":
                                         f"Plusieurs ajouts proposés au même endroit ({len(inserts)}) : vérifier l'ordre."})
                rap["À vérifier"].append(f"après [{e['id']}] : {len(inserts)} ajouts différents au même endroit")
            sortie.append(nouveau)
            rap["Appliqué"].append(f"après [{e['id']}] ajout ({nouveau['type']}"
                                   + (f", {nouveau['personnage']}" if nouveau["personnage"] else "")
                                   + f") « {nouveau['texte'][:60]} » — {o['auteur']}" + _doute(o, rap, e))
    return sortie


def _doute(o: dict, rap: dict, e: dict) -> str:
    if o["certitude"] == "douteuse" or "[illisible]" in (o.get("nouveau") or ""):
        cible = (f"« {o['ancien']} »" if o.get("ancien") else
                 "après cet élément" if o["action"] == "inserer_apres" else "tout l'élément")
        rap["À vérifier"].append(f"[{e['id']}] lecture douteuse ({o['auteur']}) : {o['action']} {cible}"
                                 + (f" → « {o['nouveau']} »" if o.get("nouveau") else "")
                                 + " — voir « Remarques de lecture »")
        return " ⚠ à vérifier"
    return ""


def _conflit(e: dict, liste: list[dict], rap: dict) -> None:
    props = "\n".join(f"- {o['auteur']} : {o['action']} « {o.get('ancien') or 'tout'} »"
                      + (f" → « {o['nouveau']} »" if o.get("nouveau") else "") for o in liste)
    e["notes"].append({"auteur": OUTIL, "texte": "Conflit, rien n'a été appliqué :\n" + props})
    rap["Conflits"].append(f"[{e['id']}]\n{props}")


def _appliquer_partielles(e: dict, ops: list[dict], rap: dict) -> None:
    texte = e["texte"]
    places = []
    for o in ops:
        pos = _localiser(texte, o["ancien"] or "")
        if pos is None:
            e["notes"].append({"auteur": o["auteur"], "texte":
                               f"Modification non localisée dans le texte : {o['action']} « {o['ancien']} »"
                               + (f" → « {o['nouveau']} »" if o.get("nouveau") else "")})
            rap["À vérifier"].append(f"[{e['id']}] passage introuvable « {o['ancien']} » ({o['auteur']}) → laissé en commentaire")
            continue
        places.append((pos[0], pos[1], o))
    places.sort(key=lambda p: p[0])
    # chevauchements entre auteurs différents → conflit sur ces opérations
    retenues, en_conflit = [], []
    for p in places:
        if retenues and p[0] < retenues[-1][1]:
            en_conflit += [retenues.pop()[2], p[2]]
        else:
            retenues.append(p)
    if en_conflit:
        _conflit(e, en_conflit, rap)
    # segments par paragraphe (le texte peut contenir des \n)
    segs, curseur = [], 0
    for d, f, o in retenues:
        segs.append((texte[curseur:d], None, ""))
        segs.append((texte[d:f], "del", o["auteur"]))
        if o["action"] == "remplacer" and o.get("nouveau"):
            segs.append((o["nouveau"], "ins", o["auteur"]))
        curseur = f
        rap["Appliqué"].append(f"[{e['id']}] {o['action']} « {o['ancien']} »"
                               + (f" → « {o['nouveau']} »" if o.get("nouveau") else "")
                               + f" — {o['auteur']}" + _doute(o, rap, e))
    segs.append((texte[curseur:], None, ""))
    paragraphes = [[]]
    for t, mode, auteur in segs:
        morceaux = t.split("\n")
        for k, m in enumerate(morceaux):
            if k:
                paragraphes.append([])
            if m:
                paragraphes[-1].append((m, mode, auteur))
    e["segments"] = paragraphes


# --------------------------------------------------------------------------
# Commande
# --------------------------------------------------------------------------

RUBRIQUES = ["Conflits", "À vérifier", "Appliqué", "Notes de jeu", "Remarques de lecture"]


def finaliser(nom_reference: str, elements: list[dict], lectures: dict[str, tuple[list[dict], list[str]]],
              prof: dict, sortie: Path, nom: str) -> Path:
    """Fusionne les lectures de toutes les personnes (auteur → (opérations, messages)),
    écrit le DOCX à valider et le rapport."""
    sys.stdout.reconfigure(encoding="utf-8")
    sortie.mkdir(parents=True, exist_ok=True)
    rap = {r: [] for r in RUBRIQUES}
    toutes = []
    for auteur, (ops, msgs) in lectures.items():
        for o in ops:
            o["auteur"] = auteur
        toutes += ops
        rap["Remarques de lecture"] += [f"{auteur} — {m}" for m in msgs]
    resultat = fusionner(elements, toutes, rap)
    cible = sortie / f"{nom}.docx"
    rendu.construire(resultat, prof).save(str(cible))
    from . import pdf
    cible.with_suffix(".pdf").write_bytes(pdf.construire(resultat, prof, nom))

    md = [f"# Mise à jour de {nom_reference}", "",
          f"Personnes : {', '.join(lectures)} — {len(toutes)} opération(s) lue(s).", "",
          "Ouvrir le DOCX dans Word : Révision > accepter / refuser chaque modification, "
          "lire les commentaires, puis enregistrer. Ce fichier devient la nouvelle référence.", ""]
    for r in RUBRIQUES:
        md.append(f"## {r} ({len(rap[r])})")
        md += [f"- {l}" for l in rap[r]] or ["- rien"]
        md.append("")
    (sortie / "rapport_maj.md").write_text("\n".join(md), encoding="utf-8")
    for r in RUBRIQUES:
        print(f"{r} : {len(rap[r])}")
    return cible


def executer(reference_docx: Path, annotations: Path, prof: dict, sortie: Path, nom: str,
             modele: str = claude.MODELE, effort: str = "low", force: bool = False) -> Path:
    sys.stdout.reconfigure(encoding="utf-8")
    debut_journal = len(claude.JOURNAL)
    elements = reference.importer(reference_docx, prof)
    print(f"Référence : {reference_docx.name} — {len(elements)} éléments")
    auteurs = sorted(d for d in annotations.iterdir() if d.is_dir())
    if not auteurs:  # photos directement dans le dossier : un seul auteur
        auteurs = [annotations]
    lectures = {}
    for d in auteurs:
        print(f"— Annotations de {d.name}")
        lectures[d.name] = lire_annotations(d, sortie / "travail" / d.name, elements, prof, modele, effort, force)
        print(f"  {len(lectures[d.name][0])} opération(s) lue(s)")
    cible = finaliser(reference_docx.name, elements, lectures, prof, sortie, nom)
    claude.bilan(sortie, debut_journal)
    print(f"DOCX à valider : {cible}\nRapport : {sortie / 'rapport_maj.md'}")
    return cible
