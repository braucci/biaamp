#!/usr/bin/env python3
"""
app.py — Strato di presentazione Cocoa di BiaAmp.

Quattro finestre:
  • Lettore   — deck senza cornice disegnato a mano (NSBezierPath);
  • Playlist  — la coda di YouTube Music, modificabile, agganciata sotto;
  • Ricerca   — ricerca integrata, agganciata a destra del lettore;
  • Libreria  — WKWebView con music.youtube.com (accesso, pagina completa).
Playlist e Ricerca seguono il lettore anche quando va nel Dock.

Nessun calcolo di dominio qui: spettro, testi, geometria e decodifica
vivono in core.py. Questo modulo disegna e smista gli eventi.

Regole PyObjC rispettate (vedi README-progetto.md):
  • i metodi Python "puri" dentro le classi Objective-C hanno
    @objc.python_method;
  • le azioni (selettori) sono in camelCase con il solo '_' finale;
  • NSApplication.sharedApplication() si invoca solo a runtime.

© 2026 B. Raucci — Licenza MIT
"""
import logging
import sys
import time
from pathlib import Path

import objc
import AppKit
import Foundation
import WebKit
from PyObjCTools import AppHelper

import core

try:                                    # Centro di Controllo e tasti multimediali
    import MediaPlayer as MP
except Exception:                       # pragma: no cover — opzionale
    MP = None

log = logging.getLogger("biaamp")


def applicazione():
    return AppKit.NSApplication.sharedApplication()


def nome_selettore(sel) -> str:
    """Un selettore come testo: PyObjC può restituirlo come str o come bytes."""
    if sel is None:
        return ""
    return sel.decode() if isinstance(sel, bytes) else str(sel)


def _costante(modulo, nome, predefinito):
    return getattr(modulo, nome, predefinito)


# Maschere e costanti (con valori di ripiego documentati da Apple).
MASCHERA_SENZA_CORNICE = _costante(AppKit, "NSWindowStyleMaskBorderless", 0)
MASCHERA_RIDUCIBILE = _costante(AppKit, "NSWindowStyleMaskMiniaturizable", 1 << 2)
MASCHERA_TITOLO = _costante(AppKit, "NSWindowStyleMaskTitled", 1 << 0)
MASCHERA_CHIUDIBILE = _costante(AppKit, "NSWindowStyleMaskClosable", 1 << 1)
MASCHERA_RIDIMENSIONABILE = _costante(AppKit, "NSWindowStyleMaskResizable", 1 << 3)
MOD_CMD = _costante(AppKit, "NSEventModifierFlagCommand", 1 << 20)
MOD_OPT = _costante(AppKit, "NSEventModifierFlagOption", 1 << 19)
MOD_CTRL = _costante(AppKit, "NSEventModifierFlagControl", 1 << 18)
MOD_SHIFT = _costante(AppKit, "NSEventModifierFlagShift", 1 << 17)
OCCLUSIONE_VISIBILE = _costante(AppKit, "NSWindowOcclusionStateVisible", 1 << 1)
FINESTRA_SOTTO = _costante(AppKit, "NSWindowBelow", -1)
LIVELLO_NORMALE = _costante(AppKit, "NSNormalWindowLevel", 0)
LIVELLO_GALLEGGIANTE = _costante(AppKit, "NSFloatingWindowLevel", 3)
TASTO_SU, TASTO_GIU = chr(0xF700), chr(0xF701)
TASTO_SINISTRA, TASTO_DESTRA = chr(0xF702), chr(0xF703)

# Etichette (tag) delle voci di menu con stato da mostrare.
TAG_PRIMO_PIANO, TAG_SPETTRO, TAG_OSCILLOSCOPIO, TAG_VIZ_SPENTA, TAG_RIMANENTE = 101, 102, 103, 104, 105
TAG_PLAYLIST, TAG_LIBRERIA, TAG_RICERCA = 106, 107, 108
TAG_SCALA = {1.0: 111, 1.5: 112, 2.0: 113}


# ═══════════════════════════════════════════════════════════════════════════
# Palette e tipografia
# ═══════════════════════════════════════════════════════════════════════════

def colore(esa, alfa=1.0):
    esa = esa.lstrip("#")
    r, g, b = (int(esa[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alfa)


CHASSIS_ALTO, CHASSIS_BASSO = colore("#323848"), colore("#1b1e27")
SPAZZOLATO = colore("#ffffff", 0.025)
LUCE, OMBRA = colore("#6b7385"), colore("#07080b")
TITOLO_FONDO = colore("#14161d")
SOLCO_LUCE, SOLCO_OMBRA = colore("#4a5162"), colore("#0a0b0f")
LCD_FONDO = colore("#030807")
TEAL, TEAL_SCURO = colore("#6df0d2"), colore("#2c8f7c")
TEAL_TENUE, TEAL_FANTASMA = colore("#6df0d2", 0.55), colore("#6df0d2", 0.07)
ORO, ORO_SCURO = colore("#f7c873"), colore("#a8823d")
ROSA = colore("#ff8a8a")
PICCO = colore("#ffe3e3")
TESTO_TENUE = colore("#8b94a8")
TASTO_ALTO, TASTO_BASSO = colore("#444c5e"), colore("#262b36")
TASTO_P_ALTO, TASTO_P_BASSO = colore("#1b1f28"), colore("#2b313d")
ICONA, ICONA_PREMUTA = colore("#e1e6ef"), colore("#9aa3b5")
SELEZIONE = colore("#6df0d2", 0.16)
LED_SPENTO = colore("#24332f")

_FONT = {}


def font(tipo):
    """Font in unità skin (vengono scalati insieme al disegno)."""
    if tipo not in _FONT:
        nome, dim, peso = {
            "lcd": ("Menlo-Bold", 7.0, 0.4),
            "lista": ("Menlo-Regular", 6.0, 0.0),
            "piccolo": ("Menlo-Regular", 5.2, 0.0),
            "etichetta": ("Menlo-Bold", 5.2, 0.4),
            "titolo": ("Helvetica-Bold", 6.2, 0.4),
        }[tipo]
        f = AppKit.NSFont.fontWithName_size_(nome, dim)
        if f is None:
            f = AppKit.NSFont.monospacedSystemFontOfSize_weight_(dim, peso)
        _FONT[tipo] = f
    return _FONT[tipo]


_LARGHEZZA_CARATTERE = {}


def larghezza_carattere(tipo):
    if tipo not in _LARGHEZZA_CARATTERE:
        a = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            "0" * 20, {AppKit.NSFontAttributeName: font(tipo)})
        _LARGHEZZA_CARATTERE[tipo] = a.size().width / 20.0
    return _LARGHEZZA_CARATTERE[tipo]


# ═══════════════════════════════════════════════════════════════════════════
# Primitive di disegno (unità skin, y verso il basso)
# ═══════════════════════════════════════════════════════════════════════════

def rett(x, y, w, h):
    return Foundation.NSMakeRect(x, y, w, h)


def riempi(x, y, w, h, col):
    col.setFill()
    AppKit.NSBezierPath.fillRect_(rett(x, y, w, h))


def linea(x1, y1, x2, y2, col, spessore=0.5):
    linee(((x1, y1, x2, y2),), col, spessore)


def linee(segmenti, col, spessore=0.5):
    """Più segmenti in un solo tracciato: un'unica chiamata di disegno."""
    p = AppKit.NSBezierPath.bezierPath()
    for x1, y1, x2, y2 in segmenti:
        p.moveToPoint_((x1, y1))
        p.lineToPoint_((x2, y2))
    p.setLineWidth_(spessore)
    col.setStroke()
    p.stroke()


def smusso(x, y, w, h, incassato=True, sp=0.5):
    """Bordo a rilievo: luce in alto a sinistra (sporgente) o in basso a
    destra (incassato), come una lamiera stampata illuminata dall'alto."""
    alto, basso = (OMBRA, LUCE) if incassato else (LUCE, OMBRA)
    m = sp / 2
    linea(x, y + m, x + w, y + m, alto, sp)
    linea(x + m, y, x + m, y + h, alto, sp)
    linea(x, y + h - m, x + w, y + h - m, basso, sp)
    linea(x + w - m, y, x + w - m, y + h, basso, sp)


_GRADIENTI = {}


def gradiente(x, y, w, h, colori, angolo=90):
    """In una vista capovolta, angolo 90 = primo colore in alto.
    I gradienti sono creati una volta sola e riusati."""
    if w <= 0 or h <= 0:
        return
    chiave = tuple(id(c) for c in colori)
    g = _GRADIENTI.get(chiave)
    if g is None:
        g = _GRADIENTI[chiave] = AppKit.NSGradient.alloc().initWithColors_(colori)
    g.drawInRect_angle_(rett(x, y, w, h), angolo)


def poligono(punti, col):
    p = AppKit.NSBezierPath.bezierPath()
    p.moveToPoint_(punti[0])
    for q in punti[1:]:
        p.lineToPoint_(q)
    p.closePath()
    col.setFill()
    p.fill()


def testo(s, x, y, tipo, col, kern=0.0, allinea="sx", w=0.0, h=None):
    """Disegna una riga di testo; con h la centra verticalmente nel riquadro."""
    attr = {AppKit.NSFontAttributeName: font(tipo), AppKit.NSForegroundColorAttributeName: col}
    if kern:
        attr[AppKit.NSKernAttributeName] = kern
    a = Foundation.NSAttributedString.alloc().initWithString_attributes_(s, attr)
    dim = a.size()
    if allinea == "centro":
        x += (w - dim.width) / 2
    elif allinea == "dx":
        x += w - dim.width
    if h is not None:
        y += (h - dim.height) / 2
    a.drawAtPoint_((x, y))


def con_ritaglio(x, y, w, h, funzione):
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSBezierPath.clipRect_(rett(x, y, w, h))
    try:
        funzione()
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()


def tasto(r, premuto=False):
    x, y, w, h = r
    colori = [TASTO_P_ALTO, TASTO_P_BASSO] if premuto else [TASTO_ALTO, TASTO_BASSO]
    gradiente(x, y, w, h, colori)
    smusso(x, y, w, h, incassato=premuto)


def icona(nome, r, premuto=False):
    x, y, w, h = r
    d = 0.5 if premuto else 0.0
    cx, cy = x + w / 2 + d, y + h / 2 + d
    col = ICONA_PREMUTA if premuto else ICONA
    if nome == "play":
        poligono([(cx - 3, cy - 4), (cx + 4, cy), (cx - 3, cy + 4)], col)
    elif nome == "pause":
        riempi(cx - 3.5, cy - 4, 2.5, 8, col)
        riempi(cx + 1.0, cy - 4, 2.5, 8, col)
    elif nome == "stop":
        riempi(cx - 3.5, cy - 3.5, 7, 7, col)
    elif nome == "prev":
        riempi(cx - 5, cy - 4, 1.6, 8, col)
        poligono([(cx + 4, cy - 4), (cx - 3, cy), (cx + 4, cy + 4)], col)
    elif nome == "next":
        poligono([(cx - 4, cy - 4), (cx + 3, cy), (cx - 4, cy + 4)], col)
        riempi(cx + 3.4, cy - 4, 1.6, 8, col)
    elif nome == "eject":
        poligono([(cx - 5, cy + 1), (cx, cy - 4.5), (cx + 5, cy + 1)], col)
        riempi(cx - 5, cy + 2.4, 10, 1.8, col)
    elif nome == "menu":
        for dy in (-2.0, 0.0, 2.0):
            linea(cx - 2.5, cy + dy, cx + 2.5, cy + dy, col, 0.8)
    elif nome == "riduci":
        linea(cx - 2.5, cy + 2, cx + 2.5, cy + 2, col, 1.0)
    elif nome == "chiudi":
        linea(cx - 2.2, cy - 2.2, cx + 2.2, cy + 2.2, col, 0.9)
        linea(cx - 2.2, cy + 2.2, cx + 2.2, cy - 2.2, col, 0.9)


def tasto_led(r, etichetta, acceso, premuto, col_led=None):
    tasto(r, premuto)
    x, y, w, h = r
    d = 0.5 if premuto else 0.0
    riempi(x + 2.5 + d, y + h / 2 - 1.2 + d, 3.5, 2.4, (col_led or TEAL) if acceso else LED_SPENTO)
    testo(etichetta, x + 7 + d, y + d, "etichetta", ICONA_PREMUTA if premuto else ICONA, h=h)


def barra_titolo(W, titolo, attiva):
    riempi(1, 1, W - 2, 12.5, TITOLO_FONDO)
    tratti = [(x1, yy, x2, yy) for yy in (4.0, 6.0, 8.0, 10.0)
              for x1, x2 in ((18, W / 2 - 34), (W / 2 + 34, W - 32))]
    linee(tratti, SOLCO_OMBRA, 0.5)
    linee([(a, b + 0.5, c, d + 0.5) for a, b, c, d in tratti], SOLCO_LUCE, 0.35)
    testo(titolo, 0, 1, "titolo", ORO if attiva else TESTO_TENUE, kern=2.2, allinea="centro", w=W, h=12.5)


def chassis(W, H):
    gradiente(0, 0, W, H, [CHASSIS_ALTO, CHASSIS_BASSO])
    linee([(1, yy + 0.25, W - 1, yy + 0.25) for yy in range(15, int(H) - 1, 2)], SPAZZOLATO, 0.25)
    smusso(0, 0, W, H, incassato=False)


def pannello(x, y, w, h):
    riempi(x, y, w, h, LCD_FONDO)
    smusso(x, y, w, h, incassato=True)


# ═══════════════════════════════════════════════════════════════════════════
# Finestre e viste
# ═══════════════════════════════════════════════════════════════════════════

class FinestraSkin(AppKit.NSWindow):
    """Finestra senza cornice che può comunque ricevere la tastiera."""

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


class VistaBase(AppKit.NSView):
    """Comportamenti comuni: coordinate skin, trascinamento manuale della
    finestra (così le finestre agganciate si muovono insieme)."""

    def initWithFrame_(self, cornice):
        self = objc.super(VistaBase, self).initWithFrame_(cornice)
        if self is None:
            return None
        self.ctrl = None
        self.trascina = None
        self.premuto = None
        self.premuto_dentro = False
        self._mouse0 = None
        self._orig0 = None
        self._cornice0 = None
        return self

    def isFlipped(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, evento):
        return True

    @objc.python_method
    def scala(self):
        return self.ctrl.scala if self.ctrl is not None else core.SCALA_PREDEFINITA

    @objc.python_method
    def applica_scala(self):
        """Trasformazione unità skin → punti, da chiamare all'inizio del disegno."""
        t = Foundation.NSAffineTransform.transform()
        t.scaleBy_(self.scala())
        t.concat()

    @objc.python_method
    def punto(self, evento):
        p = self.convertPoint_fromView_(evento.locationInWindow(), None)
        s = self.scala()
        return p.x / s, p.y / s

    @objc.python_method
    def altezza(self):
        return self.bounds().size.height / self.scala()

    @objc.python_method
    def inizia_spostamento(self):
        self.trascina = "finestra"
        self._mouse0 = AppKit.NSEvent.mouseLocation()
        self._orig0 = self.window().frame().origin

    @objc.python_method
    def continua_spostamento(self):
        m = AppKit.NSEvent.mouseLocation()
        self.ctrl.sposta_finestra(self.window(),
                                  self._orig0.x + m.x - self._mouse0.x,
                                  self._orig0.y + m.y - self._mouse0.y)

    def keyDown_(self, evento):
        if self.ctrl is None or not self.ctrl.gestisci_tasto(evento):
            objc.super(VistaBase, self).keyDown_(evento)

    def rightMouseDown_(self, evento):
        if self.ctrl is not None:
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self.ctrl.menu_contestuale(), evento, self)


class VistaPrincipale(VistaBase):

    def drawRect_(self, rettangolo):
        try:
            self._disegna()
        except Exception:
            log.exception("disegno del lettore")

    @objc.python_method
    def _disegna(self):
        c = self.ctrl
        st, L = c.stato, core.LAYOUT_PRINCIPALE
        self.applica_scala()
        W, H = core.DIM_PRINCIPALE
        chassis(W, H)
        finestra = self.window()
        barra_titolo(W, "BIAAMP", finestra is not None and finestra.isKeyWindow())
        for nome in ("menu", "riduci", "chiudi"):
            p = self.premuto == nome and self.premuto_dentro
            tasto(L[nome], p)
            icona(nome, L[nome], p)

        # ── pannello LCD: stato, tempo, visualizzazione ──
        pannello(*core.PANNELLO_LCD)
        self._glifo_stato(st)
        self._tempo(st)
        self._viz(st)

        # ── display di testo ──
        x, y, w, h = L["marquee"]
        pannello(x, y, w, h)
        n = int((w - 6) / larghezza_carattere("lcd"))
        testo(core.marquee(c.testo_marquee, n, c.passo_marquee), x + 3, y, "lcd", ORO, h=h)
        x, y, w, h = L["info"]
        pannello(x, y, w, h)
        info = core.testo_info(st, c.transitorio)
        n = int((w - 6) / larghezza_carattere("piccolo"))
        testo(core.tronca(info, n), x + 3, y, "piccolo", TEAL_TENUE, h=h)

        # ── volume e interruttori ──
        testo("VOL", 112, L["volume"][1], "etichetta", TESTO_TENUE, h=L["volume"][3])
        self._cursore("volume", c.volume_mostrato() / 100.0, [TEAL, ORO, ROSA])
        tasto_led(L["like"], "♥", st.mi_piace, self._premuto("like"), ROSA)
        tasto_led(L["pl"], "PL", c.playlist_visibile(), self._premuto("pl"))
        tasto_led(L["ric"], "RIC", c.ricerca_visibile(), self._premuto("ric"))

        # ── posizione ──
        frazione = c.posizione_mostrata() / st.durata if st.durata > 0 else 0.0
        self._cursore("seek", frazione, [TEAL_SCURO, TEAL], attivo=st.durata > 0)

        # ── trasporto ──
        for nome in ("prev", "play", "pause", "stop", "next", "eject"):
            tasto(L[nome], self._premuto(nome))
            icona(nome, L[nome], self._premuto(nome))
        tasto_led(L["shuffle"], "SHUF", st.casuale, self._premuto("shuffle"))
        etichetta_rip = {"ONE": "RPT 1", "ALL": "RPT"}.get(st.ripetizione, "RPT")
        tasto_led(L["repeat"], etichetta_rip, st.ripetizione != "NONE", self._premuto("repeat"), ORO)
        self._logo(L["logo"])

        # ── riga di stato ──
        x, y, w, h = core.RIGA_STATO
        riserva = 30 if c.viz_disponibile else 4      # con la diagnosi la riga è tutta sua
        n = int((w - riserva) / larghezza_carattere("piccolo"))
        testo(core.tronca(core.testo_stato(st, c.viz_disponibile), n), x + 2, y, "piccolo", TESTO_TENUE, h=h)
        if c.viz_disponibile:
            testo(f"v{core.__version__}", x, y, "piccolo", SOLCO_LUCE, allinea="dx", w=w - 2, h=h)

    @objc.python_method
    def _premuto(self, nome):
        return self.premuto == nome and self.premuto_dentro

    @objc.python_method
    def _glifo_stato(self, st):
        x, y, w, h = core.GLIFO_STATO
        if st.in_riproduzione:
            poligono([(x + 1, y), (x + w - 1, y + h / 2), (x + 1, y + h)], TEAL)
            if st.codice == 3:          # caricamento: puntino lampeggiante
                if core.lampeggio_visibile(time.monotonic(), 0.5):
                    riempi(x + w + 1, y + h - 2, 2, 2, ORO)
        elif st.in_pausa:
            riempi(x + 1, y, 2.2, h, ORO)
            riempi(x + 4.8, y, 2.2, h, ORO)
        else:
            riempi(x + 1, y + 1, w - 2, h - 2, TEAL_FANTASMA if not st.pronto else TEAL_TENUE)

    @objc.python_method
    def _tempo(self, st):
        c = self.ctrl
        negativo, cifre, ore = core.tempo_lcd(c.posizione_mostrata(), st.durata, c.impost.tempo_rimanente)
        acceso = st.pronto and not (st.in_pausa and not core.lampeggio_visibile(time.monotonic()))
        y, cw, ch, sp = 23.0, 10.0, 15.0, 1.8
        # segno meno (tempo rimanente)
        if negativo and acceso:
            riempi(23.5, y + ch / 2 - 0.9, 5, 1.8, TEAL)
        posizioni = {0: 31.0, 1: 42.0, 3: 58.0, 4: 69.0}
        for i, carattere in enumerate(cifre):
            if carattere == ":":
                col = TEAL if acceso else TEAL_FANTASMA
                riempi(53.6, y + 3.5, 1.9, 1.9, col)
                riempi(53.6, y + 9.6, 1.9, 1.9, col)
                continue
            accesi = core.SEGMENTI.get(carattere, "") if acceso else ""
            for seg in "abcdefg":
                punti = core.geometria_segmento(seg, posizioni[i], y, cw, ch, sp)
                poligono(punti, TEAL if seg in accesi else TEAL_FANTASMA)
        if ore:
            testo("h", 81, y + 7, "piccolo", TEAL)

    @objc.python_method
    def _viz(self, st):
        c = self.ctrl
        x, y, w, h = core.LAYOUT_PRINCIPALE["viz"]
        modo = c.impost.modo_viz
        if modo == "spettro":
            n = c.spettro.n
            bw = w / n
            for i in range(n):
                lv, pk = c.spettro.livelli[i], c.spettro.picchi[i]
                bx, bh = x + i * bw, lv * h
                if bh > 0.3:
                    con_ritaglio(bx, y + h - bh, bw - 1, bh,
                                 lambda: gradiente(x, y, w, h, [ROSA, ORO, TEAL]))
                if pk > 0.02:
                    py = y + h - pk * h
                    riempi(bx, max(y, py - 0.8), bw - 1, 0.8, PICCO)
            righe = int(h / 1.5)        # segmentazione "a LED"
            linee([(x, y + k * 1.5, x + w, y + k * 1.5) for k in range(1, righe + 1)], LCD_FONDO, 0.45)
        elif modo == "oscilloscopio":
            linea(x, y + h / 2, x + w, y + h / 2, TEAL_FANTASMA, 0.4)
            punti = c.onda if (c.onda and st.in_riproduzione) else []
            if len(punti) > 1:
                p = AppKit.NSBezierPath.bezierPath()
                passo = w / (len(punti) - 1)
                for j, v in enumerate(punti):
                    q = (x + j * passo, y + h / 2 - core.limita(v, -1, 1) * (h / 2 - 0.5))
                    if j == 0:
                        p.moveToPoint_(q)
                    else:
                        p.lineToPoint_(q)
                p.setLineWidth_(0.7)
                TEAL.setStroke()
                p.stroke()
        if modo != "spento" and not c.viz_disponibile:
            testo("analisi n/d", x, y, "piccolo", TESTO_TENUE, allinea="centro", w=w, h=h)

    @objc.python_method
    def _cursore(self, nome, frazione, colori, attivo=True):
        x, y, w, h = core.LAYOUT_PRINCIPALE[nome]
        pannello(x, y, w, h)
        f = core.limita(frazione, 0.0, 1.0)
        if attivo and f > 0:
            con_ritaglio(x + 1, y + 1, (w - 2) * f, h - 2,
                         lambda: gradiente(x + 1, y + 1, w - 2, h - 2, colori, 0))
        if attivo:
            mw = 7.0
            mx = core.limita(x + 1 + (w - 2) * f - mw / 2, x, x + w - mw)
            gradiente(mx, y - 0.5, mw, h + 1, [ORO, ORO_SCURO])
            smusso(mx, y - 0.5, mw, h + 1, incassato=False)
            linea(mx + mw / 2, y + 1.5, mx + mw / 2, y + h - 1.5, ORO_SCURO, 0.5)

    @objc.python_method
    def _logo(self, r):
        x, y, w, h = r
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rett(x, y, w, h), 4, 4)
        LCD_FONDO.setFill()
        p.fill()
        p.setLineWidth_(0.5)
        LUCE.setStroke()
        p.stroke()
        for i, (col, hh) in enumerate(((TEAL, 6), (ORO, 10), (ROSA, 14))):
            riempi(x + 4 + i * 4.5, y + h - 3 - hh, 3, hh, col)

    # ── mouse ──────────────────────────────────────────────────────────────
    def mouseDown_(self, evento):
        x, y = self.punto(evento)
        nome = core.colpito(core.LAYOUT_PRINCIPALE, x, y)
        self.trascina, self.premuto = None, None
        if nome in ("seek", "volume"):
            self.trascina = nome
            self._muovi_cursore(x, definitivo=False)
        elif nome == "tempo":
            self.ctrl.esegui("tempo")
        elif nome == "viz":
            self.ctrl.esegui("viz")
        elif nome == "menu":
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self.ctrl.menu_contestuale(), evento, self)
        elif nome in core.PULSANTI_PRINCIPALE:
            self.premuto, self.premuto_dentro = nome, True
        else:
            self.inizia_spostamento()
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, evento):
        x, y = self.punto(evento)
        if self.trascina in ("seek", "volume"):
            self._muovi_cursore(x, definitivo=False)
        elif self.trascina == "finestra":
            self.continua_spostamento()
        elif self.premuto:
            dentro = core.dentro(core.LAYOUT_PRINCIPALE[self.premuto], x, y)
            if dentro != self.premuto_dentro:
                self.premuto_dentro = dentro
                self.setNeedsDisplay_(True)

    def mouseUp_(self, evento):
        x, y = self.punto(evento)
        if self.trascina in ("seek", "volume"):
            self._muovi_cursore(x, definitivo=True)
        elif self.premuto and self.premuto_dentro:
            self.ctrl.esegui(self.premuto)
        self.trascina, self.premuto, self.premuto_dentro = None, None, False
        self.setNeedsDisplay_(True)

    def scrollWheel_(self, evento):
        dy = evento.scrollingDeltaY()
        if not evento.hasPreciseScrollingDeltas():
            dy *= 4.0
        if abs(dy) > 0.01:
            self.ctrl.esegui("vol_rel", dy * 0.5)

    @objc.python_method
    def _muovi_cursore(self, x, definitivo):
        rx, _, rw, _ = core.LAYOUT_PRINCIPALE[self.trascina]
        f = core.frazione_da_x(x, rx + 1, rw - 2)
        if self.trascina == "seek":
            self.ctrl.imposta_posizione(f, definitivo)
        else:
            self.ctrl.imposta_volume(f * 100.0, definitivo)
        self.setNeedsDisplay_(True)


class VistaPlaylist(VistaBase):
    """Coda di riproduzione modificabile: selezione multipla (⇧, ⌘),
    trascinamento delle righe per riordinare, ⌥↑ ⌥↓ per spostare,
    ⌫ per togliere dalla coda, ⌘Z per annullare l'ultima modifica."""

    def initWithFrame_(self, cornice):
        self = objc.super(VistaPlaylist, self).initWithFrame_(cornice)
        if self is None:
            return None
        self.prima_riga = 0
        self.selezione = ()             # indici selezionati, crescenti
        self.ancora = None              # estremo fisso della selezione con ⇧
        self.cursore = None             # riga attiva per le frecce
        self.varco = None               # durante il riordino: dove cadranno le righe (0…n)
        self._resto_scroll = 0.0
        self._y0 = 0.0
        self._clic_differito = None
        return self

    def drawRect_(self, rettangolo):
        try:
            self._disegna()
        except Exception:
            log.exception("disegno della playlist")

    @objc.python_method
    def _disegna(self):
        c = self.ctrl
        self.applica_scala()
        W, H = core.LARGHEZZA_PLAYLIST, self.altezza()
        L = core.layout_playlist(H)
        chassis(W, H)
        finestra = self.window()
        barra_titolo(W, "PLAYLIST", finestra is not None and finestra.isKeyWindow())
        p = self.premuto == "chiudi" and self.premuto_dentro
        tasto(L["chiudi"], p)
        icona("chiudi", L["chiudi"], p)

        x, y, w, h = L["lista"]
        pannello(x, y, w, h)
        brani = c.coda.brani
        n_vis = core.righe_visibili(H)
        self.prima_riga = int(core.limita(self.prima_riga, 0, max(0, len(brani) - n_vis)))
        cw = larghezza_carattere("lista")
        n_car = int((w - 40) / cw)
        if not brani:
            testo("La coda è vuota: cerca un brano (tasto L)",
                  x, y, "piccolo", TESTO_TENUE, allinea="centro", w=w, h=min(h, 30))
        scelti = set(self.selezione)
        for k in range(n_vis):
            i = self.prima_riga + k
            if i >= len(brani):
                break
            b = brani[i]
            ry = y + 1 + k * core.ALTEZZA_RIGA
            if i in scelti:
                riempi(x + 1, ry, w - 2, core.ALTEZZA_RIGA, SELEZIONE)
            col = TESTO_TENUE if b.segnaposto else (ORO if b.selezionato else TEAL)
            testo(core.tronca(b.etichetta(i), n_car), x + 3, ry, "lista", col, h=core.ALTEZZA_RIGA)
            testo(b.durata_testo, x, ry, "lista", col, allinea="dx", w=w - 5, h=core.ALTEZZA_RIGA)
        if self.varco is not None:      # linea d'inserimento con le frecce ai lati
            k = self.varco - self.prima_riga
            if 0 <= k <= n_vis:
                yy = y + 1 + k * core.ALTEZZA_RIGA
                riempi(x + 3, yy - 0.5, w - 6, 1.0, ORO)
                poligono(((x + 1, yy - 2.5), (x + 4, yy), (x + 1, yy + 2.5)), ORO)
                poligono(((x + w - 1, yy - 2.5), (x + w - 4, yy), (x + w - 1, yy + 2.5)), ORO)
        if len(brani) > n_vis:          # indicatore di scorrimento
            frazione_vis = n_vis / len(brani)
            hh = max(4.0, (h - 2) * frazione_vis)
            yy = y + 1 + (h - 2 - hh) * (self.prima_riga / max(1, len(brani) - n_vis))
            riempi(x + w - 2.2, yy, 1.2, hh, TEAL_TENUE)

        x, y, w, h = L["piede"]
        piede = c.piede_playlist(len(self.selezione))
        testo(core.tronca(piede, int((w - 4) / larghezza_carattere("piccolo"))), x + 2, y, "piccolo",
              TESTO_TENUE, h=h)
        px, py, pw, ph = L["presa"]
        for k in range(3):              # presa di ridimensionamento
            d = 3.0 + k * 2.5
            linea(px + pw - d, py + ph - 1, px + pw - 1, py + ph - d, LUCE, 0.5)

    # ── selezione ─────────────────────────────────────────────────────────
    @objc.python_method
    def imposta_selezione(self, selezione, cursore=None):
        n = len(self.ctrl.coda.brani)
        self.selezione = core.selezione_valida(selezione, n)
        if self.selezione:
            self.cursore = cursore if cursore is not None else self.selezione[-1]
            self.ancora = self.selezione[0]
            self.prima_riga = core.scroll_visibile(self.cursore, self.prima_riga,
                                                   core.righe_visibili(self.altezza()), n)
        else:
            self.cursore = self.ancora = None
        self.setNeedsDisplay_(True)

    @objc.python_method
    def normalizza_selezione(self):
        """Dopo un aggiornamento della coda: via gli indici non più validi."""
        n = len(self.ctrl.coda.brani)
        self.selezione = core.selezione_valida(self.selezione, n)
        for nome in ("ancora", "cursore"):
            if getattr(self, nome) is not None and getattr(self, nome) >= n:
                setattr(self, nome, None)

    @objc.python_method
    def _riga_da_punto(self, x, y):
        L = core.layout_playlist(self.altezza())
        if not core.dentro(L["lista"], x, y):
            return None
        k = int((y - L["lista"][1] - 1) // core.ALTEZZA_RIGA)
        i = self.prima_riga + k
        if 0 <= k < core.righe_visibili(self.altezza()) and i < len(self.ctrl.coda.brani):
            return i
        return None

    # ── mouse ─────────────────────────────────────────────────────────────
    def mouseDown_(self, evento):
        x, y = self.punto(evento)
        H = self.altezza()
        L = core.layout_playlist(H)
        nome = core.colpito(L, x, y)
        self.trascina, self.premuto, self._clic_differito = None, None, None
        if nome == "chiudi":
            self.premuto, self.premuto_dentro = "chiudi", True
        elif nome == "presa":
            self.trascina = "presa"
            self._mouse0 = AppKit.NSEvent.mouseLocation()
            self._cornice0 = self.window().frame()
        elif nome == "lista":
            self.window().makeFirstResponder_(self)
            i = self._riga_da_punto(x, y)
            mod = evento.modifierFlags()
            if i is None:
                if not mod & (MOD_SHIFT | MOD_CMD):
                    self.selezione, self.ancora, self.cursore = (), None, None
            elif evento.clickCount() >= 2:
                self.selezione, self.ancora, self.cursore = (i,), i, i
                self.ctrl.esegui("indice", i)
            elif mod & (MOD_SHIFT | MOD_CMD):
                self.selezione, self.ancora = core.selezione_dopo_clic(
                    self.selezione, self.ancora, i, estendi=bool(mod & MOD_SHIFT), alterna=bool(mod & MOD_CMD))
                self.cursore = i
            else:
                if i in self.selezione:     # forse inizia un trascinamento della selezione multipla
                    self._clic_differito = i
                else:
                    self.selezione, self.ancora = (i,), i
                self.cursore = i
                self.trascina, self._y0 = "righe", y
        else:
            self.inizia_spostamento()
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, evento):
        if self.trascina == "finestra":
            self.continua_spostamento()
        elif self.trascina == "presa":
            m = AppKit.NSEvent.mouseLocation()
            self.ctrl.ridimensiona_playlist(self._cornice0, self._mouse0.y - m.y)
        elif self.trascina == "righe":
            x, y = self.punto(evento)
            if self.varco is None and abs(y - self._y0) < 3:
                return                              # soglia: un clic non è un trascinamento
            self._clic_differito = None
            if not self.ctrl.coda.modificabile:
                self.trascina = None
                self.ctrl.avviso_playlist("coda non modificabile: YouTube Music non è pronto")
                return
            H = self.altezza()
            lx, ly, lw, lh = core.layout_playlist(H)["lista"]
            n, n_vis = len(self.ctrl.coda.brani), core.righe_visibili(H)
            if y < ly and self.prima_riga > 0:      # scorrimento automatico ai bordi
                self.prima_riga -= 1
            elif y > ly + lh and self.prima_riga < n - n_vis:
                self.prima_riga += 1
            self.varco = core.varco_da_y(y, ly, self.prima_riga, n, n_vis)
            self.setNeedsDisplay_(True)
        elif self.premuto == "chiudi":
            x, y = self.punto(evento)
            dentro = core.dentro(core.layout_playlist(self.altezza())["chiudi"], x, y)
            if dentro != self.premuto_dentro:
                self.premuto_dentro = dentro
                self.setNeedsDisplay_(True)

    def mouseUp_(self, evento):
        if self.premuto == "chiudi" and self.premuto_dentro:
            self.ctrl.esegui("pl")
        elif self.trascina == "righe":
            if self.varco is not None:
                self.ctrl.esegui("pl_varco", self.varco)
            elif self._clic_differito is not None:
                i = self._clic_differito
                self.selezione, self.ancora, self.cursore = (i,), i, i
        self.trascina, self.premuto, self.premuto_dentro = None, None, False
        self.varco, self._clic_differito = None, None
        self.setNeedsDisplay_(True)

    def rightMouseDown_(self, evento):
        x, y = self.punto(evento)
        i = self._riga_da_punto(x, y)
        if i is None:
            objc.super(VistaPlaylist, self).rightMouseDown_(evento)
            return
        if i not in self.selezione:
            self.selezione, self.ancora, self.cursore = (i,), i, i
        self.setNeedsDisplay_(True)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self.ctrl.menu_playlist(), evento, self)

    def scrollWheel_(self, evento):
        dy = evento.scrollingDeltaY()
        righe = dy / core.ALTEZZA_RIGA if evento.hasPreciseScrollingDeltas() else dy
        self._resto_scroll -= righe
        passi = int(self._resto_scroll)
        if passi:
            self._resto_scroll -= passi
            self.prima_riga += passi
            self.setNeedsDisplay_(True)

    # ── tastiera e menu Modifica ──────────────────────────────────────────
    def keyDown_(self, evento):
        codice, n = evento.keyCode(), len(self.ctrl.coda.brani)
        mod = evento.modifierFlags()
        if codice in (125, 126) and mod & MOD_OPT:          # ⌥↑ ⌥↓: spostano i brani selezionati
            self.ctrl.esegui("pl_giu" if codice == 125 else "pl_su")
        elif n and codice in (125, 126):                    # ↑ ↓: selezione (⇧ la estende)
            self.selezione, self.ancora, self.cursore = core.naviga_selezione(
                self.ancora, self.cursore, 1 if codice == 125 else -1, n,
                estendi=bool(mod & MOD_SHIFT), partenza=self.ctrl.coda.indice_corrente)
            self.prima_riga = core.scroll_visibile(self.cursore, self.prima_riga,
                                                   core.righe_visibili(self.altezza()), n)
            self.setNeedsDisplay_(True)
        elif codice in (51, 117) and self.selezione:        # ⌫ ⌦: tolgono dalla coda
            self.ctrl.esegui("pl_rimuovi")
        elif codice in (36, 76) and self.selezione:         # Invio: riproduce
            self.ctrl.esegui("pl_riproduci")
        elif codice == 53 and self.selezione:               # Esc: deseleziona
            self.selezione, self.ancora, self.cursore = (), None, None
            self.setNeedsDisplay_(True)
        else:
            objc.super(VistaPlaylist, self).keyDown_(evento)

    def selectAll_(self, mittente):
        self.ctrl.esegui("pl_tutto")

    def undo_(self, mittente):
        self.ctrl.esegui("pl_annulla")

    def validateMenuItem_(self, voce):
        azione = nome_selettore(voce.action())
        if azione == "undo:":
            return self.ctrl.puo_annullare()
        if azione == "selectAll:":
            return bool(self.ctrl.coda.brani)
        return True

    @objc.python_method
    def segui_brano_corrente(self):
        self.prima_riga = core.scroll_visibile(self.ctrl.coda.indice_corrente, self.prima_riga,
                                               core.righe_visibili(self.altezza()), len(self.ctrl.coda.brani))


class CampoRicerca(AppKit.NSTextField):
    """Campo di testo nativo con il cursore dorato, leggibile sul fondo LCD."""

    def becomeFirstResponder(self):
        accettato = objc.super(CampoRicerca, self).becomeFirstResponder()
        if accettato:
            editor = self.currentEditor()
            if editor is not None and editor.respondsToSelector_("setInsertionPointColor:"):
                editor.setInsertionPointColor_(ORO)
        return accettato


class VistaRicerca(VistaBase):
    """Pannello di ricerca: campo, filtri, risultati riproducibili."""

    def initWithFrame_(self, cornice):
        self = objc.super(VistaRicerca, self).initWithFrame_(cornice)
        if self is None:
            return None
        self.prima_riga = 0
        self.selezione = None
        self._resto_scroll = 0.0
        return self

    def drawRect_(self, rettangolo):
        try:
            self._disegna()
        except Exception:
            log.exception("disegno della ricerca")

    @objc.python_method
    def _disegna(self):
        c = self.ctrl
        self.applica_scala()
        W, H = core.LARGHEZZA_RICERCA, self.altezza()
        L = core.layout_ricerca(H)
        chassis(W, H)
        finestra = self.window()
        barra_titolo(W, "RICERCA", finestra is not None and finestra.isKeyWindow())
        p = self._premuto("chiudi")
        tasto(L["chiudi"], p)
        icona("chiudi", L["chiudi"], p)

        pannello(*L["campo"])                   # il campo nativo è disegnato sopra
        tasto(L["cerca"], self._premuto("cerca"))
        d = 0.5 if self._premuto("cerca") else 0.0
        x, y, w, h = L["cerca"]
        testo("CERCA", x + d, y + d, "etichetta", ICONA, allinea="centro", w=w, h=h)
        for nome, etichetta in (("brani", "BRANI"), ("album", "ALBUM"), ("playlist", "PLAYLIST")):
            tasto_led(L[nome], etichetta, c.impost.filtro_ricerca == nome, self._premuto(nome))
        tasto_led(L["libreria"], "LIBRERIA", c.libreria_visibile(), self._premuto("libreria"), ORO)

        x, y, w, h = L["lista"]
        pannello(x, y, w, h)
        risultati = c.risultati
        n_vis = core.righe_visibili_ricerca(H)
        self.prima_riga = int(core.limita(self.prima_riga, 0, max(0, len(risultati) - n_vis)))
        n_car = int((w - 40) / larghezza_carattere("lista"))
        if not risultati:
            messaggio = c.stato_ricerca or "Scrivi qui sopra e premi Invio"
            testo(core.tronca(messaggio, int((w - 8) / larghezza_carattere("piccolo"))),
                  x, y, "piccolo", TESTO_TENUE, allinea="centro", w=w, h=min(h, 30))
        corrente = c.stato.video_id
        for k in range(n_vis):
            i = self.prima_riga + k
            if i >= len(risultati):
                break
            r = risultati[i]
            ry = y + 1 + k * core.ALTEZZA_RIGA
            if i == self.selezione:
                riempi(x + 1, ry, w - 2, core.ALTEZZA_RIGA, SELEZIONE)
            col = ORO if (r.tipo == "brano" and r.video_id == corrente) else (TEAL if r.disponibile else TESTO_TENUE)
            testo(core.tronca(r.etichetta(), n_car), x + 3, ry, "lista", col, h=core.ALTEZZA_RIGA)
            testo(core.tronca(r.colonna_destra(), 8), x, ry, "lista", col, allinea="dx", w=w - 5,
                  h=core.ALTEZZA_RIGA)
        if len(risultati) > n_vis:
            hh = max(4.0, (h - 2) * n_vis / len(risultati))
            yy = y + 1 + (h - 2 - hh) * (self.prima_riga / max(1, len(risultati) - n_vis))
            riempi(x + w - 2.2, yy, 1.2, hh, TEAL_TENUE)

        x, y, w, h = L["piede"]
        piede = c.stato_ricerca if (c.stato_ricerca and risultati) else (
            f"{len(risultati)} risultati · ⏎ riproduci · ⌥⏎ in coda" if risultati else "")
        testo(core.tronca(piede, int((w - 4) / larghezza_carattere("piccolo"))), x + 2, y, "piccolo",
              TESTO_TENUE, h=h)
        px, py, pw, ph = L["presa"]
        for k in range(3):
            dd = 3.0 + k * 2.5
            linea(px + pw - dd, py + ph - 1, px + pw - 1, py + ph - dd, LUCE, 0.5)

    @objc.python_method
    def _premuto(self, nome):
        return self.premuto == nome and self.premuto_dentro

    @objc.python_method
    def _riga_da_punto(self, x, y):
        L = core.layout_ricerca(self.altezza())
        if not core.dentro(L["lista"], x, y):
            return None
        k = int((y - L["lista"][1] - 1) // core.ALTEZZA_RIGA)
        i = self.prima_riga + k
        if 0 <= k < core.righe_visibili_ricerca(self.altezza()) and i < len(self.ctrl.risultati):
            return i
        return None

    def mouseDown_(self, evento):
        x, y = self.punto(evento)
        nome = core.colpito(core.layout_ricerca(self.altezza()), x, y)
        self.trascina, self.premuto = None, None
        if nome in core.PULSANTI_RICERCA:
            self.premuto, self.premuto_dentro = nome, True
        elif nome == "presa":
            self.trascina = "presa"
            self._mouse0 = AppKit.NSEvent.mouseLocation()
            self._cornice0 = self.window().frame()
        elif nome == "lista":
            i = self._riga_da_punto(x, y)
            if i is not None:
                self.selezione = i
                if evento.clickCount() >= 2:
                    self.ctrl.accoda_risultato(i, "fine" if evento.modifierFlags() & MOD_OPT else "ora")
            self.window().makeFirstResponder_(self)
        elif nome == "campo":
            self.ctrl.attiva_campo_ricerca()
        else:
            self.inizia_spostamento()
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, evento):
        if self.trascina == "finestra":
            self.continua_spostamento()
        elif self.trascina == "presa":
            m = AppKit.NSEvent.mouseLocation()
            self.ctrl.ridimensiona_ricerca(self._cornice0, self._mouse0.y - m.y)
        elif self.premuto:
            x, y = self.punto(evento)
            dentro = core.dentro(core.layout_ricerca(self.altezza())[self.premuto], x, y)
            if dentro != self.premuto_dentro:
                self.premuto_dentro = dentro
                self.setNeedsDisplay_(True)

    def mouseUp_(self, evento):
        if self.premuto and self.premuto_dentro:
            self.ctrl.pulsante_ricerca(self.premuto)
        self.trascina, self.premuto, self.premuto_dentro = None, None, False
        self.setNeedsDisplay_(True)

    def rightMouseDown_(self, evento):
        x, y = self.punto(evento)
        i = self._riga_da_punto(x, y)
        if i is None:
            objc.super(VistaRicerca, self).rightMouseDown_(evento)
            return
        self.selezione = i
        self.setNeedsDisplay_(True)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self.ctrl.menu_risultato(i), evento, self)

    def scrollWheel_(self, evento):
        dy = evento.scrollingDeltaY()
        righe = dy / core.ALTEZZA_RIGA if evento.hasPreciseScrollingDeltas() else dy
        self._resto_scroll -= righe
        passi = int(self._resto_scroll)
        if passi:
            self._resto_scroll -= passi
            self.prima_riga += passi
            self.setNeedsDisplay_(True)

    def keyDown_(self, evento):
        codice, n = evento.keyCode(), len(self.ctrl.risultati)
        mod = evento.modifierFlags()
        if n and codice in (125, 126):                      # ↓ ↑
            s = self.selezione if self.selezione is not None else -1
            if codice == 126 and s <= 0:
                self.ctrl.attiva_campo_ricerca()
                return
            self.selezione = int(core.limita(s + (1 if codice == 125 else -1), 0, n - 1))
            self.prima_riga = core.scroll_visibile(self.selezione, self.prima_riga,
                                                   core.righe_visibili_ricerca(self.altezza()), n)
            self.setNeedsDisplay_(True)
        elif codice in (36, 76) and self.selezione is not None:   # Invio
            modo = "fine" if mod & MOD_OPT else ("dopo" if mod & MOD_SHIFT else "ora")
            self.ctrl.accoda_risultato(self.selezione, modo)
        elif codice == 53:                                  # Esc: torna al campo
            self.ctrl.attiva_campo_ricerca()
        else:
            objc.super(VistaRicerca, self).keyDown_(evento)

    @objc.python_method
    def mostra_risultati(self):
        self.prima_riga = 0
        self.selezione = 0 if self.ctrl.risultati else None
        self.setNeedsDisplay_(True)


# ═══════════════════════════════════════════════════════════════════════════
# Centro di Controllo (Now Playing) e tasti multimediali
# ═══════════════════════════════════════════════════════════════════════════

class NowPlaying:
    """Pubblica titolo, artista, copertina e avanzamento nel Centro di
    Controllo e riceve i comandi dei tasti multimediali (F7-F9, cuffie)."""

    def __init__(self, esegui):
        self.centro = MP.MPNowPlayingInfoCenter.defaultCenter()
        cc = MP.MPRemoteCommandCenter.sharedCommandCenter()
        successo = _costante(MP, "MPRemoteCommandHandlerStatusSuccess", 0)
        self._obiettivi = []
        self._url_copertina = ""
        self._immagine = None
        self._opera = None

        def collega(comando, azione, con_posizione=False):
            def gestore(evento):
                arg = float(evento.positionTime()) if con_posizione else None
                AppHelper.callAfter(esegui, azione, arg)
                return successo
            comando.setEnabled_(True)
            self._obiettivi.append((comando, comando.addTargetWithHandler_(gestore)))

        collega(cc.togglePlayPauseCommand(), "toggle")
        collega(cc.playCommand(), "riprendi")
        collega(cc.pauseCommand(), "sospendi")
        collega(cc.nextTrackCommand(), "next")
        collega(cc.previousTrackCommand(), "prev")
        collega(cc.changePlaybackPositionCommand(), "seek", con_posizione=True)

    def aggiorna(self, st):
        info = {
            MP.MPMediaItemPropertyTitle: st.titolo or core.NOME_APP,
            MP.MPMediaItemPropertyArtist: st.artista,
            MP.MPMediaItemPropertyAlbumTitle: st.album,
            MP.MPMediaItemPropertyPlaybackDuration: float(st.durata),
            MP.MPNowPlayingInfoPropertyElapsedPlaybackTime: float(st.posizione),
            MP.MPNowPlayingInfoPropertyPlaybackRate: 1.0 if st.in_riproduzione else 0.0,
        }
        if self._opera is not None and self._url_copertina == st.copertina:
            info[MP.MPMediaItemPropertyArtwork] = self._opera
        self.centro.setNowPlayingInfo_(info)
        if hasattr(self.centro, "setPlaybackState_"):
            self.centro.setPlaybackState_(
                _costante(MP, "MPNowPlayingPlaybackStatePlaying", 1) if st.in_riproduzione
                else _costante(MP, "MPNowPlayingPlaybackStatePaused", 2))
        if st.copertina and st.copertina != self._url_copertina:
            self._scarica_copertina(st.copertina)

    def _scarica_copertina(self, url_testo):
        self._url_copertina, self._opera = url_testo, None
        url = Foundation.NSURL.URLWithString_(url_testo)
        if url is None:
            return

        def completato(dati, risposta, errore):
            if dati is not None:
                AppHelper.callAfter(self._imposta_copertina, url_testo, dati)

        sessione = Foundation.NSURLSession.sharedSession()
        sessione.dataTaskWithURL_completionHandler_(url, completato).resume()

    def _imposta_copertina(self, url_testo, dati):
        if url_testo != self._url_copertina:
            return                      # nel frattempo è cambiato brano
        immagine = AppKit.NSImage.alloc().initWithData_(dati)
        if immagine is None:
            return
        self._immagine = immagine
        try:
            self._opera = MP.MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
                immagine.size(), lambda dimensione: self._immagine)
        except Exception:
            log.exception("copertina per il Centro di Controllo")
            return
        info = dict(self.centro.nowPlayingInfo() or {})
        info[MP.MPMediaItemPropertyArtwork] = self._opera
        self.centro.setNowPlayingInfo_(info)


# ═══════════════════════════════════════════════════════════════════════════
# Controllore dell'applicazione
# ═══════════════════════════════════════════════════════════════════════════

def _risorsa(nome):
    """Percorso di una risorsa: nel pacchetto .app oppure in ./risorse."""
    base = Foundation.NSBundle.mainBundle().resourcePath()
    candidati = [Path(base) / nome] if base else []
    candidati.append(Path(__file__).resolve().parent / "risorse" / nome)
    for p in candidati:
        if p.is_file():
            return p
    raise FileNotFoundError(nome)


class ControlloreApp(Foundation.NSObject):
    """Delegato dell'applicazione, delle finestre e della WKWebView.

    Nessuna dichiarazione `protocols=`: i metodi dei delegati sono già noti a
    PyObjC come protocolli informali su NSObject (metadati di AppKit e WebKit),
    con i tipi corretti, compresi i blocchi come `decisionHandler`. Dichiarare i
    protocolli formali fa invece fallire PyObjC 12, che confronta le firme alla
    lettera: per i metodi che restituiscono BOOL i metadati usano 'Z', il
    protocollo di sistema 'B' (TypeError all'avvio).
    """

    def init(self):
        self = objc.super(ControlloreApp, self).init()
        if self is None:
            return None
        self.impost = core.carica_impostazioni(core.percorso_impostazioni())
        self.scala = self.impost.scala
        self.risultati = []
        self.stato_ricerca = ""
        self.ultima_ricerca = ""
        self.fin_ricerca = self.vista_ricerca = self.campo = None
        self._n_ricerca = 0
        self._indice_menu = None
        self._diagnosi = ""
        self._avviso_pl = None          # (testo, scadenza) nel piede della playlist
        self._annullabile = None        # ultima modifica della coda che ⌘Z può annullare
        self._gen_coda = 0              # generazione: scarta le letture della coda superate
        self._compagne = []             # finestre nascoste insieme al lettore ridotto a icona
        self.stato = core.StatoLettore()
        self.coda = core.Coda()
        self.spettro = core.Spettro()
        self.monitor = core.MonitorViz()
        self.viz_disponibile = True
        self.onda = []
        self.testo_marquee = core.testo_marquee(self.stato)
        self.passo_marquee = 0
        self.transitorio = None
        self.np = None
        self.web = None
        self.fin_principale = self.fin_playlist = self.fin_libreria = None
        self.vista_principale = self.vista_playlist = None
        self._t_prec = time.monotonic()
        self._t_invio = 0.0
        self._t_coda = 0.0
        self._t_vol = 0.0
        self._t_np = 0.0
        self._t_ingresso = 0.0
        self._acc_marquee = 0.0
        self._scadenza_transitorio = 0.0
        self._poll_in_volo = False
        self._coda_in_volo = False
        self._fallimenti = 0
        self._ingresso = None
        self._np_prec = None
        self._anteprima_seek = None
        self._anteprima_vol = None
        return self

    # ── ciclo di vita ──────────────────────────────────────────────────────
    def applicationDidFinishLaunching_(self, notifica):
        try:
            self._costruisci_menu()
            self._costruisci_libreria()
            self._costruisci_principale()
            self._costruisci_playlist()
            self._costruisci_ricerca()
            self._posiziona_finestre()
            self._applica_primo_piano()
            if MP is not None:
                try:
                    self.np = NowPlaying(self.esegui)
                except Exception:
                    log.exception("Now Playing non disponibile")
            for intervallo, selettore in ((1.0 / 30.0, "tickVeloce:"), (1.0, "tickCoda:")):
                timer = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                    intervallo, self, selettore, None, True)
                Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(timer, Foundation.NSRunLoopCommonModes)
            app = applicazione()
            if hasattr(app, "activate"):
                app.activate()
            else:
                app.activateIgnoringOtherApps_(True)
            log.info("BiaAmp %s avviato", core.__version__)
        except Exception:
            log.exception("avvio")
            raise

    def applicationWillTerminate_(self, notifica):
        try:
            self._memorizza_cornici()
            core.salva_impostazioni(self.impost, core.percorso_impostazioni())
        except Exception:
            log.exception("salvataggio impostazioni")

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visibili):
        self._mostra_lettore()
        return True

    # ── costruzione delle finestre ─────────────────────────────────────────
    @objc.python_method
    def _finestra_skin(self, w_unita, h_unita, titolo, classe_vista, riducibile=False):
        s = self.scala
        maschera = MASCHERA_SENZA_CORNICE | (MASCHERA_RIDUCIBILE if riducibile else 0)
        f = FinestraSkin.alloc().initWithContentRect_styleMask_backing_defer_(
            rett(0, 0, w_unita * s, h_unita * s), maschera, AppKit.NSBackingStoreBuffered, False)
        f.setTitle_(titolo)
        f.setReleasedWhenClosed_(False)
        f.setHasShadow_(True)
        f.setBackgroundColor_(AppKit.NSColor.blackColor())
        f.setDelegate_(self)
        v = classe_vista.alloc().initWithFrame_(rett(0, 0, w_unita * s, h_unita * s))
        v.ctrl = self
        v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        f.setContentView_(v)
        f.makeFirstResponder_(v)
        return f, v

    @objc.python_method
    def _costruisci_principale(self):
        W, H = core.DIM_PRINCIPALE
        self.fin_principale, self.vista_principale = self._finestra_skin(
            W, H, core.NOME_APP, VistaPrincipale, riducibile=True)

    @objc.python_method
    def _costruisci_playlist(self):
        self.fin_playlist, self.vista_playlist = self._finestra_skin(
            core.LARGHEZZA_PLAYLIST, self.impost.altezza_playlist, "Playlist", VistaPlaylist)

    @objc.python_method
    def _costruisci_ricerca(self):
        f, v = self._finestra_skin(core.LARGHEZZA_RICERCA, self.impost.altezza_ricerca, "Ricerca", VistaRicerca)
        scuro = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        if scuro is not None:
            f.setAppearance_(scuro)             # cursore e selezione chiari nel campo
        campo = CampoRicerca.alloc().initWithFrame_(rett(0, 0, 100, 20))
        campo.setBordered_(False)
        campo.setBezeled_(False)
        campo.setDrawsBackground_(True)
        campo.setBackgroundColor_(LCD_FONDO)
        campo.setTextColor_(ORO)
        campo.setFocusRingType_(_costante(AppKit, "NSFocusRingTypeNone", 1))
        campo.cell().setUsesSingleLineMode_(True)
        campo.cell().setScrollable_(True)
        campo.cell().setSendsActionOnEndEditing_(False)
        campo.setTarget_(self)
        campo.setAction_("cercaInvio:")
        campo.setDelegate_(self)
        v.addSubview_(campo)
        self.fin_ricerca, self.vista_ricerca, self.campo = f, v, campo
        self._disponi_campo()

    @objc.python_method
    def _disponi_campo(self):
        """Posizione e corpo del campo di ricerca secondo la scala corrente."""
        s = self.scala
        x, y, w, h = core.layout_ricerca(self.impost.altezza_ricerca)["campo"]
        self.campo.setFrame_(rett((x + 3) * s, (y + 2) * s, (w - 6) * s, (h - 3.5) * s))
        corpo = font("lcd").pointSize() * s * 0.95
        f = AppKit.NSFont.fontWithName_size_("Menlo-Bold", corpo) or \
            AppKit.NSFont.monospacedSystemFontOfSize_weight_(corpo, 0.4)
        self.campo.setFont_(f)
        segnaposto = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            "cerca su YouTube Music…",
            {AppKit.NSFontAttributeName: f, AppKit.NSForegroundColorAttributeName: TESTO_TENUE})
        self.campo.setPlaceholderAttributedString_(segnaposto)

    @objc.python_method
    def _costruisci_libreria(self):
        maschera = MASCHERA_TITOLO | MASCHERA_CHIUDIBILE | MASCHERA_RIDUCIBILE | MASCHERA_RIDIMENSIONABILE
        f = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rett(0, 0, 1100, 740), maschera, AppKit.NSBackingStoreBuffered, False)
        f.setTitle_("Libreria — YouTube Music")
        f.setReleasedWhenClosed_(False)
        f.setDelegate_(self)
        f.setMinSize_((640, 480))
        scuro = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        if scuro is not None:
            f.setAppearance_(scuro)

        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        cfg.setAllowsAirPlayForMediaPlayback_(True)
        cfg.setMediaTypesRequiringUserActionForPlayback_(_costante(WebKit, "WKAudiovisualMediaTypeNone", 0))
        pref_pagina = WebKit.WKWebpagePreferences.alloc().init()
        pref_pagina.setAllowsContentJavaScript_(True)
        cfg.setDefaultWebpagePreferences_(pref_pagina)
        pref = cfg.preferences()
        pref.setJavaScriptCanOpenWindowsAutomatically_(True)
        # macOS 14+: niente sospensione della pagina quando la Libreria è nascosta.
        politica = getattr(WebKit, "WKInactiveSchedulingPolicyNone", None)
        if politica is not None and pref.respondsToSelector_("setInactiveSchedulingPolicy:"):
            pref.setInactiveSchedulingPolicy_(politica)

        # La presa audio va iniettata PRIMA del lettore di YouTube (inizio del
        # documento), così intercetta la creazione dei SourceBuffer; il ponte
        # alla fine, quando gli elementi della pagina esistono.
        for nome, momento in (("presa.js", _costante(WebKit, "WKUserScriptInjectionTimeAtDocumentStart", 0)),
                              ("ponte.js", _costante(WebKit, "WKUserScriptInjectionTimeAtDocumentEnd", 1))):
            sorgente = _risorsa(nome).read_text(encoding="utf-8")
            script = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                sorgente, momento, True)
            cfg.userContentController().addUserScript_(script)

        web = WebKit.WKWebView.alloc().initWithFrame_configuration_(rett(0, 0, 1100, 740), cfg)
        web.setCustomUserAgent_(core.USER_AGENT)
        web.setNavigationDelegate_(self)
        web.setUIDelegate_(self)
        web.setAllowsBackForwardNavigationGestures_(True)
        web.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        if web.respondsToSelector_("setInspectable:"):
            web.setInspectable_(True)   # Safari ▸ Sviluppo ▸ BiaAmp: ispezione della pagina
        f.setContentView_(web)
        web.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(core.URL_SERVIZIO)))
        self.fin_libreria, self.web = f, web

    @objc.python_method
    def _schermo_utile(self, finestra=None):
        schermo = (finestra.screen() if finestra is not None else None) or AppKit.NSScreen.mainScreen()
        r = schermo.visibleFrame()
        return (r.origin.x, r.origin.y, r.size.width, r.size.height)

    @objc.python_method
    def _su_schermo(self, x, y, w, h):
        for schermo in AppKit.NSScreen.screens():
            r = schermo.visibleFrame()
            if core.si_sovrappongono((x + 20, y + 20, w - 40, h - 40),
                                     (r.origin.x, r.origin.y, r.size.width, r.size.height)):
                return True
        return False

    @objc.python_method
    def _posiziona_finestre(self):
        sx, sy, sw, sh = self._schermo_utile()
        s = self.scala
        W, H = core.DIM_PRINCIPALE
        wm, hm = W * s, H * s
        o = self.impost.origine_principale
        if o and self._su_schermo(o[0], o[1], wm, hm):
            x, y = o
        else:
            x, y = sx + 40, sy + sh - hm - 40
        self.fin_principale.setFrameOrigin_((x, y))
        self.fin_principale.makeKeyAndOrderFront_(None)

        hp = self.fin_playlist.frame().size.height
        op = self.impost.origine_playlist
        if self.impost.playlist_agganciata or not op or not self._su_schermo(op[0], op[1], wm, hp):
            self.impost.playlist_agganciata = True
            self._attacca_playlist()
        else:
            self.fin_playlist.setFrameOrigin_(tuple(op))
        if self.impost.playlist_visibile:
            self.fin_playlist.orderFront_(None)

        fr = self.fin_ricerca.frame()
        orr = self.impost.origine_ricerca
        if self.impost.ricerca_agganciata or not orr or \
                not self._su_schermo(orr[0], orr[1], fr.size.width, fr.size.height):
            self.impost.ricerca_agganciata = True
            self._attacca_ricerca()
        else:
            self.fin_ricerca.setFrameOrigin_(tuple(orr))
        if self.impost.ricerca_visibile:
            self.fin_ricerca.orderFront_(None)

        c = self.impost.cornice_libreria
        if c and self._su_schermo(*c):
            self.fin_libreria.setFrame_display_(rett(*c), False)
        else:
            lx = x + wm + fr.size.width + 12
            lw = min(1100.0, sx + sw - lx - 20)
            if lw < 640:
                lx, lw = sx + 60, min(1100.0, sw - 120)
            lh = min(760.0, sh - 80)
            self.fin_libreria.setFrame_display_(rett(lx, sy + sh - lh - 40, lw, lh), False)
        if self.impost.libreria_visibile:
            self.fin_libreria.orderFront_(None)
        self.fin_principale.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _memorizza_cornici(self):
        s = self.scala
        fp = self.fin_principale.frame()
        self.impost.origine_principale = [fp.origin.x, fp.origin.y]
        fl = self.fin_playlist.frame()
        self.impost.origine_playlist = [fl.origin.x, fl.origin.y]
        self.impost.altezza_playlist = fl.size.height / s
        self.impost.playlist_visibile = bool(self.fin_playlist.isVisible()) or self.fin_playlist in self._compagne
        fr = self.fin_ricerca.frame()
        self.impost.origine_ricerca = [fr.origin.x, fr.origin.y]
        self.impost.altezza_ricerca = fr.size.height / s
        self.impost.ricerca_visibile = bool(self.fin_ricerca.isVisible()) or self.fin_ricerca in self._compagne
        fb = self.fin_libreria.frame()
        self.impost.cornice_libreria = [fb.origin.x, fb.origin.y, fb.size.width, fb.size.height]
        self.impost.libreria_visibile = bool(self.fin_libreria.isVisible())
        self.impost.scala = s

    @objc.python_method
    def _applica_primo_piano(self):
        livello = LIVELLO_GALLEGGIANTE if self.impost.primo_piano else LIVELLO_NORMALE
        for f in (self.fin_principale, self.fin_playlist, self.fin_ricerca):
            f.setLevel_(livello)

    @objc.python_method
    def applica_scala(self, nuova):
        """Ridimensiona le tre finestre disegnate tenendo fermo il loro bordo
        superiore sinistro, poi ricompone gli agganci."""
        if nuova not in core.SCALE or nuova == self.scala:
            return
        self.scala = self.impost.scala = nuova
        W, H = core.DIM_PRINCIPALE
        for fin, (w_u, h_u) in ((self.fin_principale, (W, H)),
                                (self.fin_playlist, (core.LARGHEZZA_PLAYLIST, self.impost.altezza_playlist)),
                                (self.fin_ricerca, (core.LARGHEZZA_RICERCA, self.impost.altezza_ricerca))):
            fr = fin.frame()
            alto = fr.origin.y + fr.size.height
            fin.setFrame_display_(rett(fr.origin.x, alto - h_u * nuova, w_u * nuova, h_u * nuova), True)
        if self.impost.playlist_agganciata:
            self._attacca_playlist()
        if self.impost.ricerca_agganciata:
            self._attacca_ricerca()
        self._disponi_campo()
        self._ridisegna_tutto()

    # ── spostamento, aggancio, ridimensionamento ───────────────────────────
    @objc.python_method
    def sposta_finestra(self, finestra, x, y):
        fr = finestra.frame()
        w, h = fr.size.width, fr.size.height
        x, y = core.aggancia_schermo((x, y, w, h), self._schermo_utile(finestra))
        p = self.fin_principale.frame()
        ancora = (p.origin.x, p.origin.y, p.size.width, p.size.height)
        if finestra is self.fin_principale:
            finestra.setFrameOrigin_((x, y))
            if self.impost.playlist_agganciata and self.fin_playlist.isVisible():
                self._attacca_playlist()
            if self.impost.ricerca_agganciata and self.fin_ricerca.isVisible():
                self._attacca_ricerca()
        elif finestra is self.fin_playlist:
            agganciato = core.aggancia_sotto((x, y, w, h), ancora)
            self.impost.playlist_agganciata = agganciato is not None
            finestra.setFrameOrigin_(agganciato or (x, y))
        elif finestra is self.fin_ricerca:
            agganciato = core.aggancia_destra((x, y, w, h), ancora)
            self.impost.ricerca_agganciata = agganciato is not None
            finestra.setFrameOrigin_(agganciato or (x, y))
        else:
            finestra.setFrameOrigin_((x, y))

    @objc.python_method
    def _attacca_playlist(self):
        p, q = self.fin_principale.frame(), self.fin_playlist.frame()
        self.fin_playlist.setFrameOrigin_((p.origin.x, p.origin.y - q.size.height))

    @objc.python_method
    def _attacca_ricerca(self):
        p, q = self.fin_principale.frame(), self.fin_ricerca.frame()
        self.fin_ricerca.setFrameOrigin_((p.origin.x + p.size.width,
                                          p.origin.y + p.size.height - q.size.height))

    @objc.python_method
    def _ridimensiona(self, finestra, cornice0, delta_y, minimo, massimo):
        s = self.scala
        h = core.limita(cornice0.size.height + delta_y, minimo * s, massimo * s)
        alto = cornice0.origin.y + cornice0.size.height
        finestra.setFrame_display_(rett(cornice0.origin.x, alto - h, cornice0.size.width, h), True)
        return h / s

    @objc.python_method
    def ridimensiona_playlist(self, cornice0, delta_y):
        self.impost.altezza_playlist = self._ridimensiona(self.fin_playlist, cornice0, delta_y,
                                                          core.ALTEZZA_PLAYLIST_MIN, 600)

    @objc.python_method
    def ridimensiona_ricerca(self, cornice0, delta_y):
        self.impost.altezza_ricerca = self._ridimensiona(self.fin_ricerca, cornice0, delta_y,
                                                         core.ALTEZZA_RICERCA_MIN, 800)
        self._disponi_campo()

    # ── finestre: visibilità ───────────────────────────────────────────────
    @objc.python_method
    def playlist_visibile(self):
        return self.fin_playlist is not None and bool(self.fin_playlist.isVisible())

    @objc.python_method
    def ricerca_visibile(self):
        return self.fin_ricerca is not None and bool(self.fin_ricerca.isVisible())

    @objc.python_method
    def libreria_visibile(self):
        return self.fin_libreria is not None and bool(self.fin_libreria.isVisible())

    @objc.python_method
    def _principale_visibile(self):
        f = self.fin_principale
        return (f is not None and f.isVisible() and not f.isMiniaturized()
                and bool(f.occlusionState() & OCCLUSIONE_VISIBILE))

    @objc.python_method
    def _mostra_libreria(self):
        self.fin_libreria.makeKeyAndOrderFront_(None)
        self.impost.libreria_visibile = True

    @objc.python_method
    def _commuta_libreria(self):
        if self.libreria_visibile() and self.fin_libreria.isKeyWindow():
            self.fin_libreria.orderOut_(None)
            self.impost.libreria_visibile = False
        else:
            self._mostra_libreria()
        self._ridisegna_tutto()

    @objc.python_method
    def _non_compagna(self, finestra):
        self._compagne = [f for f in self._compagne if f is not finestra]

    @objc.python_method
    def _commuta_playlist(self):
        self._non_compagna(self.fin_playlist)
        if self.playlist_visibile():
            self.fin_playlist.orderOut_(None)
            self.impost.playlist_visibile = False
        else:
            if self.impost.playlist_agganciata:
                self._attacca_playlist()
            self.fin_playlist.orderFront_(None)
            self.impost.playlist_visibile = True
        self._ridisegna_tutto()

    @objc.python_method
    def _mostra_ricerca(self):
        self._non_compagna(self.fin_ricerca)
        if not self.ricerca_visibile() and self.impost.ricerca_agganciata:
            self._attacca_ricerca()
        self.fin_ricerca.orderFront_(None)
        self.impost.ricerca_visibile = True

    @objc.python_method
    def _commuta_ricerca(self):
        self._non_compagna(self.fin_ricerca)
        if self.ricerca_visibile():
            self.fin_ricerca.orderOut_(None)
            self.impost.ricerca_visibile = False
        else:
            self._mostra_ricerca()
        self._ridisegna_tutto()

    @objc.python_method
    def attiva_campo_ricerca(self):
        self._mostra_ricerca()
        self.fin_ricerca.makeKeyAndOrderFront_(None)
        self.fin_ricerca.makeFirstResponder_(self.campo)
        self._ridisegna_tutto()

    def windowShouldClose_(self, finestra):
        if finestra is self.fin_libreria:
            finestra.orderOut_(None)    # la pagina deve restare viva: suona lì
            self.impost.libreria_visibile = False
            self._ridisegna_tutto()
            return False
        return True

    # ── riduzione a icona: playlist e ricerca seguono il lettore ──────────
    # Come in Winamp le finestre agganciate formano un solo strumento: quando
    # il lettore va nel Dock, le compagne visibili si nascondono con lui e
    # ricompaiono (riagganciate) quando torna. Le impostazioni di visibilità
    # non cambiano, quindi anche un'uscita a lettore ridotto le conserva.
    def windowWillMiniaturize_(self, notifica):
        if notifica.object() != self.fin_principale:
            return
        self._compagne = [f for f in (self.fin_playlist, self.fin_ricerca) if f.isVisible()]
        for f in self._compagne:
            f.orderOut_(None)

    def windowDidDeminiaturize_(self, notifica):
        if notifica.object() == self.fin_principale:
            self._ripristina_compagne()

    @objc.python_method
    def _ripristina_compagne(self):
        compagne, self._compagne = self._compagne, []
        for f in compagne:
            if f is self.fin_playlist and self.impost.playlist_agganciata:
                self._attacca_playlist()
            elif f is self.fin_ricerca and self.impost.ricerca_agganciata:
                self._attacca_ricerca()
            f.orderFront_(None)
        if compagne:
            self.fin_principale.makeKeyAndOrderFront_(None)
            self._ridisegna_tutto()

    @objc.python_method
    def _mostra_lettore(self):
        if self.fin_principale.isMiniaturized():
            self.fin_principale.deminiaturize_(None)    # le compagne tornano in windowDidDeminiaturize_
        else:
            self.fin_principale.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _riduci_lettore(self):
        self.fin_principale.miniaturize_(None)

    def windowDidBecomeKey_(self, notifica):
        self._solleva_gruppo(notifica.object())
        self._ridisegna_tutto()

    # ── le finestre agganciate salgono insieme ────────────────────────────
    # Un clic porta davanti solo la finestra cliccata: le compagne agganciate
    # resterebbero dietro le finestre delle altre applicazioni. Quando una
    # finestra del gruppo riceve l'input, le altre vengono ordinate subito
    # sotto di lei (orderWindow:relativeTo:), così tutto il deck sale insieme,
    # come se fosse un'unica finestra. La Libreria non fa parte del gruppo.
    @objc.python_method
    def _gruppo_agganciato(self):
        gruppo = [self.fin_principale]
        if self.impost.playlist_agganciata and self.playlist_visibile():
            gruppo.append(self.fin_playlist)
        if self.impost.ricerca_agganciata and self.ricerca_visibile():
            gruppo.append(self.fin_ricerca)
        return gruppo

    @objc.python_method
    def _solleva_gruppo(self, finestra):
        gruppo = self._gruppo_agganciato()
        if finestra is None or not any(f == finestra for f in gruppo):
            return
        numero = finestra.windowNumber()
        for f in gruppo:
            if f != finestra and f.isVisible() and not f.isMiniaturized():
                f.orderWindow_relativeTo_(FINESTRA_SOTTO, numero)

    def windowDidResignKey_(self, notifica):
        self._ridisegna_tutto()

    @objc.python_method
    def _ridisegna_tutto(self):
        for v in (self.vista_principale, self.vista_playlist, self.vista_ricerca):
            if v is not None:
                v.setNeedsDisplay_(True)

    # ── interrogazione periodica della pagina ──────────────────────────────
    def tickVeloce_(self, timer):
        try:
            self._tick()
        except Exception:
            log.exception("ciclo principale")

    @objc.python_method
    def _tick(self):
        ora = time.monotonic()
        dt = min(0.25, max(0.0, ora - self._t_prec))
        self._t_prec = ora
        visibile = self._principale_visibile()
        modo = core.CODICE_VIZ.get(self.impost.modo_viz, "") if visibile else ""
        intervallo = 1.0 / 30.0 if visibile else 0.25

        if self.web is not None and ora - self._t_invio >= intervallo * 0.9 and \
                (not self._poll_in_volo or ora - self._t_invio > 1.5):
            self._poll_in_volo, self._t_invio = True, ora
            self.web.evaluateJavaScript_completionHandler_(core.js_stato(modo), self._ricevi_stato)

        fresco = ora - self._t_ingresso < 0.12
        self.spettro.aggiorna(self._ingresso if fresco else None, dt)
        attesa = bool(modo) and self.stato.in_riproduzione
        presente = bool(self.stato.viz_tipo) and not core.viz_vuota(self.stato.viz_tipo, self.stato.viz)
        self.viz_disponibile = self.monitor.aggiorna(attesa, presente, dt)

        self._acc_marquee += dt
        if self._acc_marquee >= 0.18:
            self._acc_marquee = 0.0
            self.passo_marquee += 1
        if self.transitorio and ora > self._scadenza_transitorio:
            self.transitorio = None
        if self._avviso_pl and ora > self._avviso_pl[1]:
            self._avviso_pl = None
            self.vista_playlist.setNeedsDisplay_(True)

        if self.np is not None and self.stato.titolo and \
                core.np_da_aggiornare(self._np_prec, self.stato, ora - self._t_np):
            try:
                self.np.aggiorna(self.stato)
            except Exception:
                log.exception("aggiornamento Now Playing")
            self._np_prec, self._t_np = self.stato, ora

        if visibile:
            self.vista_principale.setNeedsDisplay_(True)

    @objc.python_method
    def _ricevi_stato(self, risultato, errore):
        self._poll_in_volo = False
        if errore is not None or not isinstance(risultato, str) or not risultato:
            self._fallimenti += 1
            if self._fallimenti == 45:          # ~1,5 s senza risposta: pagina non pronta
                self.stato = core.StatoLettore()
                self.testo_marquee = core.testo_marquee(self.stato)
            return
        self._fallimenti = 0
        nuovo = core.StatoLettore.da_json(str(risultato))
        if nuovo.diagnosi_audio != self._diagnosi:          # traccia della presa audio nel registro
            self._diagnosi = nuovo.diagnosi_audio
            log.info("presa audio: %s", self._diagnosi)
        if nuovo.viz_tipo == "f":
            self._ingresso = self.spettro.bande(nuovo.viz, nuovo.freq_camp or 48000.0, nuovo.n_fft or 2048)
            self._t_ingresso = time.monotonic()
        elif nuovo.viz_tipo == "w":
            self.onda = core.forma_onda(nuovo.viz, 88)
        cambio = nuovo.chiave_brano != self.stato.chiave_brano
        self.stato = nuovo
        indice = self.coda.indice_corrente
        self.testo_marquee = core.testo_marquee(nuovo, indice + 1 if indice is not None else None)
        if cambio:
            self.passo_marquee = 0
            self._coda_in_volo = False
            self.tickCoda_(None)                # rilegge subito la coda

    def tickCoda_(self, timer):
        try:
            ora = time.monotonic()
            if self.web is None or (self._coda_in_volo and ora - self._t_coda < 3.0):
                return
            self._coda_in_volo, self._t_coda = True, ora
            generazione = self._gen_coda
            self.web.evaluateJavaScript_completionHandler_(
                core.js_coda(self.coda.firma),
                lambda risultato, errore: self._ricevi_coda(risultato, errore, generazione))
        except Exception:
            log.exception("lettura della coda")

    @objc.python_method
    def _ricevi_coda(self, risultato, errore, generazione=None):
        if generazione is not None and generazione != self._gen_coda:
            return          # partita prima di una modifica: descrive una coda superata
        self._coda_in_volo = False
        if errore is not None or not isinstance(risultato, str):
            return
        id_prima, via_prima, errore_prima = self.coda.id_corrente, self.coda.via, self.coda.errore
        if self.coda.aggiorna_da_json(str(risultato)):
            if self.coda.errore and self.coda.errore != errore_prima:
                log.warning("pagina: %s", self.coda.errore)
            if self.coda.via and self.coda.via != via_prima:
                # 'nativa': come l'interfaccia di YouTube Music (server aggiornato);
                # 'locale': solo nella pagina, il server potrebbe ripristinare la coda.
                log.info("modifiche della coda: via %s", self.coda.via)
            vp = self.vista_playlist
            vp.normalizza_selezione()
            if self.coda.id_corrente != id_prima:       # cambio di brano: la lista lo segue
                vp.segui_brano_corrente()
            vp.setNeedsDisplay_(True)

    @objc.python_method
    def _rileggi_coda(self):
        self._coda_in_volo = False
        self.tickCoda_(None)

    # ── comandi ────────────────────────────────────────────────────────────
    @objc.python_method
    def _js(self, nome, arg=None):
        if self.web is None:
            return
        try:
            codice = core.comando_js(nome, arg)
        except (ValueError, TypeError):
            log.warning("comando rifiutato: %r %r", nome, arg)
            return
        self.web.evaluateJavaScript_completionHandler_(codice, None)
        self._t_invio = 0.0                     # stato aggiornato al prossimo ciclo

    @objc.python_method
    def esegui(self, azione, arg=None):
        try:
            self._esegui(azione, arg)
        except Exception:
            log.exception("azione %s", azione)

    @objc.python_method
    def _esegui(self, azione, arg):
        if azione == "pl":
            self._commuta_playlist()
        elif azione == "ric":
            self._commuta_ricerca()
        elif azione == "lib":
            self._commuta_libreria()
        elif azione == "eject":                     # come "apri file" dei lettori classici
            self.attiva_campo_ricerca()
        elif azione == "seek_rel":
            self._js("seek", self.stato.posizione + float(arg or 0))
        elif azione == "vol_rel":
            self.imposta_volume(self.volume_mostrato() + float(arg or 0), True)
        elif azione == "tempo":
            self.impost.tempo_rimanente = not self.impost.tempo_rimanente
        elif azione == "viz":
            self._imposta_viz(core.prossimo_modo_viz(self.impost.modo_viz))
        elif azione == "riduci":
            self._riduci_lettore()
        elif azione in core.AZIONI_PLAYLIST:
            self._azione_playlist(azione, arg)
        elif azione == "chiudi":
            applicazione().terminate_(None)
        elif azione == "logo":
            self.informazioni_(None)
        elif core.comando_per_azione(azione) is not None:
            self._js(core.comando_per_azione(azione), arg)
        else:
            log.warning("azione sconosciuta: %s", azione)
        self._ridisegna_tutto()

    @objc.python_method
    def _imposta_viz(self, modo):
        self.impost.modo_viz = modo
        self.spettro.azzera()
        self.monitor = core.MonitorViz()
        self.viz_disponibile = True
        self.onda = []

    @objc.python_method
    def posizione_mostrata(self):
        if self._anteprima_seek is not None:
            valore, t = self._anteprima_seek
            if time.monotonic() - t < 1.2:
                return valore
            self._anteprima_seek = None
        return self.stato.posizione

    @objc.python_method
    def volume_mostrato(self):
        if self._anteprima_vol is not None:
            valore, t = self._anteprima_vol
            if time.monotonic() - t < 1.2:
                return valore
            self._anteprima_vol = None
        return self.stato.volume

    @objc.python_method
    def imposta_posizione(self, frazione, definitivo):
        if self.stato.durata <= 0:
            return
        secondi = frazione * self.stato.durata
        self._anteprima_seek = (secondi, time.monotonic())
        self.transitorio = f"POSIZIONE {core.fmt_durata(secondi)} / {core.fmt_durata(self.stato.durata)}"
        self._scadenza_transitorio = time.monotonic() + 1.2
        if definitivo:
            self._js("seek", secondi)

    @objc.python_method
    def imposta_volume(self, valore, definitivo):
        v = int(round(core.limita(valore, 0, 100)))
        ora = time.monotonic()
        self._anteprima_vol = (v, ora)
        self.transitorio = f"VOLUME {v}%"
        self._scadenza_transitorio = ora + 1.5
        if definitivo or ora - self._t_vol >= 0.06:
            self._t_vol = ora
            self._js("volume", v)

    @objc.python_method
    def gestisci_tasto(self, evento):
        if evento.modifierFlags() & (MOD_CMD | MOD_CTRL | MOD_OPT):
            return False
        codice = evento.keyCode()
        if codice in core.FRECCE:
            azione, arg = core.FRECCE[codice]
            self.esegui(azione, arg)
            return True
        carattere = (evento.charactersIgnoringModifiers() or "").lower()
        if carattere in core.TASTI:
            self.esegui(core.TASTI[carattere])
            return True
        return False

    # ── modifica della playlist ───────────────────────────────────────────
    # Ogni modifica (1) aggiorna subito la copia locale, così la lista non
    # "salta"; (2) incrementa la generazione, così una lettura della coda
    # partita prima viene scartata; (3) chiede la stessa operazione alla
    # pagina; (4) rilegge la coda reale. Se la pagina non conferma tutte le
    # operazioni, la rilettura ripristina lo stato vero e lo si segnala.
    @objc.python_method
    def avviso_playlist(self, testo, durata=3.0):
        self._avviso_pl = (str(testo), time.monotonic() + durata)
        if self.vista_playlist is not None:
            self.vista_playlist.setNeedsDisplay_(True)

    @objc.python_method
    def piede_playlist(self, n_selezionati):
        if self._avviso_pl and time.monotonic() <= self._avviso_pl[1]:
            return self._avviso_pl[0]
        return core.piede_playlist(self.coda, n_selezionati)

    @objc.python_method
    def _azione_playlist(self, azione, arg=None):
        vp, n = self.vista_playlist, len(self.coda.brani)
        sel, corrente = vp.selezione, self.coda.indice_corrente
        if azione == "pl_tutto":
            vp.imposta_selezione(range(n), cursore=n - 1 if n else None)
        elif azione == "pl_annulla":
            self.annulla_modifica_coda()
        elif azione == "pl_radio":                      # la coda non si estende più da sola
            self.esegui("ferma_radio")
            self.avviso_playlist("radio fermata: dopo l'ultimo brano la coda non si allunga", 4.0)
            self._gen_coda += 1
            self._rileggi_coda()
        elif not sel and azione != "pl_successivi":
            return
        elif azione == "pl_riproduci":
            self.esegui("indice", sel[0])
        elif azione in ("pl_su", "pl_giu"):
            mosse, nuova = (core.mosse_su if azione == "pl_su" else core.mosse_giu)(sel, n)
            self._sposta(mosse, nuova)
        elif azione in ("pl_varco", "pl_fondo"):
            varco = n if azione == "pl_fondo" else int(arg)
            self._sposta(core.mosse_blocco(n, sel, varco), core.selezione_dopo_blocco(n, sel, varco))
        elif azione == "pl_dopo":                       # riproduci dopo il brano attuale
            if corrente is None:
                self.avviso_playlist("nessun brano in riproduzione")
                return
            altri = tuple(i for i in sel if i != corrente)
            self._sposta(core.mosse_blocco(n, altri, corrente + 1),
                         core.selezione_dopo_blocco(n, altri, corrente + 1))
        elif azione == "pl_rimuovi":
            self._rimuovi(sel)
        elif azione == "pl_successivi":
            if corrente is not None and corrente + 1 < n:
                self._rimuovi(range(corrente + 1, n))
        self._ridisegna_tutto()

    @objc.python_method
    def _modificabile(self):
        if not self.coda.modificabile:
            self.avviso_playlist("coda non modificabile: YouTube Music non è pronto")
        return self.coda.modificabile

    @objc.python_method
    def _sposta(self, mosse, nuova_selezione, annullamento=False):
        if not mosse or not self._modificabile():
            return
        vp = self.vista_playlist
        argomenti = core.argomenti_sposta(self.coda.brani, mosse)
        selezione_prima = vp.selezione
        self.coda.applica_mosse(mosse)
        vp.imposta_selezione(nuova_selezione)
        self._annullabile = None if annullamento else {
            "tipo": "mosse", "dati": core.inverti_mosse(mosse), "selezione": selezione_prima,
            "n": len(self.coda.brani)}
        self._invia_modifica(core.JS_SPOSTA, argomenti, len(mosse), "spostamento")

    @objc.python_method
    def _rimuovi(self, indici):
        if not self._modificabile():
            return
        vp = self.vista_playlist
        scelti = core.selezione_valida(indici, len(self.coda.brani))
        togliere = core.rimovibili(self.coda.brani, scelti)
        if not togliere:
            self.avviso_playlist("il brano in riproduzione resta in coda")
            return
        argomenti = core.argomenti_rimuovi(self.coda.brani, togliere)
        selezione_prima, n_prima = vp.selezione, len(self.coda.brani)
        rimossi = self.coda.rimuovi(togliere)
        vp.imposta_selezione(core.selezione_dopo_rimozione(n_prima, togliere))
        self._annullabile = {"tipo": "rimozione", "dati": rimossi, "selezione": selezione_prima,
                             "n": len(self.coda.brani)}
        self.avviso_playlist(core.testo_rimozione(len(togliere), len(togliere) < len(scelti), self.coda.radio), 4.0)
        self._invia_modifica(core.JS_RIMUOVI, argomenti, len(togliere), "rimozione")

    @objc.python_method
    def puo_annullare(self):
        a = self._annullabile
        return bool(a) and a["n"] == len(self.coda.brani) and self.coda.modificabile

    @objc.python_method
    def annulla_modifica_coda(self):
        if not self.puo_annullare():
            return
        a, self._annullabile = self._annullabile, None
        if a["tipo"] == "mosse":
            self._sposta(a["dati"], a["selezione"], annullamento=True)
        else:
            self.coda.reinserisci(a["dati"])
            self.vista_playlist.imposta_selezione(a["selezione"])
            self.avviso_playlist("rimozione annullata")
            self._invia_modifica(core.JS_RIPRISTINA, {}, len(a["dati"]), "ripristino")

    @objc.python_method
    def _invia_modifica(self, corpo, argomenti, attese, descrizione):
        self._gen_coda += 1

        def esito(risultato, errore):
            fatte = -1 if errore is not None else int(core.finito(risultato, -1))
            if fatte < attese:
                log.warning("%s nella coda: %s operazioni su %s (%s)", descrizione, fatte, attese, errore or "-")
                self.avviso_playlist(f"{descrizione}: YouTube Music non ha confermato · coda riletta", 4.0)
                self._annullabile = None
            self._rileggi_coda()

        self._asincrono(corpo, argomenti, esito)

    @objc.python_method
    def menu_playlist(self):
        m = AppKit.NSMenu.alloc().initWithTitle_("Playlist")
        sep = AppKit.NSMenuItem.separatorItem
        self._voce(m, "Riproduci", "playlistRiproduci:", "\r", 0)
        self._voce(m, "Riproduci dopo il brano attuale", "playlistDopoCorrente:")
        self._voce(m, "Sposta in fondo alla coda", "playlistInFondo:")
        m.addItem_(sep())
        self._voce(m, "Sposta su", "playlistSu:", TASTO_SU, MOD_OPT)
        self._voce(m, "Sposta giù", "playlistGiu:", TASTO_GIU, MOD_OPT)
        m.addItem_(sep())
        self._voce(m, "Rimuovi dalla coda", "playlistRimuovi:", "\x08", 0)
        self._voce(m, "Rimuovi i brani successivi", "playlistRimuoviSuccessivi:")
        self._voce(m, "Ferma la radio (la coda finisce qui)", "playlistFermaRadio:")
        m.addItem_(sep())
        self._voce(m, "Annulla l'ultima modifica", "playlistAnnulla:", "z", MOD_CMD)
        self._voce(m, "Seleziona tutto", "playlistSelezionaTutto:", "a", MOD_CMD)
        return m

    @objc.python_method
    def _voce_playlist_abilitata(self, azione):
        """Abilitazione delle voci del menu della playlist (None: non è sua)."""
        if self.vista_playlist is None or not azione.startswith("playlist"):
            return None
        sel, n, corrente = self.vista_playlist.selezione, len(self.coda.brani), self.coda.indice_corrente
        mod = self.coda.modificabile
        regole = {
            "playlistRiproduci:": bool(sel),
            "playlistDopoCorrente:": mod and corrente is not None and any(i != corrente for i in sel),
            "playlistInFondo:": mod and bool(core.mosse_blocco(n, sel, n)),
            "playlistSu:": mod and bool(core.mosse_su(sel, n)[0]),
            "playlistGiu:": mod and bool(core.mosse_giu(sel, n)[0]),
            "playlistRimuovi:": mod and bool(core.rimovibili(self.coda.brani, sel)),
            "playlistRimuoviSuccessivi:": mod and corrente is not None and corrente + 1 < n,
            "playlistFermaRadio:": mod and self.coda.radio,
            "playlistAnnulla:": self.puo_annullare(),
            "playlistSelezionaTutto:": n > 0,
        }
        return regole.get(azione)

    def playlistRiproduci_(self, mittente):
        self.esegui("pl_riproduci")

    def playlistDopoCorrente_(self, mittente):
        self.esegui("pl_dopo")

    def playlistInFondo_(self, mittente):
        self.esegui("pl_fondo")

    def playlistSu_(self, mittente):
        self.esegui("pl_su")

    def playlistGiu_(self, mittente):
        self.esegui("pl_giu")

    def playlistRimuovi_(self, mittente):
        self.esegui("pl_rimuovi")

    def playlistRimuoviSuccessivi_(self, mittente):
        self.esegui("pl_successivi")

    def playlistAnnulla_(self, mittente):
        self.esegui("pl_annulla")

    def playlistFermaRadio_(self, mittente):
        self.esegui("pl_radio")

    def playlistSelezionaTutto_(self, mittente):
        self.esegui("pl_tutto")

    # ── ricerca e inserimento in coda ──────────────────────────────────────
    @objc.python_method
    def _asincrono(self, corpo, argomenti, gestore):
        """Esegue nella pagina una funzione asincrona e ne attende la promessa
        (callAsyncJavaScript); gli argomenti arrivano come variabili JS."""
        if self.web is None:
            gestore(None, "pagina non disponibile")
            return

        def fatto(risultato, errore):
            try:
                if errore is not None:
                    info = errore.userInfo() or {}
                    messaggio = info.get("WKJavaScriptExceptionMessage") or errore.localizedDescription()
                    gestore(None, str(messaggio))
                else:
                    gestore(risultato, None)
            except Exception:
                log.exception("risposta asincrona della pagina")

        self.web.callAsyncJavaScript_arguments_inFrame_inContentWorld_completionHandler_(
            corpo, argomenti, None, WebKit.WKContentWorld.pageWorld(), fatto)

    @objc.python_method
    def avvia_ricerca(self, testo):
        filtro = self.impost.filtro_ricerca
        argomenti = core.argomenti_ricerca(str(testo or ""), filtro)
        if not argomenti["testo"]:
            return
        self.ultima_ricerca = argomenti["testo"]
        self._n_ricerca += 1
        numero = self._n_ricerca
        self.risultati = []
        self.stato_ricerca = f"ricerca di «{argomenti['testo']}»…"
        self.vista_ricerca.mostra_risultati()

        def esito(risultato, errore):
            if numero != self._n_ricerca:
                return                                  # superata da una ricerca più recente
            if errore:
                self.stato_ricerca = "errore: " + errore
                log.warning("ricerca «%s»: %s", argomenti["testo"], errore)
            else:
                self.risultati = core.analizza_ricerca(str(risultato) if risultato is not None else None, filtro)
                self.stato_ricerca = "" if self.risultati else f"nessun risultato per «{argomenti['testo']}»"
            self.vista_ricerca.mostra_risultati()

        self._asincrono(core.JS_CERCA, argomenti, esito)

    @objc.python_method
    def accoda_risultato(self, indice, modo):
        if indice is None or not 0 <= indice < len(self.risultati):
            return
        r = self.risultati[indice]
        if not r.disponibile:
            self.stato_ricerca = f"«{r.titolo}» non è disponibile"
            self.vista_ricerca.setNeedsDisplay_(True)
            return
        descrizione = {"ora": "in riproduzione", "dopo": "dopo il brano attuale", "fine": "aggiunto in coda"}[modo]
        self.stato_ricerca = f"«{r.titolo}»: invio a YouTube Music…"
        self.vista_ricerca.setNeedsDisplay_(True)

        def esito(risultato, errore):
            if errore:
                self.stato_ricerca = "errore: " + errore
                log.warning("inserimento in coda di «%s»: %s", r.titolo, errore)
            else:
                n = str(risultato or "")
                if n == "navigazione":
                    self.stato_ricerca = f"apertura di «{r.titolo}»…"
                else:
                    self.stato_ricerca = f"«{r.titolo}» {descrizione}" + (f" · {n} brani" if n not in ("", "1") else "")
                self._coda_in_volo = False
                self.tickCoda_(None)
                self._t_invio = 0.0
            self.vista_ricerca.setNeedsDisplay_(True)

        self._asincrono(core.JS_ACCODA, core.argomenti_accoda(r, modo), esito)

    @objc.python_method
    def pulsante_ricerca(self, nome):
        if nome == "chiudi":
            self._commuta_ricerca()
        elif nome == "cerca":
            self.avvia_ricerca(self.campo.stringValue())
        elif nome in core.FILTRI_RICERCA:
            self.impost.filtro_ricerca = nome
            testo = str(self.campo.stringValue() or "") or self.ultima_ricerca
            if testo:
                self.avvia_ricerca(testo)
        elif nome == "libreria":
            self._commuta_libreria()
        self._ridisegna_tutto()

    @objc.python_method
    def menu_risultato(self, indice):
        self._indice_menu = indice
        m = AppKit.NSMenu.alloc().initWithTitle_("Risultato")
        self._voce(m, "Riproduci ora", "menuRiproduciOra:")
        self._voce(m, "Riproduci dopo il brano attuale", "menuRiproduciDopo:")
        self._voce(m, "Aggiungi in fondo alla coda", "menuAccoda:")
        return m

    def menuRiproduciOra_(self, mittente):
        self.accoda_risultato(self._indice_menu, "ora")

    def menuRiproduciDopo_(self, mittente):
        self.accoda_risultato(self._indice_menu, "dopo")

    def menuAccoda_(self, mittente):
        self.accoda_risultato(self._indice_menu, "fine")

    def cercaInvio_(self, mittente):
        self.avvia_ricerca(mittente.stringValue())

    def control_textView_doCommandBySelector_(self, controllo, vista, selettore):
        """Freccia giù dal campo → risultati; Esc → svuota il campo."""
        nome = nome_selettore(selettore)
        if controllo is not self.campo:
            return False
        if nome == "moveDown:" and self.risultati:
            self.fin_ricerca.makeFirstResponder_(self.vista_ricerca)
            self.vista_ricerca.selezione = 0
            self.vista_ricerca.setNeedsDisplay_(True)
            return True
        if nome == "cancelOperation:":
            self.campo.setStringValue_("")
            return True
        return False

    # ── navigazione nella Libreria ─────────────────────────────────────────
    def webView_decidePolicyForNavigationAction_decisionHandler_(self, web, azione, decisione):
        consenti = _costante(WebKit, "WKNavigationActionPolicyAllow", 1)
        try:
            url = azione.request().URL()
            schema = str(url.scheme() or "") if url is not None else ""
            host = str(url.host() or "") if url is not None else ""
            link = azione.navigationType() == _costante(WebKit, "WKNavigationTypeLinkActivated", 0)
            if link and schema in ("http", "https") and not core.host_interno(host):
                AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
                decisione(_costante(WebKit, "WKNavigationActionPolicyCancel", 0))
                return
        except Exception:
            log.exception("politica di navigazione")
        decisione(consenti)

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, web, configurazione, azione, caratteristiche):
        url = azione.request().URL()
        if url is not None:
            if core.host_interno(str(url.host() or "")):
                web.loadRequest_(azione.request())
            else:
                AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
        return None

    def webView_didFinishNavigation_(self, web, navigazione):
        log.info("pagina caricata: %s", web.URL())

    def webViewWebContentProcessDidTerminate_(self, web):
        log.warning("processo della pagina terminato: ricarico")
        web.reload()

    # ── menu ───────────────────────────────────────────────────────────────
    @objc.python_method
    def _voce(self, menu, titolo, azione, tasto="", modificatori=None, tag=0, obiettivo=True):
        v = menu.addItemWithTitle_action_keyEquivalent_(titolo, azione, tasto)
        if modificatori is not None:
            v.setKeyEquivalentModifierMask_(modificatori)
        if tag:
            v.setTag_(tag)
        if obiettivo:
            v.setTarget_(self)
        return v

    @objc.python_method
    def _sottomenu(self, barra, titolo):
        voce = AppKit.NSMenuItem.alloc().init()
        barra.addItem_(voce)
        menu = AppKit.NSMenu.alloc().initWithTitle_(titolo)
        voce.setSubmenu_(menu)
        return menu

    @objc.python_method
    def _costruisci_menu(self):
        barra = AppKit.NSMenu.alloc().init()
        sep = AppKit.NSMenuItem.separatorItem
        m = self._sottomenu(barra, core.NOME_APP)
        self._voce(m, f"Informazioni su {core.NOME_APP}", "informazioni:")
        m.addItem_(sep())
        self._voce(m, f"Nascondi {core.NOME_APP}", "hide:", "h", obiettivo=False)
        self._voce(m, "Nascondi altre", "hideOtherApplications:", "h", MOD_CMD | MOD_OPT, obiettivo=False)
        self._voce(m, "Mostra tutte", "unhideAllApplications:", obiettivo=False)
        m.addItem_(sep())
        self._voce(m, f"Esci da {core.NOME_APP}", "terminate:", "q", obiettivo=False)

        # Indispensabile: senza il menu Modifica, ⌘C/⌘V non funzionano nella Libreria.
        m = self._sottomenu(barra, "Modifica")
        for titolo, sel, k in (("Annulla", "undo:", "z"), ("Ripeti", "redo:", "Z"), (None, None, None),
                               ("Taglia", "cut:", "x"), ("Copia", "copy:", "c"), ("Incolla", "paste:", "v"),
                               ("Seleziona tutto", "selectAll:", "a")):
            if titolo is None:
                m.addItem_(sep())
            else:
                self._voce(m, titolo, sel, k, obiettivo=False)
        m.addItem_(sep())
        self._voce(m, "Cerca in YouTube Music", "cercaBrano:", "f")

        cmd_opt = MOD_CMD | MOD_OPT
        m = self._sottomenu(barra, "Controlli")
        self._voce(m, "Riproduci / Pausa", "comandoRiproduci:", "p", cmd_opt)
        self._voce(m, "Stop", "comandoStop:", ".", MOD_CMD)
        self._voce(m, "Brano successivo", "comandoSuccessivo:", TASTO_DESTRA, cmd_opt)
        self._voce(m, "Brano precedente", "comandoPrecedente:", TASTO_SINISTRA, cmd_opt)
        m.addItem_(sep())
        self._voce(m, "Alza il volume", "comandoAlzaVolume:", TASTO_SU, cmd_opt)
        self._voce(m, "Abbassa il volume", "comandoAbbassaVolume:", TASTO_GIU, cmd_opt)
        m.addItem_(sep())
        self._voce(m, "Mi piace", "comandoMiPiace:", "l", cmd_opt)
        self._voce(m, "Riproduzione casuale", "comandoCasuale:", "s", cmd_opt)
        self._voce(m, "Ripetizione (no / tutto / uno)", "comandoRipeti:", "r", cmd_opt)

        m = self._sottomenu(barra, "Vista")
        self._riempi_menu_vista(m)

        m = self._sottomenu(barra, "Finestra")
        self._voce(m, "Lettore", "mostraLettore:", "1")
        self._voce(m, "Playlist", "commutaPlaylist:", "2", tag=TAG_PLAYLIST)
        self._voce(m, "Ricerca", "commutaRicerca:", "3", tag=TAG_RICERCA)
        self._voce(m, "Libreria (pagina di YouTube Music)", "commutaLibreria:", "4", tag=TAG_LIBRERIA)
        m.addItem_(sep())
        self._voce(m, "Riduci a icona", "riduciFinestra:", "m")
        self._voce(m, "Ricarica YouTube Music", "ricaricaServizio:", "r")
        applicazione().setWindowsMenu_(m)

        m = self._sottomenu(barra, "Aiuto")
        self._voce(m, "Pagina del progetto", "apriProgetto:")
        self._voce(m, "Mostra il registro", "apriRegistro:")
        applicazione().setMainMenu_(barra)

    @objc.python_method
    def _riempi_menu_vista(self, m):
        self._voce(m, "Analizzatore di spettro", "vizSpettro:", tag=TAG_SPETTRO)
        self._voce(m, "Oscilloscopio", "vizOscilloscopio:", tag=TAG_OSCILLOSCOPIO)
        self._voce(m, "Nessuna visualizzazione", "vizSpenta:", tag=TAG_VIZ_SPENTA)
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        self._voce(m, "Tempo rimanente", "commutaTempo:", tag=TAG_RIMANENTE)
        self._voce(m, "Sempre in primo piano", "commutaPrimoPiano:", "t", MOD_CMD | MOD_OPT, tag=TAG_PRIMO_PIANO)
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        self._voce(m, "Dimensione piccola (1×)", "scalaPiccola:", tag=TAG_SCALA[1.0])
        self._voce(m, "Dimensione classica (1,5×)", "scalaClassica:", tag=TAG_SCALA[1.5])
        self._voce(m, "Dimensione doppia (2×)", "scalaDoppia:", tag=TAG_SCALA[2.0])

    @objc.python_method
    def menu_contestuale(self):
        m = AppKit.NSMenu.alloc().initWithTitle_(core.NOME_APP)
        self._voce(m, "Ricerca", "commutaRicerca:", tag=TAG_RICERCA)
        self._voce(m, "Playlist", "commutaPlaylist:", tag=TAG_PLAYLIST)
        self._voce(m, "Libreria (pagina di YouTube Music)", "commutaLibreria:", tag=TAG_LIBRERIA)
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        self._riempi_menu_vista(m)
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        self._voce(m, f"Informazioni su {core.NOME_APP}", "informazioni:")
        self._voce(m, f"Esci da {core.NOME_APP}", "terminate:", obiettivo=False)
        return m

    def validateMenuItem_(self, voce):
        stati = {
            TAG_PRIMO_PIANO: self.impost.primo_piano,
            TAG_SPETTRO: self.impost.modo_viz == "spettro",
            TAG_OSCILLOSCOPIO: self.impost.modo_viz == "oscilloscopio",
            TAG_VIZ_SPENTA: self.impost.modo_viz == "spento",
            TAG_RIMANENTE: self.impost.tempo_rimanente,
            TAG_PLAYLIST: self.playlist_visibile(),
            TAG_LIBRERIA: self.libreria_visibile(),
            TAG_RICERCA: self.ricerca_visibile(),
        }
        stati.update({tag: self.scala == s for s, tag in TAG_SCALA.items()})
        if voce.tag() in stati:
            voce.setState_(1 if stati[voce.tag()] else 0)
        abilitata = self._voce_playlist_abilitata(nome_selettore(voce.action()))
        return True if abilitata is None else bool(abilitata)

    # ── azioni dei menu (selettori Objective-C) ───────────────────────────
    def informazioni_(self, mittente):
        crediti = Foundation.NSAttributedString.alloc().initWithString_(
            "Lettore per YouTube Music con interfaccia a deck.\n"
            "Derivato da youtube-music-macos di jem (0xjemm), MIT.\n"
            "Non affiliato a Google né a YouTube.")
        applicazione().orderFrontStandardAboutPanelWithOptions_({
            "ApplicationName": core.NOME_APP,
            "ApplicationVersion": core.__version__,
            "Credits": crediti,
            "Copyright": "© 2026 B. Raucci — Licenza MIT",
        })

    def mostraLettore_(self, mittente):
        self._mostra_lettore()

    def commutaPlaylist_(self, mittente):
        self.esegui("pl")

    def commutaLibreria_(self, mittente):
        self.esegui("lib")

    def cercaBrano_(self, mittente):
        self.esegui("eject")

    def commutaRicerca_(self, mittente):
        self.esegui("ric")

    def scalaPiccola_(self, mittente):
        self.applica_scala(1.0)

    def scalaClassica_(self, mittente):
        self.applica_scala(1.5)

    def scalaDoppia_(self, mittente):
        self.applica_scala(2.0)

    def riduciFinestra_(self, mittente):
        """⌘M: dalle finestre del deck si riduce il lettore (che porta con sé
        playlist e ricerca); la Libreria si riduce da sola."""
        finestra = applicazione().keyWindow()
        if finestra is not None and finestra == self.fin_libreria:
            finestra.miniaturize_(None)
        else:
            self._riduci_lettore()

    def ricaricaServizio_(self, mittente):
        if self.web is not None:
            self.web.reload()

    def commutaPrimoPiano_(self, mittente):
        self.impost.primo_piano = not self.impost.primo_piano
        self._applica_primo_piano()

    def commutaTempo_(self, mittente):
        self.esegui("tempo")

    def vizSpettro_(self, mittente):
        self._imposta_viz("spettro")

    def vizOscilloscopio_(self, mittente):
        self._imposta_viz("oscilloscopio")

    def vizSpenta_(self, mittente):
        self._imposta_viz("spento")

    def comandoRiproduci_(self, mittente):
        self.esegui("toggle")

    def comandoStop_(self, mittente):
        self.esegui("stop")

    def comandoSuccessivo_(self, mittente):
        self.esegui("next")

    def comandoPrecedente_(self, mittente):
        self.esegui("prev")

    def comandoAlzaVolume_(self, mittente):
        self.esegui("vol_rel", 5.0)

    def comandoAbbassaVolume_(self, mittente):
        self.esegui("vol_rel", -5.0)

    def comandoMiPiace_(self, mittente):
        self.esegui("like")

    def comandoCasuale_(self, mittente):
        self.esegui("shuffle")

    def comandoRipeti_(self, mittente):
        self.esegui("repeat")

    def apriProgetto_(self, mittente):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_(core.URL_PROGETTO))

    def apriRegistro_(self, mittente):
        percorso = str(percorso_registro())
        AppKit.NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(percorso, "")


# ═══════════════════════════════════════════════════════════════════════════
# Avvio
# ═══════════════════════════════════════════════════════════════════════════

def percorso_registro():
    return Path.home() / "Library" / "Logs" / f"{core.NOME_APP}.log"


def configura_registro():
    p = percorso_registro()
    p.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(p, encoding="utf-8"), logging.StreamHandler(sys.stderr)],
    )

    def eccezione_non_gestita(tipo, valore, traccia):
        log.critical("eccezione non gestita", exc_info=(tipo, valore, traccia))
    sys.excepthook = eccezione_non_gestita


_controllore = None     # riferimento forte: il delegato di NSApplication è debole


def main():
    global _controllore
    configura_registro()
    app = applicazione()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    _controllore = ControlloreApp.alloc().init()
    app.setDelegate_(_controllore)
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
