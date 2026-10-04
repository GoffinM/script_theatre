"""Compare les documents assemblés (04_controles/document.json) de plusieurs réglages.

Usage : python outils/comparer_structures.py eval/ref eval/autre1 eval/autre2 ...
(chaque dossier contient test1/, test2/… avec 04_controles/document.json)
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ref, *autres = map(Path, sys.argv[1:])


def elements(dossier: Path):
    out = []
    for f in sorted(dossier.glob("*/04_controles/document.json")):
        for e in json.loads(f.read_text(encoding="utf-8")):
            texte = re.sub(r"[\s_]", "", e["texte"])
            out.append(f"{f.parts[-3]} | {e['type']} | {e.get('personnage') or ''} | "
                       f"{texte[:40]}…{texte[-15:]}")
    return out


R = elements(ref)
for a in autres:
    A = elements(a)
    diff = [l for l in difflib.unified_diff(R, A, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("---", "+++")]
    print(f"== {a.name} : {len(A)} éléments (réf. {len(R)}), {len(diff)} lignes de différence")
    print("\n".join(diff))
