"""Fabrique des photos de pages annotées « à la main » (simulées) pour tester
la commande mettre-a-jour, faute de vraies photos annotées.

  python outils/fabriquer_annotations_test.py

Produit exemple/annotations_test/{Lea,Paul,Marc}/*.jpg :
  - Léa et Paul annotent la copie imprimée par l'outil (un_remugle_a_annoter.pdf, page 2) ;
  - Marc annote une photo du livre d'origine (page 158).
"""
import random
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from remise_en_forme.pretraitement import lignes_texte, masque_encre  # noqa: E402

random.seed(1)
SORTIE = Path("exemple/annotations_test")
PDF = Path("sortie/05_rendu/un_remugle_a_annoter.pdf")
LIVRE_158 = Path("eval/base/test1/01_pretraitement/pages_couleur/003_WhatsApp_Image_2026-10-04_at_10.05.40_g.jpg")
ECHELLE = 150 / 72
STYLO = (30, 50, 160)
STYLO_ROUGE = (170, 30, 30)


def police(taille):
    return ImageFont.truetype(r"C:\Windows\Fonts\Inkfree.ttf", taille)


def page_pdf(n):
    page = pymupdf.open(PDF)[n]
    pix = page.get_pixmap(dpi=150)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    mots = [(w[0] * ECHELLE, w[1] * ECHELLE, w[2] * ECHELLE, w[3] * ECHELLE, w[4]) for w in page.get_text("words")]
    return img, mots


def trouver(mots, suite, occurrence=0):
    """Boîte englobante de la suite de mots `suite` (liste) dans la page."""
    textes = [m[4] for m in mots]
    vus = 0
    for i in range(len(textes) - len(suite) + 1):
        if textes[i:i + len(suite)] == suite:
            if vus == occurrence:
                b = mots[i:i + len(suite)]
                return min(m[0] for m in b), min(m[1] for m in b), max(m[2] for m in b), max(m[3] for m in b)
            vus += 1
    raise ValueError(f"introuvable : {suite}")


def barrer(d, boite, couleur=STYLO):
    x0, y0, x1, y1 = boite
    y = (y0 + y1) / 2
    pts = [(x, y + random.uniform(-1.5, 1.5)) for x in np.linspace(x0 - 3, x1 + 3, 12)]
    d.line(pts, fill=couleur, width=3)


def ecrire(d, xy, texte, taille=30, couleur=STYLO):
    d.text(xy, texte, font=police(taille), fill=couleur)


def en_photo(img, chemin, angle):
    """Pose la page sur un fond sombre, la tourne légèrement, un peu de flou : simule une photo."""
    a = np.array(img)[:, :, ::-1]
    h, w = a.shape[:2]
    fond = np.full((h + 300, w + 300, 3), (45, 40, 38), np.uint8)
    fond[150:150 + h, 150:150 + w] = a
    M = cv2.getRotationMatrix2D((fond.shape[1] / 2, fond.shape[0] / 2), angle, 1.0)
    fond = cv2.warpAffine(fond, M, (fond.shape[1], fond.shape[0]), borderValue=(45, 40, 38))
    fond = cv2.GaussianBlur(fond, (3, 3), 0)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", fond, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(chemin))
    print(chemin)


def lea():
    img, mots = page_pdf(1)
    d = ImageDraw.Draw(img)
    # remplace « divagues » par « délires »
    b = trouver(mots, ["Tu", "divagues."])
    bd = trouver(mots, ["divagues."])
    barrer(d, bd)
    ecrire(d, (bd[0] + 5, bd[1] - 30), "délires.")
    # ajoute une réplique après « JEAN-BERNARD. Oui. » (le premier de la page)
    o = trouver(mots, ["JEAN-BERNARD.", "Oui."])
    ecrire(d, (o[2] + 120, o[3] - 4), "ANTOINE. Vraiment ?")
    d.line([(o[2] + 115, o[3] + 8), (o[0] + 30, o[3] + 6)], fill=STYLO, width=2)
    d.polygon([(o[0] + 30, o[3] + 6), (o[0] + 44, o[3]), (o[0] + 44, o[3] + 12)], fill=STYLO)
    # note de jeu
    t = trouver(mots, ["JEAN-BERNARD.", "Je", "t'assure."])
    ecrire(d, (t[2] + 60, t[1] - 6), "(il se lève)", 28, STYLO_ROUGE)
    en_photo(img, SORTIE / "Lea" / "lea_p2.jpg", 2.5)


def paul():
    img, mots = page_pdf(1)
    d = ImageDraw.Draw(img)
    # autre proposition pour « divagues » : conflit avec Léa
    bd = trouver(mots, ["divagues."])
    barrer(d, bd, (20, 20, 20))
    ecrire(d, (bd[0] + 5, bd[1] - 30), "rêves !", 30, (20, 20, 20))
    # coupe de deux répliques (crochet dans la marge)
    a = trouver(mots, ["ANTOINE.", "Aujourd'hui"])
    b = trouver(mots, ["JEAN-BERNARD.", "Oui", "là", "maintenant."])
    x = a[0] - 40
    d.line([(x + 15, a[1]), (x, a[1]), (x, b[3]), (x + 15, b[3])], fill=(20, 20, 20), width=3)
    ecrire(d, (x - 85, (a[1] + b[3]) / 2 - 15), "coupé", 28, (20, 20, 20))
    # note de jeu
    q = trouver(mots, ["ANTOINE.", "Quelles", "choses", "?"])
    ecrire(d, (q[2] + 60, q[1] - 6), "plus fort", 28, (20, 20, 20))
    en_photo(img, SORTIE / "Paul" / "paul_p2.jpg", -2.0)


def marc():
    a = cv2.imdecode(np.fromfile(str(LIVRE_158), np.uint8), cv2.IMREAD_COLOR)
    gris = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    st = lignes_texte(masque_encre(gris))
    # regroupe les morceaux par ligne (même hauteur) et trie de haut en bas
    lignes = []
    for x, y, w, h, _ in sorted(st, key=lambda s: s[1] + s[3] / 2):
        c = y + h / 2
        if lignes and abs(lignes[-1]["c"] - c) < h * 0.6:
            L = lignes[-1]
            L["x0"], L["x1"] = min(L["x0"], x), max(L["x1"], x + w)
        else:
            lignes.append({"c": c, "x0": x, "x1": x + w, "h": h})
    img = Image.fromarray(a[:, :, ::-1])
    d = ImageDraw.Draw(img)
    L = lignes[8]  # 9e ligne : « ANTOINE. Tu es obnubilé. »
    barrer(d, (L["x0"], L["c"] - 2, L["x1"], L["c"] + 2), STYLO)
    ecrire(d, (L["x1"] + 20, L["c"] - 18), "non !", 26)
    en_photo(img, SORTIE / "Marc" / "marc_p158.jpg", 1.0)


if __name__ == "__main__":
    lea()
    paul()
    marc()
