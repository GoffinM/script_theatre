// Interface de l'application web. Le travail lourd (Python, appels à Claude) se fait
// dans le Web Worker web/moteur.js ; ici : réglages, photos, suivi, résultats, archives.

import { archives, brouillons } from "./stockage.js";

const $ = (s) => document.querySelector(s);
const TAILLE_MAX = 2000; // côté le plus long des photos envoyées au moteur (pixels)
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

// ---------------------------------------------------------------------------
// Réglages (enregistrés sur l'appareil)
// ---------------------------------------------------------------------------

const stock = {
  lire(k, defaut = "") { try { return localStorage.getItem(k) ?? defaut; } catch { return defaut; } },
  ecrire(k, v) { try { localStorage.setItem(k, v); } catch { /* navigation privée */ } },
  effacer(k) { try { localStorage.removeItem(k); } catch { /* idem */ } },
};

async function profilOrigine(nom) {
  const rep = await fetch(`profils/${nom}.yaml`);
  return rep.text();
}

async function afficherProfil() {
  const nom = $("#profil").value;
  $("#profil-texte").value = stock.lire(`profil:${nom}`) || await profilOrigine(nom);
}

function majEtatCle() {
  const ok = Boolean(stock.lire("cle"));
  $("#etat-cle").textContent = ok ? "✓ clé enregistrée" : "clé à saisir";
  $("#etat-cle").className = "etat " + (ok ? "ok" : "erreur");
  if (!ok) $("#reglages").open = true;
}

$("#cle").value = stock.lire("cle");
$("#modele").value = stock.lire("modele", "claude-opus-5-5");
$("#profil").value = stock.lire("profil", "theatre");
afficherProfil();
majEtatCle();

$("#profil").addEventListener("change", afficherProfil);
$("#enregistrer").addEventListener("click", () => {
  stock.ecrire("cle", $("#cle").value.trim());
  stock.ecrire("modele", $("#modele").value);
  stock.ecrire("profil", $("#profil").value);
  majEtatCle();
  $("#reglages").open = false;
});
$("#profil-enregistrer").addEventListener("click", () => {
  stock.ecrire(`profil:${$("#profil").value}`, $("#profil-texte").value);
  $("#profil-enregistrer").textContent = "Profil enregistré ✓";
  setTimeout(() => ($("#profil-enregistrer").textContent = "Enregistrer le profil"), 1500);
});
$("#profil-defaut").addEventListener("click", async () => {
  stock.effacer(`profil:${$("#profil").value}`);
  await afficherProfil();
});

// ---------------------------------------------------------------------------
// Onglets
// ---------------------------------------------------------------------------

function ouvrirOnglet(nom) {
  document.querySelectorAll("[data-onglet]").forEach((x) => x.setAttribute("aria-selected", x.dataset.onglet === nom));
  for (const o of ["nouveau", "maj", "documents"]) $(`#onglet-${o}`).hidden = o !== nom;
  if (nom === "documents") afficherArchives();
}
document.querySelectorAll("[data-onglet]").forEach((b) => b.addEventListener("click", () => ouvrirOnglet(b.dataset.onglet)));

// ---------------------------------------------------------------------------
// Photos : réduites à TAILLE_MAX dès la prise (orientation EXIF appliquée),
// et enregistrées aussitôt sur l'appareil (brouillon)
// ---------------------------------------------------------------------------

async function reduirePhoto(fichier) {
  const img = await createImageBitmap(fichier, { imageOrientation: "from-image" });
  const f = Math.min(1, TAILLE_MAX / Math.max(img.width, img.height));
  const toile = document.createElement("canvas");
  toile.width = Math.round(img.width * f);
  toile.height = Math.round(img.height * f);
  toile.getContext("2d").drawImage(img, 0, 0, toile.width, toile.height);
  img.close?.();
  const blob = await new Promise((ok) => toile.toBlob(ok, "image/jpeg", 0.92));
  return { nom: fichier.name.replace(/\.[^.]+$/, "") + ".jpg", blob };
}

// Sélecteur : appareil photo (une page après l'autre) ou galerie ; liste ordonnée,
// vignettes numérotées ; ◀ avance une photo d'un cran, ✕ la retire.
// `surChangement(liste)` est appelé à chaque modification (pour le brouillon).
function selecteurPhotos(conteneur, surChangement = () => {}) {
  let liste = []; // [{nom, blob, url}]
  conteneur.classList.add("selecteur");
  conteneur.innerHTML = `
    <div class="rangee">
      <button type="button" data-action="camera">📷 Prendre une photo</button>
      <button type="button" class="second" data-action="galerie">Ajouter depuis la galerie</button>
      <span class="compte"></span>
    </div>
    <input type="file" accept="image/*" capture="environment" data-role="camera">
    <input type="file" accept="image/*" multiple data-role="galerie">
    <div class="vignettes"></div>`;
  const zone = conteneur.querySelector(".vignettes");
  const compte = conteneur.querySelector(".compte");

  function afficher(enregistrer = true) {
    compte.textContent = liste.length ? `${liste.length} photo${liste.length > 1 ? "s" : ""}` : "";
    zone.replaceChildren(...liste.map((p, i) => {
      const fig = document.createElement("figure");
      const img = document.createElement("img");
      img.src = p.url;
      img.alt = `Photo ${i + 1}`;
      const leg = document.createElement("figcaption");
      leg.textContent = i + 1;
      const outils = document.createElement("div");
      outils.className = "outils";
      const avant = document.createElement("button");
      avant.type = "button";
      avant.textContent = "◀";
      avant.title = "Placer avant";
      avant.disabled = i === 0;
      avant.addEventListener("click", () => { [liste[i - 1], liste[i]] = [liste[i], liste[i - 1]]; afficher(); });
      const retirer = document.createElement("button");
      retirer.type = "button";
      retirer.textContent = "✕";
      retirer.title = "Retirer";
      retirer.addEventListener("click", () => { URL.revokeObjectURL(p.url); liste.splice(i, 1); afficher(); });
      outils.append(avant, retirer);
      fig.append(img, leg, outils);
      return fig;
    }));
    if (enregistrer) surChangement(liste.map(({ nom, blob }) => ({ nom, blob })));
  }

  for (const role of ["camera", "galerie"]) {
    const entree = conteneur.querySelector(`[data-role=${role}]`);
    conteneur.querySelector(`[data-action=${role}]`).addEventListener("click", () => entree.click());
    entree.addEventListener("change", async () => {
      const nouveaux = [...entree.files];
      entree.value = ""; // permet de reprendre une photo / d'en ajouter d'autres
      compte.textContent = "Ajout…";
      for (const f of nouveaux) {
        try {
          const p = await reduirePhoto(f);
          liste.push({ ...p, url: URL.createObjectURL(p.blob) });
        } catch (e) {
          alert(`Photo illisible (${f.name}) : ${e.message}`);
        }
      }
      afficher();
    });
  }
  return {
    photos: () => liste.map(({ nom, blob }) => ({ nom, blob })),
    restaurer(photos) {
      liste.forEach((p) => URL.revokeObjectURL(p.url));
      liste = (photos || []).map((p) => ({ ...p, url: URL.createObjectURL(p.blob) }));
      afficher(false);
    },
    vider() { this.restaurer([]); surChangement([]); },
  };
}

// --- Nouveau document : brouillon « nouveau »
const photosNouveau = selecteurPhotos($("#photos"), (photos) => brouillons.ecrire("nouveau", photos));
brouillons.lire("nouveau").then((photos) => photos?.length && photosNouveau.restaurer(photos));

// --- Mise à jour : brouillon « maj » = [{nom, photos}]
function enregistrerPersonnes() {
  brouillons.ecrire("maj", [...document.querySelectorAll(".personne")].map((b) => ({
    nom: b.querySelector("input[type=text]").value, photos: b.selecteur.photos(),
  })));
}

function ajouterPersonne(nom = "", photos = []) {
  const bloc = document.createElement("div");
  bloc.className = "personne";
  bloc.innerHTML = `
    <div class="rangee">
      <input type="text" placeholder="Prénom (auteur des modifications)">
      <button class="second" title="Retirer cette personne">✕</button>
    </div>
    <div class="photos-personne"></div>`;
  const champ = bloc.querySelector("input[type=text]");
  champ.value = nom;
  champ.addEventListener("change", enregistrerPersonnes);
  bloc.querySelector("button").addEventListener("click", () => { bloc.remove(); enregistrerPersonnes(); });
  bloc.selecteur = selecteurPhotos(bloc.querySelector(".photos-personne"), enregistrerPersonnes);
  if (photos.length) bloc.selecteur.restaurer(photos);
  $("#personnes").append(bloc);
}
$("#ajouter-personne").addEventListener("click", () => { ajouterPersonne(); enregistrerPersonnes(); });
brouillons.lire("maj").then((personnes) => {
  if (personnes?.length) personnes.forEach((p) => ajouterPersonne(p.nom, p.photos));
  else ajouterPersonne();
});

// --- Référence de la mise à jour : fichier choisi, ou document de « Mes documents »
let referenceArchivee = null; // {nom, blob}
$("#reference").addEventListener("change", () => { referenceArchivee = null; $("#reference-archive").hidden = true; });

function utiliserCommeReference(nom, blob) {
  referenceArchivee = { nom, blob };
  $("#reference").value = "";
  $("#reference-archive").hidden = false;
  $("#reference-archive").textContent = `Référence : ${nom} (depuis Mes documents)`;
  ouvrirOnglet("maj");
  window.scrollTo({ top: $("#onglet-maj").offsetTop - 16, behavior: "smooth" });
}

// ---------------------------------------------------------------------------
// Moteur (Web Worker) — chargé seulement au lancement, pas pendant la prise de
// photos : sur téléphone, la mémoire qu'il occupe pousse le navigateur à
// recharger la page quand l'appareil photo s'ouvre.
// ---------------------------------------------------------------------------

let moteur = null;
let moteurPret = null;
let enCours = false;
let finTravail = null;

function demarrerMoteur() {
  if (moteurPret) return moteurPret;
  moteur = new Worker(new URL("./moteur.js", import.meta.url), { type: "module" });
  let pret = false;
  moteurPret = new Promise((ok, ko) => {
    moteur.onmessage = ({ data }) => {
      if (data.type === "pret") { pret = true; ok(); }
      else if (data.type === "erreur" && !pret) { moteurPret = null; ko(new Error(data.texte)); }
      else recevoir(data);
    };
  });
  moteur.postMessage({ type: "init" });
  return moteurPret;
}

function recevoir(m) {
  if (m.type === "log") {
    const j = $("#journal");
    j.textContent += m.texte + "\n";
    j.scrollTop = j.scrollHeight;
  } else if (m.type === "etape") {
    $("#etape").textContent = m.total > 1 ? `${m.texte} (${m.fait}/${m.total})` : m.texte;
    $("#barre").style.width = `${m.total ? (100 * m.fait) / m.total : 0}%`;
  } else if (m.type === "fini" || m.type === "erreur") {
    finTravail?.(m);
  }
}

function afficherSuivi() {
  $("#suivi").hidden = false;
  $("#resultat").replaceChildren();
  $("#journal").textContent = "";
  $("#suivi").scrollIntoView({ behavior: "smooth", block: "start" });
}

function reglagesValides() {
  const cle = stock.lire("cle");
  if (!cle) {
    $("#reglages").open = true;
    $("#cle").focus();
    alert("Saisissez d'abord votre clé d'API dans les réglages.");
    return null;
  }
  // adresseApi : réservé aux tests en local (relais de développement), vide sinon
  return { cle, modele: stock.lire("modele", "claude-opus-5-5"), profil: $("#profil-texte").value,
           adresseApi: stock.lire("adresseApi") || null };
}

async function lancer(type, nom, message, apresSucces) {
  if (enCours) return;
  enCours = true;
  document.querySelectorAll("#lancer-nouveau, #lancer-maj").forEach((b) => (b.disabled = true));
  afficherSuivi();
  try {
    $("#etape").textContent = "Démarrage du moteur (premier lancement : ~40 Mo)…";
    await demarrerMoteur();
    const fin = new Promise((ok) => (finTravail = ok));
    moteur.postMessage(await message());
    const m = await fin;
    if (m.type === "erreur") throw new Error(m.texte);
    $("#etape").innerHTML = `<span class="ok">Terminé ✓</span> — coût : ${m.cout.toFixed(2)} $ — `
      + "enregistré dans « Mes documents »";
    $("#barre").style.width = "100%";
    const fichiers = m.fichiers.map((f) => ({ nom: f.nom, type: f.type, blob: new Blob([f.octets], { type: f.type }) }));
    await archives.ajouter({ id: `${Date.now()}`, date: Date.now(), type, nom, cout: m.cout, fichiers });
    afficherResultats(fichiers, $("#resultat"), type === "nouveau");
    apresSucces();
  } catch (e) {
    $("#etape").innerHTML = "";
    const p = document.createElement("p");
    p.className = "erreur";
    p.textContent = "Erreur : " + e.message;
    $("#resultat").replaceChildren(p);
  } finally {
    enCours = false;
    document.querySelectorAll("#lancer-nouveau, #lancer-maj").forEach((b) => (b.disabled = false));
  }
}

const versMoteur = async (photos) =>
  Promise.all(photos.map(async (p) => ({ nom: p.nom, octets: await p.blob.arrayBuffer() })));

$("#lancer-nouveau").addEventListener("click", () => {
  const r = reglagesValides();
  if (!r) return;
  const photos = photosNouveau.photos();
  if (!photos.length) return alert("Prenez ou choisissez au moins une photo.");
  const nom = $("#nom").value.trim() || "document";
  lancer("nouveau", nom,
    async () => ({ type: "nouveau", ...r, nom, photos: await versMoteur(photos) }),
    () => photosNouveau.vider());
});

$("#lancer-maj").addEventListener("click", () => {
  const r = reglagesValides();
  if (!r) return;
  const fichierRef = $("#reference").files[0];
  const ref = fichierRef ? { nom: fichierRef.name, blob: fichierRef } : referenceArchivee;
  if (!ref) return alert("Choisissez le document de référence (.docx), ou un document de « Mes documents ».");
  const blocs = [...document.querySelectorAll(".personne")]
    .map((b) => ({ bloc: b, nom: b.querySelector("input[type=text]").value.trim(), photos: b.selecteur.photos() }))
    .filter((p) => p.photos.length);
  if (!blocs.length) return alert("Ajoutez les photos d'au moins une personne.");
  if (blocs.some((p) => !p.nom)) return alert("Indiquez le prénom de chaque personne.");
  if (new Set(blocs.map((p) => p.nom)).size !== blocs.length) return alert("Deux personnes ont le même prénom.");
  const nom = $("#nom-maj").value.trim() || "document_maj";
  lancer("maj", nom, async () => ({
    type: "maj", ...r, nom,
    reference: { nom: ref.nom, octets: await ref.blob.arrayBuffer() },
    personnes: await Promise.all(blocs.map(async (p) => ({ nom: p.nom, photos: await versMoteur(p.photos) }))),
  }), () => {
    blocs.forEach((p) => p.bloc.selecteur.vider());
    enregistrerPersonnes();
  });
});

// ---------------------------------------------------------------------------
// Fichiers produits : téléchargement, partage, rapport lisible
// ---------------------------------------------------------------------------

function boutonFichier(texte, classe, action) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = texte;
  if (classe) b.className = classe;
  b.addEventListener("click", action);
  return b;
}

function afficherResultats(fichiers, cible, referenceProposee = false) {
  const liste = document.createElement("div");
  liste.className = "fichiers";
  for (const f of fichiers) {
    const ligne = document.createElement("div");
    ligne.className = "fichier";
    const nom = document.createElement("span");
    nom.textContent = f.nom;
    const actions = document.createElement("div");
    actions.className = "rangee";
    actions.append(boutonFichier("Télécharger", "", () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(f.blob);
      a.download = f.nom;
      a.click();
    }));
    const fichier = new File([f.blob], f.nom, { type: f.type });
    if (navigator.canShare?.({ files: [fichier] })) {
      actions.append(boutonFichier("Partager", "second",
        () => navigator.share({ files: [fichier], title: f.nom }).catch(() => {})));
    }
    ligne.append(nom, actions);
    liste.append(ligne);
    // un document fraîchement transcrit (sans modifications suivies) peut servir de référence
    if (referenceProposee && f.type === DOCX && !f.nom.endsWith("_a_annoter.docx")) {
      const r = document.createElement("div");
      r.className = "rangee";
      r.style.marginTop = "0";
      r.append(boutonFichier("Utiliser comme référence pour une mise à jour", "second",
        () => utiliserCommeReference(f.nom, f.blob)));
      liste.append(r);
    }
  }
  const rapport = fichiers.find((f) => f.nom.endsWith(".md"));
  const zone = document.createElement("details");
  zone.className = "rapport";
  zone.open = cible.id === "resultat";
  const titre = document.createElement("summary");
  titre.textContent = "Rapport";
  zone.append(titre);
  if (rapport) rapport.blob.text().then((t) => zone.append(...markdownSimple(t)));
  cible.replaceChildren(liste, zone);
}

function markdownSimple(md) {
  // titres, listes et paragraphes du rapport (texte inséré sans HTML : pas d'injection)
  const out = [];
  let ul = null;
  for (const ligne of md.split("\n")) {
    if (ligne.startsWith("#")) {
      ul = null;
      const h = document.createElement("h3");
      h.textContent = ligne.replace(/^#+\s*/, "");
      out.push(h);
    } else if (ligne.startsWith("- ")) {
      if (!ul) out.push((ul = document.createElement("ul")));
      const li = document.createElement("li");
      li.textContent = ligne.slice(2);
      ul.append(li);
    } else if (ligne.trim()) {
      if (ul && /^\s/.test(ligne)) { ul.lastChild.textContent += " " + ligne.trim(); continue; }
      ul = null;
      const p = document.createElement("p");
      p.textContent = ligne;
      out.push(p);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Mes documents (archives sur l'appareil)
// ---------------------------------------------------------------------------

const FORMAT_DATE = new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" });

async function afficherArchives() {
  const liste = await archives.toutes();
  const zone = $("#archives");
  if (!liste.length) {
    zone.innerHTML = `<p class="aide">Aucun document pour l'instant. Chaque résultat sera enregistré ici automatiquement.</p>`;
    return;
  }
  zone.replaceChildren(...liste.map((a) => {
    const carte = document.createElement("div");
    carte.className = "archive";
    const tete = document.createElement("div");
    tete.className = "archive-tete";
    const titre = document.createElement("strong");
    titre.textContent = a.nom;
    const info = document.createElement("span");
    info.className = "aide";
    info.textContent = `${a.type === "maj" ? "Mise à jour" : "Transcription"} — ${FORMAT_DATE.format(a.date)} — ${a.cout.toFixed(2)} $`;
    tete.append(titre, info);
    const fichiers = document.createElement("div");
    afficherResultats(a.fichiers, fichiers, a.type === "nouveau");
    const actions = document.createElement("div");
    actions.className = "rangee";
    actions.append(boutonFichier("Supprimer", "second", async () => {
      if (!confirm(`Supprimer « ${a.nom} » de cet appareil ?`)) return;
      await archives.supprimer(a.id);
      afficherArchives();
    }));
    carte.append(tete, fichiers, actions);
    return carte;
  }));
}
