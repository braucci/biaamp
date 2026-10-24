"""
banco.py — Banco di prova di app.py senza Mac e senza display.

Carica moduli AppKit / Foundation / WebKit / MediaPlayer SIMULATI (cartella
finto/), esegue il vero codice di app.py (controllore, timer, eventi, disegno),
verifica il comportamento e rasterizza con Pillow le primitive di disegno
registrate, producendo anteprime PNG delle finestre.

Intercetta gli errori di logica dell'interfaccia, non quelli di integrazione
con il sistema: il collaudo finale resta quello su un Mac.

Uso:   python3 test/banco.py [cartella del progetto] [cartella delle anteprime]
Serve: Pillow; font DejaVu Sans Mono (Linux) oppure Menlo (macOS).

© 2026 B. Raucci — Licenza MIT
"""
import base64
import json
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace as NS

QUI = Path(__file__).resolve().parent
PROGETTO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else QUI.parent
USCITA = Path(sys.argv[2]) if len(sys.argv) > 2 else QUI / "anteprime"
sys.path[:0] = [str(QUI / "finto"), str(PROGETTO)]
USCITA.mkdir(parents=True, exist_ok=True)

import core                                    # noqa: E402
core.percorso_impostazioni = lambda: USCITA / "imp.json"
import app                                     # noqa: E402
import AppKit, WebKit, MediaPlayer             # noqa: E402
from Foundation import TELA                    # noqa: E402
from PIL import Image, ImageDraw, ImageFont    # noqa: E402

# ── orologio controllato ────────────────────────────────────────────────────
class Orologio:
    t = 1000.0
app.time.monotonic = lambda: Orologio.t

# ── pagina simulata ─────────────────────────────────────────────────────────
random.seed(7)
PAGINA = {"st": 1, "cur": 83.0, "dur": 412.0, "vol": 72, "rip": "ALL", "shuf": False, "like": "LIKE",
          "titolo": "Shine On You Crazy Diamond (Pts. 1-5)",
          "byline": "Pink Floyd • Wish You Were Here • 1975",
          "cop": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"}
CODA = [("Shine On You Crazy Diamond (Pts. 1-5)", "Pink Floyd", "13:32"), ("Welcome to the Machine", "Pink Floyd", "7:30"),
        ("Have a Cigar", "Pink Floyd", "5:07"), ("Wish You Were Here", "Pink Floyd", "5:34"),
        ("Shine On You Crazy Diamond (Pts. 6-9)", "Pink Floyd", "12:31"), ("Time", "Pink Floyd", "6:53"),
        ("Us and Them", "Pink Floyd", "7:49"), ("Kashmir", "Led Zeppelin", "8:28"),
        ("Roundabout", "Yes", "8:33"), ("Starless", "King Crimson", "12:18"),
        ("Firth of Fifth", "Genesis", "9:36"), ("Echoes", "Pink Floyd", "23:31")]
# Coda viva della pagina: le modifiche chieste dall'app la cambiano davvero,
# con le stesse regole del ponte (indice atteso o videoId, brano corrente
# non rimovibile, ripristino dell'ultima rimozione).
VIVA = [{"t": t, "a": a, "d": d, "id": f"v{i}"} for i, (t, a, d) in enumerate(CODA)]
corrente = {"id": "v1"}
MODIFICA = {"rifiuta": False, "m": 1, "cestino": [], "radio": True}
comandi = []


def spettro_finto(t):
    """Spettro 'musicale': pendenza rosa + armoniche che si muovono nel tempo."""
    out = bytearray(1024)
    for k in range(1, 1024):
        f = k * 48000 / 2048
        base = 215 - 26 * math.log2(max(f, 30) / 60) + random.uniform(-14, 6)
        for f0, a in ((110 * (1 + 0.1 * math.sin(t)), 30), (880, 22 + 15 * math.sin(3 * t)), (3300, 18)):
            base += a * math.exp(-((math.log2(f / f0)) ** 2) / 0.02)
        out[k] = int(min(255, max(0, base)))
    return bytes(out)


def pagina(js):
    if ".stato(" in js:
        modo = js.split('stato("')[1][0] if 'stato("' in js and js.split('stato("')[1][0] in "fw" else ""
        d = dict(PAGINA, pronto=1, fs=48000, nfft=2048, ad=DIAGNOSI["testo"])
        if modo == "f" and PAGINA["st"] == 1 and DIAGNOSI["dati"]:
            d.update(vk="f", viz=base64.b64encode(spettro_finto(Orologio.t)).decode())
        elif modo == "w" and PAGINA["st"] == 1 and DIAGNOSI["dati"]:
            onda = bytes(int(128 + 90 * math.sin(2 * math.pi * i / 97) + 25 * math.sin(2 * math.pi * i / 23))
                         for i in range(2048))
            d.update(vk="w", viz=base64.b64encode(onda).decode())
        return json.dumps(d)
    if ".coda(" in js:
        voci = [dict(v_, s=v_["id"] == corrente["id"]) for v_ in VIVA]
        firma = f"{len(voci)}-" + "".join(v_["id"] for v_ in VIVA) + "-" + corrente["id"] + str(MODIFICA["m"]) \
            + str(MODIFICA["radio"])
        if f'coda("{firma}")' in js:
            return json.dumps({"firma": firma, "uguale": 1})
        return json.dumps({"firma": firma, "voci": voci, "m": MODIFICA["m"], "inf": int(MODIFICA["radio"]),
                           "via": "nativa"})
    if ".cmd(" in js:
        comandi.append(js.split(".cmd(")[1].split(")")[0])
        if '"ferma_radio"' in js:
            MODIFICA["radio"] = False
        return True
    return None


WebKit.WKWebView.pagina = staticmethod(pagina)
DIAGNOSI = {"testo": "audio/mp4 · seg 12 · dec 12 · diretto", "dati": True}

# ── rete simulata di YouTube Music (ricerca e coda) ─────────────────────────
RICHIESTE = []


def _run(t, browse=None):
    return {"text": t, **({"navigationEndpoint": {"browseEndpoint": {"browseId": browse}}} if browse else {})}


def _riga(titolo, info, video=None, lista=None, browse=None, grigia=False):
    r = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": titolo}]}}},
                         {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": info}}}]}
    gioca = {"watchEndpoint": {"videoId": video}} if video else {"watchPlaylistEndpoint": {"playlistId": lista}}
    r["overlay"] = {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {"playNavigationEndpoint": gioca}}}}
    if browse:
        r["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse}}
    if grigia:
        r["musicItemRendererDisplayPolicy"] = "MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT"
    return {"musicResponsiveListItemRenderer": r}


SEP = {"text": " • "}
BRANI_TROVATI = [("Time", "The Dark Side of the Moon", "6:53"), ("Money", "The Dark Side of the Moon", "6:22"),
                 ("Comfortably Numb", "The Wall", "6:23"), ("Echoes", "Meddle", "23:31"),
                 ("Wish You Were Here", "Wish You Were Here", "5:34"), ("Hey You", "The Wall", "4:40"),
                 ("High Hopes", "The Division Bell", "8:31"), ("Us and Them", "The Dark Side of the Moon", "7:49"),
                 ("Dogs", "Animals", "17:04"), ("Bootleg (non disponibile)", "Live", "4:10")]


def risposta_ricerca(testo):
    righe = [_riga(f"{t} [{testo}]" if testo.startswith("prova") else t,
                   [_run("Pink Floyd", "UCpf"), SEP, _run(a, "MPREb_" + a[:4]), SEP, _run(d)],
                   video=f"id{i}", grigia=t.startswith("Bootleg"))
             for i, (t, a, d) in enumerate(BRANI_TROVATI)]
    righe += [_riga("Wish You Were Here", [_run("Album"), SEP, _run("Pink Floyd", "UCpf"), SEP, _run("1975")],
                    lista="OLAK5uy_wywh", browse="MPREb_wywh"),
              _riga("Meddle", [_run("Album"), SEP, _run("Pink Floyd", "UCpf"), SEP, _run("1971")],
                    lista="OLAK5uy_meddle", browse="MPREb_meddle"),
              _riga("Prog Classics", [_run("Playlist"), SEP, _run("Biagio"), SEP, _run("50 brani")],
                    lista="PLprog", browse="VLPLprog")]
    return json.dumps({"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": righe}}]}}}}]}}})


def _risolvi(indice, id_):
    if 0 <= indice < len(VIVA) and VIVA[indice]["id"] == id_:
        return indice
    trovati = [i for i, v_ in enumerate(VIVA) if v_["id"] == id_]
    return min(trovati, key=lambda i: abs(i - indice)) if trovati else -1


def modifica_coda(corpo, argomenti):
    """Le operazioni di ponte.js sulla coda viva; restituisce quante riescono."""
    if MODIFICA["rifiuta"]:
        return 0
    if corpo == core.JS_SPOSTA:
        fatte = 0
        for da, a, id_ in argomenti["mosse"]:
            da = _risolvi(da, id_)
            if da < 0:
                break
            VIVA.insert(a, VIVA.pop(da))
            fatte += 1
        return fatte
    if corpo == core.JS_RIMUOVI:
        tolti = []
        for n_, id_ in sorted(argomenti["voci"], key=lambda x: -x[0]):
            i = _risolvi(n_, id_)
            if i >= 0 and VIVA[i]["id"] != corrente["id"]:
                tolti.append((i, VIVA.pop(i)))
        MODIFICA["cestino"] = tolti or MODIFICA["cestino"]
        if tolti:                                            # come ponte.js: via il gettone di continuazione
            MODIFICA["radio_sospesa"], MODIFICA["radio"] = MODIFICA["radio"], False
        return len(tolti)
    if corpo == core.JS_RIPRISTINA:
        voci, MODIFICA["cestino"] = sorted(MODIFICA["cestino"], key=lambda x: x[0]), []
        for i, v_ in voci:
            VIVA.insert(i, v_)
        MODIFICA["radio"] = MODIFICA.pop("radio_sospesa", False)
        return len(voci)


def asincrona(corpo, argomenti):
    RICHIESTE.append((corpo, argomenti))
    if corpo in (core.JS_SPOSTA, core.JS_RIMUOVI, core.JS_RIPRISTINA):
        return modifica_coda(corpo, argomenti), None
    if corpo == core.JS_CERCA:
        if argomenti["testo"] == "errore":
            return None, "YouTube Music non è ancora pronto"
        return risposta_ricerca(argomenti["testo"]), None
    if corpo == core.JS_ACCODA:
        return ("1" if "videoId" in argomenti["bersaglio"] else "12"), None
    return None, "corpo sconosciuto"


WebKit.WKWebView.asincrona = staticmethod(asincrona)

# ── rasterizzazione ─────────────────────────────────────────────────────────
DPR = 2.0
_CARATTERI = {}


def carattere(grassetto, dim):
    """DejaVu Sans Mono (Linux) o Menlo (macOS): stesse metriche, 0,602 em."""
    chiave = (grassetto, dim)
    if chiave not in _CARATTERI:
        candidati = [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono" + ("-Bold" if grassetto else "") + ".ttf", 0),
            ("/System/Library/Fonts/Menlo.ttc", 1 if grassetto else 0),
        ]
        for percorso, indice in candidati:
            if Path(percorso).exists():
                _CARATTERI[chiave] = ImageFont.truetype(percorso, dim, index=indice)
                break
        else:
            _CARATTERI[chiave] = ImageFont.load_default()
    return _CARATTERI[chiave]


def c8(col):
    r, g, b, a = col.rgba
    return (int(r * 255), int(g * 255), int(b * 255), int(round(a * 255)))


def interseca(r, clip):
    if not clip:
        return r
    return (max(r[0], clip[0]), max(r[1], clip[1]), min(r[2], clip[2]), min(r[3], clip[3]))


def px(v):
    return v * DPR


def rasterizza(ops, w_pt, h_pt):
    img = Image.new("RGBA", (int(w_pt * DPR), int(h_pt * DPR)), (0, 0, 0, 255))
    for op in ops:
        livello = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(livello)
        tipo = op[0]
        maschera_clip = None
        if tipo == "rett":
            _, r, col, clip = op
            r = interseca(r, clip)
            if r[2] > r[0] and r[3] > r[1]:
                d.rectangle([px(r[0]), px(r[1]), px(r[2]) - 1, px(r[3]) - 1], fill=c8(col))
        elif tipo == "gradiente":
            _, r, colori, ang, clip = op
            ri = interseca(r, clip)
            if ri[2] <= ri[0] or ri[3] <= ri[1]:
                continue
            x0, y0, x1, y1 = (int(px(v)) for v in r)
            w, h = max(1, x1 - x0), max(1, y1 - y0)
            n = len(colori)
            grad = Image.new("RGBA", (w, h))
            gd = ImageDraw.Draw(grad)
            lung = h if ang == 90 else w
            for i in range(lung):
                f = i / max(1, lung - 1) * (n - 1)
                k = min(int(f), n - 2) if n > 1 else 0
                t = f - k
                a, b = c8(colori[k]), c8(colori[min(k + 1, n - 1)])
                c = tuple(int(a[j] + (b[j] - a[j]) * t) for j in range(4))
                if ang == 90:
                    gd.line([(0, i), (w, i)], fill=c)
                else:
                    gd.line([(i, 0), (i, h)], fill=c)
            livello.paste(grad, (x0, y0))
            maschera_clip = [int(px(v)) for v in ri]
        elif tipo == "poligono":
            _, punti, col, clip = op
            d.polygon([(px(x), px(y)) for x, y in punti], fill=c8(col))
        elif tipo == "linee":
            _, tratti, col, sp, clip = op
            for t in tratti:
                d.line([(px(x), px(y)) for x, y in t], fill=c8(col), width=max(1, round(px(sp))))
        elif tipo == "rrett":
            _, (r, rad), col = op
            d.rounded_rectangle([px(v) for v in r], radius=px(rad), fill=c8(col))
        elif tipo == "rrett_bordo":
            _, (r, rad), col, sp = op
            d.rounded_rectangle([px(v) for v in r], radius=px(rad), outline=c8(col), width=max(1, round(px(sp))))
        elif tipo == "testo":
            _, s, (x, y), dim, grassetto, col, kern, clip = op
            f = carattere(grassetto, max(1, round(px(dim))))
            xx = px(x)
            if kern:
                for ch in s:
                    d.text((xx, px(y) + px(dim) * 0.12), ch, font=f, fill=c8(col))
                    xx += f.getlength(ch) + px(kern)
            else:
                d.text((xx, px(y) + px(dim) * 0.12), s, font=f, fill=c8(col))
        if maschera_clip:
            m = Image.new("L", img.size, 0)
            ImageDraw.Draw(m).rectangle([maschera_clip[0], maschera_clip[1], maschera_clip[2] - 1, maschera_clip[3] - 1], fill=255)
            livello.putalpha(Image.composite(livello.getchannel("A"), Image.new("L", img.size, 0), m))
        img = Image.alpha_composite(img, livello)
    return img


def disegna(vista, nome):
    TELA.azzera()
    vista.drawRect_(None)
    c = vista._cornice
    img = rasterizza(TELA.ops, c.w, c.h)
    img.save(USCITA / f"{nome}.png")
    return img, len(TELA.ops)


def evento(x, y, clic=1, tasto=None, codice=0, mod=0, dy=0.0, preciso=False):
    """Evento simulato; (x, y) in unità skin, convertite con la scala corrente."""
    s = ctrl.scala
    return NS(locationInWindow=lambda: (x * s, y * s), clickCount=lambda: clic,
              charactersIgnoringModifiers=lambda: tasto or "", keyCode=lambda: codice,
              modifierFlags=lambda: mod, scrollingDeltaY=lambda: dy, hasPreciseScrollingDeltas=lambda: preciso)


def centro(nome, layout=None):
    x, y, w, h = (layout or core.LAYOUT_PRINCIPALE)[nome]
    return x + w / 2, y + h / 2


def clic(vista, nome, layout=None, mod=0):
    vista.mouseDown_(evento(*centro(nome, layout), mod=mod))
    vista.mouseUp_(evento(*centro(nome, layout), mod=mod))


def quasi(a, b):
    return abs(a - b) < 1e-6


def tick(n, dt=1 / 30):
    for _ in range(n):
        Orologio.t += dt
        if PAGINA["st"] == 1:
            PAGINA["cur"] += dt
        ctrl.tickVeloce_(None)


verifiche = 0


def ok(cond, msg):
    global verifiche
    assert cond, msg
    verifiche += 1
    print("  ✓", msg)


# ═════════════════════════════════════════════════════════════════════════════
print("▸ avvio del controllore")
(USCITA / "imp.json").unlink(missing_ok=True)
ctrl = app.ControlloreApp.alloc().init()
ctrl.applicationDidFinishLaunching_(None)
ok(ctrl.web.caricato == core.URL_SERVIZIO, "Libreria carica music.youtube.com")
script = ctrl.web.cfg.userContentController().script
ok([("window.__biaPresa = {" in s.sorgente, "window.__bia = {" in s.sorgente, s.momento) for s in script]
   == [(True, False, 0), (False, True, 1)], "presa.js all'inizio della pagina, ponte.js alla fine")
ok(ctrl.np is not None, "Now Playing inizializzato")
ok(len(AppKit.NSApplication.sharedApplication().__dict__) >= 0, "menu costruiti senza errori")
ok(all(f.isVisible() for f in (ctrl.fin_principale, ctrl.fin_playlist, ctrl.fin_ricerca, ctrl.fin_libreria)),
   "quattro finestre visibili")
fp, fl, fr0 = ctrl.fin_principale.frame(), ctrl.fin_playlist.frame(), ctrl.fin_ricerca.frame()
ok(ctrl.scala == 1.5 and quasi(fp.w, 275 * 1.5) and quasi(fp.h, 116 * 1.5), "scala classica 1,5× all'avvio")
ok(abs(fl.y + fl.h - fp.y) < 1e-6 and fl.x == fp.x, "playlist agganciata sotto il lettore")
ok(quasi(fr0.x, fp.x + fp.w) and quasi(fr0.y + fr0.h, fp.y + fp.h), "ricerca agganciata a destra, bordi superiori allineati")
ok(quasi(fr0.h, fp.h + fl.h), "ricerca alta quanto lettore + playlist")

print("▸ interrogazioni e animazione (3 s simulati)")
ctrl.tickCoda_(None)
tick(90)
ok(ctrl.stato.titolo.startswith("Shine On") and ctrl.stato.album == "Wish You Were Here", "stato letto dalla pagina")
ok(len(ctrl.coda.brani) == len(CODA) and ctrl.coda.indice_corrente == 1 and ctrl.coda.modificabile, "coda letta (modificabile)")
ok(ctrl.coda.radio and ctrl.piede_playlist(1).endswith("· radio"), "coda radio segnalata nel piede")
ok(max(ctrl.spettro.livelli) > 0.3 and ctrl.viz_disponibile, "spettro animato")
cc = MediaPlayer.MPNowPlayingInfoCenter.defaultCenter()
ok(cc.info["title"].startswith("Shine On") and cc.stato == 1 and "artwork" in cc.info, "Centro di Controllo con copertina")
pubbl = cc.pubblicazioni
tick(90)
ok(cc.pubblicazioni == pubbl, "nessuna ripubblicazione superflua (estrapolazione valida)")
PAGINA["cur"] += 60.0
tick(2)
ok(cc.pubblicazioni == pubbl + 1, "salto di posizione → ripubblicazione")
PAGINA["cur"] = 83.0
tick(2)

img, n = disegna(ctrl.vista_principale, "lettore")
ok(n > 200, f"lettore disegnato ({n} primitive)")
img_pl, n = disegna(ctrl.vista_playlist, "playlist")
ok(n > 30, f"playlist disegnata ({n} primitive)")

print("▸ eventi del mouse e della tastiera")
v = ctrl.vista_principale
for nome, atteso in (("play", '"play"'), ("next", '"next"'), ("stop", '"stop"'), ("shuffle", '"shuffle"'),
                     ("repeat", '"repeat"'), ("like", '"like"'), ("prev", '"prev"'), ("pause", '"toggle"')):
    comandi.clear()
    v.mouseDown_(evento(*centro(nome)))
    v.mouseUp_(evento(*centro(nome)))
    ok(comandi and comandi[-1].startswith(atteso), f"pulsante {nome} → {comandi[-1] if comandi else '∅'}")
comandi.clear()
x, y, w, h = core.LAYOUT_PRINCIPALE["play"]
v.mouseDown_(evento(x + 2, y + 2))
v.mouseDragged_(evento(x + 60, y + 2))
v.mouseUp_(evento(x + 60, y + 2))
ok(not comandi, "rilascio fuori dal pulsante: nessuna azione")
sx, sy, sw, sh = core.LAYOUT_PRINCIPALE["seek"]
v.mouseDown_(evento(sx + 20, sy + 4))
v.mouseDragged_(evento(sx + 1 + (sw - 2) * 0.75, sy + 4))
ok(not any(c.startswith('"seek"') for c in comandi), "trascinamento del cursore: solo anteprima")
v.mouseUp_(evento(sx + 1 + (sw - 2) * 0.75, sy + 4))
ok(comandi[-1] == '"seek", 309.0', f"seek al 75 % di 412 s → {comandi[-1]}")
comandi.clear()
v.scrollWheel_(evento(150, 50, dy=1.0))
ok(comandi[-1] == '"volume", 74', f"rotellina → {comandi[-1]}")
ok(ctrl.transitorio == "VOLUME 74%", "messaggio transitorio del volume")
comandi.clear()
atteso = json.dumps(ctrl.stato.posizione + 5.0)
v.keyDown_(evento(0, 0, tasto="b"))
v.keyDown_(evento(0, 0, codice=124))
ok(comandi == ['"next", null', f'"seek", {atteso}'], f"tasti B e → : {comandi}")
comandi.clear()
v.keyDown_(evento(0, 0, tasto="b", mod=app.MOD_CMD))
ok(not comandi, "⌘B non intercettato dal deck")
modo0 = ctrl.impost.modo_viz
v.mouseDown_(evento(*centro("viz")))
ok(ctrl.impost.modo_viz == "oscilloscopio" and modo0 == "spettro", "clic sulla visualizzazione: spettro → oscilloscopio")
v.mouseDown_(evento(*centro("tempo")))
ok(ctrl.impost.tempo_rimanente, "clic sul tempo: tempo rimanente")
tick(30)
disegna(v, "lettore_oscilloscopio_rimanente")
ctrl.impost.tempo_rimanente = False
ctrl._imposta_viz("spettro")
v.mouseDown_(evento(*centro("pl")))
v.mouseUp_(evento(*centro("pl")))
ok(not ctrl.fin_playlist.isVisible(), "PL nasconde la playlist")
v.mouseDown_(evento(*centro("pl")))
v.mouseUp_(evento(*centro("pl")))
ok(ctrl.fin_playlist.isVisible(), "PL la rimostra")
v.mouseDown_(evento(*centro("menu")))
ok(AppKit.NSMenu.aperti and len(AppKit.NSMenu.aperti[-1].voci) > 6, "menu contestuale aperto")
for voce in AppKit.NSMenu.aperti[-1].voci:
    ctrl.validateMenuItem_(voce)
ok([vv._stato for vv in AppKit.NSMenu.aperti[-1].voci if vv.tag() == app.TAG_SPETTRO] == [1], "spunta sulla visualizzazione attiva")

print("▸ spostamento e aggancio delle finestre")
AppKit.NSEvent.posizione_mouse = NS(x=100.0, y=500.0)
v.mouseDown_(evento(150, 106))                       # riga di stato: area libera
AppKit.NSEvent.posizione_mouse = NS(x=160.0, y=470.0)
v.mouseDragged_(evento(150, 106))
fp2, fl2, fr2 = ctrl.fin_principale.frame(), ctrl.fin_playlist.frame(), ctrl.fin_ricerca.frame()
ok(fp2.x == fp.x + 60 and fp2.y == fp.y - 30, "lettore spostato")
ok(fl2.x == fp2.x and abs(fl2.y + fl2.h - fp2.y) < 1e-6, "playlist trascinata insieme")
ok(quasi(fr2.x, fp2.x + fp2.w) and quasi(fr2.y + fr2.h, fp2.y + fp2.h), "ricerca trascinata insieme")
v.mouseUp_(evento(150, 106))
vp = ctrl.vista_playlist
AppKit.NSEvent.posizione_mouse = NS(x=0.0, y=0.0)
vp.mouseDown_(evento(100, 140))                      # piede della playlist
AppKit.NSEvent.posizione_mouse = NS(x=200.0, y=-200.0)
vp.mouseDragged_(evento(100, 140))
ok(not ctrl.impost.playlist_agganciata, "playlist staccata")
AppKit.NSEvent.posizione_mouse = NS(x=6.0, y=-4.0)
vp.mouseDragged_(evento(100, 140))
ok(ctrl.impost.playlist_agganciata, "playlist riagganciata per magnetismo")
vp.mouseUp_(evento(100, 140))

print("▸ playlist")
comandi.clear()
lx, ly, _, _ = core.layout_playlist(vp.altezza())["lista"]
vp.mouseDown_(evento(lx + 30, ly + 1 + 3 * core.ALTEZZA_RIGA + 4, clic=2))
ok(comandi[-1] == '"indice", 3', f"doppio clic sulla 4ª riga → {comandi[-1]}")
vp.keyDown_(evento(0, 0, codice=125))
ok(vp.selezione == (4,), "freccia giù sposta la selezione")
vp.scrollWheel_(evento(0, 0, dy=-3.0))
ok(vp.prima_riga == 3, f"rotellina scorre la lista ({vp.prima_riga})")
corrente["id"] = "v10"
ctrl._coda_in_volo = False
ctrl.tickCoda_(None)
n_vis = core.righe_visibili(vp.altezza())
ok(vp.prima_riga <= 10 < vp.prima_riga + n_vis, "la lista segue il brano corrente")
disegna(vp, "playlist_scorrimento")

print("▸ playlist modificabile")
ids_pagina = lambda: [v_["id"] for v_ in VIVA]                        # noqa: E731
ids_locali = lambda: [b_.video_id for b_ in ctrl.coda.brani]          # noqa: E731
ORIGINE = [f"v{i}" for i in range(len(CODA))]


def rileggi():
    ctrl._coda_in_volo = False
    ctrl.tickCoda_(None)


def riga_pl(i):
    return lx + 30, ly + 1 + (i - vp.prima_riga) * core.ALTEZZA_RIGA + 4


corrente["id"] = "v1"
rileggi()
vp.prima_riga = 0
lx, ly, lw, lh = core.layout_playlist(vp.altezza())["lista"]
ok(n_vis >= 8, f"righe visibili sufficienti per le prove ({n_vis})")
vp.mouseDown_(evento(*riga_pl(2))); vp.mouseUp_(evento(*riga_pl(2)))
vp.mouseDown_(evento(*riga_pl(4), mod=app.MOD_SHIFT)); vp.mouseUp_(evento(*riga_pl(4)))
ok(vp.selezione == (2, 3, 4), "⇧-clic: intervallo 3–5")
vp.mouseDown_(evento(*riga_pl(3), mod=app.MOD_CMD)); vp.mouseUp_(evento(*riga_pl(3)))
ok(vp.selezione == (2, 4), "⌘-clic toglie una riga")
vp.mouseDown_(evento(*riga_pl(3), mod=app.MOD_CMD)); vp.mouseUp_(evento(*riga_pl(3)))
ok(vp.selezione == (2, 3, 4) and ctrl.piede_playlist(3).endswith("3 selezionati"), "⌘-clic la rimette; piede con il conteggio")
n_richieste = len(RICHIESTE)
vp.mouseDown_(evento(*riga_pl(3)))                  # trascinamento della selezione verso il varco 7
vp.mouseDragged_(evento(riga_pl(3)[0], riga_pl(3)[1] + 1))
ok(vp.varco is None and vp.selezione == (2, 3, 4), "sotto la soglia: ancora un clic, selezione intatta")
vp.mouseDragged_(evento(lx + 30, ly + 1 + 7 * core.ALTEZZA_RIGA + 2))
ok(vp.varco == 7, f"varco durante il trascinamento: {vp.varco}")
img_tr, _ = disegna(vp, "playlist_trascinamento")
vp.mouseUp_(evento(lx + 30, ly + 1 + 7 * core.ALTEZZA_RIGA + 2))
atteso = core.applica_mosse(ORIGINE, core.mosse_blocco(12, (2, 3, 4), 7))
ok(RICHIESTE[-1][0] == core.JS_SPOSTA and len(RICHIESTE) == n_richieste + 1, "rilascio: spostamento inviato alla pagina")
ok(ids_pagina() == atteso == ids_locali() and atteso[4:7] == ["v2", "v3", "v4"], "blocco 3–5 spostato prima della riga 8")
ok(vp.selezione == (4, 5, 6) and vp.varco is None, "la selezione segue il blocco")
vp.mouseDown_(evento(*riga_pl(5))); vp.mouseUp_(evento(*riga_pl(5)))
ok(vp.selezione == (5,), "clic semplice dentro la selezione multipla: resta solo quella riga")
vp.mouseDown_(evento(*riga_pl(4), mod=app.MOD_SHIFT)); vp.mouseUp_(evento(*riga_pl(4)))
vp.keyDown_(evento(0, 0, codice=126, mod=app.MOD_OPT))
ok(ids_pagina() == ids_locali() and ids_locali()[3:5] == atteso[4:6] and vp.selezione == (3, 4), "⌥↑ sposta su la selezione")
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_OPT))
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_OPT))
ok(ids_pagina() == ids_locali() and vp.selezione == (5, 6), "⌥↓ due volte")
ctrl.esegui("pl_annulla")
ok(ids_pagina() == ids_locali() and vp.selezione == (4, 5), "⌘Z annulla l'ultimo spostamento")
ok(not ctrl.puo_annullare(), "un solo livello di annullamento")

prima = ids_pagina()
vp.mouseDown_(evento(*riga_pl(0))); vp.mouseUp_(evento(*riga_pl(0)))
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_SHIFT))
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_SHIFT))
ok(vp.selezione == (0, 1, 2), "⇧↓ estende la selezione")
vp.keyDown_(evento(0, 0, codice=51))
ok(ids_pagina() == ids_locali() == [prima[1]] + prima[3:] and prima[1] == "v1", "⌫: tolti 1 e 3, resta il brano in riproduzione")
ok("non quello in riproduzione" in ctrl.piede_playlist(1) and "radio fermata" in ctrl.piede_playlist(1) and vp.selezione == (0,),
   "avviso (anche «radio fermata»); selezione su chi ha preso il posto del primo rimosso")
ok(not ctrl.coda.radio, "dopo una rimozione la coda non si allunga più da sola")
img_rim, _ = disegna(vp, "playlist_rimozione")
ok(ctrl.puo_annullare(), "rimozione annullabile")
vp.undo_(None)
ok(ids_pagina() == ids_locali() == prima and vp.selezione == (0, 1, 2), "⌘Z: brani rimessi al loro posto")
ok(ctrl.coda.radio, "…e la radio riprende")
vp.mouseDown_(evento(*riga_pl(1))); vp.mouseUp_(evento(*riga_pl(1)))
vp.keyDown_(evento(0, 0, codice=51))
ok(ids_pagina() == prima and "resta in coda" in ctrl.piede_playlist(1), "⌫ sul solo brano in riproduzione: nulla")

AppKit.NSMenu.aperti.clear()
vp.rightMouseDown_(evento(*riga_pl(6)))
voci_pl = AppKit.NSMenu.aperti[-1].voci
azioni_pl = [x.azione for x in voci_pl if x.azione]
ok(vp.selezione == (6,) and azioni_pl[:3] == ["playlistRiproduci:", "playlistDopoCorrente:", "playlistInFondo:"]
   and "playlistRimuoviSuccessivi:" in azioni_pl, "clic destro: menu della playlist")
abil = {x.azione: ctrl.validateMenuItem_(x) for x in voci_pl if x.azione}
ok(abil["playlistSu:"] and abil["playlistRimuovi:"] and not abil["playlistAnnulla:"] and abil["playlistFermaRadio:"],
   "voci abilitate a dovere (anche «Ferma la radio»)")
ctrl.playlistDopoCorrente_(None)
ok(ids_pagina() == ids_locali() and ids_locali()[2] == prima[6] and vp.selezione == (2,), "riproduci dopo il brano attuale")
ctrl.playlistRimuoviSuccessivi_(None)
ok(ids_pagina() == ids_locali() == ids_locali()[:2] and ids_locali()[1] == "v1", "rimuovi i brani successivi")
ok(not ctrl.coda.radio and not ctrl.piede_playlist(1).endswith("radio"), "…e la radio smette di allungare la coda")
vp.undo_(None)
ok(len(ids_pagina()) == 12 and ids_pagina() == ids_locali() and ctrl.coda.radio, "annullata anche questa (radio ripresa)")
comandi.clear()
ctrl.playlistFermaRadio_(None)
ok(comandi == ['"ferma_radio", null'] and not ctrl.coda.radio and "radio fermata" in ctrl.piede_playlist(1),
   "Ferma la radio: comando alla pagina, coda riletta, avviso")
abil = {x.azione: ctrl.validateMenuItem_(x) for x in voci_pl if x.azione}
ok(not abil["playlistFermaRadio:"], "voce disattivata quando la radio è già ferma")
MODIFICA["radio"] = True
rileggi()
ok(vp.validateMenuItem_(NS(action=lambda: "selectAll:")) and not vp.validateMenuItem_(NS(action=lambda: b"undo:")),
   "menu Modifica: Seleziona tutto sì, Annulla no")
vp.selectAll_(None)
ok(vp.selezione == tuple(range(12)), "⌘A seleziona tutto")
vp.keyDown_(evento(0, 0, codice=53))
ok(vp.selezione == (), "Esc deseleziona")

# Lettura della coda partita PRIMA di una modifica: deve essere ignorata.
vp.imposta_selezione((3,))
prima_mossa = ids_pagina()
ctrl.coda.firma = ""                                 # la lettura vecchia porterà l'elenco completo
WebKit.WKWebView.differiti_js = []
rileggi()                                            # lettura vecchia, in attesa
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_OPT))
dopo = ids_locali()
consegne, WebKit.WKWebView.differiti_js = WebKit.WKWebView.differiti_js, None
ok(len(consegne) == 2, "due letture in attesa: quella vecchia e la rilettura")
consegne[0]()
ok(prima_mossa != dopo and ids_locali() == dopo, "lettura superata scartata: la lista non torna indietro")
for c_ in consegne[1:]:
    c_()
ok(ids_locali() == ids_pagina() == dopo, "la rilettura successiva conferma")

MODIFICA["rifiuta"] = True
vp.keyDown_(evento(0, 0, codice=125, mod=app.MOD_OPT))
ok(ids_locali() == ids_pagina() == dopo and "non ha confermato" in ctrl.piede_playlist(1),
   "pagina che non conferma: coda riletta e avviso")
MODIFICA["rifiuta"] = False
MODIFICA["m"] = 0
rileggi()
n_richieste = len(RICHIESTE)
vp.mouseDown_(evento(*riga_pl(3)))
vp.mouseDragged_(evento(lx + 30, ly + 1 + 6 * core.ALTEZZA_RIGA))
vp.mouseUp_(evento(lx + 30, ly + 1 + 6 * core.ALTEZZA_RIGA))
vp.keyDown_(evento(0, 0, codice=51))
ok(len(RICHIESTE) == n_richieste and "non modificabile" in ctrl.piede_playlist(1), "coda dal DOM: nessuna modifica")
MODIFICA["m"] = 1
VIVA.sort(key=lambda v_: int(v_["id"][1:]))
rileggi()
Orologio.t += 5
tick(1)
ok(ctrl.piede_playlist(0) == core.piede_playlist(ctrl.coda, 0), "l'avviso scade e il piede torna al riepilogo")

print("▸ finestre agganciate in primo piano insieme")
AppKit.NSWindow.ordinamenti.clear()
ctrl.windowDidBecomeKey_(NS(object=lambda: ctrl.fin_ricerca))
ordinati = [(f, m) for f, m, n in AppKit.NSWindow.ordinamenti if n == ctrl.fin_ricerca.windowNumber()]
ok(ordinati == [(ctrl.fin_principale, app.FINESTRA_SOTTO), (ctrl.fin_playlist, app.FINESTRA_SOTTO)],
   "la ricerca riceve l'input: lettore e playlist ordinati subito sotto di lei")
AppKit.NSWindow.ordinamenti.clear()
ctrl.windowDidBecomeKey_(NS(object=lambda: ctrl.fin_libreria))
ok(AppKit.NSWindow.ordinamenti == [], "la Libreria non fa parte del gruppo")
ctrl.impost.playlist_agganciata = False
AppKit.NSWindow.ordinamenti.clear()
ctrl.windowDidBecomeKey_(NS(object=lambda: ctrl.fin_principale))
ok([f for f, m, n in AppKit.NSWindow.ordinamenti] == [ctrl.fin_ricerca], "playlist staccata: non segue il lettore")
ctrl.impost.playlist_agganciata = True

print("▸ riduzione a icona")
ok(ctrl.fin_playlist.isVisible() and ctrl.fin_ricerca.isVisible(), "playlist e ricerca visibili")
ctrl.riduciFinestra_(None)
ok(ctrl.fin_principale.isMiniaturized() and not ctrl.fin_playlist.isVisible() and not ctrl.fin_ricerca.isVisible(),
   "⌘M: il lettore va nel Dock e porta con sé playlist e ricerca")
ok(ctrl.fin_libreria.isMiniaturized() is False, "la Libreria resta dov'è")
ctrl._memorizza_cornici()
ok(ctrl.impost.playlist_visibile and ctrl.impost.ricerca_visibile, "uscita a lettore ridotto: visibilità conservata")
ok(ctrl.applicationShouldHandleReopen_hasVisibleWindows_(None, False) and not ctrl.fin_principale.isMiniaturized()
   and ctrl.fin_playlist.isVisible() and ctrl.fin_ricerca.isVisible(), "clic sull'icona nel Dock: tornano tutte e tre")
fpm, flm, frm = ctrl.fin_principale.frame(), ctrl.fin_playlist.frame(), ctrl.fin_ricerca.frame()
ok(quasi(flm.y + flm.h, fpm.y) and quasi(frm.x, fpm.x + fpm.w), "e tornano agganciate")
clic(v, "pl")
clic(v, "riduci")
ok(ctrl.fin_principale.isMiniaturized() and not ctrl.fin_ricerca.isVisible(), "pulsante di riduzione del deck")
ctrl.mostraLettore_(None)
ok(not ctrl.fin_playlist.isVisible() and ctrl.fin_ricerca.isVisible(), "una playlist chiusa prima resta chiusa (⌘1)")
clic(v, "pl")
ctrl.riduciFinestra_(None)
ctrl.commutaPlaylist_(None)                          # ⌘2 a lettore ridotto: scelta esplicita
ok(ctrl.fin_playlist.isVisible() and ctrl.fin_playlist not in ctrl._compagne, "⌘2 a lettore ridotto mostra la playlist")
ctrl.commutaPlaylist_(None)
ctrl.fin_principale.deminiaturize_(None)
ok(not ctrl.fin_playlist.isVisible() and ctrl.fin_ricerca.isVisible(), "…e l'ultima scelta prevale al ripristino")
ctrl.commutaPlaylist_(None)

print("▸ stati particolari")
PAGINA["st"] = 2
tick(20)
Orologio.t = math.floor(Orologio.t) + 0.75          # fase 'spenta' del lampeggio
disegna(v, "lettore_pausa_spento")
Orologio.t = math.floor(Orologio.t) + 1.25
disegna(v, "lettore_pausa_acceso")
ok(cc.stato == 2, "Centro di Controllo in pausa")
PAGINA["st"] = 1
PAGINA["cur"], PAGINA["dur"] = 6000.0, 9000.0
tick(5)
disegna(v, "lettore_ore")
PAGINA.update(cur=83.0, dur=412.0)
WebKit.WKWebView.pagina = staticmethod(lambda js: "" if ".stato(" in js else pagina(js))
tick(60)
ok(not ctrl.stato.pronto, "pagina che non risponde → stato 'in attesa'")
disegna(v, "lettore_attesa")
WebKit.WKWebView.pagina = staticmethod(pagina)

print("▸ pannello di ricerca")
vr, LR = ctrl.vista_ricerca, core.layout_ricerca(ctrl.impost.altezza_ricerca)
s = ctrl.scala
campo_atteso = ((8 + 3) * s, (18 + 2) * s, (215 - 6) * s, (13 - 3.5) * s)
cf = ctrl.campo.frame()
ok(all(quasi(a_, b_) for a_, b_ in zip((cf.x, cf.y, cf.w, cf.h), campo_atteso)), "campo nativo sopra il riquadro disegnato")
ok(ctrl.campo.azione == "cercaInvio:" and ctrl.campo.delegato is ctrl, "Invio e tasti del campo al controllore")
clic(v, "eject")
ok(ctrl.fin_ricerca.primo is ctrl.campo and ctrl.campo.editor.colore is app.ORO, "⏏ porta il cursore (dorato) nel campo")
disegna(vr, "ricerca_vuota")
ctrl.campo.setStringValue_("pink floyd")
ctrl.cercaInvio_(ctrl.campo)
ok(RICHIESTE[-1] == (core.JS_CERCA, {"testo": "pink floyd", "parametri": core.PARAMETRI_RICERCA["brani"]}),
   "ricerca inviata con il filtro Brani")
ok(len(ctrl.risultati) == len(BRANI_TROVATI) and all(r.tipo == "brano" for r in ctrl.risultati), "brani trovati")
ok(vr.selezione == 0 and vr.prima_riga == 0, "selezione sul primo risultato")
PAGINA["vid"] = "id2"                                   # "Comfortably Numb" è il brano in riproduzione
tick(2)
img_r, n = disegna(vr, "ricerca")
ok(n > 60, f"ricerca disegnata ({n} primitive)")
clic(vr, "album", LR)
ok(ctrl.impost.filtro_ricerca == "album" and RICHIESTE[-1][1]["parametri"] == core.PARAMETRI_RICERCA["album"]
   and [r.titolo for r in ctrl.risultati] == ["Wish You Were Here", "Meddle"], "filtro Album: nuova ricerca")
disegna(vr, "ricerca_album")
clic(vr, "brani", LR)
lx, ly, lw, lh = LR["lista"]
riga = lambda i: (lx + 30, ly + 1 + i * core.ALTEZZA_RIGA + 4)          # noqa: E731
vr.mouseDown_(evento(*riga(1), clic=2))
ok(RICHIESTE[-1] == (core.JS_ACCODA, {"bersaglio": {"videoId": "id1"}, "posizione": "INSERT_AFTER_CURRENT_VIDEO",
                                      "salta": True}), "doppio clic: riproduci ora (dopo il corrente + salto)")
ok(ctrl.stato_ricerca == "«Money» in riproduzione", f"esito mostrato: {ctrl.stato_ricerca}")
vr.mouseDown_(evento(*riga(3), clic=2, mod=app.MOD_OPT))
ok(RICHIESTE[-1][1] == {"bersaglio": {"videoId": "id3"}, "posizione": "INSERT_AT_END", "salta": False},
   "⌥ doppio clic: in fondo alla coda")
vr.keyDown_(evento(0, 0, codice=125))
vr.keyDown_(evento(0, 0, codice=36, mod=app.MOD_SHIFT))
ok(vr.selezione == 4 and RICHIESTE[-1][1]["posizione"] == "INSERT_AFTER_CURRENT_VIDEO"
   and RICHIESTE[-1][1]["salta"] is False, "↓ e ⇧⏎: riproduci dopo")
n_prima = len(RICHIESTE)
vr.mouseDown_(evento(*riga(9), clic=2))
ok(len(RICHIESTE) == n_prima and "non è disponibile" in ctrl.stato_ricerca, "brano non disponibile: nessun invio")
AppKit.NSMenu.aperti.clear()
vr.rightMouseDown_(evento(*riga(5)))
ok(AppKit.NSMenu.aperti and [x.azione for x in AppKit.NSMenu.aperti[-1].voci] ==
   ["menuRiproduciOra:", "menuRiproduciDopo:", "menuAccoda:"], "clic destro su una riga: menu del risultato")
ctrl.menuAccoda_(None)
ok(RICHIESTE[-1][1]["bersaglio"] == {"videoId": "id5"} and RICHIESTE[-1][1]["posizione"] == "INSERT_AT_END",
   "menu: aggiungi in fondo alla coda")
clic(vr, "album", LR)
vr.mouseDown_(evento(*riga(0), clic=2))
ok(RICHIESTE[-1][1]["bersaglio"] == {"playlistId": "OLAK5uy_wywh"} and "12 brani" in ctrl.stato_ricerca,
   "album: intera raccolta in riproduzione")
clic(vr, "brani", LR)
ok(ctrl.control_textView_doCommandBySelector_(ctrl.campo, None, "moveDown:") is True
   and ctrl.fin_ricerca.primo is vr, "↓ dal campo porta ai risultati")
vr.keyDown_(evento(0, 0, codice=53))
ok(ctrl.fin_ricerca.primo is ctrl.campo, "Esc dai risultati torna al campo")
ok(ctrl.control_textView_doCommandBySelector_(ctrl.campo, None, b"cancelOperation:") is True
   and ctrl.campo.stringValue() == "", "Esc nel campo lo svuota (selettore anche come bytes)")
ok(ctrl.control_textView_doCommandBySelector_(ctrl.campo, None, "insertTab:") is False, "altri tasti al campo")
ctrl.avvia_ricerca("errore")
ok(ctrl.stato_ricerca == "errore: YouTube Music non è ancora pronto" and ctrl.risultati == [], "errore di ricerca mostrato")
disegna(vr, "ricerca_errore")
WebKit.WKWebView.differiti = []
ctrl.avvia_ricerca("prova uno")
ctrl.avvia_ricerca("prova due")
for consegna in reversed(WebKit.WKWebView.differiti):  # la risposta vecchia arriva per ultima
    consegna()
WebKit.WKWebView.differiti = None
ok(ctrl.risultati and ctrl.risultati[0].titolo == "Time [prova due]", "risposta superata ignorata")
clic(v, "ric")
ok(not ctrl.fin_ricerca.isVisible(), "RIC nasconde la ricerca")
clic(v, "ric")
fpx, frx = ctrl.fin_principale.frame(), ctrl.fin_ricerca.frame()
ok(ctrl.fin_ricerca.isVisible() and quasi(frx.x, fpx.x + fpx.w), "RIC la rimostra agganciata")
AppKit.NSEvent.posizione_mouse = NS(x=0.0, y=0.0)
vr.mouseDown_(evento(150, 5))                          # barra del titolo: trascinamento
AppKit.NSEvent.posizione_mouse = NS(x=300.0, y=0.0)
vr.mouseDragged_(evento(150, 5))
ok(not ctrl.impost.ricerca_agganciata, "ricerca staccata")
AppKit.NSEvent.posizione_mouse = NS(x=8.0, y=5.0)
vr.mouseDragged_(evento(150, 5))
vr.mouseUp_(evento(150, 5))
ok(ctrl.impost.ricerca_agganciata and quasi(ctrl.fin_ricerca.frame().x, fpx.x + fpx.w), "ricerca riagganciata per magnetismo")
h0 = ctrl.fin_ricerca.frame().h
px_, py_, pw_, ph_ = LR["presa"]
AppKit.NSEvent.posizione_mouse = NS(x=0.0, y=0.0)
vr.mouseDown_(evento(px_ + 5, py_ + 5))
AppKit.NSEvent.posizione_mouse = NS(x=0.0, y=-60.0)
vr.mouseDragged_(evento(px_ + 5, py_ + 5))
vr.mouseUp_(evento(px_ + 5, py_ + 5))
ok(quasi(ctrl.fin_ricerca.frame().h, h0 + 60) and quasi(ctrl.impost.altezza_ricerca, (h0 + 60) / ctrl.scala),
   "ricerca ridimensionata con la presa")

print("▸ diagnosi della presa audio")
DIAGNOSI.update(testo="audio/mp4 · seg 3 · dec 0 · decodifica ADTS: EncodingError", dati=False)
tick(120)
ok(not ctrl.viz_disponibile and "decodifica ADTS: EncodingError" in core.testo_stato(ctrl.stato, False),
   "senza dati: la riga di stato mostra la diagnosi")
disegna(v, "lettore_diagnosi")
DIAGNOSI.update(testo="audio/mp4 · seg 12 · dec 12 · diretto", dati=True)
tick(10)
ok(ctrl.viz_disponibile, "dati di nuovo presenti: analisi disponibile")

print("▸ scala")
for s_nuova in (2.0, 1.0, 1.5):
    ctrl.applica_scala(s_nuova)
    a_, b_, c_ = ctrl.fin_principale.frame(), ctrl.fin_playlist.frame(), ctrl.fin_ricerca.frame()
    ok(quasi(a_.w, 275 * s_nuova) and quasi(c_.w, 275 * s_nuova) and quasi(b_.w, 275 * s_nuova)
       and quasi(b_.y + b_.h, a_.y) and quasi(c_.x, a_.x + a_.w) and quasi(c_.y + c_.h, a_.y + a_.h)
       and quasi(ctrl.campo.frame().x, 11 * s_nuova), f"scala {s_nuova}: dimensioni e agganci")
    if s_nuova == 2.0:
        disegna(v, "lettore_2x")
AppKit.NSMenu.aperti.clear()
v.rightMouseDown_(evento(150, 106))
voci = AppKit.NSMenu.aperti[-1].voci
for voce in voci:
    ctrl.validateMenuItem_(voce)
ok([x._stato for x in voci if x.tag() in app.TAG_SCALA.values()] == [0, 1, 0], "menu: spunta su 1,5×")
ok([x._stato for x in voci if x.tag() == app.TAG_RICERCA] == [1], "menu: spunta su Ricerca")

print("▸ link esterni e chiusura")
azione = NS(request=lambda: NS(URL=lambda: __import__("Foundation").NSURL.URLWithString_("https://www.example.com/x")),
            navigationType=lambda: 0)
decisioni = []
ctrl.webView_decidePolicyForNavigationAction_decisionHandler_(ctrl.web, azione, decisioni.append)
ok(decisioni == [0] and AppKit.NSWorkspace.aperti[-1] == "https://www.example.com/x", "link esterno aperto nel browser")
azione = NS(request=lambda: NS(URL=lambda: __import__("Foundation").NSURL.URLWithString_("https://accounts.google.com/x")),
            navigationType=lambda: 0)
ctrl.webView_decidePolicyForNavigationAction_decisionHandler_(ctrl.web, azione, decisioni.append)
ok(decisioni[-1] == 1, "accesso Google resta nella Libreria")
ok(ctrl.windowShouldClose_(ctrl.fin_libreria) is False and not ctrl.fin_libreria.isVisible(), "chiudere la Libreria la nasconde soltanto")
ctrl.applicationWillTerminate_(None)
salvate = core.carica_impostazioni(USCITA / "imp.json")
ok(salvate.origine_principale == [fp2.x, fp2.y] and salvate.libreria_visibile is False, "impostazioni salvate all'uscita")
ok(salvate.scala == 1.5 and salvate.ricerca_agganciata and salvate.filtro_ricerca == "brani"
   and quasi(salvate.altezza_ricerca, ctrl.impost.altezza_ricerca), "scala e pannello di ricerca salvati")
ok(getattr(AppKit.NSView, "beep", 0) == 1, "solo ⌘B passato al sistema, nessun altro tasto rifiutato")

# composizione: lettore e playlist (riordino in corso) a sinistra, ricerca agganciata a destra
ctrl.impost.altezza_ricerca = core.ALTEZZA_RICERCA_PREDEFINITA
ctrl.fin_ricerca.setFrame_display_(app.rett(0, 0, 275 * 1.5, core.ALTEZZA_RICERCA_PREDEFINITA * 1.5), True)
ctrl.campo.setStringValue_("pink floyd")
ctrl.avvia_ricerca("pink floyd")
disegna(vr, "ricerca")
a = Image.open(USCITA / "lettore.png")
b = Image.open(USCITA / "playlist_trascinamento.png")     # tre righe trascinate: la novità della 1.2
c = Image.open(USCITA / "ricerca.png")
comp = Image.new("RGBA", (a.width + c.width + 40, max(a.height + b.height, c.height) + 40), (40, 44, 52, 255))
comp.paste(a, (20, 20))
comp.paste(b, (20, 20 + a.height))
comp.paste(c, (20 + a.width, 20))
d_ = ImageDraw.Draw(comp)                       # il campo è nativo: qui se ne simula il testo
fx, fy = 20 + a.width + campo_atteso[0] * DPR, 20 + campo_atteso[1] * DPR
d_.text((fx + 4, fy + 2), "pink floyd", font=carattere(True, round(7 * 1.5 * 0.95 * DPR)), fill=(247, 200, 115, 255))
comp.save(USCITA / "anteprima.png")
print(f"\n{verifiche} verifiche superate; immagini in {USCITA}")
