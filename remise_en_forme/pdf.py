"""PDF du document, à partir des mêmes éléments et du même profil que le DOCX.

Le PDF sert à lire et à partager (il passe partout, y compris dans le partage de
Chrome sur Android, qui refuse les fichiers Word). Le DOCX reste le fichier de
travail. Pour une mise à jour, les modifications apparaissent en couleur, une
couleur par personne : ajouts soulignés, suppressions barrées ; les notes et les
conflits sont imprimés sous l'élément concerné.

Police : EB Garamond (licence SIL OFL, voir polices/OFL-EBGaramond.txt), embarquée.
Fonctionne aussi dans le navigateur (Pyodide) : fpdf2 est du pur Python.
"""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

POLICES = Path(__file__).resolve().parent.parent / "polices"
PT = 0.3528  # 1 point en millimètres
ITALIQUE = re.compile(r"_(.+?)_")
COULEURS = [(31, 95, 170), (190, 40, 40), (30, 130, 60), (130, 70, 170), (200, 110, 0), (0, 130, 140)]
GRIS = (110, 110, 110)


class _Document(FPDF):
    def __init__(self, pied: str):
        super().__init__(format="A4", unit="mm")
        self._pied = pied

    def footer(self):
        self.set_y(-12)
        self.set_font("Garamond", "I", 8)
        self.set_text_color(*GRIS)
        self.cell(0, 5, f"{self._pied}   —   {self.page_no()}", align="C")


class _Rendu:
    def __init__(self, prof: dict, titre: str, auteurs: list[str]):
        mep = prof.get("mise_en_page", {})
        self.prof, self.mep = prof, mep
        self.styles = mep.get("styles", {})
        self.taille = float(mep.get("taille", 12))
        self.couleur_auteur = {a: COULEURS[i % len(COULEURS)] for i, a in enumerate(auteurs)}
        d = self.doc = _Document(titre)
        for style, fichier in [("", "Regular"), ("B", "Bold"), ("I", "Italic"), ("BI", "BoldItalic")]:
            d.add_font("Garamond", style, str(POLICES / f"EBGaramond-{fichier}.ttf"))
        # l'italique d'EB Garamond n'a pas les exposants ¹ ² ³ : repris de la version droite
        d.add_font("GaramondSecours", "", str(POLICES / "EBGaramond-Regular.ttf"))
        d.add_font("GaramondSecours", "B", str(POLICES / "EBGaramond-Bold.ttf"))
        d.set_fallback_fonts(["GaramondSecours"], exact_match=False)
        m = float(mep.get("marges_cm", 2.5)) * 10
        d.set_margins(m, m, m)
        d.set_auto_page_break(True, margin=m)
        d.set_title(titre)
        d.set_creator("Remise en forme — M&M's productions")
        d.add_page()
        self.marge_g = m

    # -- styles ---------------------------------------------------------------
    def _style(self, nom: str) -> dict:
        return self.styles.get(nom, {})

    def _police(self, st: dict, italique: bool = False, extra: str = "", taille: float | None = None,
                bascule: bool = False):
        # _italique_ : italique ; dans une didascalie (déjà en italique), `bascule` le repasse en romain
        if bascule:
            ital = bool(st.get("italique")) != italique
        else:
            ital = bool(st.get("italique")) or italique
        style = ("B" if st.get("gras") else "") + ("I" if ital else "")
        self.doc.set_font("Garamond", "".join(sorted(set(style + extra))), taille or float(st.get("taille", self.taille)))

    def _hauteur(self, st: dict) -> float:
        return float(st.get("taille", self.taille)) * 1.35 * PT

    # -- texte riche ------------------------------------------------------------
    def _ecrire(self, st: dict, texte: str, mode: str | None = None, auteur: str = "",
                majuscules: bool = False, bascule: bool = False):
        """Écrit un morceau de texte (avec _italique_) à la suite, dans le style st."""
        h = self._hauteur(st)
        couleur = self.couleur_auteur.get(auteur.split(",")[0].strip(), COULEURS[0]) if mode else (0, 0, 0)
        extra = {"ins": "U", "del": "S"}.get(mode, "")
        self.doc.set_text_color(*couleur)
        pos = 0
        for m in list(ITALIQUE.finditer(texte)) + [None]:
            fin = m.start() if m else len(texte)
            if fin > pos:
                self._police(st, extra=extra, bascule=bascule)
                self._mots(h, texte[pos:fin].upper() if majuscules else texte[pos:fin])
            if m:
                self._police(st, italique=True, extra=extra, bascule=bascule)
                self._mots(h, m.group(1).upper() if majuscules else m.group(1))
                pos = m.end()
        self.doc.set_text_color(0, 0, 0)

    def _mots(self, h: float, texte: str):
        """Écrit mot par mot, en passant à la ligne AVANT un mot qui ne tient plus :
        write() seul coupe parfois un mot en deux au début d'un changement de style."""
        d = self.doc
        for mot in re.findall(r"\S+\s*|\s+", texte):
            # marge de sécurité : write() mesure un peu plus large (marges de cellule, espace final)
            largeur = d.get_string_width(mot) + 2 * d.c_margin + 0.5
            if mot.strip() and d.get_x() + largeur > d.w - d.r_margin and d.get_x() > d.l_margin + 0.5:
                d.ln(h)
                if not mot.strip():
                    continue
            d.write(h, mot)

    def _espace(self, pt: float):
        if pt:
            self.doc.ln(pt * PT)

    # -- éléments -------------------------------------------------------------
    def element(self, e: dict, saut_apres: bool = False):
        d = self.doc
        style_nom = self.prof["elements"].get(e["type"], {}).get("style", "Normal")
        st = self._style(style_nom)
        italique_source = self.prof["elements"].get(e["type"], {}).get("italique_source", True)
        segments = e.get("segments") or [[(t, None, "")] for t in e["texte"].split("\n")]
        mode_elt, auteur_elt = e.get("mode"), e.get("auteur", "")
        retrait = float(st.get("retrait_gauche", 0)) * 10
        h = self._hauteur(st)

        for k, segs in enumerate(segments):
            self._espace(float(st.get("espace_avant", 0)) if k == 0 else 0)
            if st.get("centre"):
                # éléments centrés (titres, liste des personnages) : une seule ligne de style
                texte = "".join(t for t, _, _ in segs)
                texte = ITALIQUE.sub(lambda m: m.group(1), texte)
                self._police(st, extra={"ins": "U", "del": "S"}.get(mode_elt, ""))
                if mode_elt:
                    d.set_text_color(*self.couleur_auteur.get(auteur_elt, COULEURS[0]))
                d.multi_cell(0, h, texte.upper() if st.get("majuscules") else texte, align="C",
                             new_x="LMARGIN", new_y="NEXT")
                d.set_text_color(0, 0, 0)
            else:
                d.set_left_margin(self.marge_g + retrait)
                d.set_x(self.marge_g + retrait)
                if e["type"] == "replique" and e.get("personnage") and k == 0:
                    sp = self._style("Personnage")
                    self._ecrire({**st, **{c: sp[c] for c in ("gras", "italique") if c in sp}},
                                 f"{e['personnage']}.", mode_elt, auteur_elt, majuscules=sp.get("majuscules", True))
                    self._ecrire(st, " ")
                for texte, mode, auteur in segs:
                    if e["type"] == "didascalie":
                        texte = texte.strip().strip("_") if len(segs) == 1 else texte
                    elif not italique_source:
                        texte = ITALIQUE.sub(lambda m: m.group(1), texte)
                    self._ecrire(st, texte, mode or mode_elt, auteur if mode else auteur_elt,
                                 majuscules=st.get("majuscules", False), bascule=e["type"] == "didascalie")
                d.ln(h)
                d.set_left_margin(self.marge_g)
            self._espace(float(st.get("espace_apres", 4)))

        for n in e.get("notes", []):
            self.note(n)
        if saut_apres:
            d.add_page()

    def note(self, n: dict):
        d = self.doc
        auteur = n.get("auteur", "?")
        couleur = self.couleur_auteur.get(auteur, GRIS)
        d.set_left_margin(self.marge_g + 8)
        d.set_x(self.marge_g + 8)
        d.set_text_color(*couleur)
        d.set_font("Garamond", "BI", self.taille - 2)
        conflit = auteur == "remise-en-forme"
        d.write(self.taille * 1.25 * PT, "◆ " + ("" if conflit else f"{auteur} : "))
        d.set_font("Garamond", "I", self.taille - 2)
        texte = n.get("texte", "").replace(" :\n- ", " : ").replace("\n- ", " ; ").replace("\n", " ")
        d.write(self.taille * 1.25 * PT, texte)
        d.ln(self.taille * 1.25 * PT)
        d.set_text_color(0, 0, 0)
        d.set_left_margin(self.marge_g)
        self._espace(4)

    def legende(self, auteurs: list[str]):
        """En tête d'une mise à jour : qui est de quelle couleur."""
        d = self.doc
        d.set_font("Garamond", "I", self.taille - 1)
        d.set_text_color(*GRIS)
        d.write(self.taille * 1.3 * PT, "Modifications proposées (ajouts soulignés, suppressions barrées) : ")
        for i, a in enumerate(auteurs):
            d.set_text_color(*self.couleur_auteur[a])
            d.set_font("Garamond", "B", self.taille - 1)
            d.write(self.taille * 1.3 * PT, a + ("" if i == len(auteurs) - 1 else ", "))
        d.set_text_color(0, 0, 0)
        d.ln(self.taille * 1.3 * PT)
        self._espace(10)


def construire(elements: list[dict], prof: dict, titre: str) -> bytes:
    """PDF (octets) des éléments ; mêmes champs que pour le DOCX (segments, mode, notes)."""
    auteurs = []
    for e in elements:
        candidats = [e.get("auteur")] + [a for segs in e.get("segments") or [] for _, m, a in segs if m]
        for a in candidats:
            for x in (a or "").split(","):
                if x.strip() and x.strip() not in auteurs:
                    auteurs.append(x.strip())
        for n in e.get("notes", []):
            if n.get("auteur") not in auteurs and n.get("auteur") != "remise-en-forme":
                auteurs.append(n.get("auteur"))
    r = _Rendu(prof, titre, auteurs)
    if auteurs:
        r.legende(auteurs)
    dernier_liste = max((i for i, e in enumerate(elements) if e["type"] == "personnage_liste"), default=None)
    saut = prof.get("mise_en_page", {}).get("saut_apres_liste")
    for i, e in enumerate(elements):
        r.element(e, saut_apres=bool(saut and i == dernier_liste))
    return bytes(r.doc.output())
