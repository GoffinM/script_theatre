// Interface de l'application web. Le travail lourd (Python, appels à Claude) se fait
// dans le Web Worker web/moteur.js ; ici : réglages, choix des fichiers, suivi, résultats.

const $ = (s) => document.querySelector(s);
const TAILLE_MAX = 2000; // côté le plus long des photos envoyées au moteur (pixels)

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

document.querySelectorAll("[data-onglet]").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("[data-onglet]").forEach((x) => x.setAttribute("aria-selected", x === b));
    $("#onglet-nouveau").hidden = b.dataset.onglet !== "nouveau";
    $("#onglet-maj").hidden = b.dataset.onglet !== "maj";
  }));

// ---------------------------------------------------------------------------
// Photos : réduction à TAILLE_MAX, orientation EXIF appliquée
// ---------------------------------------------------------------------------

async function preparerPhoto(fichier) {
  const img = await createImageBitmap(fichier, { imageOrientation: "from-image" });
  const f = Math.min(1, TAILLE_MAX / Math.max(img.width, img.height));
  const toile = document.createElement("canvas");
  toile.width = Math.round(img.width * f);
  toile.height = Math.round(img.height * f);
  toile.getContext("2d").drawImage(img, 0, 0, toile.width, toile.height);
  const blob = await new Promise((ok) => toile.toBlob(ok, "image/jpeg", 0.92));
  const nom = fichier.name.replace(/\.[^.]+$/, "") + ".jpg";
  return { nom, octets: await blob.arrayBuffer() };
}

function vignettes(fichiers, cible) {
  cible.replaceChildren(...[...fichiers].map((f, i) => {
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.src = URL.createObjectURL(f);
    img.alt = f.name;
    const leg = document.createElement("figcaption");
    leg.textContent = i + 1;
    fig.append(img, leg);
    return fig;
  }));
}

$("#photos").addEventListener("change", (e) => { vignettes(e.target.files, $("#vignettes")); demarrerMoteur(); });

// ---------------------------------------------------------------------------
// Personnes (mise à jour)
// ---------------------------------------------------------------------------

function ajouterPersonne(nom = "") {
  const bloc = document.createElement("div");
  bloc.className = "personne";
  bloc.innerHTML = `
    <div class="rangee">
      <input type="text" placeholder="Prénom (auteur des modifications)">
      <button class="second" title="Retirer">✕</button>
    </div>
    <div class="rangee"><input type="file" accept="image/*" multiple></div>
    <div class="vignettes"></div>`;
  bloc.querySelector("input[type=text]").value = nom;
  bloc.querySelector("button").addEventListener("click", () => bloc.remove());
  bloc.querySelector("input[type=file]").addEventListener("change", (e) => {
    vignettes(e.target.files, bloc.querySelector(".vignettes"));
    demarrerMoteur();
  });
  $("#personnes").append(bloc);
}
ajouterPersonne();
$("#ajouter-personne").addEventListener("click", () => ajouterPersonne());

// ---------------------------------------------------------------------------
// Moteur (Web Worker)
// ---------------------------------------------------------------------------

let moteur = null;
let moteurPret = null;
let enCours = false;

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

let finTravail = null;

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

async function lancer(message) {
  if (enCours) return;
  enCours = true;
  document.querySelectorAll("#lancer-nouveau, #lancer-maj").forEach((b) => (b.disabled = true));
  afficherSuivi();
  try {
    $("#etape").textContent = "Démarrage du moteur…";
    await demarrerMoteur();
    const fin = new Promise((ok) => (finTravail = ok));
    moteur.postMessage(await message());
    const m = await fin;
    if (m.type === "erreur") throw new Error(m.texte);
    $("#etape").innerHTML = `<span class="ok">Terminé ✓</span> — coût : ${m.cout.toFixed(2)} $`;
    $("#barre").style.width = "100%";
    afficherResultats(m.fichiers);
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

$("#lancer-nouveau").addEventListener("click", () => {
  const r = reglagesValides();
  const fichiers = [...$("#photos").files];
  if (!r) return;
  if (!fichiers.length) return alert("Choisissez au moins une photo.");
  lancer(async () => {
    $("#etape").textContent = "Préparation des photos…";
    return {
      type: "nouveau", ...r, nom: $("#nom").value.trim() || "document",
      photos: await Promise.all(fichiers.map(preparerPhoto)),
    };
  });
});

$("#lancer-maj").addEventListener("click", () => {
  const r = reglagesValides();
  if (!r) return;
  const ref = $("#reference").files[0];
  if (!ref) return alert("Choisissez le document de référence (.docx).");
  const blocs = [...document.querySelectorAll(".personne")]
    .map((b) => ({ nom: b.querySelector("input[type=text]").value.trim(), fichiers: [...b.querySelector("input[type=file]").files] }))
    .filter((p) => p.fichiers.length);
  if (!blocs.length) return alert("Ajoutez les photos d'au moins une personne.");
  if (blocs.some((p) => !p.nom)) return alert("Indiquez le prénom de chaque personne.");
  if (new Set(blocs.map((p) => p.nom)).size !== blocs.length) return alert("Deux personnes ont le même prénom.");
  lancer(async () => {
    $("#etape").textContent = "Préparation des photos…";
    const personnes = [];
    for (const p of blocs) personnes.push({ nom: p.nom, photos: await Promise.all(p.fichiers.map(preparerPhoto)) });
    return {
      type: "maj", ...r, nom: $("#nom-maj").value.trim() || "document_maj",
      reference: { nom: ref.name, octets: await ref.arrayBuffer() }, personnes,
    };
  });
});

// ---------------------------------------------------------------------------
// Résultats : téléchargement, partage, rapport lisible
// ---------------------------------------------------------------------------

function afficherResultats(fichiers) {
  const liste = document.createElement("div");
  liste.className = "fichiers";
  for (const f of fichiers) {
    const blob = new Blob([f.octets], { type: f.type });
    const ligne = document.createElement("div");
    ligne.className = "fichier";
    const nom = document.createElement("span");
    nom.textContent = f.nom;
    const actions = document.createElement("div");
    actions.className = "rangee";
    const tele = document.createElement("button");
    tele.textContent = "Télécharger";
    tele.addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = f.nom;
      a.click();
    });
    actions.append(tele);
    const fichier = new File([blob], f.nom, { type: f.type });
    if (navigator.canShare?.({ files: [fichier] })) {
      const part = document.createElement("button");
      part.className = "second";
      part.textContent = "Partager";
      part.addEventListener("click", () => navigator.share({ files: [fichier], title: f.nom }).catch(() => {}));
      actions.append(part);
    }
    ligne.append(nom, actions);
    liste.append(ligne);
  }
  const rapport = fichiers.find((f) => f.nom.endsWith(".md"));
  const zone = document.createElement("div");
  zone.id = "rapport";
  if (rapport) zone.append(...markdownSimple(new TextDecoder().decode(rapport.octets)));
  $("#resultat").replaceChildren(liste, zone);
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
