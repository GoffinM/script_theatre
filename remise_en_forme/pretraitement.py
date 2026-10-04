"""Étape 1 — Prétraitement des photos de pages (OpenCV).

Pour chaque photo :
  1. orientation (0/90/180/270°) par analyse des lignes de texte ;
  2. détection du papier (clair, peu saturé) et séparation des doubles pages
     au niveau de la reliure ;
  3. pour chaque page : correction de perspective (quadrilatère du papier),
     redressement fin (deskew), aplanissement de la courbure près de la
     reliure (décalage vertical par bandes) ;
  4. normalisation du fond et du contraste (atténue la transparence du verso).

Sorties dans <sortie>/01_pretraitement/ :
  pages/NNN_<photo>_<g|d|u>.png   pages redressées (niveaux de gris)
  debug/<photo>.jpg               photo annotée (contours, reliure)
  planche.jpg                     planche-contact de toutes les pages
  manifest.json                   correspondance photo -> pages + paramètres
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

SEUIL_SENS = 0.3  # écart minimal de score haut/bas pour trancher 0° vs 180°
EXTENSIONS = {".jpg", ".jpeg", ".png"}


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def lire_image(chemin: Path) -> np.ndarray:
    # imdecode gère les chemins Windows avec accents/espaces
    data = np.fromfile(str(chemin), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Image illisible : {chemin}")
    return img


def ecrire_image(chemin: Path, img: np.ndarray) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(chemin.suffix, img)
    if not ok:
        raise ValueError(f"Écriture impossible : {chemin}")
    buf.tofile(str(chemin))


def tourner(img: np.ndarray, angle: int) -> np.ndarray:
    """Rotation par multiple de 90° (sens horaire)."""
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
             270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return img if angle % 360 == 0 else cv2.rotate(img, codes[angle % 360])


def masque_encre(gris: np.ndarray) -> np.ndarray:
    """Pixels de texte (encre sombre sur fond local clair)."""
    fond = cv2.medianBlur(gris, 31)
    diff = cv2.subtract(fond, gris)
    _, m = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m[diff < 18] = 0  # ignore le bruit et la transparence légère
    return m


def masque_papier(img: np.ndarray) -> np.ndarray:
    """Papier : clair et peu saturé (exclut fond sombre, doigts, post-it)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    seuil_v, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = ((v > max(seuil_v, 90)) & (s < 70)).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)  # bouche les lignes de texte
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    return m


# --------------------------------------------------------------------------
# 1. Orientation
# --------------------------------------------------------------------------

def score_horizontal(encre: np.ndarray, n_bandes: int = 6) -> float:
    """Netteté du profil des lignes : forte si les lignes de texte sont horizontales.

    Calculé sur des bandes verticales étroites (robuste à la courbure et à une
    légère inclinaison) ; le profil est passé en filtre passe-haut pour ne
    garder que l'alternance ligne/interligne, pas la répartition du texte.
    """
    h, w = encre.shape
    score = 0.0
    for i in range(n_bandes):
        bande = encre[:, i * w // n_bandes:(i + 1) * w // n_bandes]
        prof = bande.sum(axis=1).astype(np.float64)
        if prof.sum() == 0:
            continue
        lisse = cv2.GaussianBlur(prof.reshape(-1, 1), (1, 0), sigmaX=0, sigmaY=h / 40).ravel()
        fin = cv2.GaussianBlur(prof.reshape(-1, 1), (1, 0), sigmaX=0, sigmaY=1.5).ravel()
        score += float(np.sum((fin - lisse) ** 2))
    return score


def blocs_mots(encre: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boîtes des mots / groupes de mots (texte supposé horizontal)."""
    h, w = encre.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, w // 120), 1))
    fusion = cv2.dilate(encre, k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(fusion)
    hauteurs = stats[1:, cv2.CC_STAT_HEIGHT]
    if len(hauteurs) == 0:
        return []
    h_med = np.median(hauteurs[hauteurs > 4]) if (hauteurs > 4).any() else 0
    boites = []
    for x, y, bw, bh, aire in stats[1:]:
        if h_med and 0.5 * h_med <= bh <= 2.5 * h_med and bw >= 1.5 * bh:
            boites.append((x, y, bw, bh))
    return boites


def score_haut_bas(encre: np.ndarray) -> float:
    """> 0 si le texte horizontal est à l'endroit, < 0 s'il est à l'envers.

    Deux indices, calculés mot par mot (insensibles à la courbure) :
      - ascendantes/majuscules/accents (au-dessus de la bande des minuscules)
        plus fréquentes que les descendantes ;
      - débuts de ligne alignés à gauche, fins de ligne irrégulières
        (très marqué au théâtre : répliques courtes).
    """
    boites = blocs_mots(encre)
    if len(boites) < 5:
        return 0.0
    asc = []
    for x, y, bw, bh in boites:
        prof = encre[y:y + bh, x:x + bw].sum(axis=1).astype(np.float64)
        pic = prof.max()
        fort = np.where(prof > 0.5 * pic)[0]
        a, b = fort[0], fort[-1]
        dessus, dessous = prof[:a].sum(), prof[b + 1:].sum()
        if dessus + dessous > 0:
            asc.append((dessus - dessous) / (dessus + dessous))
    s_asc = float(np.mean(asc)) if asc else 0.0

    # alignement : extrémités gauche/droite des lignes (regroupement par y)
    h, w = encre.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, w // 25), 1))
    lignes = cv2.dilate(encre, k)
    n, _, st, _ = cv2.connectedComponentsWithStats(lignes)
    st = st[1:]
    if len(st) >= 6:
        h_med = np.median(st[:, 3])
        st = st[(st[:, 3] < 3 * h_med) & (st[:, 2] > 3 * h_med)]
    s_al = 0.0
    if len(st) >= 6:
        g = st[:, 0].astype(float)
        d = (st[:, 0] + st[:, 2]).astype(float)
        tol = w / 60
        fg = np.mean(np.abs(g - np.median(g)) < tol)
        fd = np.mean(np.abs(d - np.median(d)) < tol)
        s_al = float(fg - fd)
    return s_asc + s_al


def detecter_axe(encre: np.ndarray) -> int:
    """0 si les lignes sont horizontales, 90 si verticales."""
    return 0 if score_horizontal(encre) >= score_horizontal(tourner(encre, 90)) else 90


def petite_encre(img: np.ndarray, taille: int = 1400) -> np.ndarray:
    f = taille / max(img.shape[:2])
    petit = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    encre = masque_encre(cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY))
    encre[masque_papier(petit) == 0] = 0
    return encre


# --------------------------------------------------------------------------
# 2. Papier et reliure
# --------------------------------------------------------------------------

def plus_grand_contour(m: np.ndarray):
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(cs, key=cv2.contourArea) if cs else None


def detecter_reliure(img: np.ndarray, papier: np.ndarray, bbox) -> tuple[float, float] | None:
    """Reliure d'une double page sous forme de droite x = a*y + b, sinon None.

    On cherche, bande horizontale par bande horizontale, le plus large
    couloir vertical sans encre vers le centre du papier (l'espace entre les
    deux blocs de texte), renforcé par l'ombre de la reliure. Les centres des
    couloirs sont ensuite ajustés par une droite (la reliure peut être
    inclinée par la perspective).
    """
    x, y, w, h = bbox
    if w < 1.15 * h:  # une page seule est plus haute que large
        return None
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    encre = masque_encre(gris)
    encre[papier == 0] = 0
    lum = gris.astype(np.float64)
    a0, a1 = x + int(0.3 * w), x + int(0.7 * w)
    n_bandes = 10
    pts = []
    for i in range(n_bandes):
        y0, y1 = y + i * h // n_bandes, y + (i + 1) * h // n_bandes
        zone = encre[y0:y1, a0:a1]
        if not papier[y0:y1, a0:a1].any():
            continue
        prof = zone.sum(0).astype(np.float64)
        vide = prof <= 0.02 * max(prof.max(), 1)
        # plus long couloir vide
        meilleur, debut, j = (0, 0), None, 0
        for j, v in enumerate(np.append(vide, False)):
            if v and debut is None:
                debut = j
            elif not v and debut is not None:
                if j - debut > meilleur[1] - meilleur[0]:
                    meilleur = (debut, j)
                debut = None
        largeur = meilleur[1] - meilleur[0]
        if largeur < w / 60:
            continue
        # dans le couloir, la reliure est au plus sombre
        seg = lum[y0:y1, a0 + meilleur[0]:a0 + meilleur[1]].mean(0)
        cx = a0 + meilleur[0] + int(np.argmin(cv2.GaussianBlur(seg.reshape(1, -1), (0, 0), 3).ravel()))
        pts.append(((y0 + y1) / 2, cx, largeur))
    if len(pts) < n_bandes // 2:
        return None
    pts = np.array(pts)
    # ajustement robuste : écarte les bandes aberrantes
    garde = np.ones(len(pts), bool)
    for _ in range(3):
        a, b = np.polyfit(pts[garde, 0], pts[garde, 1], 1)
        res = np.abs(pts[:, 1] - (a * pts[:, 0] + b))
        garde = res < max(w / 80, np.median(res) * 2.5)
        if garde.sum() < 3:
            return None
    if abs(a) > 0.25:
        return None
    return float(a), float(b)


def quad_depuis_masque(m: np.ndarray) -> np.ndarray | None:
    """Quadrilatère (4 coins TL, TR, BR, BL) englobant le papier."""
    c = plus_grand_contour(m)
    if c is None or cv2.contourArea(c) < 0.05 * m.size:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return ordonner_coins(approx.reshape(4, 2).astype(np.float32))
    box = cv2.boxPoints(cv2.minAreaRect(c))
    return ordonner_coins(box.astype(np.float32))


def ordonner_coins(p: np.ndarray) -> np.ndarray:
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.array([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]],
                    dtype=np.float32)


def lignes_texte(encre: np.ndarray) -> np.ndarray:
    """Composantes « ligne de texte » : tableau (N, 5) x, y, w, h, aire."""
    h, w = encre.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, min(h, w) // 40), 1))
    fusion = cv2.dilate(encre, k)
    n, _, st, _ = cv2.connectedComponentsWithStats(fusion)
    st = st[1:]
    if len(st) == 0:
        return st
    h_med = np.median(st[st[:, 3] > 5, 3]) if (st[:, 3] > 5).any() else 10
    garde = (st[:, 3] > 0.5 * h_med) & (st[:, 3] < 3.0 * h_med) & (st[:, 2] > 2.5 * h_med)
    return st[garde]


def hauteur_caracteres(cc: np.ndarray) -> float:
    """Hauteur médiane d'un caractère, d'après les composantes connexes d'encre.

    Plus fiable que la hauteur des « lignes » fusionnées, qui peuvent regrouper
    plusieurs lignes quand le texte est incliné ou courbé.
    """
    h = cc[:, 3]
    ok = (cc[:, 4] > 10) & (h >= 4) & (cc[:, 2] < 4 * h)
    return float(np.median(h[ok])) if ok.any() else 10.0


def _ransac_x(ys: np.ndarray, xs: np.ndarray, tol: float, extreme: str, rng) -> tuple[float, float, int]:
    """Droite x = a*y + b alignant le plus de points ; à égalité, la plus extrême."""
    meilleur = (0.0, float(np.median(xs)), 0)
    score_best = -1e18
    n = len(xs)
    for _ in range(300):
        i, j = rng.choice(n, 2, replace=False)
        if abs(ys[i] - ys[j]) < 1:
            continue
        a = (xs[j] - xs[i]) / (ys[j] - ys[i])
        if abs(a) > 0.3:
            continue
        b = xs[i] - a * ys[i]
        res = xs - (a * ys + b)
        inl = np.abs(res) < tol
        cnt = int(inl.sum())
        # une marge borne le texte : presque aucun point au-delà
        dehors = (res < -tol) if extreme == "min" else (res > tol)
        if dehors.mean() > 0.05:
            continue
        pos = float(np.mean(a * ys + b))
        score = cnt * 1e6 + (-pos if extreme == "min" else pos)
        if score > score_best:
            score_best = score
            # réajustement moindres carrés sur les points alignés
            if cnt >= 2:
                a, b = np.polyfit(ys[inl], xs[inl], 1)
            meilleur = (float(a), float(b), cnt)
    return meilleur


def _droite_horizontale(st: np.ndarray, encre: np.ndarray, haut: bool) -> tuple[float, float]:
    """Droite y = c*x + d passant au bord haut (ou bas) du bloc de texte.

    La pente est celle de la plus longue ligne parmi les 3 premières (ou
    dernières), mesurée sur ses pixels d'encre.
    """
    ordre = np.argsort(st[:, 1] if haut else -(st[:, 1] + st[:, 3]))[:3]
    cand = st[ordre]
    x, y, w, h, _ = cand[np.argmax(cand[:, 2])]
    ys, xs = np.nonzero(encre[y:y + h, x:x + w])
    c = np.polyfit(xs + x, ys + y, 1)[0] if len(xs) > 10 else 0.0
    # positionne la droite pour englober toutes les lignes retenues
    if haut:
        d = min(float(yy - c * (xx + ww / 2)) for xx, yy, ww, hh, _ in st[ordre])
    else:
        d = max(float(yy + hh - c * (xx + ww / 2)) for xx, yy, ww, hh, _ in st[ordre])
    return float(c), d


def quad_texte(img: np.ndarray, masque: np.ndarray, marge: float = 0.04) -> np.ndarray | None:
    """Quadrilatère du bloc de texte (marges gauche/droite, première/dernière ligne).

    Plus fiable que le contour du papier (tranche, doigts, post-it) : la marge
    gauche est l'alignement des débuts de ligne, la marge droite celui des
    lignes justifiées.
    """
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    encre = masque_encre(gris)
    encre[cv2.erode(masque, np.ones((15, 15), np.uint8)) == 0] = 0
    st = lignes_texte(encre)
    if len(st) < 6:
        return None
    rng = np.random.default_rng(0)
    yc = st[:, 1] + st[:, 3] / 2
    tol = max(3.0, float(np.median(st[:, 3])) * 0.6)
    ag, bg, ng = _ransac_x(yc, st[:, 0].astype(float), tol, "min", rng)
    xd = (st[:, 0] + st[:, 2]).astype(float)
    ad, bd, nd = _ransac_x(yc, xd, tol, "max", rng)
    if ng < 3:
        return None
    if nd < 3:  # pas de lignes justifiées : parallèle à la marge gauche
        ad, bd = ag, float(np.max(xd - ag * yc))
    ch, dh = _droite_horizontale(st, encre, haut=True)
    cb, db = _droite_horizontale(st, encre, haut=False)
    # les lignes du haut/bas peuvent manquer (courbure, numéro de page) :
    # on étend aux caractères isolés situés entre les marges
    n, _, cc, ctr_cc = cv2.connectedComponentsWithStats(encre)
    hc = hauteur_caracteres(cc[1:])
    for (x, y, w, h, aire), (cx, cy) in zip(cc[1:], ctr_cc[1:]):
        if not (0.5 * hc <= h <= 2.5 * hc and w <= 4 * hc and aire > 8):
            continue
        if not (ag * cy + bg - tol <= cx <= ad * cy + bd + tol):
            continue
        dh = min(dh, y - ch * cx)
        db = max(db, y + h - cb * cx)
    def inter(a, b, c, d):  # x = a*y + b  et  y = c*x + d
        y = (c * b + d) / (1 - c * a)
        return [a * y + b, y]

    q = np.array([inter(ag, bg, ch, dh), inter(ad, bd, ch, dh),
                  inter(ad, bd, cb, db), inter(ag, bg, cb, db)], dtype=np.float32)
    # contrôle de cohérence : quadrilatère convexe et de taille raisonnable
    if not cv2.isContourConvex(q.reshape(-1, 1, 2)) or cv2.contourArea(q) < 0.05 * masque.sum() / 255:
        return None
    # agrandit autour du centre pour garder une marge (et les lignes débordantes)
    ctr = q.mean(0)
    return (ctr + (q - ctr) * (1 + 2 * marge)).astype(np.float32)


def redresser_perspective(img: np.ndarray, q: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = q
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(q, dst)
    return cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


# --------------------------------------------------------------------------
# 3. Deskew + courbure
# --------------------------------------------------------------------------

def deskew(gris: np.ndarray, plage: float = 6.0, pas: float = 0.2) -> tuple[np.ndarray, float]:
    encre = masque_encre(gris)
    h, w = encre.shape
    meilleur, best = 0.0, -1.0
    petit = cv2.resize(encre, (w // 2, h // 2))
    centre = (petit.shape[1] / 2, petit.shape[0] / 2)
    for ang in np.arange(-plage, plage + 1e-9, pas):
        M = cv2.getRotationMatrix2D(centre, ang, 1.0)
        r = cv2.warpAffine(petit, M, petit.shape[::-1])
        s = score_horizontal(r)
        if s > best:
            best, meilleur = s, float(ang)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), meilleur, 1.0)
    out = cv2.warpAffine(gris, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, meilleur


def interligne(profil: np.ndarray) -> float:
    """Pas des lignes (pixels) par autocorrélation du profil horizontal."""
    p = profil - profil.mean()
    ac = np.correlate(p, p, mode="full")[len(p) - 1:]
    if ac[0] <= 0:
        return 0.0
    ac /= ac[0]
    # premier maximum local après le premier passage sous zéro
    neg = np.where(ac < 0)[0]
    if len(neg) == 0:
        return 0.0
    debut = neg[0]
    fin = min(len(ac), debut * 6)
    return float(debut + np.argmax(ac[debut:fin])) if fin > debut else 0.0


def _decalage(ref: np.ndarray, q: np.ndarray, max_d: int) -> float:
    """Décalage vertical (sous-pixel) maximisant la corrélation de q sur ref."""
    h = len(ref)
    ds = np.arange(-max_d, max_d + 1)
    corr = np.array([np.dot(ref[max(0, d):h + min(0, d)], q[max(0, -d):h - max(0, d)]) for d in ds])
    i = int(np.argmax(corr))
    if 0 < i < len(corr) - 1:  # interpolation parabolique
        a, b, c = corr[i - 1], corr[i], corr[i + 1]
        den = a - 2 * b + c
        return float(ds[i] + (0.5 * (a - c) / den if den != 0 else 0))
    return float(ds[i])


def aplanir_courbure(gris: np.ndarray, n_bandes: int = 24, n_blocs: int = 4) -> tuple[np.ndarray, float]:
    """Corrige la courbure des lignes (près de la reliure).

    La page est découpée en n_blocs horizontaux × n_bandes verticales. Dans
    chaque bloc, on part de la bande la plus encrée et on suit les lignes de
    proche en proche vers la gauche et la droite : chaque bande est corrélée
    à sa voisine avec un décalage limité à une fraction d'interligne (évite
    de « sauter » d'une ligne). On obtient une grille de décalages verticaux,
    lissée et interpolée en un champ continu, appliqué par remap.
    """
    h, w = gris.shape
    encre = masque_encre(gris).astype(np.float32)
    pas = interligne(encre.sum(1))
    if pas < 4:
        return gris, 0.0
    bornes_x = np.linspace(0, w, n_bandes + 1).astype(int)
    bornes_y = np.linspace(0, h, n_blocs + 1).astype(int)
    grille = np.zeros((n_blocs, n_bandes), np.float32)
    max_d = max(1, int(0.35 * pas))
    for j in range(n_blocs):
        # bloc élargi (recouvrement) pour la stabilité de la corrélation
        y0 = max(0, bornes_y[j] - int(pas))
        y1 = min(h, bornes_y[j + 1] + int(pas))
        prof = [encre[y0:y1, bornes_x[i]:bornes_x[i + 1]].sum(1) for i in range(n_bandes)]
        masse = np.array([p.sum() for p in prof])
        if masse.max() == 0:
            continue
        valide = masse > 0.2 * masse.max()
        centre = int(np.argmax(masse * (1.2 - np.abs(np.linspace(-1, 1, n_bandes)))))
        prof = [cv2.GaussianBlur(p.reshape(-1, 1), (1, 0), sigmaX=0, sigmaY=1.0).ravel() - p.mean()
                for p in prof]
        for sens in (1, -1):
            cumul, ref = 0.0, prof[centre]
            i = centre + sens
            while 0 <= i < n_bandes:
                if valide[i]:
                    cumul += _decalage(ref, prof[i], max_d)
                    ref = prof[i]
                grille[j, i] = cumul
                i += sens
    # lissage : la courbure d'une page est régulière
    for j in range(n_blocs):
        xs = np.arange(n_bandes)
        grille[j] = np.polyval(np.polyfit(xs, grille[j], 3), xs)
    grille -= np.median(grille)
    if np.abs(grille).max() < 1.0:
        return gris, 0.0
    # champ continu : interpolation aux centres de bandes/blocs
    champ = cv2.resize(grille, (w, h), interpolation=cv2.INTER_LINEAR)
    champ = cv2.GaussianBlur(champ, (0, 0), sigmaX=w / n_bandes, sigmaY=h / n_blocs / 2)
    # marge blanche haut/bas : les lignes déplacées ne sortent pas du cadre
    pad = int(np.ceil(np.abs(champ).max())) + 2
    fond = int(np.percentile(gris, 90))
    gris = cv2.copyMakeBorder(gris, pad, pad, 0, 0, cv2.BORDER_CONSTANT, value=fond)
    champ = cv2.copyMakeBorder(champ, pad, pad, 0, 0, cv2.BORDER_REPLICATE)
    h = gris.shape[0]
    map_x = np.tile(np.arange(w, dtype=np.float32), (h, 1))
    map_y = np.arange(h, dtype=np.float32)[:, None] + champ
    out = cv2.remap(gris, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=fond)
    return out, float(np.abs(grille).max())


# --------------------------------------------------------------------------
# 4. Contraste
# --------------------------------------------------------------------------

def normaliser(gris: np.ndarray) -> np.ndarray:
    """Aplatit l'éclairage et blanchit le fond (atténue la transparence du verso)."""
    k = max(15, (min(gris.shape) // 20) | 1)
    fond = cv2.morphologyEx(gris, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    fond = cv2.GaussianBlur(fond, (0, 0), k / 3)
    norm = cv2.divide(gris, fond, scale=255)
    # niveaux : le verso transparaît en gris clair (~200-240) -> poussé au blanc
    noir, blanc = np.percentile(norm, 1), 225.0
    lut = np.clip((np.arange(256) - noir) / max(blanc - noir, 1), 0, 1)
    lut = (np.power(lut, 1.4) * 255).astype(np.uint8)  # gamma : renforce l'encre
    return cv2.LUT(norm, lut)


def rogner_marges(gris: np.ndarray, marge: int = 25) -> np.ndarray:
    """Recadre sur le texte (+ marge) ; ignore doigts, tranche, taches.

    Base : les lignes de texte ; on y ajoute les caractères isolés proches
    (numéro de page, ligne courte) de taille compatible avec le texte.
    """
    encre = masque_encre(gris)
    st = lignes_texte(encre)
    h, w = gris.shape
    if len(st) == 0:
        return gris
    x0, y0 = st[:, 0].min(), st[:, 1].min()
    x1, y1 = (st[:, 0] + st[:, 2]).max(), (st[:, 1] + st[:, 3]).max()
    _, _, cc, _ = cv2.connectedComponentsWithStats(encre)
    hc = hauteur_caracteres(cc[1:])
    for x, y, cw, ch, aire in cc[1:]:
        if not (0.5 * hc <= ch <= 2.5 * hc and cw <= 4 * hc and aire > 8):
            continue
        # caractère à l'intérieur des marges, au plus 4 interlignes (≈ 8 hauteurs) du bloc
        if x0 - hc <= x and x + cw <= x1 + hc and y0 - 8 * hc <= y and y + ch <= y1 + 8 * hc:
            y0, y1 = min(y0, y), max(y1, y + ch)
    return gris[max(0, y0 - marge):min(h, y1 + marge), max(0, x0 - marge):min(w, x1 + marge)]


# --------------------------------------------------------------------------
# Chaîne
# --------------------------------------------------------------------------

@dataclass
class PageSortie:
    fichier: str
    cote: str              # "g" gauche, "d" droite, "u" page unique
    deskew_deg: float
    courbure_px: float


@dataclass
class PhotoSortie:
    photo: str
    rotation_deg: int
    double_page: bool
    orientation_incertaine: bool = False
    pages: list[PageSortie] = field(default_factory=list)


def traiter_page(img: np.ndarray, masque: np.ndarray) -> tuple[np.ndarray, float, float]:
    q = quad_texte(img, masque)
    plage = 1.5  # la perspective par le texte a déjà redressé
    if q is None:
        q, plage = quad_depuis_masque(masque), 6.0
    page = redresser_perspective(img, q) if q is not None else img
    gris = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    gris, ang = deskew(gris, plage=plage, pas=0.1)
    gris, courb = aplanir_courbure(gris)
    gris = normaliser(gris)
    gris = rogner_marges(gris)
    return gris, ang, courb, page


def decouper(img: np.ndarray):
    """Masque du papier principal, découpé en pages ; abscisse de la reliure ou None."""
    papier = masque_papier(img)
    c = plus_grand_contour(papier)
    bbox = cv2.boundingRect(c) if c is not None else (0, 0, img.shape[1], img.shape[0])
    principal = np.zeros_like(papier)
    if c is not None:
        cv2.drawContours(principal, [c], -1, 255, cv2.FILLED)
        principal &= papier
    reliure = detecter_reliure(img, principal, bbox)
    if reliure is None:
        return [("u", principal)], None
    a, b = reliure
    xs = np.arange(img.shape[1])[None, :]
    ys = np.arange(img.shape[0])[:, None]
    gauche = xs < a * ys + b
    g, d = principal.copy(), principal.copy()
    g[~gauche] = 0
    d[gauche] = 0
    return [("g", g), ("d", d)], reliure


def traiter_photo(chemin: Path, dossier: Path, index: int, rotation_forcee: int | None = None):
    original = lire_image(chemin)
    if rotation_forcee is not None:
        candidats = [rotation_forcee % 360]
    else:
        axe = detecter_axe(petite_encre(original))
        candidats = [axe, axe + 180]

    # 0° vs 180° : on traite les deux, puis on compare le sens du texte sur
    # les pages obtenues. Si l'écart est trop faible (page presque vide), on
    # garde 0° pour une photo déjà horizontale, et on signale le doute.
    essais = []
    for rot in candidats:
        img = tourner(original, rot)
        morceaux, reliure = decouper(img)
        pages = [(cote, m, *traiter_page(img, m)) for cote, m in morceaux]  # (cote, masque, gris, ang, courb, couleur)
        encres = [masque_encre(p[2]) for p in pages]
        sens = sum(score_haut_bas(e) for e in encres)
        n_lignes = sum(len(lignes_texte(e)) for e in encres)
        essais.append((sens, rot, img, reliure, pages, n_lignes))
    if len(essais) == 2:
        ecart = abs(essais[0][0] - essais[1][0])
        incertain = ecart < SEUIL_SENS or min(e[5] for e in essais) < 8
        if incertain and candidats[0] == 0:
            choix = essais[0]
        else:
            choix = max(essais, key=lambda e: e[0])
    else:
        incertain, choix = False, essais[0]
    _, rot, img, reliure, pages, _ = choix

    debug = img.copy()
    if reliure is not None:
        a, b = reliure
        H = img.shape[0]
        cv2.line(debug, (int(b), 0), (int(a * H + b), H), (0, 0, 255), 3)
    res = PhotoSortie(chemin.name, rot % 360, reliure is not None, incertain)
    stem = chemin.stem.replace(" ", "_")
    for cote, m, page, ang, courb, couleur in pages:
        q = quad_texte(img, m)
        if q is None:
            q = quad_depuis_masque(m)
        if q is not None:
            cv2.polylines(debug, [q.astype(np.int32)], True, (0, 200, 0), 3)
        nom = f"{index:03d}_{stem}_{cote}.png"
        ecrire_image(dossier / "pages" / nom, page)
        # version couleur simplement recadrée : aide la transcription sur les passages pâles
        ecrire_image(dossier / "pages_couleur" / nom.replace(".png", ".jpg"), couleur)
        res.pages.append(PageSortie(nom, cote, round(ang, 2), round(courb, 1)))
    ecrire_image(dossier / "debug" / f"{stem}.jpg", debug)
    return res


def planche_contact(dossier: Path, pages: list[str], hauteur: int = 700) -> None:
    vignettes = []
    for nom in pages:
        p = lire_image(dossier / "pages" / nom)
        p = cv2.resize(p, (int(p.shape[1] * hauteur / p.shape[0]), hauteur))
        p = cv2.copyMakeBorder(p, 30, 10, 10, 10, cv2.BORDER_CONSTANT, value=(90, 90, 90))
        cv2.putText(p, nom[:40], (12, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        vignettes.append(p)
    if not vignettes:
        return
    par_ligne = 4
    lignes = []
    for i in range(0, len(vignettes), par_ligne):
        rang = vignettes[i:i + par_ligne]
        largeur = sum(v.shape[1] for v in rang)
        lignes.append((rang, largeur))
    W = max(l for _, l in lignes)
    rangs = []
    for rang, l in lignes:
        r = np.hstack(rang)
        rangs.append(cv2.copyMakeBorder(r, 0, 0, 0, W - l, cv2.BORDER_CONSTANT, value=(90, 90, 90)))
    ecrire_image(dossier / "planche.jpg", np.vstack(rangs))


def executer(entree: Path, sortie: Path, rotations: dict[str, int] | None = None) -> list[PhotoSortie]:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    photos = sorted(p for p in entree.iterdir() if p.suffix.lower() in EXTENSIONS)
    if not photos:
        raise SystemExit(f"Aucune photo JPG/PNG dans {entree}")
    dossier = sortie / "01_pretraitement"
    resultats = []
    for i, p in enumerate(photos, 1):
        r = traiter_photo(p, dossier, i, (rotations or {}).get(p.name))
        resultats.append(r)
        cotes = ", ".join(f"{pg.cote}: deskew {pg.deskew_deg}°, courbure {pg.courbure_px}px"
                          for pg in r.pages)
        print(f"[{i}/{len(photos)}] {p.name} — rotation {r.rotation_deg}°, "
              f"{'double page' if r.double_page else 'page unique'} ({cotes})"
              + ("  ⚠ orientation incertaine, forcer avec --rotation si besoin" if r.orientation_incertaine else ""))
    planche_contact(dossier, [pg.fichier for r in resultats for pg in r.pages])
    (dossier / "manifest.json").write_text(
        json.dumps([asdict(r) for r in resultats], ensure_ascii=False, indent=2), encoding="utf-8")
    return resultats
