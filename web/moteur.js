// Moteur de l'application web, exécuté dans un Web Worker (l'écran reste fluide).
// Il charge Python (Pyodide) avec le code de remise_en_forme/, et fait les appels
// à Claude avec le SDK JavaScript officiel, directement depuis le navigateur.
//
// Un traitement (« travail ») est décrit dans IndexedDB (web/stockage.js) ; chaque
// réponse de Claude y est enregistrée dès sa réception. Relancer un travail interrompu
// refait les calculs locaux (rapides, déterministes) et ne renvoie à Claude que les
// requêtes sans réponse.
//
// Mode « lot » (arrière-plan) : la première étape (la plus lourde) est confiée à
// l'API Batch d'Anthropic, qui travaille même téléphone fermé ; au retour, le travail
// reprend et termine directement. L'API Batch refuse les appels directs depuis un
// navigateur : elle passe par un relais (outils/relais-cloudflare.js).
//
// Messages reçus  : {type: "init"} | {type: "lancer", id, cle, adresseApi, relais}
// Messages envoyés : {type: "log"|"etape"|"pret"|"fini"|"attente"|"erreur", …}

import { reponses, travaux } from "./stockage.js";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const SDK = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.131.0/+esm";
const RACINE = new URL("../", import.meta.url);
const SOURCES = [
  "remise_en_forme/__init__.py", "remise_en_forme/claude.py", "remise_en_forme/controles.py",
  "remise_en_forme/mise_a_jour.py", "remise_en_forme/pdf.py", "remise_en_forme/pretraitement.py",
  "remise_en_forme/profil.py",
  "remise_en_forme/reference.py", "remise_en_forme/rendu.py", "remise_en_forme/structuration.py",
  "remise_en_forme/transcription.py", "remise_en_forme/web.py",
];
// Bibliothèques Python fournies avec le site (pur Python)
const ROUES = ["defusedxml-0.7.1-py2.py3-none-any.whl", "python_docx-1.2.0-py3-none-any.whl",
               "fpdf2-2.8.9-py3-none-any.whl"];
const POLICES = ["EBGaramond-Regular.ttf", "EBGaramond-Bold.ttf", "EBGaramond-Italic.ttf", "EBGaramond-BoldItalic.ttf"];
const PARALLELES = 4;
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

let py = null;
let web = null;
let Anthropic = null;

const envoyer = (m) => self.postMessage(m);
const log = (texte) => envoyer({ type: "log", texte });

class EnAttente extends Error {}

// Téléchargement d'un fichier du site, avec nouvelles tentatives (réseau mobile instable).
// Le contenu est lu DANS la boucle : une coupure en plein transfert est aussi retentée.
async function charger(chemin, format = "octets", essais = 4) {
  for (let i = 1; ; i++) {
    try {
      const rep = await fetch(new URL(chemin, RACINE));
      if (rep.status === 404) throw Object.assign(new Error(`Fichier introuvable sur le site : ${chemin}`), { definitif: true });
      if (rep.ok) return format === "texte" ? await rep.text() : await rep.arrayBuffer();
      throw new Error(`HTTP ${rep.status}`);
    } catch (e) {
      if (e.definitif) throw e;
      if (i >= essais) throw new Error(`Téléchargement impossible (${chemin}) : ${e.message}`);
    }
    await new Promise((r) => setTimeout(r, 800 * i));
  }
}

async function init() {
  envoyer({ type: "etape", texte: "Chargement de Python (premier lancement : ~40 Mo)…", fait: 0, total: 4 });
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({ indexURL: PYODIDE, stdout: log, stderr: log });
  envoyer({ type: "etape", texte: "Chargement d'OpenCV et des bibliothèques…", fait: 1, total: 4 });
  await py.loadPackage(["numpy", "opencv-python", "pyyaml", "lxml", "typing-extensions", "pillow", "fonttools"]);
  // python-docx, fpdf2… (fournis avec le site) : décompressés directement, sans installateur —
  // micropip interroge PyPI, ce qui échoue sur les réseaux filtrés.
  // décompressées comme de simples zip dans site-packages : le format « wheel » de Pyodide
  // vérifie les dépendances et va chercher sur PyPI celles qu'il ne connaît pas.
  const sitePackages = py.runPython("import site; site.getsitepackages()[0]");
  for (const nom of ROUES) {
    py.unpackArchive(await charger(`web/wheels/${nom}`), "zip", { extractDir: sitePackages });
  }
  envoyer({ type: "etape", texte: "Chargement de l'outil…", fait: 2, total: 4 });
  py.FS.mkdirTree("/app/remise_en_forme");
  py.FS.mkdirTree("/app/polices");
  for (const nom of POLICES) {
    py.FS.writeFile(`/app/polices/${nom}`, new Uint8Array(await charger(`polices/${nom}`)));
  }
  const version = Date.now(); // évite un code périmé en cache après une mise à jour du site
  for (const f of SOURCES) {
    py.FS.writeFile("/app/" + f, await charger(f + "?v=" + version, "texte"));
  }
  py.runPython("import sys; sys.path.insert(0, '/app')");
  web = py.pyimport("remise_en_forme.web");
  envoyer({ type: "etape", texte: "Chargement du SDK Claude…", fait: 3, total: 4 });
  Anthropic = (await import(SDK)).default;
  envoyer({ type: "pret" });
}

// ---------------------------------------------------------------------------
// Fichiers (système de fichiers virtuel de Pyodide)
// ---------------------------------------------------------------------------

function viderDossier(chemin) {
  const { FS } = py;
  if (!FS.analyzePath(chemin).exists) return;
  for (const n of FS.readdir(chemin)) {
    if (n === "." || n === "..") continue;
    const p = `${chemin}/${n}`;
    if (FS.isDir(FS.stat(p).mode)) viderDossier(p), FS.rmdir(p);
    else FS.unlink(p);
  }
}

async function deposer(dossier, photos) {
  // photos : [{nom, blob}] — préfixe numérique pour garder l'ordre choisi
  py.FS.mkdirTree(dossier);
  for (const [i, p] of photos.entries()) {
    const nom = `${String(i + 1).padStart(3, "0")}_${p.nom.replace(/[^\w.-]+/g, "_")}`;
    py.FS.writeFile(`${dossier}/${nom}`, new Uint8Array(await p.blob.arrayBuffer()));
  }
}

function lire(chemin, nom, type) {
  const octets = py.FS.readFile(chemin);
  return { nom, type, octets: octets.buffer.slice(octets.byteOffset, octets.byteOffset + octets.byteLength) };
}

// ---------------------------------------------------------------------------
// Appels à Claude : réponses mémorisées, directement ou par lot
// ---------------------------------------------------------------------------

let contexte = null; // {travail, client, relais, cle}

async function appeler(etape, requetesJson, libelle, lotPossible = false) {
  const { travail } = contexte;
  const requetes = JSON.parse(requetesJson);
  const recues = await reponses.lire(travail.id, etape);
  const manquantes = requetes.filter((r) => !(r.cle in recues));
  if (manquantes.length) {
    if (travail.mode === "lot" && lotPossible) await parLot(etape, manquantes, recues, libelle);
    else await directement(etape, manquantes, recues, libelle, requetes.length);
  } else if (requetes.length) {
    log(`${libelle} : déjà fait (réponses mémorisées).`);
  }
  return JSON.stringify(Object.fromEntries(requetes.map((r) => [r.cle, recues[r.cle]])));
}

async function directement(etape, requetes, recues, libelle, total) {
  const { travail, client } = contexte;
  let fait = total - requetes.length;
  envoyer({ type: "etape", texte: libelle, fait, total });
  const une = async (r) => {
    const p = r.params;
    const rep = p.betas ? await client.beta.messages.create(p) : await client.messages.create(p);
    recues[r.cle] = rep;
    await reponses.ecrire(travail.id, etape, r.cle, rep);
    envoyer({ type: "etape", texte: libelle, fait: ++fait, total });
  };
  // la première seule (elle écrit le cache des consignes), puis les autres en parallèle
  const file = [...requetes];
  await une(file.shift());
  await Promise.all(Array.from({ length: Math.min(PARALLELES, file.length) }, async () => {
    while (file.length) await une(file.shift());
  }));
}

async function parLot(etape, requetes, recues, libelle) {
  const { travail, relais, cle } = contexte;
  if (!relais) throw new Error("Le mode « en arrière-plan » demande l'adresse du relais (Réglages).");
  const viaRelais = new Anthropic({ apiKey: cle, baseURL: relais, dangerouslyAllowBrowser: true, maxRetries: 3 });
  travail.lots ??= {};
  let lot = travail.lots[etape];
  if (!lot) {
    // identifiants de lot : [a-zA-Z0-9_-]{1,64} ; la correspondance avec nos clés est mémorisée
    const correspondance = {};
    const demandes = requetes.map((r, i) => {
      const { betas, fallbacks, ...params } = r.params; // ni bêta ni repli sur l'API Batch
      correspondance[`r${i}`] = r.cle;
      return { custom_id: `r${i}`, params };
    });
    envoyer({ type: "etape", texte: `${libelle} : envoi du lot à Anthropic…`, fait: 0, total: 1 });
    const b = await viaRelais.messages.batches.create({ requests: demandes });
    lot = travail.lots[etape] = { id: b.id, correspondance, envoye: Date.now() };
    await travaux.ecrire(travail);
    log(`Lot ${b.id} confié à Anthropic (${demandes.length} requête(s)).`);
  }
  const etat = await viaRelais.messages.batches.retrieve(lot.id);
  if (etat.processing_status !== "ended") {
    const c = etat.request_counts;
    const total = c.processing + c.succeeded + c.errored + c.canceled + c.expired;
    throw new EnAttente(`${libelle} : en cours chez Anthropic (${total - c.processing}/${total} terminées).`);
  }
  // Résultats (JSONL) lus via le relais : l'adresse absolue `results_url` fournie par
  // l'API (que suivrait le SDK) n'est pas accessible depuis un navigateur.
  const rep = await fetch(`${relais.replace(/\/$/, "")}/v1/messages/batches/${lot.id}/results`, {
    headers: { "x-api-key": cle, "anthropic-version": "2023-06-01" },
  });
  if (!rep.ok) throw new Error(`Résultats du lot illisibles (${rep.status}).`);
  const echecs = [];
  for (const ligne of (await rep.text()).split("\n")) {
    if (!ligne.trim()) continue;
    const r = JSON.parse(ligne);
    const cleLocale = lot.correspondance[r.custom_id];
    if (r.result?.type === "succeeded") {
      const message = { ...r.result.message, _lot: true }; // pour le calcul du coût (moitié prix)
      recues[cleLocale] = message;
      await reponses.ecrire(travail.id, etape, cleLocale, message);
    } else echecs.push(cleLocale);
  }
  const reste = requetes.filter((r) => !(r.cle in recues));
  if (reste.length) {
    log(`${echecs.length || reste.length} requête(s) du lot en échec : renvoyées directement.`);
    await directement(etape, reste, recues, libelle, requetes.length);
  }
}

// ---------------------------------------------------------------------------
// Les deux parcours
// ---------------------------------------------------------------------------

async function nouveau(t) {
  const { photos, nom, modele, profil } = t.entrees;
  web.definir_profil(profil);
  viderDossier("/travail");
  await deposer("/travail/photos", photos);
  const S = "/travail/sortie";

  envoyer({ type: "etape", texte: "Redressement des photos…", fait: 0, total: 1 });
  const p = JSON.parse(web.pretraiter("/travail/photos", S));
  log(`${p.photos} photo(s) → ${p.pages} page(s)`);
  if (p.incertaines.length) log(`⚠ orientation incertaine : ${p.incertaines.join(", ")}`);

  let rep = await appeler("T", web.preparer_transcription(S, modele), "Transcription des pages", true);
  web.terminer_transcription(S, rep);
  rep = await appeler("S", web.preparer_structuration(S, modele), "Mise en forme (étiquetage)");
  web.terminer_structuration(S, rep);

  envoyer({ type: "etape", texte: "Contrôles et document Word…", fait: 0, total: 1 });
  const f = JSON.parse(web.controler_et_rendre(S, nom));
  return [lire(f.pdf, `${nom}.pdf`, "application/pdf"), lire(f.docx, `${nom}.docx`, DOCX),
          lire(f.a_annoter, `${nom}_a_annoter.docx`, DOCX), lire(f.rapport, `${nom}_rapport.md`, "text/markdown")];
}

async function maj(t) {
  const { reference, personnes, nom, modele, profil } = t.entrees;
  web.definir_profil(profil);
  viderDossier("/travail");
  py.FS.mkdirTree("/travail");
  py.FS.writeFile("/travail/reference.docx", new Uint8Array(await reference.blob.arrayBuffer()));
  const r = JSON.parse(web.maj_commencer("/travail/reference.docx", reference.nom));
  log(`Référence : ${reference.nom} — ${r.elements} éléments`);

  // 1. toutes les personnes : prétraitement, puis transcription du texte imprimé (ensemble)
  const dossiers = [];
  let demandes = [];
  for (const [i, pers] of personnes.entries()) {
    const d = `/travail/maj/p${i}`;
    await deposer(`${d}/photos`, pers.photos);
    envoyer({ type: "etape", texte: `${pers.nom} : redressement des photos…`, fait: 0, total: 1 });
    demandes = demandes.concat(JSON.parse(web.maj_preparer_transcription(`${d}/photos`, `${d}/travail`, modele)));
    dossiers.push(`${d}/travail`);
  }
  const repT = JSON.parse(await appeler("T", JSON.stringify(demandes), "Lecture du texte imprimé", true));
  const pour = (toutes, T) => JSON.stringify(Object.fromEntries(Object.entries(toutes).filter(([k]) => k.startsWith(T + "|"))));

  // 2. lecture des annotations (ensemble)
  demandes = [];
  for (const T of dossiers) demandes = demandes.concat(JSON.parse(web.maj_preparer_annotations(T, pour(repT, T), modele)));
  const repA = JSON.parse(await appeler("A", JSON.stringify(demandes), "Lecture des annotations"));
  for (const [i, pers] of personnes.entries()) {
    const o = JSON.parse(web.maj_terminer_auteur(pers.nom, dossiers[i], pour(repA, dossiers[i])));
    log(`${pers.nom} : ${o.operations} modification(s) / note(s) lue(s)`);
  }
  envoyer({ type: "etape", texte: "Fusion et document Word…", fait: 0, total: 1 });
  const f = JSON.parse(web.maj_finaliser("/travail/resultat", nom));
  return [lire(f.pdf, `${nom}.pdf`, "application/pdf"), lire(f.docx, `${nom}.docx`, DOCX),
          lire(f.rapport, `${nom}_rapport.md`, "text/markdown")];
}

async function lancer({ id, cle, adresseApi, relais }) {
  const travail = await travaux.lire(id);
  if (!travail) throw new Error("Traitement introuvable sur cet appareil.");
  const options = { apiKey: cle, dangerouslyAllowBrowser: true, maxRetries: 3 };
  if (adresseApi) options.baseURL = adresseApi; // tests en local uniquement
  contexte = { travail, client: new Anthropic(options), relais: relais || adresseApi, cle };
  travail.etat = "en_cours";
  await travaux.ecrire(travail);
  try {
    const fichiers = travail.type === "maj" ? await maj(travail) : await nouveau(travail);
    envoyer({ type: "fini", id, cout: web.cout_total(), fichiers });
  } catch (e) {
    if (!(e instanceof EnAttente)) throw e;
    travail.etat = "attente";
    travail.message = e.message;
    await travaux.ecrire(travail);
    envoyer({ type: "attente", id, texte: e.message });
  }
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "init") await init();
    else if (data.type === "lancer") await lancer(data);
  } catch (e) {
    log(`[détail de l'erreur] ${e?.stack || e}`); // visible dans « Détails »
    let texte = String(e?.message || e);
    // erreur Python : garder la dernière ligne (le message utile)
    if (texte.includes("Traceback")) texte = texte.trim().split("\n").pop();
    if (e?.status === 401) texte = "Clé d'API refusée : vérifiez-la dans les réglages.";
    envoyer({ type: "erreur", id: data.id, texte });
  }
};
