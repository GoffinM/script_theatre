// Stockage durable sur l'appareil (IndexedDB) :
//  - « brouillons » : photos en cours de sélection, pour les retrouver si le
//    navigateur recharge la page (fréquent au retour de l'appareil photo) ;
//  - « archives »   : chaque résultat produit (fichiers, date, coût), consultable
//    ensuite dans « Mes documents ».
// Tout reste sur l'appareil ; rien n'est envoyé ailleurs.

const BASE = "remise-en-forme";
const VERSION = 1;
let ouverture = null;

function base() {
  if (!ouverture) {
    ouverture = new Promise((ok, ko) => {
      const req = indexedDB.open(BASE, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("brouillons")) db.createObjectStore("brouillons");
        if (!db.objectStoreNames.contains("archives")) db.createObjectStore("archives", { keyPath: "id" });
      };
      req.onsuccess = () => ok(req.result);
      req.onerror = () => ko(req.error);
    });
    // demande au navigateur de ne pas effacer ces données en cas de manque de place
    navigator.storage?.persist?.().catch(() => {});
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
