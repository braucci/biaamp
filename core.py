#!/usr/bin/env python3
"""
core.py — Motore di BiaAmp (puro Python, nessuna dipendenza grafica).

BiaAmp è un lettore per YouTube Music con interfaccia "deck" in stile anni
Novanta. Questo modulo contiene TUTTA la logica di dominio, così da poter
essere verificato senza display, senza Cocoa e senza rete:

    • decodifica dello stato che arriva dalla pagina web (JSON);
    • analizzatore di spettro: bande logaritmiche + balistica delle barre
      (sistema del 1° ordine) e dei picchi (caduta grave);
    • oscilloscopio con trigger sul fronte di salita;
    • coda di riproduzione e sua modifica (selezione, spostamenti a
      blocco, rimozioni, annullamento), testi del display, marquee;
    • geometria delle finestre (layout, hit-test, aggancio magnetico);
    • costruzione sicura dei comandi JavaScript;
    • impostazioni persistenti (scrittura atomica).

Lo strato di presentazione (app.py) si limita a disegnare ciò che questo
modulo calcola: è la stessa separazione che si ha tra solutore e
post-processore in un codice CFD.

Esecuzione degli autotest:   python3 core.py

© 2026 B. Raucci — Licenza MIT
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

__version__ = "1.4.1"

NOME_APP = "BiaAmp"
ID_BUNDLE = "net.raucci.biaamp"
URL_SERVIZIO = "https://music.youtube.com"
URL_PROGETTO = "https://github.com/braucci/biaamp"

# User-Agent di Safari: Google rifiuta l'accesso dai "browser incorporati"
# riconosciuti come tali; lo stesso accorgimento è usato dal progetto originale.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)

# Fattore di scala: la skin è progettata in "unità skin" (u) e disegnata a
# `scala` punti per unità. Il deck classico era largo 275 pixel; su un CRT
# da 15" o 17" a 1024×768 (93 e 80 ppi) misurava quindi 75–87 mm. Sul
# MacBook Pro 14" (127 punti per pollice) la scala 1,5 dà 412 punti = 82 mm,
# al centro di quell'intervallo; la 2 (110 mm) è la vecchia "double size".
SCALE = (1.0, 1.5, 2.0)
SCALA_PREDEFINITA = 1.5


# ═══════════════════════════════════════════════════════════════════════════
# 1. Utilità numeriche
# ═══════════════════════════════════════════════════════════════════════════

def finito(x, predefinito: float = 0.0) -> float:
    """Converte x in float finito; NaN, ±inf, None e testo non numerico
    diventano `predefinito`. Protegge il motore da dati sporchi del DOM."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(predefinito)
    return v if math.isfinite(v) else float(predefinito)


def limita(x: float, lo: float, hi: float) -> float:
    """Saturazione: restituisce x confinato nell'intervallo [lo, hi]."""
    return lo if x < lo else hi if x > hi else x


def _stringa(x, lunghezza_max: int = 400) -> str:
    """Stringa ripulita (spazi multipli compressi) e di lunghezza limitata."""
    if not isinstance(x, str):
        return ""
    return re.sub(r"\s+", " ", x).strip()[:lunghezza_max]


def tronca(testo: str, n: int) -> str:
    """Tronca a n caratteri aggiungendo l'ellissi se necessario."""
    if n <= 0:
        return ""
    if len(testo) <= n:
        return testo
    return testo[: n - 1] + "…" if n > 1 else "…"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Tempo
# ═══════════════════════════════════════════════════════════════════════════

def fmt_durata(secondi: float) -> str:
    """Durata leggibile: 185 → '3:05', 3723 → '1:02:03'."""
    s = int(max(0.0, finito(secondi)))
    ore, resto = divmod(s, 3600)
    minuti, sec = divmod(resto, 60)
    return f"{ore}:{minuti:02d}:{sec:02d}" if ore else f"{minuti}:{sec:02d}"


def durata_secondi(testo: str) -> int:
    """Inversa di fmt_durata: '3:05' → 185, '1:02:03' → 3723; non valido → 0."""
    if not isinstance(testo, str):
        return 0
    parti = testo.strip().split(":")
    if not parti or not all(p.isdigit() for p in parti) or len(parti) > 3:
        return 0
    totale = 0
    for p in parti:
        totale = totale * 60 + int(p)
    return totale


def tempo_lcd(posizione: float, durata: float, rimanente: bool):
    """Testo del display a 7 segmenti.

    Restituisce (negativo, cifre, ore) dove `cifre` ha sempre la forma
    'AA:BB'. Sotto i 100 minuti AA:BB = minuti:secondi; oltre, AA:BB =
    ore:minuti e `ore` è True (l'interfaccia aggiunge il marcatore 'h').
    """
    pos = max(0.0, finito(posizione))
    dur = max(0.0, finito(durata))
    t = max(0.0, dur - pos) if (rimanente and dur > 0) else pos
    totale = int(t)
    minuti, secondi = divmod(totale, 60)
    if minuti >= 100:
        ore, minuti = divmod(minuti, 60)
        return (rimanente and dur > 0), f"{min(ore, 99):02d}:{minuti:02d}", True
    return (rimanente and dur > 0), f"{minuti:02d}:{secondi:02d}", False


def lampeggio_visibile(t: float, periodo: float = 1.0) -> bool:
    """Onda quadra con duty cycle 50 %: usata per far lampeggiare il tempo
    in pausa, come sui deck a cassette."""
    return (finito(t) % periodo) < periodo / 2


# ═══════════════════════════════════════════════════════════════════════════
# 3. Stato del lettore
# ═══════════════════════════════════════════════════════════════════════════

# Codici di stato del player di YouTube (API #movie_player.getPlayerState()).
STATI_YT = {
    -1: "non avviato",
    0: "terminato",
    1: "in riproduzione",
    2: "in pausa",
    3: "caricamento",
    5: "pronto",
}

_RE_ANNO = re.compile(r"^(19|20)\d{2}$")
_RE_STATISTICHE = re.compile(
    r"\d.*(visualizz|view|mi piace|like|riproduz|play|ascolt)", re.IGNORECASE
)


def analizza_byline(testo: str):
    """Scompone la riga sotto il titolo di YouTube Music.

    Brano:   'Artista • Album • 2019'               → (Artista, Album, 2019)
    Video:   'Artista • 1,2 Mln di visualizzazioni' → (Artista, '', '')
    """
    parti = [p.strip() for p in _stringa(testo).split("•") if p.strip()]
    if not parti:
        return "", "", ""
    artista, album, anno = parti[0], "", ""
    resto = parti[1:]
    if resto and _RE_ANNO.match(resto[-1]):
        anno = resto.pop()
    if resto and not _RE_STATISTICHE.search(resto[0]):
        album = resto[0]
    return artista, album, anno


_RE_DIM_WH = re.compile(r"=w\d+-h\d+")
_RE_DIM_S = re.compile(r"=s\d+")


def ingrandisci_copertina(url: str, lato: int = 544) -> str:
    """Le miniature di Google codificano la dimensione nell'URL
    ('=w60-h60-…' oppure '=s60'): la sostituiamo con una più grande."""
    url = _stringa(url, 2000)
    if not url.startswith("http"):
        return ""
    if _RE_DIM_WH.search(url):
        return _RE_DIM_WH.sub(f"=w{lato}-h{lato}", url, count=1)
    return _RE_DIM_S.sub(f"=s{lato}", url, count=1)


def normalizza_ripetizione(valore) -> str:
    v = valore.upper() if isinstance(valore, str) else ""
    return v if v in ("NONE", "ALL", "ONE") else "NONE"


@dataclass
class StatoLettore:
    """Fotografia istantanea del lettore, ricostruita dal JSON del ponte."""
    pronto: bool = False
    codice: int = -1              # vedi STATI_YT
    posizione: float = 0.0        # s
    durata: float = 0.0           # s
    volume: int = 100             # 0…100
    muto: bool = False
    titolo: str = ""
    artista: str = ""
    album: str = ""
    anno: str = ""
    copertina: str = ""
    video_id: str = ""
    ripetizione: str = "NONE"     # NONE | ALL | ONE
    casuale: bool = False
    mi_piace: bool = False
    viz_tipo: str = ""            # 'f' spettro | 'w' forma d'onda | ''
    viz: bytes = b""
    freq_camp: float = 0.0        # Hz
    n_fft: int = 0
    diagnosi_audio: str = ""      # stato della presa audio (presa.js)

    @property
    def in_riproduzione(self) -> bool:
        return self.codice in (1, 3)

    @property
    def in_pausa(self) -> bool:
        return self.codice == 2

    @property
    def descrizione(self) -> str:
        if not self.pronto:
            return "in attesa"
        return STATI_YT.get(self.codice, "—")

    @property
    def chiave_brano(self):
        return (self.video_id, self.titolo, self.artista)

    @classmethod
    def da_json(cls, testo) -> "StatoLettore":
        """Decodifica robusta: qualunque input non valido produce uno stato
        'non pronto' invece di un'eccezione."""
        try:
            d = json.loads(testo) if isinstance(testo, str) else None
        except (ValueError, TypeError):
            d = None
        if not isinstance(d, dict):
            return cls()
        s = cls()
        s.pronto = bool(d.get("pronto"))
        s.codice = int(finito(d.get("st"), -1))
        s.durata = max(0.0, finito(d.get("dur")))
        s.posizione = max(0.0, finito(d.get("cur")))
        if s.durata > 0:
            s.posizione = min(s.posizione, s.durata)
        s.volume = int(round(limita(finito(d.get("vol"), 100), 0, 100)))
        s.muto = bool(d.get("muto"))
        s.titolo = _stringa(d.get("titolo"))
        s.artista, s.album, s.anno = analizza_byline(d.get("byline"))
        s.copertina = ingrandisci_copertina(d.get("cop"))
        s.video_id = _stringa(d.get("vid"), 32)
        s.ripetizione = normalizza_ripetizione(d.get("rip"))
        s.casuale = bool(d.get("shuf"))
        s.mi_piace = _stringa(d.get("like")).upper() == "LIKE"
        tipo, dati = d.get("vk"), d.get("viz")
        if tipo in ("f", "w") and isinstance(dati, str) and dati:
            try:
                s.viz = base64.b64decode(dati, validate=True)
                s.viz_tipo = tipo
            except (ValueError, binascii.Error):
                pass
        s.freq_camp = max(0.0, finito(d.get("fs")))
        s.n_fft = int(max(0.0, finito(d.get("nfft"))))
        s.diagnosi_audio = _stringa(d.get("ad"), 200)
        return s


def np_da_aggiornare(prec: StatoLettore | None, att: StatoLettore, dt: float) -> bool:
    """Decide se ripubblicare le informazioni nel Centro di Controllo.

    Il sistema estrapola da solo il tempo trascorso (posizione + rate·t):
    serve un aggiornamento solo quando questa estrapolazione diventa falsa,
    cioè cambio brano, cambio di stato o salto di posizione (seek).
    """
    if prec is None:
        return True
    if prec.chiave_brano != att.chiave_brano or prec.in_riproduzione != att.in_riproduzione:
        return True
    if abs(prec.durata - att.durata) > 1.0:
        return True
    attesa = prec.posizione + (dt if prec.in_riproduzione else 0.0)
    return abs(att.posizione - attesa) > 2.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Coda di riproduzione
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BranoCoda:
    titolo: str
    artista: str
    durata_testo: str
    durata_s: int
    selezionato: bool           # True per il brano in riproduzione
    video_id: str
    segnaposto: bool = False    # elemento della coda che il ponte non sa leggere

    def etichetta(self, indice: int) -> str:
        if self.segnaposto:
            return f"{indice + 1}. —"
        nome = f"{self.artista} - {self.titolo}" if self.artista else self.titolo
        return f"{indice + 1}. {nome}"


class Coda:
    """Copia locale della coda di YouTube Music, aggiornata per firma.

    La posizione di un brano in `brani` coincide con il suo indice nella coda
    interna di YouTube Music (il ponte non salta elementi: quelli illeggibili
    arrivano come segnaposto), quindi gli indici si possono usare così come
    sono per SET_INDEX, MOVE_ITEM e REMOVE_ITEM.
    """

    def __init__(self):
        self.firma = ""
        self.brani: list[BranoCoda] = []
        self.modificabile = False   # True se la coda viene dallo store (non dal DOM)
        self.radio = False          # coda «infinita»: YouTube Music la estende da sola
        self.via = ""               # via delle ultime modifiche: 'nativa' o 'locale'
        self.errore = ""            # motivo dell'ultima operazione fallita nella pagina

    def aggiorna_da_json(self, testo) -> bool:
        """Restituisce True se la coda è cambiata."""
        try:
            d = json.loads(testo) if isinstance(testo, str) else None
        except (ValueError, TypeError):
            return False
        if not isinstance(d, dict) or d.get("uguale"):
            return False
        voci = d.get("voci")
        if not isinstance(voci, list):
            return False
        brani = []
        for v in voci[:2000]:
            if not isinstance(v, dict):
                continue
            dt = _stringa(v.get("d"), 12)
            brani.append(BranoCoda(
                titolo=_stringa(v.get("t")),
                artista=_stringa(v.get("a")),
                durata_testo=dt,
                durata_s=durata_secondi(dt),
                selezionato=bool(v.get("s")),
                video_id=_stringa(v.get("id"), 32),
                segnaposto=bool(v.get("x")),
            ))
        firma = _stringa(d.get("firma"), 64)
        cambiata = firma != self.firma or len(brani) != len(self.brani)
        self.firma, self.brani = firma, brani
        self.modificabile = bool(d.get("m"))
        self.radio = bool(d.get("inf"))
        self.via = _stringa(d.get("via"), 12)
        self.errore = _stringa(d.get("err"), 200)
        return cambiata

    @property
    def indice_corrente(self):
        for i, b in enumerate(self.brani):
            if b.selezionato:
                return i
        return None

    @property
    def id_corrente(self) -> str:
        i = self.indice_corrente
        return self.brani[i].video_id if i is not None else ""

    def applica_mosse(self, mosse) -> None:
        """Aggiornamento immediato dopo uno spostamento chiesto alla pagina.
        La firma si azzera: la lettura successiva riporta comunque la coda reale."""
        self.brani = applica_mosse(self.brani, mosse)
        self.firma = ""

    def rimuovi(self, indici) -> list:
        """Toglie gli indici dati; restituisce [(indice, brano)] in ordine
        crescente, ciò che serve per reinserirli."""
        tolti = sorted({i for i in indici if 0 <= i < len(self.brani)})
        rimossi = [(i, self.brani[i]) for i in tolti]
        insieme = set(tolti)
        self.brani = [b for i, b in enumerate(self.brani) if i not in insieme]
        self.firma = ""
        return rimossi

    def reinserisci(self, rimossi) -> None:
        """Inverso di rimuovi(): reinserimento in ordine crescente di indice."""
        for i, b in sorted(rimossi, key=lambda x: x[0]):
            self.brani.insert(min(i, len(self.brani)), b)
        self.firma = ""

    def durata_totale(self) -> int:
        return sum(b.durata_s for b in self.brani)

    def riepilogo(self) -> str:
        n = len(self.brani)
        if n == 0:
            return "coda vuota"
        brani = "brano" if n == 1 else "brani"
        return f"{n} {brani} · {fmt_durata(self.durata_totale())}"


def scroll_visibile(indice, scroll: int, n_visibili: int, n_totale: int) -> int:
    """Nuovo offset di scorrimento che rende visibile `indice` (con una riga
    di margine), senza muovere la lista se è già visibile."""
    massimo = max(0, n_totale - n_visibili)
    if indice is None or n_visibili <= 0:
        return int(limita(scroll, 0, massimo))
    if indice < scroll:
        scroll = indice - 1
    elif indice >= scroll + n_visibili:
        scroll = indice - n_visibili + 2
    return int(limita(scroll, 0, massimo))


# ═══════════════════════════════════════════════════════════════════════════
# 4 bis. Modifica della coda: selezione, spostamenti, rimozioni
# ═══════════════════════════════════════════════════════════════════════════
#
# Una "mossa" (da, a) è quella di MOVE_ITEM nella convenzione di
# list.insert(a, list.pop(da)): `a` è la posizione FINALE dell'elemento.
# Un "varco" g ∈ {0, …, n} è lo spazio davanti all'elemento g (g = n: in fondo):
# è ciò che si indica trascinando una riga.

def selezione_valida(selezione, n: int) -> tuple:
    """Indici distinti, ordinati, compresi in 0…n-1."""
    return tuple(sorted({int(i) for i in selezione if 0 <= int(i) < n}))


def selezione_dopo_clic(selezione, ancora, indice: int, estendi: bool = False, alterna: bool = False):
    """Selezione multipla come nelle liste di macOS → (selezione, ancora).
    Clic: solo la riga.  ⌘-clic: aggiunge o toglie la riga.
    ⇧-clic: l'intervallo dall'ancora (l'ultima riga cliccata) alla riga."""
    if estendi and ancora is not None:
        lo, hi = sorted((ancora, indice))
        return tuple(range(lo, hi + 1)), ancora
    if alterna:
        return tuple(sorted(set(selezione) ^ {indice})), indice
    return (indice,), indice


def naviga_selezione(ancora, cursore, passo: int, n: int, estendi: bool = False, partenza=None):
    """Frecce nella lista → (selezione, ancora, cursore). Il cursore si sposta
    di `passo`; con ⇧ la selezione va dall'ancora al cursore."""
    if n <= 0:
        return (), None, None
    base = cursore if cursore is not None else partenza
    nuovo = (0 if passo > 0 else n - 1) if base is None else int(limita(base + passo, 0, n - 1))
    if estendi and ancora is not None:
        lo, hi = sorted((int(limita(ancora, 0, n - 1)), nuovo))
        return tuple(range(lo, hi + 1)), ancora, nuovo
    return (nuovo,), nuovo, nuovo


def applica_mosse(lista, mosse) -> list:
    """Esegue le mosse su una copia della lista (riferimento per le prove e
    per l'aggiornamento immediato della playlist)."""
    out = list(lista)
    for da, a in mosse:
        out.insert(a, out.pop(da))
    return out


def inverti_mosse(mosse) -> list:
    """Mosse che annullano `mosse`: le stesse, a ritroso e con da/a scambiati."""
    return [(a, da) for da, a in reversed(list(mosse))]


def mosse_blocco(n: int, selezione, varco: int) -> list:
    """Mosse che portano gli elementi selezionati, nel loro ordine, a formare
    un blocco nel varco indicato; gli altri elementi conservano l'ordine.

    Selezionati sopra il varco (i < g), dal più vicino al varco (j = 0) al
    più lontano: il j-esimo scende in g-1-j ≥ i.  pop(i) seguito da
    insert(g-1-j) fa salire di un posto solo gli indici in (i, g-1-j]:
    restano fermi sia i selezionati ancora da trattare (indici < i) sia il
    blocco già formato (indici > g-1-j).
    Selezionati sotto il varco (i ≥ g), in ordine crescente: il j-esimo sale
    in g+j ≤ i (sono distinti e ≥ g).  pop(i) e insert(g+j) fanno scendere
    solo gli indici in [g+j, i): restano fermi i selezionati successivi
    (> i) e il blocco già formato (< g+j).
    Ogni elemento si muove quindi una volta sola: mosse ≤ |S|."""
    sel = selezione_valida(selezione, n)
    g = int(limita(varco, 0, n))
    sopra = [i for i in sel if i < g]
    sotto = [i for i in sel if i >= g]
    mosse = []
    for j, i in enumerate(reversed(sopra)):
        if i != g - 1 - j:
            mosse.append((i, g - 1 - j))
    for j, i in enumerate(sotto):
        if i != g + j:
            mosse.append((i, g + j))
    return mosse


def selezione_dopo_blocco(n: int, selezione, varco: int) -> tuple:
    """Posizioni occupate dal blocco dopo mosse_blocco()."""
    sel = selezione_valida(selezione, n)
    g = int(limita(varco, 0, n))
    inizio = g - sum(1 for i in sel if i < g)
    return tuple(range(inizio, inizio + len(sel)))


def mosse_su(selezione, n: int):
    """⌥↑: ogni elemento selezionato sale di una riga → (mosse, selezione).
    Si procede dall'alto, così ogni scambio coinvolge un elemento non ancora
    mosso; se il primo è già in cima non si muove nulla (come in Winamp)."""
    sel = selezione_valida(selezione, n)
    if not sel or sel[0] == 0:
        return [], sel
    return [(i, i - 1) for i in sel], tuple(i - 1 for i in sel)


def mosse_giu(selezione, n: int):
    """⌥↓: simmetrico di mosse_su, procedendo dal basso."""
    sel = selezione_valida(selezione, n)
    if not sel or sel[-1] == n - 1:
        return [], sel
    return [(i, i + 1) for i in reversed(sel)], tuple(i + 1 for i in sel)


def varco_da_y(y: float, y_lista: float, prima_riga: int, n: int, n_visibili: int) -> int:
    """Varco più vicino al puntatore (y in unità skin), limitato alle righe
    visibili: il bordo tra due righe si arrotonda alla riga più vicina."""
    rel = (y - y_lista - 1) / ALTEZZA_RIGA
    g = prima_riga + int(math.floor(rel + 0.5))
    g = int(limita(g, prima_riga, prima_riga + n_visibili))
    return int(limita(g, 0, n))


def rimovibili(brani, selezione) -> tuple:
    """Indici selezionati che si possono togliere: tutti tranne il brano in
    riproduzione (YouTube Music non lo consente nemmeno dalla sua interfaccia)."""
    return tuple(i for i in selezione_valida(selezione, len(brani)) if not brani[i].selezionato)


def selezione_dopo_rimozione(n_prima: int, rimossi) -> tuple:
    """Dopo una rimozione si seleziona l'elemento che ha preso il posto del
    primo rimosso (o l'ultimo rimasto): ⌫ ripetuto svuota la coda in avanti."""
    rimossi = selezione_valida(rimossi, n_prima)
    n_dopo = n_prima - len(rimossi)
    if not rimossi or n_dopo <= 0:
        return ()
    return (min(rimossi[0], n_dopo - 1),)


def argomenti_sposta(brani, mosse) -> dict:
    """Argomenti di JS_SPOSTA: per ogni mossa anche il videoId dell'elemento
    che deve trovarsi in `da` a quel punto, così il ponte può ritrovarlo se
    la coda è cambiata nel frattempo."""
    lista, out = list(brani), []
    for da, a in mosse:
        out.append([int(da), int(a), lista[da].video_id])
        lista.insert(a, lista.pop(da))
    return {"mosse": out}


def argomenti_rimuovi(brani, indici) -> dict:
    """Argomenti di JS_RIMUOVI, in ordine decrescente: togliendo prima gli
    indici più alti, quelli ancora da togliere non cambiano."""
    validi = selezione_valida(indici, len(brani))
    return {"voci": [[i, brani[i].video_id] for i in reversed(validi)]}


def testo_rimozione(k: int, escluso_corrente: bool, radio_fermata: bool = False) -> str:
    base = "1 brano rimosso" if k == 1 else f"{k} brani rimossi"
    if escluso_corrente:
        base += " (non quello in riproduzione)"
    if radio_fermata:
        base += " · radio fermata"
    return base + " · ⌘Z annulla"


def piede_playlist(coda: "Coda", n_selezionati: int) -> str:
    testo = coda.riepilogo() + (" · radio" if coda.radio and coda.brani else "")
    if n_selezionati > 1:
        testo += f" · {n_selezionati} selezionati"
    return testo


# ═══════════════════════════════════════════════════════════════════════════
# 5. Testi del display
# ═══════════════════════════════════════════════════════════════════════════

SEPARATORE_MARQUEE = "  ***  "


def marquee(testo: str, n: int, passo: int) -> str:
    """Finestra di n caratteri su un nastro circolare (testo + separatore).
    Se il testo entra nel display resta fermo."""
    if n <= 0:
        return ""
    if len(testo) <= n:
        return testo
    nastro = testo + SEPARATORE_MARQUEE
    i = passo % len(nastro)
    return (nastro + nastro)[i:i + n]


def testo_marquee(st: StatoLettore, numero=None) -> str:
    if not st.pronto:
        return f"{NOME_APP} {__version__} — in attesa di YouTube Music"
    if not st.titolo:
        return f"{NOME_APP} {__version__} — cerca un brano: tasto L o pannello Ricerca"
    base = f"{st.artista} - {st.titolo}" if st.artista else st.titolo
    if st.durata > 0:
        base += f" ({fmt_durata(st.durata)})"
    return f"{numero}. {base}" if numero else base


def testo_info(st: StatoLettore, transitorio: str | None = None) -> str:
    """Seconda riga del display: un messaggio transitorio (es. il volume
    mentre lo si regola) ha la precedenza sui metadati del brano."""
    if transitorio:
        return transitorio
    if not st.pronto:
        return "caricamento di music.youtube.com…"
    if st.album and st.anno:
        return f"{st.album} · {st.anno}"
    if st.album or st.anno:
        return st.album or st.anno
    return "YouTube Music" if st.titolo else "nessun brano"


def testo_stato(st: StatoLettore, viz_disponibile: bool) -> str:
    """Riga di stato. Se l'analisi non arriva, la riga intera è dedicata alla
    diagnosi della presa audio (formato, segmenti, metodo, ultimo errore):
    lo stato del lettore è già indicato dal glifo ▶/❚❚ nel display."""
    if not viz_disponibile:
        return "analisi n/d: " + (st.diagnosi_audio or "nessun dato")
    return st.descrizione + (" · muto" if st.muto else "")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Analizzatore di spettro e oscilloscopio
# ═══════════════════════════════════════════════════════════════════════════

MODI_VIZ = ("spettro", "oscilloscopio", "spento")
CODICE_VIZ = {"spettro": "f", "oscilloscopio": "w", "spento": ""}


def prossimo_modo_viz(modo: str) -> str:
    i = MODI_VIZ.index(modo) if modo in MODI_VIZ else -1
    return MODI_VIZ[(i + 1) % len(MODI_VIZ)]


class Spettro:
    """Analizzatore a bande logaritmiche con balistica fisica.

    INGRESSO: i moduli della FFT calcolata da Web Audio (AnalyserNode),
    già convertiti in dB e quantizzati su 0…255 tra minDecibels e
    maxDecibels: un valore v corrisponde a L = L_min + (v/255)·ΔL.

    BANDE: n bande con bordi in progressione geometrica tra f_min e f_max,
        f_i = f_min · (f_max/f_min)^(i/n),
    cioè larghezza relativa costante (come le bande d'ottava di un
    fonometro). Ogni banda prende il massimo dei bin FFT che contiene.

    COMPENSAZIONE: la musica ha spettro circa 'rosa' (potenza ∝ 1/f), che
    su un'analisi a risoluzione costante cala di ≈3 dB/ottava. Si aggiunge
    quindi +pendenza·log2(f_c/1 kHz) dB per avere un display "piatto".

    BARRE — sistema del 1° ordine: salita istantanea, discesa secondo
        dy/dt = −y/τ   ⇒   y(t+Δt) = y(t)·e^(−Δt/τ)
    (è la risposta libera di un circuito RC, o di uno strumento ad ago con
    costante di tempo τ). Usando la soluzione esatta, il risultato non
    dipende dal passo Δt: 30 o 60 fotogrammi al secondo danno la stessa
    curva.

    PICCHI — caduta grave: il picco resta fermo per t_hold, poi cade con
    accelerazione costante g partendo da fermo:
        p(t) = p₀ − ½·g·(t − t_hold)²
    Anche qui si usa la legge oraria esatta, non un'integrazione numerica.
    """

    def __init__(self, n_bande: int = 22, f_min: float = 40.0, f_max: float = 16000.0,
                 tau: float = 0.18, t_hold: float = 0.30, g: float = 3.0,
                 pendenza_db_ottava: float = 3.0, gamma_db: float = 70.0):
        assert n_bande >= 1 and 0 < f_min < f_max and tau > 0 and g > 0
        self.n = n_bande
        self.f_min, self.f_max = f_min, f_max
        self.tau, self.t_hold, self.g = tau, t_hold, g
        self.pendenza, self.gamma_db = pendenza_db_ottava, gamma_db
        self.livelli = [0.0] * n_bande
        self.picchi = [0.0] * n_bande
        self._eta = [0.0] * n_bande        # tempo trascorso dal rilascio del picco
        self._p0 = [0.0] * n_bande         # quota del picco al rilascio

    def bordi(self) -> list[float]:
        r = self.f_max / self.f_min
        return [self.f_min * r ** (i / self.n) for i in range(self.n + 1)]

    def bande(self, moduli: bytes, freq_camp: float, n_fft: int) -> list[float]:
        """Riduce lo spettro FFT (bin lineari) a n bande normalizzate 0…1."""
        nb = len(moduli)
        if nb < 2 or freq_camp <= 0 or n_fft <= 0:
            return [0.0] * self.n
        df = freq_camp / n_fft                      # risoluzione in frequenza
        e = self.bordi()
        uscita = []
        for i in range(self.n):
            lo, hi = e[i], e[i + 1]
            k0 = max(1, math.ceil(lo / df))
            k1 = min(nb - 1, math.floor(hi / df))
            if k1 >= k0:
                v = max(moduli[k0:k1 + 1])
            else:                                   # banda più stretta di un bin
                k = int(limita(round(math.sqrt(lo * hi) / df), 1, nb - 1))
                v = moduli[k]
            if v <= 0:
                uscita.append(0.0)
                continue
            fc = math.sqrt(lo * hi)                 # centro geometrico
            x = v / 255.0 + self.pendenza * math.log2(fc / 1000.0) / self.gamma_db
            uscita.append(limita(x, 0.0, 1.0))
        return uscita

    def aggiorna(self, ingressi, dt: float) -> None:
        """Avanza la balistica di dt secondi. ingressi=None equivale a silenzio."""
        dt = max(0.0, finito(dt))
        smorzamento = math.exp(-dt / self.tau)
        for i in range(self.n):
            x = limita(finito(ingressi[i]) if ingressi else 0.0, 0.0, 1.0)
            y = max(x, self.livelli[i] * smorzamento)
            self.livelli[i] = y
            if y >= self.picchi[i]:
                self.picchi[i] = y
                self._p0[i] = y
                self._eta[i] = 0.0
            else:
                self._eta[i] += dt
                t = self._eta[i] - self.t_hold
                p = self._p0[i] - 0.5 * self.g * t * t if t > 0 else self._p0[i]
                self.picchi[i] = max(p, y)

    def azzera(self) -> None:
        self.livelli = [0.0] * self.n
        self.picchi = [0.0] * self.n
        self._eta = [0.0] * self.n
        self._p0 = [0.0] * self.n


def forma_onda(campioni: bytes, n_punti: int = 88) -> list[float]:
    """Traccia da oscilloscopio da campioni a 8 bit (128 = zero).

    Come in un oscilloscopio reale, la traccia parte dal primo
    attraversamento dello zero con pendenza positiva (trigger sul fronte
    di salita): così una forma d'onda periodica appare ferma sullo schermo
    invece di scorrere.
    """
    N = len(campioni)
    if N < 4 or n_punti < 2:
        return [0.0] * max(n_punti, 0)
    s = [(b - 128) / 128.0 for b in campioni]
    inizio = 0
    for i in range(1, N // 2):
        if s[i - 1] < 0.0 <= s[i]:
            inizio = i
            break
    finestra = N // 2
    passo = finestra / n_punti
    return [s[min(N - 1, inizio + int(j * passo))] for j in range(n_punti)]


def viz_vuota(tipo: str, dati: bytes) -> bool:
    """True se i dati non contengono segnale (tutti zero o tutti 128)."""
    if not dati:
        return True
    if tipo == "w":
        return all(b == 128 for b in dati)
    return not any(dati)


class MonitorViz:
    """Diagnosi dell'analisi audio: se si chiede la visualizzazione durante
    la riproduzione ma per più di `soglia` secondi arrivano solo dati
    vuoti, la si dichiara non disponibile (contenuto protetto, contesto
    audio sospeso, ecc.)."""

    def __init__(self, soglia: float = 3.0):
        self.soglia = soglia
        self.t_vuoto = 0.0

    def aggiorna(self, attesa: bool, presente: bool, dt: float) -> bool:
        if not attesa or presente:
            self.t_vuoto = 0.0
            return True
        self.t_vuoto += max(0.0, finito(dt))
        return self.t_vuoto < self.soglia


# ═══════════════════════════════════════════════════════════════════════════
# 7. Display a sette segmenti
# ═══════════════════════════════════════════════════════════════════════════
#
#      ─a─
#     f   b
#      ─g─
#     e   c
#      ─d─

SEGMENTI = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "", "h": "cefg",
}


def geometria_segmento(seg: str, x: float, y: float, w: float, h: float, t: float):
    """Esagono del segmento `seg` nella cella (x, y, w, h), spessore t.
    Coordinate con y verso il basso. Restituisce una lista di 6 vertici."""
    m = t / 2
    xl, xr = x, x + w
    yt, ym, yb = y, y + h / 2, y + h

    def orizzontale(yy):
        return [(xl + m, yy), (xl + t, yy - m), (xr - t, yy - m),
                (xr - m, yy), (xr - t, yy + m), (xl + t, yy + m)]

    def verticale(xx, y1, y2):
        return [(xx, y1 + m), (xx + m, y1 + t), (xx + m, y2 - t),
                (xx, y2 - m), (xx - m, y2 - t), (xx - m, y1 + t)]

    tabella = {
        "a": lambda: orizzontale(yt + m),
        "g": lambda: orizzontale(ym),
        "d": lambda: orizzontale(yb - m),
        "f": lambda: verticale(xl + m, yt, ym),
        "b": lambda: verticale(xr - m, yt, ym),
        "e": lambda: verticale(xl + m, ym, yb),
        "c": lambda: verticale(xr - m, ym, yb),
    }
    return tabella[seg]()


# ═══════════════════════════════════════════════════════════════════════════
# 8. Geometria delle finestre (unità skin, origine in alto a sinistra)
# ═══════════════════════════════════════════════════════════════════════════

DIM_PRINCIPALE = (275, 116)
PANNELLO_LCD = (8, 18, 98, 46)
RIGA_STATO = (8, 104, 259, 9)
GLIFO_STATO = (12, 24, 8, 8)

# L'ordine conta: in caso di dubbio vince il primo controllo elencato.
LAYOUT_PRINCIPALE = {
    "menu":    (5, 3, 9, 8),
    "riduci":  (247, 3, 9, 8),
    "chiudi":  (259, 3, 9, 8),
    "tempo":   (22, 21, 82, 18),
    "viz":     (12, 43, 88, 17),
    "marquee": (112, 18, 155, 14),
    "info":    (112, 35, 155, 11),
    "volume":  (128, 50, 70, 9),
    "like":    (203, 49, 17, 11),
    "pl":      (224, 49, 20, 11),
    "ric":     (247, 49, 20, 11),
    "seek":    (8, 67, 259, 10),
    "prev":    (8, 82, 22, 18),
    "play":    (31, 82, 22, 18),
    "pause":   (54, 82, 22, 18),
    "stop":    (77, 82, 22, 18),
    "next":    (100, 82, 22, 18),
    "eject":   (128, 83, 22, 16),
    "shuffle": (157, 84, 34, 14),
    "repeat":  (194, 84, 30, 14),
    "logo":    (247, 81, 20, 20),
}

# Controlli che si comportano come pulsanti (azione al rilascio).
PULSANTI_PRINCIPALE = ("menu", "riduci", "chiudi", "like", "pl", "ric", "prev", "play",
                       "pause", "stop", "next", "eject", "shuffle", "repeat", "logo")

LARGHEZZA_PLAYLIST = 275
ALTEZZA_PLAYLIST_MIN = 70
ALTEZZA_RIGA = 10


def layout_playlist(h: float) -> dict:
    h = max(ALTEZZA_PLAYLIST_MIN, h)
    return {
        "chiudi": (259, 3, 9, 8),
        "presa":  (263, h - 10, 12, 10),
        "lista":  (8, 18, 259, h - 18 - 22),
        "piede":  (8, h - 19, 250, 13),
    }


def righe_visibili(h: float) -> int:
    lista_h = layout_playlist(h)["lista"][3]
    return max(1, int((lista_h - 2) // ALTEZZA_RIGA))


# Pannello di ricerca: stessa larghezza del deck, alto quanto deck + playlist.
LARGHEZZA_RICERCA = 275
ALTEZZA_RICERCA_MIN = 120
ALTEZZA_RICERCA_PREDEFINITA = DIM_PRINCIPALE[1] + 150
PULSANTI_RICERCA = ("chiudi", "cerca", "brani", "album", "playlist", "libreria")


def layout_ricerca(h: float) -> dict:
    h = max(ALTEZZA_RICERCA_MIN, h)
    return {
        "chiudi":   (259, 3, 9, 8),
        "campo":    (8, 18, 215, 13),
        "cerca":    (226, 18, 41, 13),
        "brani":    (8, 35, 50, 12),
        "album":    (61, 35, 50, 12),
        "playlist": (114, 35, 58, 12),
        "libreria": (217, 35, 50, 12),
        "presa":    (263, h - 10, 12, 10),
        "lista":    (8, 51, 259, h - 51 - 22),
        "piede":    (8, h - 19, 250, 13),
    }


def righe_visibili_ricerca(h: float) -> int:
    lista_h = layout_ricerca(h)["lista"][3]
    return max(1, int((lista_h - 2) // ALTEZZA_RIGA))


def dentro(rett, x: float, y: float) -> bool:
    rx, ry, rw, rh = rett
    return rx <= x < rx + rw and ry <= y < ry + rh


def colpito(layout: dict, x: float, y: float):
    """Hit-test: nome del primo controllo che contiene il punto, o None."""
    for nome, r in layout.items():
        if dentro(r, x, y):
            return nome
    return None


def frazione_da_x(x: float, x0: float, w: float) -> float:
    """Posizione orizzontale → frazione 0…1 di un cursore."""
    return limita((x - x0) / w, 0.0, 1.0) if w > 0 else 0.0


def si_sovrappongono(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def aggancia_sotto(mobile, ancora, soglia: float = 12.0):
    """Aggancio magnetico (coordinate schermo AppKit, y verso l'alto):
    se il bordo superiore di `mobile` è vicino al bordo inferiore di
    `ancora` e i bordi sinistri quasi allineati, restituisce l'origine
    'agganciata'; altrimenti None."""
    mx, my, mw, mh = mobile
    ax, ay, aw, ah = ancora
    tx, ty = ax, ay - mh
    if abs(mx - tx) <= soglia and abs(my - ty) <= soglia:
        return (tx, ty)
    return None


def aggancia_destra(mobile, ancora, soglia: float = 12.0):
    """Aggancio a destra di `ancora` con i bordi superiori allineati
    (coordinate schermo AppKit: il bordo superiore è y + h)."""
    mx, my, mw, mh = mobile
    ax, ay, aw, ah = ancora
    tx, ty = ax + aw, ay + ah - mh
    if abs(mx - tx) <= soglia and abs(my - ty) <= soglia:
        return (tx, ty)
    return None


def aggancia_schermo(rett, schermo, soglia: float = 10.0):
    """Aggancio ai bordi dell'area utile dello schermo."""
    x, y, w, h = rett
    sx, sy, sw, sh = schermo
    if abs(x - sx) <= soglia:
        x = sx
    elif abs(x + w - (sx + sw)) <= soglia:
        x = sx + sw - w
    if abs(y - sy) <= soglia:
        y = sy
    elif abs(y + h - (sy + sh)) <= soglia:
        y = sy + sh - h
    return (x, y)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Tastiera (scorciatoie dei lettori classici)
# ═══════════════════════════════════════════════════════════════════════════

TASTI = {
    "z": "prev", "x": "play", "c": "pause", "v": "stop", "b": "next",
    " ": "toggle", "l": "eject", "s": "shuffle", "r": "repeat",
}
# keyCode macOS delle frecce → (azione, argomento)
FRECCE = {
    123: ("seek_rel", -5.0),   # ←
    124: ("seek_rel", +5.0),   # →
    125: ("vol_rel", -5.0),    # ↓
    126: ("vol_rel", +5.0),    # ↑
}


# ═══════════════════════════════════════════════════════════════════════════
# 10. Ponte JavaScript
# ═══════════════════════════════════════════════════════════════════════════

COMANDI = frozenset({
    "toggle", "riprendi", "sospendi", "play", "stop", "next", "prev",
    "seek", "volume", "shuffle", "repeat", "like", "indice", "cerca", "ferma_radio",
})


# Azioni gestite dal lato nativo (finestre, display) e non dalla pagina.
AZIONI_NATIVE = frozenset({
    "pl", "ric", "lib", "eject", "seek_rel", "vol_rel", "tempo", "viz", "riduci", "chiudi", "logo", "menu",
})
# Azioni di modifica della playlist (tastiera, trascinamento, menu contestuale).
AZIONI_PLAYLIST = frozenset({
    "pl_riproduci", "pl_dopo", "pl_fondo", "pl_su", "pl_giu", "pl_varco",
    "pl_rimuovi", "pl_successivi", "pl_annulla", "pl_tutto", "pl_radio",
})
# Azioni dell'interfaccia che corrispondono a un comando con altro nome:
# il tasto Pausa dei lettori classici commuta pausa/ripresa.
ALIAS_COMANDI = {"pause": "toggle"}


def comando_per_azione(azione: str):
    """Comando del ponte associato a un'azione dell'interfaccia, o None."""
    c = ALIAS_COMANDI.get(azione, azione)
    return c if c in COMANDI else None


def _argomento_js(arg):
    if arg is None or isinstance(arg, (bool, int)):
        return arg
    if isinstance(arg, float):
        return finito(arg)
    if isinstance(arg, str):
        return arg[:500]
    raise TypeError("argomento non ammesso")


def comando_js(nome: str, arg=None) -> str:
    """Espressione JS che invoca un comando del ponte.

    Gli argomenti passano per json.dumps: qualunque stringa diventa un
    letterale JS correttamente quotato, quindi non è possibile 'iniettare'
    codice attraverso un argomento.
    """
    if nome not in COMANDI:
        raise ValueError(f"comando sconosciuto: {nome!r}")
    a = json.dumps(_argomento_js(arg))
    return f"window.__bia ? window.__bia.cmd({json.dumps(nome)}, {a}) : false"


def js_stato(modo_viz: str) -> str:
    codice = modo_viz if modo_viz in ("f", "w") else ""
    return f"window.__bia ? window.__bia.stato({json.dumps(codice)}) : ''"


def js_coda(firma: str) -> str:
    return f"window.__bia ? window.__bia.coda({json.dumps(_stringa(firma, 64))}) : ''"


# Corpi per callAsyncJavaScript: gli argomenti arrivano come variabili JS
# (dizionario → nomi), quindi non vengono mai concatenati nel codice.
JS_CERCA = "return await window.__bia.cerca(testo, parametri);"
JS_ACCODA = "return await window.__bia.accoda(bersaglio, posizione, salta);"
# Modifica della coda: restituiscono il numero di operazioni riuscite (-1 se
# il ponte non è ancora caricato).
JS_SPOSTA = "return window.__bia ? await window.__bia.sposta(mosse) : -1;"
JS_RIMUOVI = "return window.__bia ? window.__bia.rimuovi(voci) : -1;"
JS_RIPRISTINA = "return window.__bia ? await window.__bia.ripristina() : -1;"


# ═══════════════════════════════════════════════════════════════════════════
# 10 bis. Ricerca in YouTube Music
# ═══════════════════════════════════════════════════════════════════════════
#
# La struttura delle risposte di '/search' (InnerTube) è quella decodificata
# dalla libreria ytmusicapi (licenza MIT); i parametri dei filtri sono gli
# stessi che essa invia: prefisso "EgWKAQ" + codice del tipo + suffisso.

FILTRI_RICERCA = ("brani", "album", "playlist")
PARAMETRI_RICERCA = {
    "brani":    "EgWKAQIIAWoMEA4QChADEAQQCRAF",
    "album":    "EgWKAQIYAWoMEA4QChADEAQQCRAF",
    "playlist": "Eg-KAQwIABAAGAAgACgBMABqChAEEAMQCRAFEAo%3D",
}
TIPO_PER_FILTRO = {"brani": "brano", "album": "album", "playlist": "playlist"}

# Inserimento in coda: (posizione per YouTube Music, salto al brano inserito).
MODI_ACCODA = {
    "ora":  ("INSERT_AFTER_CURRENT_VIDEO", True),    # riproduci subito
    "dopo": ("INSERT_AFTER_CURRENT_VIDEO", False),   # riproduci dopo il brano attuale
    "fine": ("INSERT_AT_END", False),                # aggiungi in fondo alla coda
}

_RE_DURATA = re.compile(r"^(\d+:)*\d+:\d+$")
_RE_CONTEGGIO = re.compile(r"^\d[\d.,]*\s*(brani|brano|songs?|tracks?|tracce|canzoni|video|episodi)\b", re.I)
SEPARATORE_RUN = " • "


@dataclass
class RisultatoRicerca:
    tipo: str                   # 'brano' | 'album' | 'playlist'
    titolo: str
    artista: str = ""
    album: str = ""
    anno: str = ""
    durata_testo: str = ""
    conteggio: str = ""
    video_id: str = ""
    playlist_id: str = ""
    disponibile: bool = True

    def etichetta(self) -> str:
        return f"{self.titolo} — {self.artista}" if self.artista else self.titolo

    def colonna_destra(self) -> str:
        if self.tipo == "brano":
            return self.durata_testo
        if self.tipo == "album":
            return self.anno
        return self.conteggio

    def bersaglio(self) -> dict:
        return {"videoId": self.video_id} if self.tipo == "brano" else {"playlistId": self.playlist_id}


def _naviga(o, *chiavi):
    """Accesso protetto a strutture annidate: None al primo passo mancante."""
    for k in chiavi:
        if isinstance(k, int):
            if not isinstance(o, list) or not -len(o) <= k < len(o):
                return None
        elif not isinstance(o, dict):
            return None
        o = o[k] if isinstance(k, int) else o.get(k)
        if o is None:
            return None
    return o


def _testo_runs(runs) -> str:
    if not isinstance(runs, list):
        return ""
    return _stringa("".join(r.get("text", "") for r in runs if isinstance(r, dict)))


def _tipo_run(run: dict) -> str:
    """Classificazione di una 'run' della seconda colonna (come ytmusicapi)."""
    testo = _stringa(run.get("text"))
    browse = _naviga(run, "navigationEndpoint", "browseEndpoint", "browseId")
    if "navigationEndpoint" in run:
        return "album" if isinstance(browse, str) and browse.startswith("MPRE") else "artista"
    if _RE_DURATA.match(testo):
        return "durata"
    if _RE_ANNO.match(testo):
        return "anno"
    if _RE_CONTEGGIO.match(testo):
        return "conteggio"
    if _RE_STATISTICHE.search(testo):
        return "visualizzazioni"
    return "artista"


def _riga_ricerca(r: dict):
    colonne = r.get("flexColumns")
    if not isinstance(colonne, list) or not colonne:
        return None

    def runs(i):
        v = _naviga(colonne, i, "musicResponsiveListItemFlexColumnRenderer", "text", "runs")
        return v if isinstance(v, list) else []

    titolo = _testo_runs(runs(0))
    if not titolo:
        return None
    gioca = _naviga(r, "overlay", "musicItemThumbnailOverlayRenderer", "content",
                    "musicPlayButtonRenderer", "playNavigationEndpoint") or {}
    video_id = _naviga(gioca, "watchEndpoint", "videoId") or _naviga(r, "playlistItemData", "videoId") or ""
    playlist_id = (_naviga(gioca, "watchPlaylistEndpoint", "playlistId")
                   or _naviga(gioca, "watchEndpoint", "playlistId") or "")
    browse = _naviga(r, "navigationEndpoint", "browseEndpoint", "browseId") or ""
    if not isinstance(browse, str):
        browse = ""
    if browse.startswith("MPRE"):
        tipo = "album"
    elif browse.startswith(("VL", "RD", "PL", "OLAK")):
        tipo = "playlist"
        if not playlist_id and browse.startswith("VL"):
            playlist_id = browse[2:]
    elif browse.startswith(("UC", "MPLA")):
        return None                                     # artisti: non riproducibili da qui
    elif video_id:
        tipo = "brano"
    else:
        return None

    # Seconda (ed eventuale terza) colonna: run pari = dati, dispari = " • ".
    info = runs(1) + ([{"text": ""}] + runs(2) if runs(2) else [])
    if (len(info) > 2 and "navigationEndpoint" not in info[0] and _tipo_run(info[0]) == "artista"
            and _stringa(info[1].get("text")) == _stringa(SEPARATORE_RUN)
            and _tipo_run(info[2]) in ("artista", "durata", "visualizzazioni", "anno")):
        info = info[2:]                                 # salta l'etichetta di tipo ("Brano", "Album")
    risultato = RisultatoRicerca(tipo=tipo, titolo=titolo, video_id=_stringa(video_id, 32),
                                 playlist_id=_stringa(playlist_id, 80))
    artisti = []
    for i, run in enumerate(info):
        if i % 2 or not isinstance(run, dict):
            continue
        t, testo = _tipo_run(run), _stringa(run.get("text"))
        if not testo:
            continue
        if t == "artista":
            artisti.append(testo)
        elif t == "album":
            risultato.album = testo
        elif t == "durata":
            risultato.durata_testo = testo
        elif t == "anno":
            risultato.anno = testo
        elif t == "conteggio":
            risultato.conteggio = testo
    risultato.artista = ", ".join(artisti)
    if not risultato.durata_testo:                      # pagine album: durata in colonna fissa
        fissa = _naviga(r, "fixedColumns", 0, "musicResponsiveListItemFixedColumnRenderer", "text", "runs")
        d = _testo_runs(fissa)
        if _RE_DURATA.match(d):
            risultato.durata_testo = d
    risultato.disponibile = r.get("musicItemRendererDisplayPolicy") != "MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT"
    if risultato.tipo != "brano" and not risultato.playlist_id:
        return None
    return risultato


def analizza_ricerca(testo, filtro: str, massimo: int = 100) -> list:
    """Risultati riproducibili di una risposta '/search', filtrati per tipo,
    senza doppioni. Qualunque input malformato produce una lista vuota."""
    try:
        d = json.loads(testo) if isinstance(testo, str) else None
    except (ValueError, TypeError):
        return []
    contenuti = _naviga(d, "contents")
    radice = _naviga(contenuti, "tabbedSearchResultsRenderer", "tabs", 0, "tabRenderer", "content") or contenuti
    sezioni = _naviga(radice, "sectionListRenderer", "contents")
    if not isinstance(sezioni, list):
        return []
    righe = []
    for sez in sezioni:
        for chiave in ("musicCardShelfRenderer", "musicShelfRenderer", "itemSectionRenderer"):
            elementi = _naviga(sez, chiave, "contents")
            if isinstance(elementi, list):
                righe += [e["musicResponsiveListItemRenderer"] for e in elementi
                          if isinstance(e, dict) and isinstance(e.get("musicResponsiveListItemRenderer"), dict)]
    tipo_voluto = TIPO_PER_FILTRO.get(filtro)
    risultati, visti = [], set()
    for r in righe:
        x = _riga_ricerca(r)
        if x is None or (tipo_voluto and x.tipo != tipo_voluto):
            continue
        chiave = x.video_id if x.tipo == "brano" else x.playlist_id
        if chiave in visti:
            continue
        visti.add(chiave)
        risultati.append(x)
        if len(risultati) >= massimo:
            break
    return risultati


def argomenti_ricerca(testo: str, filtro: str) -> dict:
    return {"testo": _stringa(testo, 200), "parametri": PARAMETRI_RICERCA.get(filtro, "")}


def argomenti_accoda(risultato: RisultatoRicerca, modo: str) -> dict:
    posizione, salta = MODI_ACCODA[modo]
    return {"bersaglio": risultato.bersaglio(), "posizione": posizione, "salta": salta}


_RE_HOST_INTERNO = re.compile(
    r"(^|\.)(youtube\.com|youtu\.be|ytimg\.com|ggpht\.com|gstatic\.com|"
    r"googleusercontent\.com|google\.[a-z]{2,3}(\.[a-z]{2})?)$"
)


def host_interno(host: str) -> bool:
    """Host che devono restare nella Libreria (YouTube e accesso Google);
    tutti gli altri link vengono aperti nel browser predefinito."""
    return bool(_RE_HOST_INTERNO.search((host or "").lower().rstrip(".")))


# ═══════════════════════════════════════════════════════════════════════════
# 11. Impostazioni persistenti
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Impostazioni:
    versione: str = __version__
    origine_principale: list | None = None     # [x, y] in punti schermo
    playlist_visibile: bool = True
    playlist_agganciata: bool = True
    origine_playlist: list | None = None
    altezza_playlist: float = 150.0            # unità skin
    libreria_visibile: bool = True
    cornice_libreria: list | None = None       # [x, y, w, h]
    modo_viz: str = "spettro"
    tempo_rimanente: bool = False
    primo_piano: bool = False
    scala: float = SCALA_PREDEFINITA
    ricerca_visibile: bool = True
    ricerca_agganciata: bool = True
    origine_ricerca: list | None = None
    altezza_ricerca: float = float(ALTEZZA_RICERCA_PREDEFINITA)
    filtro_ricerca: str = "brani"


def percorso_impostazioni() -> Path:
    return Path.home() / "Library" / "Application Support" / NOME_APP / "impostazioni.json"


def _lista_numeri(v, n):
    if isinstance(v, list) and len(v) == n and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
        return [float(x) for x in v]
    return None


def carica_impostazioni(percorso: Path) -> Impostazioni:
    """Lettura tollerante: file assente o corrotto → valori predefiniti;
    ogni campo è validato per tipo singolarmente."""
    imp = Impostazioni()
    try:
        d = json.loads(Path(percorso).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return imp
    if not isinstance(d, dict):
        return imp
    for f in fields(Impostazioni):
        if f.name not in d or f.name == "versione":
            continue
        v = d[f.name]
        predef = getattr(imp, f.name)
        if f.name in ("origine_principale", "origine_playlist", "origine_ricerca"):
            setattr(imp, f.name, _lista_numeri(v, 2))
        elif f.name == "cornice_libreria":
            setattr(imp, f.name, _lista_numeri(v, 4))
        elif isinstance(predef, bool):
            if isinstance(v, bool):
                setattr(imp, f.name, v)
        elif isinstance(predef, float):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(imp, f.name, finito(v, predef))
        elif f.name == "modo_viz":
            if v in MODI_VIZ:
                imp.modo_viz = v
        elif f.name == "filtro_ricerca":
            if v in FILTRI_RICERCA:
                imp.filtro_ricerca = v
    imp.altezza_playlist = limita(imp.altezza_playlist, ALTEZZA_PLAYLIST_MIN, 600)
    imp.altezza_ricerca = limita(imp.altezza_ricerca, ALTEZZA_RICERCA_MIN, 800)
    if imp.scala not in SCALE:                       # solo i valori previsti
        imp.scala = SCALA_PREDEFINITA
    return imp


def salva_impostazioni(imp: Impostazioni, percorso: Path) -> None:
    """Scrittura atomica: file temporaneo + os.replace, così un'interruzione
    a metà non lascia mai un JSON troncato."""
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    dati = json.dumps(asdict(imp), indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=percorso.parent, prefix=".imp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dati)
        os.replace(tmp, percorso)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 12. Autotest
# ═══════════════════════════════════════════════════════════════════════════

def _autotest() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # — numeri e tempo —
    ok(finito(float("nan"), 7) == 7 and finito("x", 1) == 1 and finito("2.5") == 2.5, "finito")
    ok(limita(5, 0, 1) == 1 and limita(-1, 0, 1) == 0, "limita")
    ok(fmt_durata(185) == "3:05" and fmt_durata(3723) == "1:02:03" and fmt_durata(-4) == "0:00", "fmt_durata")
    for s in (0, 59, 61, 185, 3599, 3723, 86399):
        ok(durata_secondi(fmt_durata(s)) == s, f"round-trip durata {s}")
    ok(durata_secondi("abc") == 0 and durata_secondi("1:2:3:4") == 0, "durata non valida")
    ok(tempo_lcd(125, 300, False) == (False, "02:05", False), "lcd trascorso")
    ok(tempo_lcd(125, 300, True) == (True, "02:55", False), "lcd rimanente")
    ok(tempo_lcd(6000, 0, False) == (False, "01:40", True), "lcd ore")
    ok(tempo_lcd(float("nan"), None, True) == (False, "00:00", False), "lcd dati sporchi")
    ok(lampeggio_visibile(0.2) and not lampeggio_visibile(0.7), "lampeggio")
    ok(tronca("abcdef", 4) == "abc…" and tronca("ab", 4) == "ab", "tronca")

    # — byline e copertine —
    ok(analizza_byline("Pink Floyd • The Wall • 1979") == ("Pink Floyd", "The Wall", "1979"), "byline brano")
    ok(analizza_byline("Artista • 1,2 Mln di visualizzazioni • 34.000 Mi piace") == ("Artista", "", ""), "byline video")
    ok(analizza_byline("Solo Artista") == ("Solo Artista", "", ""), "byline breve")
    ok(analizza_byline("") == ("", "", ""), "byline vuota")
    u = "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"
    ok(ingrandisci_copertina(u) == "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj", "copertina wh")
    ok(ingrandisci_copertina("https://x/y=s60") == "https://x/y=s544", "copertina s")
    ok(ingrandisci_copertina("javascript:alert(1)") == "", "copertina non http")

    # — decodifica stato —
    vuoto = StatoLettore.da_json("non json")
    ok(not vuoto.pronto and vuoto.codice == -1, "json non valido")
    ok(not StatoLettore.da_json(None).pronto, "None")
    grezzo = bytes(range(256)) * 4
    st = StatoLettore.da_json(json.dumps({
        "pronto": 1, "st": 1, "cur": 400, "dur": 300, "vol": 140, "titolo": "  Comfortably   Numb ",
        "byline": "Pink Floyd • The Wall • 1979", "rip": "one", "shuf": True, "like": "LIKE",
        "vk": "f", "viz": base64.b64encode(grezzo).decode(), "fs": 48000, "nfft": 2048,
    }))
    ok(st.pronto and st.in_riproduzione and st.posizione == 300 and st.volume == 100, "stato saturato")
    ok(st.titolo == "Comfortably Numb" and st.album == "The Wall", "stato testi")
    ok(st.ripetizione == "ONE" and st.casuale and st.mi_piace, "stato flag")
    ok(st.viz == grezzo and st.viz_tipo == "f", "round-trip base64 della FFT")
    st2 = StatoLettore.da_json('{"pronto":1,"st":2,"cur":NaN,"dur":Infinity,"vk":"f","viz":"@@@"}')
    ok(st2.in_pausa and st2.posizione == 0 and st2.durata == 0 and st2.viz == b"", "NaN/inf/base64 errato")

    a = StatoLettore(pronto=True, codice=1, posizione=10, durata=200, titolo="A")
    b = StatoLettore(pronto=True, codice=1, posizione=10.5, durata=200, titolo="A")
    ok(np_da_aggiornare(None, a, 0.5), "np primo")
    ok(not np_da_aggiornare(a, b, 0.5), "np estrapolazione valida")
    b.posizione = 80
    ok(np_da_aggiornare(a, b, 0.5), "np seek")
    b.posizione, b.codice = 10.5, 2
    ok(np_da_aggiornare(a, b, 0.5), "np pausa")

    # — coda —
    c = Coda()
    voci = [{"t": "Uno", "a": "X", "d": "3:00", "s": False, "id": "a"},
            {"t": "Due", "a": "Y", "d": "4:30", "s": True, "id": "b"},
            {"t": "Tre", "a": "", "d": "1:02:00", "s": False, "id": "c"}]
    ok(c.aggiorna_da_json(json.dumps({"firma": "f1", "voci": voci})), "coda cambiata")
    ok(c.indice_corrente == 1 and c.durata_totale() == 180 + 270 + 3720, "coda indice/durata")
    ok(c.riepilogo() == "3 brani · 1:09:30", "coda riepilogo")
    ok(not c.aggiorna_da_json(json.dumps({"firma": "f1", "uguale": 1})), "coda invariata")
    ok(c.brani[2].etichetta(2) == "3. Tre" and c.brani[0].etichetta(0) == "1. X - Uno", "etichette")
    ok(not c.aggiorna_da_json("rotto") and len(c.brani) == 3, "coda json rotto ignorato")
    ok(scroll_visibile(1, 0, 10, 50) == 0, "scroll già visibile")
    ok(scroll_visibile(30, 0, 10, 50) == 22, "scroll in avanti")
    ok(scroll_visibile(5, 20, 10, 50) == 4, "scroll indietro")
    ok(scroll_visibile(49, 0, 10, 50) == 40, "scroll limitato")
    ok(c.id_corrente == "b" and not c.modificabile, "brano corrente e coda non modificabile senza 'm'")
    voci_m = voci + [{"x": 1}]
    ok(c.aggiorna_da_json(json.dumps({"firma": "f2", "voci": voci_m, "m": 1})) and c.modificabile
       and c.brani[3].segnaposto and c.brani[3].etichetta(3) == "4. —", "segnaposto e coda modificabile")

    # — modifica della coda: verifica esaustiva dello spostamento a blocco —
    # Per ogni n ≤ 7, ogni sottoinsieme S e ogni varco g, il risultato deve
    # essere: gli elementi non selezionati prima di g, il blocco S nel suo
    # ordine, gli altri non selezionati; con al più |S| mosse, la selezione
    # finale sul blocco e le mosse inverse che ripristinano l'ordine.
    import itertools
    casi = 0
    for nn in range(0, 8):
        base = list(range(nn))
        for k in range(nn + 1):
            for S in itertools.combinations(base, k):
                for g in range(nn + 1):
                    m = mosse_blocco(nn, S, g)
                    r = applica_mosse(base, m)
                    atteso = [i for i in base if i < g and i not in S] + list(S) + \
                             [i for i in base if i >= g and i not in S]
                    sel = selezione_dopo_blocco(nn, S, g)
                    assert r == atteso, (nn, S, g, m, r)
                    assert len(m) <= len(S) and [r[i] for i in sel] == list(S), (nn, S, g)
                    assert applica_mosse(r, inverti_mosse(m)) == base, (nn, S, g)
                    casi += 1
    ok(casi == sum(2 ** nn * (nn + 1) for nn in range(8)), f"spostamento a blocco: {casi} casi esaustivi")
    ok(mosse_blocco(6, (1, 2), 3) == [] and mosse_blocco(6, (4,), 1) == [(4, 1)]
       and mosse_blocco(6, (0, 5), 3) == [(0, 2), (5, 3)], "mosse a blocco: casi notevoli")
    for nn in range(1, 8):
        for k in range(1, nn + 1):
            for S in itertools.combinations(range(nn), k):
                for f, verso in ((mosse_su, -1), (mosse_giu, +1)):
                    m, sel = f(S, nn)
                    bordo = (S[0] == 0) if verso < 0 else (S[-1] == nn - 1)
                    if bordo:
                        assert m == [] and sel == S
                        continue
                    r = applica_mosse(range(nn), m)
                    assert [r[i] for i in sel] == list(S) and all(s_ + verso == t for s_, t in zip(S, sel))
                    assert sorted(r) == list(range(nn)) and applica_mosse(r, inverti_mosse(m)) == list(range(nn))
    ok(mosse_su((2, 5), 7) == ([(2, 1), (5, 4)], (1, 4)) and mosse_giu((2, 3), 5) == ([(3, 4), (2, 3)], (3, 4)),
       "⌥↑ ⌥↓: ogni selezionato si sposta di una riga")
    ok(selezione_dopo_clic((), None, 4) == ((4,), 4) and selezione_dopo_clic((4,), 4, 7, estendi=True) == ((4, 5, 6, 7), 4)
       and selezione_dopo_clic((4, 5), 4, 2, estendi=True) == ((2, 3, 4), 4)
       and selezione_dopo_clic((4, 5), 4, 5, alterna=True) == ((4,), 5)
       and selezione_dopo_clic((4,), 4, 9, alterna=True) == ((4, 9), 9), "selezione: clic, ⇧-clic, ⌘-clic")
    ok(naviga_selezione(None, None, +1, 5, partenza=2) == ((3,), 3, 3) and naviga_selezione(None, None, -1, 5) == ((4,), 4, 4)
       and naviga_selezione(3, 3, +1, 5, estendi=True) == ((3, 4), 3, 4)
       and naviga_selezione(3, 4, +1, 5, estendi=True) == ((3, 4), 3, 4)
       and naviga_selezione(3, 3, -1, 5, estendi=True) == ((2, 3), 3, 2) and naviga_selezione(0, 0, 1, 0) == ((), None, None),
       "selezione da tastiera (↑ ↓, ⇧)")
    ok([varco_da_y(18 + 1 + y_, 18, 3, 20, 8) for y_ in (0, 4.9, 5.1, 26, 200, -50)] == [3, 3, 4, 6, 11, 3]
       and varco_da_y(100, 18, 0, 2, 8) == 2, "varco dal puntatore (arrotondato, limitato al visibile)")
    def brano(id_, corrente=False):
        return BranoCoda(id_, "", "", 0, corrente, id_)
    bb = [brano("a"), brano("b", True), brano("c"), brano("d"), brano("a")]
    ok(rimovibili(bb, (4, 1, 3, 9)) == (3, 4) and rimovibili(bb, (1,)) == (), "il brano in riproduzione non si rimuove")
    ok(selezione_dopo_rimozione(5, (3, 4)) == (2,) and selezione_dopo_rimozione(5, (1,)) == (1,)
       and selezione_dopo_rimozione(2, (0, 1)) == (), "selezione dopo la rimozione")
    ok(argomenti_sposta(bb, [(0, 2), (4, 0)]) == {"mosse": [[0, 2, "a"], [4, 0, "a"]]}
       and argomenti_sposta(bb, [(3, 0), (1, 3)]) == {"mosse": [[3, 0, "d"], [1, 3, "a"]]}, "videoId attesi a ogni mossa")
    ok(argomenti_rimuovi(bb, (3, 0, 7)) == {"voci": [[3, "d"], [0, "a"]]}, "rimozione in ordine decrescente")
    cc_ = Coda()
    cc_.brani, cc_.firma = list(bb), "f"
    cc_.applica_mosse([(0, 2)])
    ok([b_.video_id for b_ in cc_.brani] == ["b", "c", "a", "d", "a"] and cc_.firma == "", "aggiornamento immediato: mosse")
    tolti = cc_.rimuovi((3, 1))
    ok([b_.video_id for b_ in cc_.brani] == ["b", "a", "a"] and [i for i, _ in tolti] == [1, 3], "aggiornamento immediato: rimozione")
    cc_.reinserisci(tolti)
    ok([b_.video_id for b_ in cc_.brani] == ["b", "c", "a", "d", "a"], "reinserimento (annulla)")
    ok(testo_rimozione(1, False) == "1 brano rimosso · ⌘Z annulla"
       and testo_rimozione(3, True).startswith("3 brani rimossi (non quello")
       and testo_rimozione(2, False, True) == "2 brani rimossi · radio fermata · ⌘Z annulla", "messaggi di rimozione")
    ok(piede_playlist(c, 1) == c.riepilogo() and piede_playlist(c, 3).endswith("· 3 selezionati"), "piede della playlist")
    cr = Coda()
    ok(cr.aggiorna_da_json(json.dumps({"firma": "r", "voci": voci, "m": 1, "inf": 1, "via": "nativa"}))
       and cr.radio and cr.via == "nativa" and cr.errore == "" and piede_playlist(cr, 1) == cr.riepilogo() + " · radio"
       and piede_playlist(cr, 3).endswith("radio · 3 selezionati"), "coda radio: indicata nel piede; via delle modifiche")
    ok(comando_js("ferma_radio") == 'window.__bia ? window.__bia.cmd("ferma_radio", null) : false', "comando ferma radio")
    ok(all("await" in j_ for j_ in (JS_SPOSTA, JS_RIPRISTINA)) and "await" not in JS_RIMUOVI, "corpi asincroni dove serve")
    ok(AZIONI_PLAYLIST.isdisjoint(AZIONI_NATIVE) and AZIONI_PLAYLIST.isdisjoint(COMANDI)
       and all(a_.startswith("pl_") for a_ in AZIONI_PLAYLIST), "azioni della playlist distinte")

    # — marquee —
    t = "Artista - Titolo molto lungo che non entra"
    per = len(t) + len(SEPARATORE_MARQUEE)
    ok(all(len(marquee(t, 20, k)) == 20 for k in range(per * 2)), "marquee larghezza costante")
    ok(marquee(t, 20, 0) == marquee(t, 20, per) == marquee(t, 20, 7 * per), "marquee periodico")
    ok(marquee("corto", 20, 13) == "corto", "marquee fermo")
    ok(testo_marquee(st, 4).startswith("4. Pink Floyd - Comfortably Numb (5:00)"), "testo marquee")

    # — spettro: bande —
    sp = Spettro()
    e = sp.bordi()
    ok(abs(e[0] - 40) < 1e-9 and abs(e[-1] - 16000) < 1e-6, "bordi estremi")
    rapporti = [e[i + 1] / e[i] for i in range(sp.n)]
    ok(max(rapporti) - min(rapporti) < 1e-9, "bordi in progressione geometrica")
    ok(sp.bande(bytes(1024), 48000, 2048) == [0.0] * sp.n, "silenzio → bande nulle")
    fs, nfft = 48000.0, 2048
    df = fs / nfft
    for f0 in (100.0, 1000.0, 5000.0):
        mod = bytearray(1024)
        mod[round(f0 / df)] = 200
        bnd = sp.bande(bytes(mod), fs, nfft)
        attesa = next(i for i in range(sp.n) if e[i] <= round(f0 / df) * df < e[i + 1])
        ok(bnd.index(max(bnd)) == attesa and max(bnd) > 0, f"tono a {f0:.0f} Hz nella banda giusta")
    piatto = sp.bande(bytes([180]) * 1024, fs, nfft)
    ok(all(piatto[i] <= piatto[i + 1] + 1e-12 for i in range(sp.n - 1)), "compensazione rosa crescente")

    # — spettro: balistica —
    sp = Spettro(n_bande=1, tau=0.2, t_hold=0.3, g=3.0)
    sp.aggiorna([1.0], 0.0)
    sp.aggiorna([0.0], 0.2)
    ok(abs(sp.livelli[0] - math.exp(-1)) < 1e-12, "barra: y(τ) = e^-1")
    sp = Spettro(n_bande=1, tau=0.2, t_hold=0.3, g=3.0)
    sp.aggiorna([1.0], 0.0)
    sp.aggiorna([0.0], 0.29)
    ok(sp.picchi[0] == 1.0, "picco fermo durante t_hold")
    sp.aggiorna([0.0], 0.21)   # t = 0.5 s → caduta da 0.2 s
    ok(abs(sp.picchi[0] - (1 - 0.5 * 3.0 * 0.2 ** 2)) < 1e-12, "picco: legge oraria ½gt²")
    ok(sp.picchi[0] >= sp.livelli[0], "picco mai sotto la barra")

    def simula(dt, durata=1.0):
        s = Spettro(n_bande=3)
        s.aggiorna([1.0, 0.6, 0.2], 0.0)
        for _ in range(round(durata / dt)):
            s.aggiorna(None, dt)
        return s
    s30, s60, s120 = simula(1 / 30), simula(1 / 60), simula(1 / 120)
    ok(all(abs(p - q) < 1e-9 for p, q in zip(s30.livelli + s30.picchi, s60.livelli + s60.picchi)),
       "indipendenza dal passo temporale (30 vs 60 fps)")
    ok(all(abs(p - q) < 1e-9 for p, q in zip(s60.livelli + s60.picchi, s120.livelli + s120.picchi)),
       "indipendenza dal passo temporale (60 vs 120 fps)")
    r1, r2 = simula(1 / 30), simula(1 / 30)
    ok(r1.livelli == r2.livelli and r1.picchi == r2.picchi, "determinismo")

    # — oscilloscopio —
    periodo, fase = 100, 37
    seno = bytes(int(128 + 100 * math.sin(2 * math.pi * (i + fase) / periodo)) for i in range(2048))
    tr = forma_onda(seno, 88)
    ok(len(tr) == 88 and abs(tr[0]) < 0.1 and tr[1] > tr[0], "trigger sul fronte di salita")
    ok(all(-1.0 <= v <= 1.0 for v in tr), "traccia normalizzata")
    ok(forma_onda(b"", 10) == [0.0] * 10, "oscilloscopio senza dati")
    ok(viz_vuota("f", bytes(10)) and viz_vuota("w", bytes([128]) * 10) and not viz_vuota("f", b"\x00\x05"), "viz vuota")
    mv = MonitorViz(soglia=3.0)
    ok(all(mv.aggiorna(True, False, 1.0) for _ in range(2)) and not mv.aggiorna(True, False, 1.0), "monitor viz")
    ok(mv.aggiorna(True, True, 0.1), "monitor viz si riprende")

    # — sette segmenti —
    ok(len(SEGMENTI["8"]) == 7 and SEGMENTI["1"] == "bc", "tabella segmenti")
    for sg in "abcdefg":
        pts = geometria_segmento(sg, 10, 20, 10, 15, 1.8)
        ok(len(pts) == 6 and all(10 <= px <= 20 and 20 <= py <= 35 for px, py in pts), f"segmento {sg} nella cella")

    # — layout —
    W, H = DIM_PRINCIPALE
    nomi = list(LAYOUT_PRINCIPALE)
    for nome, r in LAYOUT_PRINCIPALE.items():
        ok(r[0] >= 0 and r[1] >= 0 and r[0] + r[2] <= W and r[1] + r[3] <= H, f"{nome} dentro la finestra")
    for i, p in enumerate(nomi):
        for q in nomi[i + 1:]:
            ok(not si_sovrappongono(LAYOUT_PRINCIPALE[p], LAYOUT_PRINCIPALE[q]), f"{p} ∩ {q} = ∅")
    ok(colpito(LAYOUT_PRINCIPALE, 40, 90) == "play" and colpito(LAYOUT_PRINCIPALE, 1, 1) is None, "hit-test")
    ok(all(p in LAYOUT_PRINCIPALE for p in PULSANTI_PRINCIPALE), "pulsanti nel layout")
    for h in (ALTEZZA_PLAYLIST_MIN, 150, 400):
        lp = layout_playlist(h)
        ok(not si_sovrappongono(lp["lista"], lp["piede"]) and not si_sovrappongono(lp["presa"], lp["piede"]),
           f"playlist {h}: nessuna sovrapposizione")
        ok(righe_visibili(h) * ALTEZZA_RIGA <= lp["lista"][3], f"righe della playlist {h}")
    ok(frazione_da_x(8 + 259 / 2, 8, 259) == 0.5 and frazione_da_x(-50, 8, 259) == 0.0, "frazione cursore")
    ok(aggancia_sotto((105, 495, 550, 300), (100, 800, 550, 232)) == (100, 500), "aggancio sotto")
    ok(aggancia_sotto((300, 100, 550, 300), (100, 800, 550, 232)) is None, "nessun aggancio")
    ok(aggancia_schermo((5, 300, 550, 232), (0, 25, 1512, 900)) == (0, 300), "aggancio bordo schermo")

    # — ponte JS —
    ok(comando_js("seek", 12.5) == 'window.__bia ? window.__bia.cmd("seek", 12.5) : false', "comando seek")
    ok(comando_js("seek", float("nan")).endswith('"seek", 0.0) : false'), "argomento NaN neutralizzato")
    js = comando_js("cerca", '"); alert(1); ("')
    ok('\\"); alert(1); (\\"' in js, "argomento stringa quotato in sicurezza")
    try:
        comando_js("rm -rf")
        ok(False, "comando sconosciuto accettato")
    except ValueError:
        ok(True, "comando sconosciuto rifiutato")
    ok(js_stato("x").endswith('stato("") : \'\''), "modo viz non valido → nessuna analisi")
    azioni = set(PULSANTI_PRINCIPALE) | set(TASTI.values()) | {a for a, _ in FRECCE.values()}
    for a in sorted(azioni):
        ok((a in AZIONI_NATIVE) != (comando_per_azione(a) is not None),
           f"azione '{a}' gestita in un solo posto")
    ok(comando_per_azione("pause") == "toggle" and comando_per_azione("pl") is None, "alias dei comandi")
    for h in ("music.youtube.com", "accounts.google.com", "consent.google.it", "accounts.google.co.uk",
              "i.ytimg.com", "lh3.googleusercontent.com"):
        ok(host_interno(h), f"host interno {h}")
    for h in ("www.example.com", "evilyoutube.com", "google.com.evil.net", ""):
        ok(not host_interno(h), f"host esterno {h!r}")

    # — impostazioni: round-trip e tolleranza —
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "imp.json"
        ok(carica_impostazioni(p) == Impostazioni(), "file assente → predefiniti")
        imp = Impostazioni(origine_principale=[10.0, 20.0], modo_viz="oscilloscopio",
                           tempo_rimanente=True, cornice_libreria=[1.0, 2.0, 800.0, 600.0])
        salva_impostazioni(imp, p)
        ok(carica_impostazioni(p) == imp, "round-trip impostazioni")
        p.write_text('{"modo_viz": "laser", "primo_piano": "sì", "altezza_playlist": 1e9,'
                     ' "origine_playlist": [1, "a"]}', encoding="utf-8")
        cp = carica_impostazioni(p)
        ok(cp.modo_viz == "spettro" and cp.primo_piano is False and cp.altezza_playlist == 600
           and cp.origine_playlist is None, "valori invalidi scartati")
        p.write_text("{troncato", encoding="utf-8")
        ok(carica_impostazioni(p) == Impostazioni(), "json corrotto → predefiniti")
        ok(not any(n.startswith(".imp-") for n in os.listdir(p.parent)), "nessun temporaneo residuo")
    ok(prossimo_modo_viz("spento") == "spettro" and prossimo_modo_viz("??") == "spettro", "ciclo viz")

    # — scala e impostazioni nuove —
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "imp.json"
        p.write_text('{"scala": 1.7, "filtro_ricerca": "artisti", "altezza_ricerca": 5,'
                     ' "origine_ricerca": [10, 20]}', encoding="utf-8")
        cp = carica_impostazioni(p)
        ok(cp.scala == SCALA_PREDEFINITA and cp.filtro_ricerca == "brani", "scala e filtro non previsti scartati")
        ok(cp.altezza_ricerca == ALTEZZA_RICERCA_MIN and cp.origine_ricerca == [10.0, 20.0], "geometria ricerca")
        p.write_text('{"scala": 2.0}', encoding="utf-8")
        ok(carica_impostazioni(p).scala == 2.0, "scala valida conservata")
    ok(SCALA_PREDEFINITA in SCALE and all(s > 0 for s in SCALE), "scale ammesse")
    diag = StatoLettore(pronto=True, codice=1, diagnosi_audio="audio/mp4 · seg 0 · dec 0")
    ok(testo_stato(diag, False) == "analisi n/d: audio/mp4 · seg 0 · dec 0", "stato con diagnosi")
    ok(testo_stato(diag, True) == "in riproduzione", "stato senza diagnosi quando l'analisi funziona")
    ok(StatoLettore.da_json('{"pronto":1,"ad":"presa audio assente"}').diagnosi_audio == "presa audio assente",
       "diagnosi dal ponte")

    # — pannello di ricerca: geometria e aggancio —
    for h in (ALTEZZA_RICERCA_MIN, ALTEZZA_RICERCA_PREDEFINITA, 600):
        lr = layout_ricerca(h)
        nomi_r = list(lr)
        for nome in nomi_r:
            x, y, w, hh = lr[nome]
            ok(x >= 0 and y >= 0 and x + w <= LARGHEZZA_RICERCA and y + hh <= h, f"ricerca {h}: {nome} dentro")
        for i, a_ in enumerate(nomi_r):
            for b_ in nomi_r[i + 1:]:
                ok(not si_sovrappongono(lr[a_], lr[b_]), f"ricerca {h}: {a_} ∩ {b_} = ∅")
        ok(righe_visibili_ricerca(h) * ALTEZZA_RIGA <= lr["lista"][3], f"righe ricerca {h}")
    ok(all(p_ in layout_ricerca(200) for p_ in PULSANTI_RICERCA), "pulsanti della ricerca nel layout")
    ok(aggancia_destra((515, 710, 412, 266), (100, 800, 412, 174)) == (512, 708), "aggancio a destra")
    ok(aggancia_destra((700, 710, 412, 266), (100, 800, 412, 174)) is None, "nessun aggancio a destra")

    # — ricerca: analisi di una risposta con la struttura di '/search' —
    def run(t, browse=None, video=None):
        r = {"text": t}
        if browse:
            r["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse}}
        if video:
            r["navigationEndpoint"] = {"watchEndpoint": {"videoId": video}}
        return r
    sep = {"text": " • "}

    def riga(titolo, info, video=None, lista=None, browse=None, grigia=False, fissa=None, elemento=None):
        r = {"flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [run(titolo, video=video)]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": info}}}]}
        gioca = {}
        if video:
            gioca["watchEndpoint"] = {"videoId": video}
        if lista:
            gioca["watchPlaylistEndpoint"] = {"playlistId": lista}
        if gioca:
            r["overlay"] = {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer":
                                                                              {"playNavigationEndpoint": gioca}}}}
        if browse:
            r["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse}}
        if grigia:
            r["musicItemRendererDisplayPolicy"] = "MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT"
        if fissa:
            r["fixedColumns"] = [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [{"text": fissa}]}}}]
        if elemento:
            r["playlistItemData"] = {"videoId": elemento}
        return {"musicResponsiveListItemRenderer": r}

    b1 = riga("Time", [run("Pink Floyd", "UCpf"), sep, run("The Dark Side of the Moon", "MPREb_dsotm"), sep,
                       run("6:53")], video="v1")
    b2 = riga("Money", [run("Brano"), sep, run("Pink Floyd", "UCpf"), sep, run("The Dark Side", "MPREb_dsotm"),
                        sep, run("6:22")], video="v2")
    b3 = riga("Bootleg", [run("Pink Floyd", "UCpf"), sep, run("4:10")], video="v3", grigia=True)
    b4 = riga("The Explanation", [run("XXXTENTACION", "UCnA")], fissa="0:51", elemento="0m68rsj48X0")
    al = riga("Wish You Were Here", [run("Album"), sep, run("Pink Floyd", "UCpf"), sep, run("1975")],
              lista="OLAK5uy_wywh", browse="MPREb_wywh")
    pl = riga("Prog Classics", [run("Playlist"), sep, run("Biagio"), sep, run("50 brani")],
              lista="PLprog", browse="VLPLprog")
    pl2 = riga("Senza pulsante", [run("Playlist"), sep, run("Qualcuno")], browse="VLPLsenza")
    ar = riga("Pink Floyd", [run("Artista"), sep, run("12 Mln iscritti")], browse="UCpf")
    risposta = json.dumps({"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [
            {"musicCardShelfRenderer": {"contents": [b1]}},
            {"musicShelfRenderer": {"contents": [b1, b2, b3, b4, al, pl, pl2, ar]}}]}}}}]}}})
    brani = analizza_ricerca(risposta, "brani")
    ok([b.titolo for b in brani] == ["Time", "Money", "Bootleg", "The Explanation"], "brani senza doppioni")
    ok(brani[0].artista == "Pink Floyd" and brani[0].album == "The Dark Side of the Moon"
       and brani[0].durata_testo == "6:53" and brani[0].video_id == "v1", "campi del brano")
    ok(brani[1].artista == "Pink Floyd", "etichetta di tipo 'Brano' scartata")
    ok(not brani[2].disponibile and brani[0].disponibile, "brano non disponibile riconosciuto")
    ok(brani[3].durata_testo == "0:51" and brani[3].video_id == "0m68rsj48X0", "durata in colonna fissa, videoId da playlistItemData")
    album_ = analizza_ricerca(risposta, "album")
    ok(len(album_) == 1 and album_[0].playlist_id == "OLAK5uy_wywh" and album_[0].anno == "1975"
       and album_[0].artista == "Pink Floyd" and album_[0].colonna_destra() == "1975", "album")
    pls = analizza_ricerca(risposta, "playlist")
    ok([p_.playlist_id for p_ in pls] == ["PLprog", "PLsenza"], "playlist (anche da browseId VL…)")
    ok(pls[0].artista == "Biagio" and pls[0].conteggio == "50 brani", "autore e conteggio della playlist")
    ok(brani[0].etichetta() == "Time — Pink Floyd" and brani[0].colonna_destra() == "6:53", "etichetta del risultato")
    ok(analizza_ricerca("{", "brani") == [] and analizza_ricerca('{"contents": {}}', "brani") == []
       and analizza_ricerca(None, "album") == [], "risposte malformate → nessun risultato")
    ok(argomenti_accoda(album_[0], "fine") == {"bersaglio": {"playlistId": "OLAK5uy_wywh"},
                                               "posizione": "INSERT_AT_END", "salta": False}, "accoda album in fondo")
    ok(argomenti_accoda(brani[0], "ora") == {"bersaglio": {"videoId": "v1"},
                                             "posizione": "INSERT_AFTER_CURRENT_VIDEO", "salta": True}, "riproduci ora")
    ok(argomenti_ricerca("  pink   floyd ", "album") == {"testo": "pink floyd",
                                                         "parametri": PARAMETRI_RICERCA["album"]}, "argomenti di ricerca")
    ok(all(PARAMETRI_RICERCA[f].startswith("Eg") for f in FILTRI_RICERCA) and set(TIPO_PER_FILTRO) == set(FILTRI_RICERCA),
       "parametri dei filtri")

    print(f"core.py {__version__}: {n} verifiche superate.")
    return n


if __name__ == "__main__":
    _autotest()
