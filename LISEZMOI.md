# remise-en-forme

Transforme des photos de pages imprimées en document Word (et PDF) mis en page selon un profil.

Il existe deux façons de s'en servir, avec le même code :
- **l'application web** (`index.html`), sur téléphone ou ordinateur, sans rien installer ;
- **la ligne de commande**, sur ce PC (voir plus bas).

## Application web

C'est une page statique, publiable telle quelle sur GitHub Pages. Le code Python de l'outil tourne dans le navigateur grâce à Pyodide, et les appels à Claude partent directement du navigateur vers Anthropic. Il n'y a aucun serveur.

- **Premier lancement :** environ 40 Mo sont téléchargés (Python, OpenCV), puis gardés en cache.
- **Clé d'API :** chacun saisit sa clé dans « Réglages », et elle reste enregistrée sur son appareil. Conseil : créez sur console.anthropic.com une clé par personne, avec un plafond de dépense.
- **Photos :** « 📷 Prendre une photo » ouvre l'appareil photo, une page après l'autre ; on peut aussi ajouter des photos depuis la galerie. Les photos sont enregistrées sur l'appareil dès qu'elles sont prises : elles ne se perdent pas si le navigateur recharge la page. ◀ change l'ordre, ✕ retire une photo.
- **Projets :** un projet (par exemple « Projet théâtre XXX ») regroupe le profil (dont la liste des personnages) et le modèle de mise en page. Tous les documents du projet ont donc le même format. « Mes documents » montre les documents du projet choisi.
- **Modèle de mise en page :** dans les réglages du projet, « Télécharger le modèle de départ » fournit un DOCX avec un exemple de chaque élément. On modifie ses **styles** dans Word (Accueil › Styles › clic droit › Modifier) sans les renommer, on l'enregistre, puis « Déposer un modèle ». Le DOCX et le PDF du projet suivent alors ces styles ; le PDF garde sa police Garamond embarquée.
- **Onglet « Nouveau document » :** photos → DOCX, DOCX à annoter (numéroté) et rapport. On peut aussi y **importer un document déjà propre** (PDF, DOCX ou TXT), qui est remis au format du projet. Le texte d'un DOCX ou d'un PDF exporté est lu directement, donc seul l'étiquetage est facturé. Les pages scannées d'un PDF sont transcrites. Les fichiers .doc (Word 97-2003) sont à enregistrer d'abord en DOCX ou PDF.
- **Onglet « Mes documents » :** chaque résultat y est enregistré automatiquement, sur l'appareil uniquement. On peut le retélécharger, le partager, le supprimer, ou utiliser un document transcrit comme référence d'une mise à jour.
- **Onglet « Mise à jour » :** DOCX de référence + photos annotées par personne → DOCX en suivi des modifications et rapport.
- **Interruption :** chaque réponse de Claude est enregistrée dès réception. Un traitement interrompu (page quittée, téléphone en veille) reprend tout seul à la réouverture, sans repayer ce qui est déjà fait. L'écran reste allumé pendant le traitement.
- **Mode « en arrière-plan »** (Réglages > Exécution) : la transcription est confiée à l'API Batch d'Anthropic. Elle est traitée même application fermée (en général en quelques minutes) et coûte 2 fois moins cher. À la réouverture, l'application termine le document. Anthropic refuse l'API Batch depuis un navigateur, d'où le petit relais gratuit à déployer sur Cloudflare (`outils/relais-cloudflare.js`, mode d'emploi en tête du fichier). Ce relais ne conserve aucune clé.
- **Partage :** Chrome sur Android refuse de partager les fichiers Word. « Partager » télécharge alors le fichier, et on l'envoie depuis Téléchargements ou depuis WhatsApp. Le rapport, lui, se partage en texte.
- **Profil :** « Réglages > Modifier le profil » permet par exemple d'adapter la liste des personnages. Le profil modifié est enregistré sur l'appareil.
- **PDF :** chaque résultat comprend un PDF, qui se lit et se partage partout, y compris avec le partage de Chrome sur Android, qui refuse les fichiers Word. Pour une mise à jour, le PDF montre les modifications en couleur, une couleur par personne : ajouts soulignés, suppressions barrées, notes et conflits sous les répliques concernées.

Pour tester l'application sur ce PC, avec la clé du fichier `.env` qui ne passe jamais par le navigateur :
```
.venv\Scripts\python outils\relais_dev.py
```
puis ouvrir http://localhost:8765. Dans « Réglages », mettre la clé `dev`, et dans la console du navigateur taper `localStorage.setItem("adresseApi", location.origin)`.

**Confidentialité :** les photos sont envoyées uniquement à l'API d'Anthropic, et rien n'est stocké ailleurs. Le dossier `exemple/` (photos de livres) est exclu du dépôt Git, car ces pages sont protégées par le droit d'auteur.

## Ligne de commande

## Utilisation

Ouvrir un terminal dans ce dossier (`OCR_theatre`), puis :

```
.venv\Scripts\python -m remise_en_forme tout exemple\test1 --nom ma_piece
```

- `exemple\test1` : le dossier contenant les photos (JPG/PNG), **dans l'ordre des pages** (l'ordre alphabétique des noms de fichiers).
- `--nom ma_piece` : nom du fichier produit.
- Un PDF est toujours produit, sans Word, par l'outil lui-même (police EB Garamond embarquée).

Résultat : `sortie\05_rendu\ma_piece.docx`. Lire aussi `sortie\04_controles\rapport.md`, qui liste ce qu'il faut vérifier.

Pour une nouvelle pièce, utiliser un autre dossier de sortie (`-o sortie_autre_piece`) : les pages déjà transcrites dans un dossier de sortie ne sont pas renvoyées à l'API.

## Les étapes

Chaque étape relit la sortie de la précédente. On peut les lancer une par une, par exemple pour relancer seulement le rendu après avoir modifié le profil :

| Commande | Rôle | Sortie |
|---|---|---|
| `pretraiter <photos>` | orientation, séparation des doubles pages, redressement, contraste | `sortie\01_pretraitement\` (`planche.jpg` = aperçu) |
| `transcrire` | transcription littérale, une page par appel à Claude | `sortie\02_transcription\` (un `.txt` par page) |
| `structurer` | étiquetage (réplique, didascalie…) selon le profil | `sortie\03_structuration\` |
| `controler` | contrôles et assemblage, sans appel à l'API | `sortie\04_controles\rapport.md` |
| `rendre` | DOCX, DOCX à annoter et PDF | `sortie\05_rendu\` |

Options utiles :
- `--rotation "photo.jpeg=90"` : force la rotation d'une photo mal orientée (0/90/180/270, sens horaire).
- `--force` : refait les appels à l'API même pour les pages déjà traitées (par exemple après une correction de la consigne).
- `-p mon_profil` : utilise `profils\mon_profil.yaml`.

Pour corriger une erreur de transcription à la main : modifier le `.json` de la page dans `sortie\02_transcription\`, puis relancer `structurer --force`, `controler` et `rendre`.

## Importer un document déjà propre, modèle de mise en page (ligne de commande)

```
.venv\Scripts\python -m remise_en_forme importer texte_propre.docx -p theatre --nom ma_piece -o sortie_import
.venv\Scripts\python -m remise_en_forme creer-modele mon_modele.docx -p theatre
.venv\Scripts\python -m remise_en_forme importer texte.pdf -p theatre --nom ma_piece --mise-en-page mon_modele.docx
```

`importer` accepte les formats PDF, DOCX et TXT. `--mise-en-page` (le DOCX dont on a retouché les styles dans Word) fonctionne aussi avec `rendre`, `tout` et `mettre-a-jour`.

## Mise à jour hebdomadaire à partir de pages annotées

La pièce n'est transcrite qu'une fois. Ensuite, le DOCX produit fait référence. Chaque semaine, on photographie seulement les pages annotées, une série par personne :

```
annotations\semaine_42\
    Lea\    photos de ses pages annotées
    Paul\   photos de ses pages annotées
```

Les annotations peuvent porter sur le livre d'origine ou sur la copie imprimée `<nom>_a_annoter.docx`. Celle-ci est produite en même temps que le DOCX, avec un petit numéro devant chaque réplique, ce qui facilite le recalage.

```
.venv\Scripts\python -m remise_en_forme mettre-a-jour sortie\05_rendu\ma_piece.docx annotations\semaine_42 -o maj_42
```

Résultat : `maj_42\ma_piece_maj.docx` (à valider dans Word), `maj_42\ma_piece_maj.pdf` (modifications en couleur, pour lire et partager) et `maj_42\rapport_maj.md`.
- Les textes barrés, remplacés ou ajoutés et les coupes (crochets) apparaissent en **suivi des modifications** Word, au nom de chaque personne (le nom du sous-dossier).
- Les **notes de jeu** deviennent des commentaires Word au nom de leur auteur.
- Si deux personnes proposent des choses différentes pour un même passage, **rien n'est appliqué** : un commentaire « Conflit » liste les propositions.
- Les lectures douteuses (écriture illisible, marque ambiguë) sont signalées dans le rapport.

Dans Word, on accepte ou refuse chaque modification (Révision), on supprime les commentaires réglés, puis on enregistre. **Ce fichier devient la référence de la semaine suivante.** Les corrections faites directement dans Word sont conservées. L'outil refuse un fichier où il reste des modifications en attente.

Coût mesuré : environ 4 centimes par page annotée.

## Coût et réglages

Coût mesuré pour une transcription complète : environ 3 centimes par page (Opus), soit environ 3 $ pour une pièce de 100 pages, une seule fois. Le détail de chaque étape est dans le fichier `couts.json` de son dossier.

`--modele` et `--effort` permettent de changer de modèle. Les comparaisons faites avec `outils\comparer_transcriptions.py` ont montré :
- Haiku invente des mots, il est à éviter ;
- Sonnet est deux fois moins cher, mais a fait quelques erreurs ;
- Opus n'a fait aucune erreur.

## Profils

Les profils sont dans `profils\` :
- `theatre` (par défaut) : titre, liste des personnages, répliques, didascalies ;
- `roman` : chapitres, épigraphes, paragraphes, dialogues, notes de bas de page. Exemple : `... tout exemple\test2 -o sortie_test2 -p roman --nom chapitre_II`

Les pages blanches (seul le verso transparaît) sont détectées et ignorées. Un numéro de page illisible est déduit des pages voisines, et le rapport le signale.
 Pour une autre pièce de théâtre, copier `theatre.yaml` et changer la liste `personnages`. Le profil décide aussi :
- des types d'éléments à étiqueter ;
- des styles du document : police, tailles, italique, retraits ;
- de l'affichage du rappel de page `[p. 157]` (`reference_page: fin` ou `aucune`).

Le champ `docx_reference` peut désigner un fichier Word dont les styles de même nom remplacent ceux du profil. On peut ainsi tout régler directement dans Word.

## Installation (déjà faite sur ce poste)

```
python -m venv .venv
.venv\Scripts\python -m pip install --use-feature=truststore opencv-python-headless numpy pyyaml python-dotenv anthropic python-docx fpdf2 truststore
```

La clé d'API se trouve dans `.env` (modèle : `.env.example`).

---
© 2026 M&M's productions — Tous droits réservés. Voir [LICENSE](LICENSE).
