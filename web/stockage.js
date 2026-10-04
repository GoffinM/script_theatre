// Stockage durable sur l'appareil (IndexedDB), utilisé par la page ET par le moteur :
//  - « brouillons » : photos en cours de sélection (survivent à un rechargement de la page) ;
//  - « travaux »    : le traitement lancé (entrées, mode, lots confiés à Anthropic, état) ;
//  - « reponses »   : chaque réponse de Claude dès sa réception — un traitement interrompu
//                     reprend là où il s'était arrêté, sans repayer ce qui est fait ;
//  - « archives »   : chaque résultat produit, consultable dans « Mes documents ».
// Tout reste sur l'appareil ; rien n'est envoyé ailleurs.

const BASE = "remise-en-forme";
const VERSION = 2;
let ouverture = null;

function base() {
  if (!ouverture) {
    ouverture = new Promise((ok, ko) => {
      const req = indexedDB.open(BASE, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("brouillons")) db.createObjectStore("brouillons");
        if (!db.objectStoreNames.contains("archives")) db.createObjectStore("archives", { keyPath: "id" });
        if (!db.objectStoreNames.contains("travaux")) db.createObjectStore("travaux", { keyPath: "id" });
        if (!db.objectStoreNames.contains("reponses")) db.createObjectStore("reponses");
      };
      req.onsuccess = () => ok(req.result);
      req.onerror = () => ko(req.error);
    });
    // page seulement : demande au navigateur de ne pas effacer ces données en cas de manque de place
    globalThis.navigator?.storage?.persist?.().catch(() => {});
  }
  return ouverture;
}

async function operation(magasin, mode, action) {
  const db = await base();
  return new Promise((ok, ko) => {
    const tx = db.transaction(magasin, mode);
    const req = action(tx.objectStore(magasin));
    tx.oncomplete = () => ok(req?.result);
    tx.onerror = () => ko(tx.error);
    tx.onabort = () => ko(tx.error);
  });
}

// Les erreurs de stockage (navigation privée, quota) ne doivent jamais bloquer l'application.
async function sur(promesse, defaut) {
  try { return await promesse; } catch (e) { console.warn("Stockage indisponible :", e); return defaut; }
}

const plage = (prefixe) => IDBKeyRange.bound(prefixe, prefixe + "￿");

export const brouillons = {
  lire: (cle) => sur(operation("brouillons", "readonly", (s) => s.get(cle)), undefined),
  ecrire: (cle, valeur) => sur(operation("brouillons", "readwrite", (s) => s.put(valeur, cle))),
  effacer: (cle) => sur(operation("brouillons", "readwrite", (s) => s.delete(cle))),
};

export const archives = {
  // fiche : {id, date, type: "nouveau"|"maj", nom, cout, fichiers: [{nom, type, blob}]}
  ajouter: (fiche) => sur(operation("archives", "readwrite", (s) => s.put(fiche))),
  toutes: async () => {
    const liste = await sur(operation("archives", "readonly", (s) => s.getAll()), []);
    return (liste || []).sort((a, b) => b.date - a.date);
  },
  supprimer: (id) => sur(operation("archives", "readwrite", (s) => s.delete(id))),
};

export const travaux = {
  // travail : {id, date, type, nom, mode: "rapide"|"lot", etat: "en_cours"|"attente"|"erreur",
  //            message, entrees: {...}, lots: {etape: {id, correspondance}}}
  ecrire: (t) => sur(operation("travaux", "readwrite", (s) => s.put(t))),
  lire: (id) => sur(operation("travaux", "readonly", (s) => s.get(id)), undefined),
  tous: async () => (await sur(operation("travaux", "readonly", (s) => s.getAll()), [])) || [],
  supprimer: async (id) => {
    await sur(operation("reponses", "readwrite", (s) => s.delete(plage(`${id}|`))));
    await sur(operation("travaux", "readwrite", (s) => s.delete(id)));
  },
};

export const reponses = {
  ecrire: (travail, etape, cle, rep) =>
    sur(operation("reponses", "readwrite", (s) => s.put(rep, `${travail}|${etape}|${cle}`))),
  // toutes les réponses déjà reçues pour une étape : {cle: réponse}
  lire: async (travail, etape) => {
    const db = await base().catch(() => null);
    if (!db) return {};
    const prefixe = `${travail}|${etape}|`;
    return new Promise((ok) => {
      const out = {};
      const req = db.transaction("reponses").objectStore("reponses").openCursor(plage(prefixe));
      req.onsuccess = () => {
        const c = req.result;
        if (!c) return ok(out);
        out[c.key.slice(prefixe.length)] = c.value;
        c.continue();
      };
      req.onerror = () => ok(out);
    });
  },
};
