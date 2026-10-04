"""Étape 4 — Contrôles et assemblage (sans appel à l'API).

- fidélité : le texte étiqueté (étape 3) est identique au texte transcrit
  (étape 2), aux espaces et au balisage près ;
- continuité des numéros de page ;
- noms de personnages hors de la liste fermée du profil, noms non étiquetés ;
- recollage des césures (fin de ligne et fin de page) ;
- répliques coupées entre deux pages (raccordées, ou signalées si douteuses) ;
- [illisible] restants.

Entrée  : 02_transcription/transcription.json, 03_structuration/structure.json
Sortie  : 04_controles/document.json  éléments assemblés, prêts pour le rendu
          04_controles/rapport.md     rapport lisible
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

# après un verbe, le trait d'union de fin de ligne est conservé : « écoute-|moi »
ENCLITIQUES = {"moi", "toi", "lui", "nous", "vous", "leur", "leurs", "le", "la", "les", "y", "en",
               "ci", "là", "même", "mêmes", "t-il", "t-elle", "t-on", "il", "elle", "on", "ils", "elles",
               "je", "tu", "ce"}
FIN_PHRASE = re.compile(r"[.!?…»:]\s*_?\s*$")
NOM_IMPRIME = re.compile(r"^([A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ][A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ' -]{2,})\.\s")


class Rapport:
    def __init__(self):
        self.lignes: dict[str, list[str]] = {}

    def ajout(self, rubrique: str, msg: str):
        self.lignes.setdefault(rubrique, []).append(msg)

    def nb(self, rubrique: str) -> int:
        return len(self.lignes.get(rubrique, []))


# --------------------------------------------------------------------------
# Césures
# --------------------------------------------------------------------------

def joindre(gauche: str, droite: str, ou: str, rap: Rapport) -> str:
    """Joint deux lignes ; recolle un mot coupé par un tiret de fin de ligne."""
    g, d = gauche.rstrip(), droite.lstrip()
    m = re.search(r"(\S*)-$", g)
    if not m or not re.search(r"[^\W\d_]-$", g):
        return f"{g} {d}" if g and d else g + d
    frag_g = m.group(1)
    frag_d = d.split(" ", 1)[0]
    # tiret conservé pour un mot composé : « c'est-|à-dire », « Lot-et-|Garonne »
    mot_d = frag_d.strip("_,.;:!?…»").lower()
    garde = ("-" in frag_g or "-" in frag_d.strip("_") or frag_d[:1].isupper()
             or mot_d in ENCLITIQUES)
    res = g + d if garde else g[:-1] + d
    mot = (frag_g + "-" + frag_d) if garde else (frag_g + frag_d)
    rap.ajout("Césures recollées",
              f"{ou} : « {frag_g}-|{frag_d} » → « {mot.strip('_,.;:!?…')} »"
              + (" (tiret conservé : mot composé ?)" if garde else ""))
    return res


def longueur_pleine(texte: str) -> float:
    """Longueur (caractères) d'une ligne pleine de la page, pour repérer les fins de paragraphe."""
    longs = sorted(len(l.replace("_", "").strip()) for l in texte.split("\n") if l.strip())
    return float(longs[int(0.9 * (len(longs) - 1))]) if longs else 0.0


def aplatir(texte: str, ou: str, rap: Rapport, pleine: float = 0.0, seuil: float = 0.8) -> str:
    """Joint les lignes d'un élément, césures recollées.

    Si `pleine` est donné (option de profil `paragraphes: lignes_courtes`),
    une ligne courte terminée par une ponctuation finale clôt un paragraphe :
    le suivant est séparé par un retour à la ligne.
    """
    paragraphes, prec = [""], ""
    for l in texte.split("\n"):
        if not l.strip():
            continue
        brut = l.replace("_", "").strip()
        if paragraphes[-1] and pleine and FIN_PHRASE.search(prec) and len(prec) < seuil * pleine:
            paragraphes.append(l.strip())
        else:
            paragraphes[-1] = joindre(paragraphes[-1], l, ou, rap) if paragraphes[-1] else l.strip()
        prec = brut
    return "\n".join(paragraphes)


# --------------------------------------------------------------------------
# Contrôles
# --------------------------------------------------------------------------

def _normal(s: str) -> str:
    return re.sub(r"[\s_]", "", s)


def controle_fidelite(trans: dict, struct: dict, ou: str, rap: Rapport):
    reconstitue = "".join(
        (f"{e['personnage']}." if e.get("personnage") else "") + e["texte"]
        for e in struct["elements"])
    a, b = _normal(trans["texte"]), _normal(reconstitue)
    if a == b:
        return
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            rap.ajout("Fidélité (texte modifié à l'étiquetage)",
                      f"{ou} : transcription « {a[max(0, i1 - 15):i2 + 15]} » / "
                      f"étiquetage « {b[max(0, j1 - 15):j2 + 15]} »")


def deduire_numeros(pages: list[dict], rap: Rapport) -> None:
    """Numérote une page illisible coincée entre deux pages numérotées (ou en fin de série)."""
    nums = [int(p["numero_page"]) if str(p.get("numero_page") or "").isdigit() else None for p in pages]
    for i, n in enumerate(nums):
        if n is not None:
            continue
        av = next((nums[j] + (i - j) for j in range(i - 1, -1, -1) if nums[j] is not None), None)
        ap = next((nums[j] - (j - i) for j in range(i + 1, len(nums)) if nums[j] is not None), None)
        if av is not None and (ap is None or ap == av):
            pages[i]["numero_page"] = str(av)
            pages[i]["numero_deduit"] = True
            nums[i] = av
            rap.ajout("Numéros de page déduits",
                      f"{pages[i]['image']} : p. {av} (numéro non lisible sur la photo"
                      + (", déduit des pages voisines)" if ap is not None else ", déduit de la page précédente)"))


def controle_pages(pages: list[dict], rap: Rapport):
    prec = None
    for pg in pages:
        n = pg.get("numero_page")
        if n is None or not str(n).isdigit():
            if prec is not None:
                rap.ajout("Numéros de page", f"page sans numéro après la p. {prec} ({pg['image']})")
            continue
        n = int(n)
        if prec is not None:
            if n == prec:
                rap.ajout("Numéros de page", f"p. {n} en double")
            elif n < prec:
                rap.ajout("Numéros de page", f"p. {n} après la p. {prec} (ordre des photos ?)")
            elif n > prec + 1:
                manque = f"{prec + 1}" if n == prec + 2 else f"{prec + 1} à {n - 1}"
                rap.ajout("Numéros de page", f"page(s) manquante(s) : {manque}")
        prec = n


def controle_noms(doc: list[dict], prof: dict, rap: Rapport):
    liste = [p.upper() for p in prof.get("personnages", [])]
    for e in doc:
        nom = e.get("personnage")
        if e["type"] == "replique" and nom and liste and nom.upper() not in liste:
            proche = difflib.get_close_matches(nom.upper(), liste, n=1)
            rap.ajout("Personnages hors liste",
                      f"p. {e['pages_txt']} : « {nom} »" + (f" (voulait dire {proche[0]} ?)" if proche else ""))
        if e["type"] == "replique" and not nom:
            rap.ajout("Personnages hors liste", f"p. {e['pages_txt']} : réplique sans personnage")
        for l in e["texte_brut"].split("\n"):
            m = NOM_IMPRIME.match(l.strip())
            if m:
                rap.ajout("Noms non étiquetés",
                          f"p. {e['pages_txt']} : ligne « {l.strip()[:60]} » commence par un nom "
                          f"mais est dans un élément « {e['type']} »")


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------

def _est_suite(e: dict, i: int) -> bool:
    return bool(e.get("suite")) or (e["type"] == "replique" and not e.get("personnage") and i == 0)


def _nom(e: dict) -> str:
    return f"{e['type']}" + (f" de {e['personnage']}" if e.get("personnage") else "")


def assembler(trans: list[dict], struct: list[dict], prof: dict, rap: Rapport) -> list[dict]:
    doc: list[dict] = []
    avec_suite = {t for t, e in prof["elements"].items() if "suite" in e.get("champs", [])}
    opt_par = prof.get("paragraphes") or {}
    par_courtes = opt_par.get("coupure") == "lignes_courtes"
    seuil = float(opt_par.get("seuil", 0.8))
    paires = [(t, s) for t, s in zip(trans, struct) if t["texte"].strip()]
    for t, _ in zip(trans, struct):
        if not t["texte"].strip():
            rap.ajout("Pages blanches", f"{t['image']} : aucun texte imprimé, page ignorée")
    trans = [dict(t) for t, _ in paires]
    struct = [s for _, s in paires]
    deduire_numeros(trans, rap)
    for k, (t, s) in enumerate(zip(trans, struct)):
        page = t["numero_page"] or f"[{Path(t['image']).stem[:3]}]"
        ou = f"p. {page}"
        pleine = longueur_pleine(t["texte"]) if par_courtes else 0.0
        controle_fidelite(t, s, ou, rap)
        for a in s.get("anomalies", []):
            rap.ajout("Fidélité (texte modifié à l'étiquetage)", f"{ou} : {a}")
        for i, e in enumerate(s["elements"]):
            suite = _est_suite(e, i)
            if suite and i > 0:
                rap.ajout("Éléments coupés entre pages", f"{ou} : élément « suite » au milieu de la page (ignoré)")
                suite = False
            if suite and doc and doc[-1]["type"] == e["type"]:
                prec = doc[-1]
                rap.ajout("Éléments coupés entre pages",
                          f"{_nom(prec)} raccordé(e) "
                          f"p. {prec['pages'][-1]} → p. {page}")
                prec["texte"] = joindre(prec["texte"], aplatir(e["texte"], ou, rap, pleine, seuil),
                                        f"p. {prec['pages'][-1]}→{page}", rap)
                prec["texte_brut"] += "\n" + e["texte"]
                prec["pages"].append(page)
                continue
            if suite:
                avant = doc[-1]["type"] if doc else "rien"
                rap.ajout("Éléments coupés entre pages",
                          f"{ou} : le haut de page ({e['type']}) semble continuer la page précédente, "
                          f"mais celle-ci finit par un autre type ({avant}) : gardé séparé")
            doc.append({"type": e["type"], "personnage": e.get("personnage"),
                        "texte": aplatir(e["texte"], ou, rap, pleine if e["type"] in avec_suite else 0.0, seuil),
                        "texte_brut": e["texte"],
                        "pages": [page]})
        # fin de page : réplique interrompue sans suite à la page suivante ?
        if k + 1 < len(struct) and doc:
            dernier = doc[-1]
            suivant = struct[k + 1]["elements"][:1]
            continuee = bool(suivant) and _est_suite(suivant[0], 0)
            if dernier["type"] in avec_suite and not FIN_PHRASE.search(dernier["texte"]) and not continuee:
                rap.ajout("Éléments coupés entre pages",
                          f"p. {page} : {_nom(dernier)} finit sans ponctuation "
                          f"(« …{dernier['texte'][-30:]} ») et la page suivante ne la continue pas"
                          " — page manquante ?")
    for n, e in enumerate(doc, 1):
        e["id"] = f"E{n:04d}"  # identifiant stable (signet dans le DOCX, base des mises à jour)
        e["pages_txt"] = e["pages"][0] if len(e["pages"]) == 1 else f"{e['pages'][0]}-{e['pages'][-1]}"
        if "[illisible]" in e["texte"]:
            rap.ajout("[illisible]", f"p. {e['pages_txt']} : « {e['texte'][:80]}… »")
    controle_pages(trans, rap)
    if prof.get("personnages"):
        controle_noms(doc, prof, rap)
    return doc


ORDRE = ["Fidélité (texte modifié à l'étiquetage)", "Numéros de page", "Personnages hors liste",
         "Noms non étiquetés", "Éléments coupés entre pages", "[illisible]", "Pages blanches",
         "Numéros de page déduits", "Césures recollées"]
A_VERIFIER = {"Fidélité (texte modifié à l'étiquetage)", "Numéros de page", "Personnages hors liste",
              "Noms non étiquetés", "[illisible]"}


def executer(sortie: Path, prof: dict) -> list[dict]:
    sys.stdout.reconfigure(encoding="utf-8")
    trans = json.loads((sortie / "02_transcription" / "transcription.json").read_text(encoding="utf-8"))
    struct = json.loads((sortie / "03_structuration" / "structure.json").read_text(encoding="utf-8"))
    rap = Rapport()
    doc = assembler(trans, struct, prof, rap)
    dossier = sortie / "04_controles"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "document.json").write_text(
        json.dumps([{k: v for k, v in e.items() if k != "texte_brut"} for e in doc],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Rapport de contrôle", ""]
    for r in ORDRE:
        n = rap.nb(r)
        statut = "✅" if n == 0 else ("⚠️" if r in A_VERIFIER else "ℹ️")
        md.append(f"## {statut} {r} ({n})")
        md += [f"- {l}" for l in rap.lignes.get(r, [])] or ["- rien à signaler"]
        md.append("")
        print(f"{statut} {r} : {n}")
    (dossier / "rapport.md").write_text("\n".join(md), encoding="utf-8")
    print(f"{len(doc)} éléments assemblés — rapport : {dossier / 'rapport.md'}")
    return doc
