"""Expérience : transcription directement depuis la photo brute (sans prétraitement
OpenCV), le modèle gérant lui-même rotation et doubles pages.

  python outils/test_photo_brute.py exemple/test1 eval/brute/test1

Écrit <sortie>/02_transcription/transcription.json au même format que l'étape 2,
pour comparaison avec outils/comparer_transcriptions.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from remise_en_forme import claude  # noqa: E402
from remise_en_forme.transcription import SYSTEME as SYSTEME_PAGE  # noqa: E402

SYSTEME = SYSTEME_PAGE.split("Transcris-la de façon", 1)[1]
SYSTEME = ("Tu es un transcripteur. Tu reçois la photo d'un livre ouvert : elle peut "
           "montrer UNE page ou DEUX pages (double page), être tournée de 90° ou 180°, "
           "avec une courbure près de la reliure. Repère chaque page imprimée entière "
           "et transcris-les séparément, dans l'ordre de lecture (gauche puis droite). "
           "Ignore les bouts de pages voisines coupés par le bord de la photo.\n"
           "Transcris chaque page de façon" + SYSTEME)

SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": {
        "type": "object",
        "properties": {"numero_page": {"type": ["string", "null"]}, "texte": {"type": "string"},
                       "remarques": {"type": "string"}},
        "required": ["numero_page", "texte", "remarques"], "additionalProperties": False}}},
    "required": ["pages"], "additionalProperties": False,
}

entree, sortie = Path(sys.argv[1]), Path(sys.argv[2])
sys.stdout.reconfigure(encoding="utf-8")
cl = claude.client()
res = []
for i, photo in enumerate(sorted(p for p in entree.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")), 1):
    r = claude.appel_json(cl, SYSTEME, [claude.image(photo), {"type": "text", "text": "Transcris les pages."}],
                          SCHEMA, effort="low", etiquette=photo.name)
    for k, pg in enumerate(r["pages"]):
        cote = "u" if len(r["pages"]) == 1 else "gd"[k] if k < 2 else str(k)
        res.append({"image": f"{i:03d}_{photo.stem.replace(' ', '_')}_{cote}.png", "photo": photo.name, **pg})
        print(f"{photo.name} → page {pg['numero_page']} ({len(pg['texte'])} car.) {pg['remarques'][:100]}")
d = sortie / "02_transcription"
d.mkdir(parents=True, exist_ok=True)
(d / "transcription.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
claude.bilan(d)
