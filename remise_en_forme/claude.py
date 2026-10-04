"""Accès à l'API Claude, commun à toutes les étapes.

Chaque étape qui interroge Claude fonctionne en trois temps :
  1. elle PRÉPARE une liste de requêtes (dict : cle, systeme, contenu, schema, modele, effort…) ;
  2. les requêtes sont ENVOYÉES — ici par `envoyer()` (ligne de commande, en
     parallèle), ou par le JavaScript de l'application web (même format,
     via `parametres()` et `lire_reponse()`) ;
  3. l'étape TERMINE avec les réponses (dict cle → JSON).

Chaque réponse est enregistrée dans JOURNAL (modèle, jetons, coût en $) ;
les étapes en écrivent le bilan dans leur dossier (couts.json).
"""
from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODELE = "claude-opus-5-5"

# $ par million de jetons (entrée, sortie, lecture du cache) — tarifs publics,
# à mettre à jour si besoin. L'écriture dans le cache coûte 1,25 × l'entrée.
TARIFS = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
# Modèles qui acceptent le réglage `effort` et le repli automatique en cas de refus
AVEC_EFFORT = {"claude-opus-5-5", "claude-sonnet-5-5"}
BETAS_REPLI = ["server-side-fallback-2026-07-01"]

JOURNAL: list[dict] = []


class Refus(RuntimeError):
    pass


def client():
    # Le réseau local intercepte le SSL : on utilise le magasin de certificats Windows.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")
    import anthropic
    return anthropic.Anthropic()


def image(chemin: Path) -> dict:
    data = base64.standard_b64encode(chemin.read_bytes()).decode()
    media = "image/png" if chemin.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def requete(cle: str, systeme: str, contenu: list, schema: dict, modele: str = MODELE,
            effort: str = "low", max_tokens: int = 16000) -> dict:
    return {"cle": cle, "systeme": systeme, "contenu": contenu, "schema": schema,
            "modele": modele, "effort": effort, "max_tokens": max_tokens}


def parametres(req: dict) -> dict:
    """Paramètres de l'appel `beta.messages.create` (identiques en Python et en JavaScript)."""
    p = {
        "model": req["modele"],
        "max_tokens": req["max_tokens"],
        # consignes identiques d'une page à l'autre : mises en cache (5 min)
        "system": [{"type": "text", "text": req["systeme"], "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": req["contenu"]}],
        "output_config": {"format": {"type": "json_schema", "schema": req["schema"]}},
    }
    if req["modele"] in AVEC_EFFORT:
        p["output_config"]["effort"] = req["effort"]
        p["betas"] = BETAS_REPLI
        p["fallbacks"] = "default"
    return p


def lire_reponse(req: dict, rep: dict) -> dict:
    """Enregistre le coût de la réponse (dict de l'API) et retourne le JSON produit."""
    u = rep.get("usage") or {}
    entree, sortie = u.get("input_tokens") or 0, u.get("output_tokens") or 0
    ecrit, lu = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
    pe, ps, pc = TARIFS.get(req["modele"], (0.0, 0.0, 0.0))
    cout = (entree * pe + ecrit * pe * 1.25 + lu * pc + sortie * ps) / 1e6
    if rep.get("_lot"):  # réponse obtenue par l'API Batch : facturée moitié prix
        cout /= 2
    JOURNAL.append({"etiquette": req["cle"], "modele": rep.get("model"), "effort": req.get("effort"),
                    "jetons_entree": entree, "jetons_cache_ecrits": ecrit, "jetons_cache_lus": lu,
                    "jetons_sortie": sortie, "cout_usd": round(cout, 5)})
    if rep.get("stop_reason") == "refusal":
        raise Refus(f"Refus du modèle pour {req['cle']} : {rep.get('stop_details')}")
    if rep.get("stop_reason") == "max_tokens":
        raise RuntimeError(f"Réponse tronquée pour {req['cle']} (max_tokens atteint)")
    texte = "".join(b.get("text", "") for b in rep.get("content", []) if b.get("type") == "text")
    return json.loads(texte)


def envoyer(requetes: list[dict], paralleles: int = 4) -> dict[str, dict]:
    """Envoie les requêtes (ligne de commande) ; retourne cle → JSON."""
    if not requetes:
        return {}
    cl = client()

    def un(req):
        p = parametres(req)
        if "betas" in p:
            rep = cl.beta.messages.create(**p)
        else:
            rep = cl.messages.create(**p)
        return req["cle"], lire_reponse(req, rep.to_dict())

    # la première seule, pour écrire le cache des consignes ; les suivantes en parallèle le lisent
    premiere = un(requetes[0])
    with ThreadPoolExecutor(max_workers=paralleles) as ex:
        reste = list(ex.map(un, requetes[1:]))
    return dict([premiere] + reste)


def bilan(dossier: Path, depuis: int = 0) -> None:
    """Écrit couts.json (appels de cette étape) et affiche le total."""
    appels = JOURNAL[depuis:]
    if not appels:
        return
    total = sum(a["cout_usd"] for a in appels)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "couts.json").write_text(json.dumps(
        {"total_usd": round(total, 4), "appels": appels}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Coût de l'étape : {total:.4f} $ pour {len(appels)} appel(s) "
          f"({total / len(appels):.4f} $ par appel)")
