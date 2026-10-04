/**
 * Relais « remise en forme » → API Anthropic (mode « en arrière-plan »)
 * ----------------------------------------------------------------------
 * L'API Batch d'Anthropic (traitements en arrière-plan, moitié prix) refuse les
 * appels directs depuis un navigateur. Ce relais transmet simplement la requête
 * de l'application à api.anthropic.com et renvoie la réponse avec les en-têtes
 * CORS nécessaires.
 *
 * Il ne stocke JAMAIS aucune clé : elle transite telle qu'envoyée par l'application
 * (en-tête x-api-key), sans être journalisée ni conservée. Seules les adresses de
 * l'API Messages / Batch sont relayées, et seulement pour les origines autorisées.
 *
 * Déploiement (gratuit, ~5 minutes) :
 * 1. https://dash.cloudflare.com → Workers & Pages → Create → « Start with Hello World! »
 * 2. Nom : par exemple « remise-relais »
 * 3. Remplacer le code par défaut par ce fichier entier → Deploy
 * 4. Copier l'adresse du Worker (https://remise-relais.<votre-compte>.workers.dev)
 * 5. Dans l'application : Réglages → Exécution « En arrière-plan » → coller l'adresse
 *
 * Si l'application est publiée ailleurs que sur goffinm.github.io, ajouter son
 * origine dans ORIGINES ci-dessous.
 */

const ORIGINES = ["https://goffinm.github.io", "http://localhost:8765"];
const CHEMINS = /^\/v1\/messages(\/batches(\/[A-Za-z0-9_-]+(\/results|\/cancel)?)?)?$/;
const TRANSMIS = ["x-api-key", "anthropic-version", "anthropic-beta", "content-type"];

function cors(origine) {
  return {
    "Access-Control-Allow-Origin": origine,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "x-api-key, anthropic-version, anthropic-beta, content-type, "
      + "anthropic-dangerous-direct-browser-access, x-stainless-arch, x-stainless-lang, x-stainless-os, "
      + "x-stainless-package-version, x-stainless-retry-count, x-stainless-runtime, "
      + "x-stainless-runtime-version, x-stainless-timeout",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

export default {
  async fetch(requete) {
    const origine = requete.headers.get("Origin") || "";
    if (!ORIGINES.includes(origine)) return new Response("Origine non autorisée.", { status: 403 });
    if (requete.method === "OPTIONS") return new Response(null, { headers: cors(origine) });

    const url = new URL(requete.url);
    if (!CHEMINS.test(url.pathname) || !["GET", "POST"].includes(requete.method)) {
      return new Response("Adresse non relayée.", { status: 404, headers: cors(origine) });
    }
    const entetes = new Headers();
    for (const h of TRANSMIS) {
      const v = requete.headers.get(h);
      if (v) entetes.set(h, v);
    }
    const reponse = await fetch("https://api.anthropic.com" + url.pathname + url.search, {
      method: requete.method,
      headers: entetes,
      body: requete.method === "POST" ? requete.body : undefined,
    });
    const retour = new Headers(cors(origine));
    const type = reponse.headers.get("content-type");
    if (type) retour.set("content-type", type);
    return new Response(reponse.body, { status: reponse.status, headers: retour });
  },
};
