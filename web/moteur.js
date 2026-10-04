// Moteur de l'application web, exécuté dans un Web Worker (l'écran reste fluide).
// Il charge Python (Pyodide) avec le code de remise_en_forme/, et fait les appels
// à Claude avec le SDK JavaScript officiel, directement depuis le navigateur.
//
// Messages reçus  : {type: "init"} | {type: "nouveau", ...} | {type: "maj", ...}
// Messages envoyés : {type: "log", texte} | {type: "etape", texte, fait, total}
//                    | {type: "pret"} | {type: "fini", fichiers, cout} | {type: "erreur", texte}

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const SDK = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.131.0/+esm";
const RACINE = new URL("../", import.meta.url);
const SOURCES = [
  "remise_en_forme/__init__.py", "remise_en_forme/claude.py", "remise_en_forme/controles.py",
  "remise_en_forme/mise_a_jour.py", "remise_en_forme/pretraitement.py", "remise_en_forme/profil.py",
  "remise_en_forme/reference.py", "remise_en_forme/rendu.py", "remise_en_forme/structuration.py",
  "remise_en_forme/transcription.py", "remise_en_forme/web.py",
];
const PARALLELES = 4;

let py = null;
let web = null;
let Anthropic = null;

const envoyer = (m) => self.postMessage(m);
const log = (texte) => envoyer({ type: "log", texte });

async function init() {
  envoyer({ type: "etape", texte: "Chargement de Python (une seule fois, ~40 Mo)…", fait: 0, total: 4 });
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({ indexURL: PYODIDE, stdout: log, stderr: log });
  envoyer({ type: "etape", texte: "Chargement d'OpenCV et des bibliothèques…", fait: 1, total: 4 });
  await py.loadPackage(["numpy", "opencv-python", "pyyaml", "lxml", "typing-extensions", "micropip"]);
  const micropip = py.pyimport("micropip");
  await micropip.install(new URL("web/wheels/python_docx-1.2.0-py3-none-any.whl", RACINE).href);
  envoyer({ type: "etape", texte: "Chargement de l'outil…", fait: 2, total: 4 });
  py.FS.mkdirTree("/app/remise_en_forme");
  const version = Date.now(); // évite un code périmé en cache après une mise à jour du site
  for (const f of SOURCES) {
    const rep = await fetch(new URL(f + "?v=" + version, RACINE));
    if (!rep.ok) throw new Error(`Fichier introuvable : ${f}`);
    py.FS.writeFile("/app/" + f, await rep.text());
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

function deposer(dossier, fichiers) {
  // fichiers : [{nom, octets: ArrayBuffer}] — préfixe numérique pour garder l'ordre choisi
  py.FS.mkdirTree(dossier);
  fichiers.forEach((f, i) => {
    const nom = `${String(i + 1).padStart(3, "0")}_${f.nom.replace(/[^\w.-]+/g, "_")}`;
    py.FS.writeFile(`${dossier}/${nom}`, new Uint8Array(f.octets));
  });
}

function lire(chemin, nom, type) {
  const octets = py.FS.readFile(chemin);
  return { nom, type, octets: octets.buffer.slice(octets.byteOffset, octets.byteOffset + octets.byteLength) };
}

// ---------------------------------------------------------------------------
// Appels à Claude (même logique que remise_en_forme/claude.py : envoyer)
// ---------------------------------------------------------------------------

async function appeler(client, requetesJson, libelle) {
  const requetes = JSON.parse(requetesJson);
  const reponses = {};
  let fait = 0;
  const total = requetes.length;
  if (!total) return "{}";
  envoyer({ type: "etape", texte: libelle, fait, total });

  const une = async (r) => {
    const p = r.params;
    const rep = p.betas ? await client.beta.messages.create(p) : await client.messages.create(p);
    reponses[r.cle] = rep;
    envoyer({ type: "etape", texte: libelle, fait: ++fait, total });
  };
  // la première seule (elle écrit le cache des consignes), puis les autres en parallèle
  await une(requetes[0]);
  const file = requetes.slice(1);
  const ouvriers = Array.from({ length: Math.min(PARALLELES, file.length) }, async () => {
    while (file.length) await une(file.shift());
  });
  await Promise.all(ouvriers);
  return JSON.stringify(reponses);
}

function client(cle, adresseApi) {
  const options = { apiKey: cle, dangerouslyAllowBrowser: true, maxRetries: 3 };
  if (adresseApi) options.baseURL = adresseApi; // tests en local uniquement
  return new Anthropic(options);
}

// ---------------------------------------------------------------------------
// Les deux parcours
// ---------------------------------------------------------------------------

async function nouveau({ photos, nom, cle, modele, profil, adresseApi }) {
  const cl = client(cle, adresseApi);
  web.definir_profil(profil);
  viderDossier("/travail");
  deposer("/travail/photos", photos);
  const S = "/travail/sortie";

  envoyer({ type: "etape", texte: "Redressement des photos…", fait: 0, total: 1 });
  const p = JSON.parse(web.pretraiter("/travail/photos", S));
  log(`${p.photos} photo(s) → ${p.pages} page(s)`);
  if (p.incertaines.length) log(`⚠ orientation incertaine : ${p.incertaines.join(", ")}`);

  let rep = await appeler(cl, web.preparer_transcription(S, modele), "Transcription des pages");
  web.terminer_transcription(S, rep);
  rep = await appeler(cl, web.preparer_structuration(S, modele), "Mise en forme (étiquetage)");
  web.terminer_structuration(S, rep);

  envoyer({ type: "etape", texte: "Contrôles et document Word…", fait: 0, total: 1 });
  const f = JSON.parse(web.controler_et_rendre(S, nom));
  const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  envoyer({
    type: "fini", cout: web.cout_total(), fichiers: [
      lire(f.docx, `${nom}.docx`, DOCX),
      lire(f.a_annoter, `${nom}_a_annoter.docx`, DOCX),
      lire(f.rapport, `${nom}_rapport.md`, "text/markdown"),
    ],
  });
}

async function maj({ reference, personnes, nom, cle, modele, profil, adresseApi }) {
  const cl = client(cle, adresseApi);
  web.definir_profil(profil);
  viderDossier("/travail");
  py.FS.mkdirTree("/travail");
  py.FS.writeFile("/travail/reference.docx", new Uint8Array(reference.octets));
  const r = JSON.parse(web.maj_commencer("/travail/reference.docx", reference.nom));
  log(`Référence : ${reference.nom} — ${r.elements} éléments`);

  for (const pers of personnes) {
    const dossier = `/travail/maj/${pers.nom.replace(/[^\w-]+/g, "_")}`;
    deposer(`${dossier}/photos`, pers.photos);
    const T = `${dossier}/travail`;
    envoyer({ type: "etape", texte: `${pers.nom} : redressement des photos…`, fait: 0, total: 1 });
    let rep = await appeler(cl, web.maj_preparer_transcription(`${dossier}/photos`, T, modele),
      `${pers.nom} : lecture du texte imprimé`);
    rep = await appeler(cl, web.maj_preparer_annotations(T, rep, modele), `${pers.nom} : lecture des annotations`);
    const o = JSON.parse(web.maj_terminer_auteur(pers.nom, T, rep));
    log(`${pers.nom} : ${o.operations} modification(s) / note(s) lue(s)`);
  }
  envoyer({ type: "etape", texte: "Fusion et document Word…", fait: 0, total: 1 });
  const f = JSON.parse(web.maj_finaliser("/travail/resultat", nom));
  envoyer({
    type: "fini", cout: web.cout_total(), fichiers: [
      lire(f.docx, `${nom}.docx`, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
      lire(f.rapport, `${nom}_rapport.md`, "text/markdown"),
    ],
  });
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "init") await init();
    else if (data.type === "nouveau") await nouveau(data);
    else if (data.type === "maj") await maj(data);
  } catch (e) {
    let texte = String(e?.message || e);
    // erreur Python : garder la dernière ligne (le message utile)
    if (texte.includes("Traceback")) texte = texte.trim().split("\n").pop();
    if (e?.status === 401) texte = "Clé d'API refusée : vérifiez-la dans les réglages.";
    envoyer({ type: "erreur", texte });
  }
};
