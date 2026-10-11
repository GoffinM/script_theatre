# Récapitulatif — outil « remise-en-forme » (état au 11/10/2026, v0.7.0)

## 1. Identité

| | |
|---|---|
| Nom | `remise-en-forme` (application web : « Remise en forme ») — © M&M's productions |
| Dépôt | https://github.com/GoffinM/script_theatre (public, tous droits réservés) — local : `C:\Users\Michel\Projets\teste\OCR_theatre` |
| Application web | https://goffinm.github.io/script_theatre/ (page statique GitHub Pages, sans serveur) |
| Langages | Python 3.11+ (cœur) ; JavaScript (interface web, Web Worker) |
| Bibliothèques | OpenCV, numpy, PyYAML, python-docx, fpdf2, pypdf, fontTools/Pillow ; web : Pyodide 314 (Python dans le navigateur), IndexedDB |
| IA | API Claude, SDK officiels (Python `anthropic`, JS `@anthropic-ai/sdk`) — `claude-opus-5-5`, effort `low`, sortie JSON contrainte, cache des consignes, API Batch en option |

## 2. Utilisation

| Commande (`python -m remise_en_forme …`) | Rôle |
|---|---|
| `tout <photos> -p theatre --nom X` | les 5 étapes depuis un dossier de photos |
| `pretraiter`, `transcrire`, `structurer`, `controler`, `rendre` | étapes une par une (dossier `-o sortie`) |
| `importer <fichier.pdf\|docx\|txt>` | document déjà propre → même mise en forme |
| `mettre-a-jour <ref.docx> <dossier_annotations>` | intègre des pages annotées (un sous-dossier par personne) |
| `creer-modele <modele.docx>` | modèle de mise en page de départ |

Options principales :
- `-p` : profil ;
- `-o` : dossier de sortie ;
- `--mise-en-page modele.docx` : modèle de mise en page ;
- `--modele` : modèle Claude ;
- `--effort` : profondeur de réflexion ;
- `--force` : refaire les appels déjà faits ;
- `--rotation photo=angle` : forcer l'orientation d'une photo.

Exemple d'appel type : `python -m remise_en_forme tout exemple/test1 -p theatre --nom ma_piece`.

**Application web**, quatre zones :
- **Réglages :** clé d'API, modèle, mode d'exécution rapide ou en arrière-plan.
- **Projet :** profil, personnages et modèle de mise en page.
- **Onglets :** Nouveau document (appareil photo, galerie ou import de fichier), Mise à jour, Mes documents.

## 3. Chaîne de traitement

| Étape | État | Contenu |
|---|---|---|
| 1. Prétraitement (OpenCV) | Fait | Orientation (0/90/180/270), séparation des doubles pages, perspective et cadrage sur le bloc de texte, redressement de l'inclinaison, aplanissement de la courbure, contraste, page couleur recadrée |
| 2. Transcription | Fait | Une page par appel ; littérale, `[illisible]`, italique noté `_x_`, numéro de page à part, page blanche détectée |
| 2bis. Import | Fait | TXT, DOCX et PDF à texte lus directement ; pages de PDF scannées transcrites par Claude |
| 3. Structuration | Fait | Le modèle renvoie des plages de lignes étiquetées, le code découpe le texte (aucune recopie) |
| 4. Contrôles et assemblage | Fait | Fidélité, continuité des pages (numéros déduits), noms hors liste, césures, raccords entre pages, paragraphes |
| 5. Rendu | Fait | DOCX (styles du profil ou du modèle), DOCX « à annoter » numéroté, PDF (police embarquée) |
| Mise à jour par annotations | Partiel | Suivi des modifications Word par auteur, notes en commentaires, conflits signalés. Testé sur des annotations simulées seulement |
| Notes de conduite de régie | Non fait | En attente d'exemples |

## 4. Formats

**Entrée :**
- photos JPG/PNG (une ou deux pages par photo, toute orientation) ;
- documents TXT, DOCX ou PDF (le format .doc est refusé, avec explication) ;
- DOCX de référence pour les mises à jour.

**Formats intermédiaires (JSON, dans `<sortie>/`) :**

| Fichier | Schéma |
|---|---|
| `01_pretraitement/manifest.json` | `[{photo: str, rotation_deg: int, double_page: bool, orientation_incertaine: bool, pages: [{fichier: str, cote: "g"\|"d"\|"u", deskew_deg: float, courbure_px: float}]}]` |
| `02_transcription/transcription.json` | `[{image: str, photo: str, numero_page: str\|null, texte: str, remarques: str, par_ligne?: bool}]` |
| `03_structuration/structure.json` | `[{image, numero_page, elements: [{type: str, texte: str, lignes: [int, int], personnage: str\|null, suite: bool}], anomalies: [str]}]` (champs selon le profil) |
| `04_controles/document.json` | `[{id: "E0001", type, personnage, texte, pages: [str], pages_txt: str}]` + `rapport.md` |
| `couts.json` (par étape) | `{total_usd, appels: [{etiquette, modele, effort, jetons_entree, jetons_cache_ecrits, jetons_cache_lus, jetons_sortie, cout_usd}]}` |
| `annotations.json` (mise à jour) | `{page: {operations: [{action: remplacer\|supprimer\|inserer_apres\|note, id, ancien, nouveau, type, personnage, certitude}], remarques}}` |

Dans les textes : `\n` sépare les lignes ou paragraphes, `_x_` marque l'italique et `[illisible]` remplace un mot non lu.

**Sortie :** `<nom>.docx`, `<nom>_a_annoter.docx`, `<nom>.pdf`, `rapport.md`. Pour une mise à jour : `<nom>_maj.docx` (suivi des modifications et commentaires), `<nom>_maj.pdf` (modifications en couleur) et `rapport_maj.md`.

## 5. Profils et gabarits

| | |
|---|---|
| Profils disponibles | `profils/theatre.yaml` (titre, dédicace, personnages, répliques, didascalies) et `profils/roman.yaml` (chapitres, épigraphes, paragraphes, dialogues, notes) |
| Définition | YAML déclaratif : `personnages` (liste fermée), `conventions`, `champs`, `elements` (type, description, style, `italique_source`), `paragraphes`, `mise_en_page` (police, marges, styles) |
| Ajouter un profil | Copier un YAML dans `profils/` et l'adapter, sans toucher au code ; dans l'application, le modifier dans les réglages du projet |
| Gabarit (modèle) | `creer-modele` (ou bouton dans l'application) → modifier les styles dans Word → `--mise-en-page` (ou « Déposer »). Le DOCX suit tous les styles du modèle ; le PDF suit tailles, gras, italique, retraits et espacements, mais pas la police |

## 6. Résultats des tests

| Jeu | Contenu | Résultat |
|---|---|---|
| test1 (4 photos WhatsApp, 960×1280) | Théâtre, 7 pages | Orientation, doubles pages et raccords corrects. Une seule erreur de lecture : « PERSONNAGES » vu en italique, corrigé par l'option `italique_source` |
| test2 (3 photos) | Roman, 6 pages | Verso blanc écarté, n° de page 19 déduit, n° 17 coupé mais lu sur l'image couleur. Première ligne de la page 18 d'abord coupée, corrigé |
| Imports (DOCX, TXT, PDF texte, PDF scanné) | Même texte que test1 | Résultat identique au document produit depuis les photos, élément par élément |
| Annotations simulées (3 personnes) | Copie imprimée + photo du livre | Toutes les opérations lues, conflit et coupe corrects. Une marque ambiguë a été interprétée différemment d'un essai à l'autre, mais toujours signalée comme douteuse |

| Comparaison de modèles (13 pages, ~2 000 mots) | Vraies erreurs | Coût transcription / page |
|---|---|---|
| Opus 5.5 | 0 | 1,8 c |
| Sonnet 5.5 | 2 | 0,9 c |
| Haiku 4.5 | nombreuses (mots inventés) | 0,35 c |
| Photo brute, sans prétraitement | 7 (dont une « correction ») | 1,4 c |

**Coût total mesuré :** environ 2,8 c par page (transcription 1,8 + structuration 1,0), environ 1,4 c en mode lot. Une mise à jour coûte environ 4 c par page annotée.

**Temps :** environ 30 s pour 6 pages en ligne de commande (appels en parallèle) ; 1 à 2 min dans l'application, plus le premier chargement (environ 40 Mo).

## 7. Limites, bugs ouverts, choix techniques

**Limites connues :**
- Les annotations manuscrites réelles n'ont jamais été testées.
- L'italique des PDF n'est pas lu.
- La police d'un modèle Word ne s'applique pas au PDF.
- Le partage d'un `.docx` ne fonctionne pas sur Chrome Android (le PDF, lui, se partage).
- Projets et documents restent sur l'appareil (pas de partage entre personnes).
- Le relais Cloudflare du mode en arrière-plan n'a pas été testé en production.
- Le choix entre 0° et 180° est incertain sur une page presque vide (le rapport le signale).
- Aucun bug ouvert connu.

| Choix | Raison |
|---|---|
| Transcription littérale, puis étiquetage par plages de lignes | Fidélité garantie par construction (le modèle ne peut pas modifier le texte) ; coût de sortie réduit |
| Opus par défaut, effort `low` | 0 erreur mesurée ; une réflexion plus poussée n'apporte rien |
| Prétraitement OpenCV conservé | La photo brute donne 7 vraies erreurs |
| Page couleur envoyée en 2ᵉ image | Lecture des traits pâles, fin des « reconstitutions » |
| Pyodide (le même Python dans le navigateur) | Un seul code pour la ligne de commande et le web |
| Réponses mémorisées (IndexedDB) + API Batch via relais | Reprise sans repayer ; l'API Batch refuse les appels directs depuis un navigateur (CORS) |
| PDF produit par fpdf2 | Chrome Android refuse de partager le `.docx` |
| Identifiants stables (signets Word) et suivi des modifications | Le DOCX validé dans Word devient la référence de la semaine suivante |
| Bibliothèques web décompressées comme de simples zip | L'installateur de Pyodide interroge PyPI, bloqué sur les réseaux filtrés |

## 8. Points d'intégration

| Voie | Détail |
|---|---|
| CLI | Commandes ci-dessus ; point d'entrée `remise-en-forme` déclaré dans `pyproject.toml` |
| Module Python | `pretraitement.executer(entree: Path, sortie: Path, rotations=None)` · `transcription.preparer(sortie, …) / terminer(sortie, reponses)` · `structuration.preparer(sortie, prof, …) / terminer(…)` · `controles.executer(sortie, prof) -> list[dict]` · `rendu.construire(elements, prof, numeros=False) -> Document` · `pdf.construire(elements, prof, titre) -> bytes` · `importation.executer(fichier, sortie, modele, effort)` · `mise_a_jour.executer(reference_docx, annotations, prof, sortie, nom, …)` · `reference.importer(docx, prof) -> list[dict]` · `profil.charger(nom) / charger_texte(yaml)` · `modele.creer(prof, chemin) / appliquer(prof, chemin)` · `claude.envoyer(requetes) -> dict` |
| Fichiers | Dossier de photos en entrée ; consommer `04_controles/document.json` ou les fichiers de `05_rendu/` |
| Web | Façade `remise_en_forme/web.py` (JSON en entrée et en sortie) ; Worker piloté par messages (`init`, `lancer`, `creer-modele`) |

**Ce qui manque pour une intégration propre :**
- un champ `schemaVersion` et des métadonnées de provenance (projet, profil, sources) dans les JSON ;
- une suite de tests automatisés (seuls les scripts de comparaison de `outils/` existent) ;
- une API HTTP ;
- l'échange de fichiers entre applications web (paramètres d'URL ou messages entre fenêtres) ;
- l'export et l'import de projets ;
- l'import des « digests » JSON de Calepin (format identifié, pas codé).

## 9. Prochaines étapes

Il n'y a aucun TODO dans le code. Ce qui est prévu :
1. Notes de conduite de régie (tops lumière, son, plateau ; document de conduite séparé), en attente d'exemples.
2. Test sur de vraies annotations manuscrites, avec corrigé.
3. Dossier Google Drive partagé par la troupe (mis en attente).
4. Import d'un digest Calepin : rattacher les notes de répétition aux répliques, puis synthèse par comédien et par scène.
5. Déploiement et test du relais Cloudflare (mode en arrière-plan).
