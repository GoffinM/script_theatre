"""Teste la façade web (remise_en_forme/web.py) en simulant le JavaScript, sans appel à l'API :
les réponses de Claude sont reconstituées à partir d'une exécution précédente.

  python outils/tester_web_py.py exemple/test2 eval/cli_check profils/roman.yaml
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from remise_en_forme import web  # noqa: E402

photos, precedent, profil_yaml = map(Path, sys.argv[1:4])
sys.stdout.reconfigure(encoding="utf-8")
sortie = Path("eval/web_py")
shutil.rmtree(sortie, ignore_errors=True)


def message(obj):
    """Une réponse de l'API telle que la renverrait le SDK JavaScript."""
    return {"model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0},
            "content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}


print(web.definir_profil(profil_yaml.read_text(encoding="utf-8")))
print(web.pretraiter(str(photos), str(sortie)))

reqs = json.loads(web.preparer_transcription(str(sortie), "claude-opus-5-5"))
print(len(reqs), "requêtes de transcription ; paramètres :", sorted(reqs[0]["params"]))
reps = {}
for r in reqs:
    p = json.loads((precedent / "02_transcription" / f"{Path(r['cle']).stem}.json").read_text(encoding="utf-8"))
    reps[r["cle"]] = message({k: p[k] for k in ("numero_page", "texte", "remarques")})
print(web.terminer_transcription(str(sortie), json.dumps(reps)))

reqs = json.loads(web.preparer_structuration(str(sortie), "claude-opus-5-5"))
reps = {}
for r in reqs:
    p = json.loads((precedent / "03_structuration" / f"{Path(r['cle']).stem}.json").read_text(encoding="utf-8"))
    els = []
    for e in p["elements"]:
        d = {k: v for k, v in e.items() if k not in ("texte", "lignes")}
        d["debut"], d["fin"] = e["lignes"]
        els.append(d)
    reps[r["cle"]] = message({"elements": els})
print(web.terminer_structuration(str(sortie), json.dumps(reps)))
print(web.controler_et_rendre(str(sortie), "test_web"))

a = json.loads((precedent / "04_controles" / "document.json").read_text(encoding="utf-8"))
b = json.loads((sortie / "04_controles" / "document.json").read_text(encoding="utf-8"))
print("document identique à l'exécution précédente :", a == b, len(a), len(b))
