"""Compare les transcriptions de plusieurs réglages à une référence, mot à mot.

Usage : python outils/comparer_transcriptions.py eval reference autre1 autre2 ...
(chaque réglage = eval/<nom>/<test>/02_transcription/transcription.json)
"""
import difflib
import json
import sys
from pathlib import Path

racine, ref, *autres = sys.argv[1:]
racine = Path(racine)
sys.stdout.reconfigure(encoding="utf-8")


def pages(nom):
    out = {}
    for f in sorted((racine / nom).glob("*/02_transcription/transcription.json")):
        for p in json.loads(f.read_text(encoding="utf-8")):
            out[f"{f.parts[-3]}/{p['image'][:3]}{p['image'][-6:-4]}"] = p
    return out


R = pages(ref)
for nom in autres:
    A = pages(nom)
    n_mots = n_diff = 0
    details = []
    for cle, pr in R.items():
        pa = A.get(cle)
        if pa is None:
            continue
        mr, ma = pr["texte"].split(), pa["texte"].split()
        n_mots += len(mr)
        sm = difflib.SequenceMatcher(None, mr, ma, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != "equal":
                n_diff += max(i2 - i1, j2 - j1)
                details.append(f"  {cle}: « {' '.join(mr[i1:i2])} » → « {' '.join(ma[j1:j2])} »")
        if pr.get("numero_page") != pa.get("numero_page"):
            details.append(f"  {cle}: n° page {pr.get('numero_page')} → {pa.get('numero_page')}")
    print(f"== {nom} : {n_diff} mots différents sur {n_mots} ({100 * n_diff / max(n_mots, 1):.2f} %)")
    print("\n".join(details))
