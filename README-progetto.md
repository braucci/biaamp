# BiaAmp — Documentazione tecnica

Versione 1.4.1 · © 2026 B. Raucci · Licenza MIT

Questo documento descrive come è costruito BiaAmp, perché è costruito così, che cosa è
stato verificato e come intervenire quando qualcosa non funziona.

---

## 1. Architettura

BiaAmp non riproduce l'audio da sé: la riproduzione avviene dentro la pagina ufficiale
`music.youtube.com`, caricata in una `WKWebView` (lo stesso motore di Safari). Il lettore
a deck è un **pannello strumenti** che legge lo stato della pagina e le invia comandi.

```
┌────────────────────── processo BiaAmp (Python + PyObjC) ──────────────────────┐
│   app.py  ── presentazione                                                    │
│   ├─ Lettore   (finestra senza cornice, disegno NSBezierPath)                 │
│   ├─ Playlist  (agganciata sotto il lettore, modificabile)                    │
│   ├─ Ricerca   (agganciata a destra: campo nativo + risultati disegnati)      │
│   ├─ Libreria  (finestra con WKWebView ── music.youtube.com)                  │
│   ├─ Now Playing / tasti multimediali (MediaPlayer)                           │
│   ├─ timer 30 Hz ──► evaluateJavaScript("__bia.stato('f')") ──────┐           │
│   └─ ricerca, modifica coda ─► callAsyncJavaScript("__bia.…") ────┤           │
│                                                                    ▼          │
│   core.py  ── motore (nessuna dipendenza grafica) ◄── JSON ─────────          │
│   ├─ stato, coda, testi del display, risultati di ricerca                     │
│   ├─ spettro: bande, balistica · oscilloscopio: trigger                       │
│   └─ geometria, aggancio, scala, impostazioni, comandi JS sicuri              │
└───────────────────────────────────────────────────────────────────────────────┘
                                   │ WebKit
┌──────────────────────── pagina music.youtube.com ─────────────────────────────┐
│   risorse/presa.js (inizio pagina) → window.__biaPresa                        │
│   └─ intercetta i segmenti audio dei SourceBuffer, li decodifica, FFT         │
│   risorse/ponte.js (fine pagina)   → window.__bia                             │
│   ├─ #movie_player · ytmusic-player-bar · #queue  (API interne del lettore)   │
│   └─ ytmusic-app.networkManager   ('/search', '/music/get_queue')             │
└───────────────────────────────────────────────────────────────────────────────┘
```

La separazione `core.py` / `app.py` è la stessa che c'è tra **solutore** e
**post-processore** in un codice CFD: il motore calcola e si verifica da solo, senza
display (`python3 core.py`); la presentazione si limita a visualizzare.

### 1.1 Modello a interrogazione (*pull*)

Quando la finestra della pagina è nascosta, WebKit rallenta i timer della pagina e
sospende `requestAnimationFrame`. BiaAmp quindi **interroga** la pagina dal lato nativo:
un `NSTimer` a 30 Hz chiama `evaluateJavaScript`, che esegue il codice nella pagina
indipendentemente dai suoi timer. Con il lettore non visibile la cadenza scende a 4 Hz e
l'analisi audio non viene richiesta. Su macOS 14+ si imposta `WKInactiveSchedulingPolicyNone`.

Le operazioni che nella pagina sono **asincrone** (ricerca, inserimento in coda: richieste
di rete) usano `callAsyncJavaScript`, che attende la promessa JavaScript e restituisce il
risultato; lo stesso canale serve per le modifiche della coda, che hanno argomenti
strutturati. Gli argomenti passano come variabili, mai concatenati nel codice.

### 1.2 Punti di accesso alla pagina

YouTube Music non ha un'API pubblica per il lettore web. `ponte.js` usa i componenti
interni della pagina, gli stessi del client open source più diffuso
([th-ch/youtube-music](https://github.com/th-ch/youtube-music)):

| Dato o comando | Accesso | Ripiego |
|---|---|---|
| stato, posizione, durata, volume | `#movie_player.getPlayerState()`, `getCurrentTime()`, … | elemento `<video>` |
| play, pausa, posizione | `#movie_player.playVideo()`, `pauseVideo()`, `seekTo()` | — |
| successivo / precedente | clic su `.next-button` / `.previous-button` | `nextVideo()` |
| volume | `ytmusic-player-bar.updateVolume(v)` | `setVolume(v)` |
| ripetizione | `ytmusic-player-bar.getState().queue.repeatMode` → `NONE`/`ALL`/`ONE` | clic su `.repeat` |
| casuale | attributo `shuffle-on`; `queue.shuffle()` | clic su `.shuffle` |
| mi piace | attributo `like-status` di `#like-button-renderer` | `updateLikeStatus()` |
| coda | `#queue.queue.getItems()` | elementi `ytmusic-player-queue-item` |
| salto a un brano | `#queue.dispatch({type: 'SET_INDEX', payload: i})` | — |
| spostamento nella coda | `#queue.onDrop(evento)`, il gestore del trascinamento di YouTube Music | `MOVE_ITEM` + `SET_IS_RAAR_ENABLED false` (solo locale) |
| rimozione dalla coda | `#queue.queue.removeItem(chiave)` | `REMOVE_ITEM` + `SET_IS_RAAR_ENABLED false` (solo locale) |
| annullamento della rimozione | inserimento in fondo (`get_queue`) + spostamento con `onDrop` | — |
| fine della radio | `queue.continuation = undefined` (+ `SET_IS_INFINITE false`, `SET_IS_RAAR_ENABLED false`) | — |
| ricerca | `ytmusic-app.networkManager.fetch('/search', {query, params})` | — |
| inserimento in coda | `fetch('/music/get_queue', {videoIds \| playlistId, queueInsertPosition})` + `ADD_ITEMS` | coda vuota: apertura di `/watch?v=…` |

Sono API **non documentate**: Google può cambiarle senza preavviso. Ogni accesso è
protetto (`try`/ripiego) perché un cambiamento degradi una funzione invece di bloccare
l'app; il §6 spiega come diagnosticare.

### 1.3 Formato dei messaggi

La pagina restituisce **stringhe JSON**, mai oggetti: così `core.StatoLettore.da_json()`
riceve esattamente ciò che si può salvare, rileggere e usare nei test. I dati dell'analisi
audio viaggiano in **base64** (1024 byte di FFT → 1368 caratteri; a 30 Hz circa 41 kB/s).
Ogni valore numerico è ripulito (`NaN`, `±∞`, tipi errati diventano valori neutri) e
saturato nell'intervallo fisico. I comandi sono costruiti da `core.comando_js()`: nome da
un elenco chiuso, argomenti passati per `json.dumps`.

---

## 2. L'analizzatore di spettro

### 2.1 Perché non si usa Web Audio

La via naturale sarebbe collegare l'elemento `<video>` a un `AnalyserNode` con
`createMediaElementSource()`. In WebKit però, quando l'audio arriva da **Media Source
Extensions** (lo streaming a segmenti usato da YouTube), quel nodo produce **solo zeri**:
la musica si sente, l'analizzatore riceve silenzio. È il [bug WebKit 180696](https://bugs.webkit.org/show_bug.cgi?id=180696),
aperto dal 2017 e ancora senza correzione. La versione 1.0.0 lo ha mostrato al primo
collaudo ("analisi n/d"); nessuna impostazione di `WKWebView` lo aggira.

### 2.2 La presa audio (`presa.js`)

La soluzione, suggerita nella stessa segnalazione, è **analizzare una copia dei dati**
invece del suono in uscita. `presa.js` viene iniettato *all'inizio* della pagina, prima
del lettore di YouTube, e avvolge in modo passivo alcune funzioni del browser:

1. `MediaSource.addSourceBuffer` — riconosce i buffer **audio** (tipo `audio/…`); quelli
   video non vengono toccati;
2. `SourceBuffer.appendBuffer` — copia ogni segmento che il lettore vi carica, **dopo**
   che il browser lo ha accettato;
3. `SourceBuffer.abort`, `remove` e `changeType` — tengono la presa allineata al buffer
   (§ 2.2.1);
4. `URL.createObjectURL` — ricorda quale `MediaSource` è collegato a quale `<video>`, così
   al cambio brano non si mescolano i dati di due brani diversi.

Per ogni segmento serve l'istante $t_0$ a cui corrisponde:

- **MP4 frammentato** (AAC): i byte vengono accumulati finché un riquadro (`moov`,
  `moof`, `mdat`) è completo, perché il protocollo SABR di YouTube può consegnare un
  segmento in più pezzi. L'istante viene dal riquadro `tfdt` del frammento:
  $t_0 = \text{baseMediaDecodeTime} / \text{timescale} + \text{timestampOffset}$, con la
  scala dei tempi letta dal riquadro `mdhd`. È l'informazione scritta dal codificatore,
  indipendente da come i dati vengono spezzati.
- **WebM** (Opus): $t_0$ è l'inizio dell'intervallo appena aggiunto a `buffered`.

Il segmento viene poi **decodificato a parte** con `decodeAudioData` di un
`OfflineAudioContext` (che non suona nulla), concatenandolo al segmento di
inizializzazione. Se WebKit rifiuta l'MP4 frammentato, la presa ne estrae i fotogrammi
AAC leggendo `tfhd`/`trun` (dimensione di ciascun campione) e la configurazione dal
descrittore `esds`, e li **rincapsula in ADTS** (intestazione di 7 byte per fotogramma),
formato che il decodificatore di sistema accetta sempre. Il metodo che funziona viene
ricordato per i segmenti successivi.

#### 2.2.1 Sessioni lunghe: allineamento della ricomposizione (1.4.1)

**Il guasto.** Fino alla 1.4.0, dopo un tempo imprevedibile l'analisi poteva fermarsi
per sempre (`analisi n/d: audio/mp4 · seg 299 · dec 299 · diretto`, nessun errore) e
tornava solo riavviando. La ricomposizione dei riquadri MP4 è un automa che legge
un'intestazione di 8 byte — dimensione $d$ (32 bit) e tipo (4 caratteri) — e attende
$d$ byte. YouTube Music **riusa lo stesso `SourceBuffer` da un brano all'altro**. Se un
segmento resta a metà, i byte in sospeso si saldano ai successivi e l'automa perde il
passo. Un segmento resta a metà in due casi:

- un salto di brano o una ricerca nel brano, con `abort()`, che azzera l'analizzatore
  del browser ma non quello della presa;
- un `appendBuffer` rifiutato per buffer pieno (`QuotaExceededError`), che il lettore
  ripete e la presa contava due volte.

Quattro byte qualsiasi letti come dimensione valgono in media $2^{31}$ byte ≈ 2 GB. La
presa attendeva quei byte fino al tetto di sicurezza dell'accumulo, 16 MB. Con l'audio
a 128 kbit/s (16 kB/s) l'attesa è $16 \cdot 2^{20}\ \text{B} / 16\,000\ \text{B/s}
\approx 1050$ s ≈ 17 minuti; a 256 kbit/s circa 9 minuti. Poi la presa si
azzerava con un errore, ma ripartiva da un pezzo qualsiasi e poteva perdere di nuovo
il passo. Durante l'attesa il contatore dei segmenti resta fermo senza errori: è
esattamente la diagnosi osservata.

**La correzione** agisce su tre livelli indipendenti:

1. *Prevenzione.* `abort()` e l'evento `error` azzerano anche la presa; i dati entrano
   nella presa solo dopo che `appendBuffer` è riuscito; `changeType()` attende un nuovo
   segmento di inizializzazione.
2. *Rilevazione.* Un'intestazione è credibile solo se il tipo è un riquadro di primo
   livello dell'MP4 frammentato (`moof`, `mdat`, `moov`, `ftyp`, `styp`, `sidx`, …) e se
   $8 \le d \le 4$ MB. Un frammento audio di 10 s a 256 kbit/s occupa circa 320 kB, quindi
   il limite ha un margine di oltre 10 volte.
3. *Riaggancio.* Se l'intestazione non è credibile, la presa cerca la successiva firma
   `moof`/`ftyp`/`styp`/`moov` preceduta da una dimensione plausibile e riparte da lì.
   Gli ultimi 7 byte restano in attesa, perché la firma può arrivare spezzata tra due
   pezzi. La diagnosi conta gli episodi (`riagganci N`).

*Perché il riaggancio è affidabile.* Un falso aggancio richiede che in un punto qualsiasi
dei dati compressi compaiano i 4 byte di una delle 4 firme e, prima di essi, una
dimensione nell'intervallo ammesso. Per byte casuali la probabilità per posizione è
$4 \cdot 256^{-4} \cdot (2^{22}/2^{32}) \approx 9 \cdot 10^{-13}$: su un frammento di
320 kB si ha circa $3 \cdot 10^{-7}$. Anche in quel caso il passo successivo perde di
nuovo l'allineamento e si riaggancia: l'errore si corregge da sé al frammento dopo.

**Potatura dei blocchi.** I blocchi decodificati sono al massimo 40. Ora si tengono
quelli del brano in ascolto (il `MediaSource` del `<video>` del lettore, non del primo
`<video>` del documento). Quelli finiti da più di 15 s vengono scartati, salvo che siano
arrivati da meno di 20 s: il lettore può accodare un brano prima di spostare l'istante di
ascolto. Oltre il limite si sacrificano prima i blocchi di altri brani, poi quelli più
lontani dall'istante di ascolto. `remove()` toglie anche dalla presa i blocchi del
tratto liberato. A parità di copertura vale il blocco arrivato per ultimo.

### 2.3 Dal segnale allo spettro: replica di `AnalyserNode`

A ogni interrogazione si prendono gli $N = 2048$ campioni (mono, 48 kHz) che terminano
all'istante di riproduzione $t$ = `video.currentTime`, e si calcola ciò che calcolerebbe
`AnalyserNode.getByteFrequencyData` con `smoothingTimeConstant = 0`:

$$w_n = 0{,}42 - 0{,}5\cos\frac{2\pi n}{N} + 0{,}08\cos\frac{4\pi n}{N}\quad\text{(Blackman)},\qquad
X_k = \sum_{n=0}^{N-1} w_n\,x_n\,e^{-2\pi i kn/N},$$

$$L_k = 20\log_{10}\frac{|X_k|}{N}\ \text{dB},\qquad
b_k = \left\lfloor 255\,\frac{L_k - L_{min}}{L_{max} - L_{min}}\right\rfloor,\quad L_{min} = -90,\ L_{max} = -20.$$

La FFT è una radix-2 iterativa (Cooley–Tukey) in JavaScript; il test la confronta con la
DFT diretta (scarto massimo $2\cdot 10^{-13}$). La risoluzione è

$$\Delta f = \frac{f_s}{N} = \frac{48\,000}{2048} \approx 23{,}4\ \text{Hz}, \qquad
T_{finestra} = \frac{N}{f_s} \approx 42{,}7\ \text{ms},$$

il classico compromesso tempo–frequenza: raddoppiare $N$ dimezza $\Delta f$ ma raddoppia
la finestra. L'oscilloscopio riceve invece i campioni stessi, quantizzati a 8 bit
($128 = 0$), come `getByteTimeDomainData`. Poiché il formato è identico a quello di
Web Audio, tutto il resto della catena (`core.Spettro`) è rimasto invariato.

### 2.4 Bande logaritmiche

L'orecchio percepisce gli intervalli di frequenza in modo proporzionale (un'ottava è un
raddoppio), quindi le 22 bande hanno bordi in progressione geometrica tra 40 Hz e 16 kHz:

$$f_i = f_{min}\left(\frac{f_{max}}{f_{min}}\right)^{i/n}, \qquad
\frac{f_{i+1}}{f_i} = 400^{1/22} \approx 1{,}313 \;\;(\approx 0{,}39\ \text{ottave}).$$

È lo stesso principio delle bande d'ottava dei fonometri. Ogni banda prende il **massimo**
dei bin FFT che contiene; dove una banda è più stretta di un bin si legge il bin più
vicino al centro geometrico $\sqrt{f_i f_{i+1}}$.

### 2.5 Compensazione dello spettro rosa

La musica ha in media uno spettro di potenza $S(f) \propto 1/f$ ("rumore rosa"). Un bin
FFT ha larghezza costante $\Delta f$, quindi contiene una potenza $P \propto S(f)\,\Delta f \propto 1/f$:

$$\Delta L = 10 \log_{10}\frac{P(2f)}{P(f)} = 10\log_{10}\tfrac12 \approx -3{,}01\ \text{dB/ottava}.$$

Il motore aggiunge $+3\,\text{dB} \cdot \log_2(f_c / 1\,\text{kHz})$, riportato in scala
sulla dinamica di 70 dB: −0,20 del fondo scala a 40 Hz, +0,17 a 16 kHz.

### 2.6 Barre: un sistema del primo ordine

Le barre salgono istantaneamente e scendono secondo

$$\frac{dy}{dt} = -\frac{y}{\tau} \quad\Longrightarrow\quad y(t+\Delta t) = y(t)\,e^{-\Delta t/\tau},
\qquad \tau = 0{,}18\ \text{s}.$$

È la risposta libera di un circuito RC, o di uno **strumento ad ago con ritardo**: il
variometro (VSI) ne è l'esempio aeronautico, perché la perdita calibrata del capillare
fa inseguire alla pressione nella cassa quella statica con una costante di tempo. Qui la
barra dimezza in $\tau\ln 2 \approx 125$ ms.

**Perché la soluzione esatta e non un'integrazione numerica.** Con Euler esplicito,
$y_{n+1} = y_n(1 - \Delta t/\tau)$; a 30 fotogrammi al secondo il fattore per passo è
0,8148 invece di $e^{-\Delta t/\tau} = 0{,}8310$, e dopo 1 s l'errore relativo accumulato
è del **−44,5 %**. La soluzione esatta gode della proprietà di composizione
$e^{-a/\tau}e^{-b/\tau} = e^{-(a+b)/\tau}$: è **indipendente dal passo temporale**,
l'equivalente di uno studio di convergenza nel tempo superato per costruzione. L'autotest
lo verifica a 30, 60 e 120 fps (scarto < 10⁻⁹).

### 2.7 Picchi: caduta grave

Il picco resta fermo per $t_{hold} = 0{,}30$ s, poi cade di moto uniformemente
accelerato partendo da fermo:

$$p(t) = p_0 - \tfrac12\,g\,(t - t_{hold})^2, \qquad g = 3\ \text{fondo scala/s}^2,$$

quindi dal fondo scala impiega $\sqrt{2/g} \approx 0{,}82$ s a raggiungere lo zero.

### 2.8 Oscilloscopio

I 2048 campioni sono mostrati su 88 punti. La traccia parte dal primo **attraversamento
dello zero in salita**, come il trigger di un oscilloscopio reale: una forma d'onda
periodica appare ferma invece di scorrere.

### 2.9 Diagnosi

Se durante la riproduzione l'analisi non produce dati per più di 3 s, il display mostra
"analisi n/d" e **l'intera riga di stato** riporta la diagnosi della presa, per esempio
`audio/mp4 · seg 12 · dec 12 · adts` (formato intercettato, segmenti ricevuti, segmenti
decodificati, metodo) seguita dall'ultimo errore. Ogni cambiamento della diagnosi viene
scritto anche nel registro `~/Library/Logs/BiaAmp.log`. Il §6 spiega come leggerla.

---

## 3. Finestre e interfaccia

- **Unità skin e scala.** Le finestre sono progettate in unità skin (lettore 275 × 116,
  come il deck classico) e disegnate a `scala` punti per unità: 1×, 1,5× o 2×
  (*Vista ▸ Dimensione*). Sui monitor CRT da 15" e 17" a 1024×768 (93 e 80 ppi) il deck
  classico, largo 275 pixel, misurava 75–87 mm; il MacBook Pro 14" ha 127 punti per
  pollice, quindi la scala **1,5** dà 412 punti = **82 mm**, al centro di quell'intervallo,
  ed è la predefinita. La 2× (110 mm) corrisponde alla vecchia "double size".
- **Layout verificato.** Tutto il layout è in `core.py`; l'autotest controlla che nessun
  controllo esca dalla finestra e che nessuna coppia di controlli si sovrapponga, per il
  lettore, per la playlist e per la ricerca a più altezze.
- **Coordinate.** Le viste sono "capovolte" (`isFlipped`, y verso il basso); le finestre
  usano le coordinate schermo di AppKit (y verso l'alto), nelle quali lavorano
  `core.aggancia_sotto()` e `core.aggancia_destra()`.
- **Aggancio magnetico.** La playlist si aggancia sotto il lettore, la ricerca alla sua
  destra con i bordi superiori allineati, entro 12 punti; le finestre agganciate seguono
  il lettore. Tutte si agganciano ai bordi dell'area utile entro 10 punti.
- **Ricerca.** Campo di testo nativo (`NSTextField`, cursore dorato) sopra un riquadro
  disegnato; filtri Brani / Album / Playlist con i parametri che invia `ytmusicapi`;
  risultati estratti da `core.analizza_ricerca()` seguendo la struttura delle risposte
  `/search` decodificata da quella libreria. Una risposta arrivata dopo una ricerca più
  recente viene ignorata (numero d'ordine della richiesta).
- **Riproduzione dai risultati.** *Riproduci ora* = inserimento dopo il brano corrente e
  salto (`SET_INDEX`); *Riproduci dopo* = inserimento senza salto; *In coda* = in fondo.
  Album e playlist vengono inseriti interi tramite il loro `playlistId`.
- **Primo piano solidale.** Un clic porta davanti solo la finestra cliccata, e le
  compagne agganciate resterebbero dietro le finestre di altre applicazioni. In
  `windowDidBecomeKey_` le altre finestre del gruppo agganciato (lettore + playlist e
  ricerca, se agganciate e visibili) vengono ordinate subito sotto quella che ha ricevuto
  l'input con `orderWindow:relativeTo:` (`NSWindowBelow`): il deck sale come un'unica
  finestra. La Libreria è fuori dal gruppo. (Non si usano le *child window* di AppKit:
  seguirebbero il genitore anche nello spostamento, che qui è già gestito dall'aggancio.)
- **Riduzione a icona solidale.** AppKit riduce nel Dock una finestra alla volta. Quando
  vi va il lettore (`windowWillMiniaturize_`), le compagne visibili si nascondono e se ne
  conserva l'elenco; al ritorno (`windowDidDeminiaturize_`) si riagganciano e ricompaiono.
  Le impostazioni di visibilità non cambiano, quindi un'uscita a lettore ridotto le
  conserva; una scelta esplicita fatta nel frattempo (`⌘2`, `⌘3`) prevale. `⌘M` da
  playlist o ricerca riduce il lettore.
- **Libreria.** Serve per l'accesso a Google e per sfogliare: chiuderla la *nasconde*
  soltanto, perché la pagina deve restare viva (è lì che suona la musica).
- **Centro di Controllo.** Si ripubblica solo quando l'estrapolazione del sistema
  (posizione + velocità × tempo) diventa falsa: cambio brano, pausa, salto > 2 s.

### 3.1 Playlist modificabile

La coda resta **di YouTube Music**: la playlist non ne tiene una copia propria da
sincronizzare, ma chiede alla pagina le stesse operazioni che fa l'interfaccia web e poi
la rilegge. Perché gli indici coincidano, il ponte non salta mai un elemento: quelli che
non sa leggere arrivano come segnaposto («—»). Se la coda è letta dal DOM (ripiego), la
modifica è disattivata.

#### La coda sta anche sul server

Leggendo il codice di `music.youtube.com` (pacchetto `af273e12`, ottobre 2026) e lo stato
di una pagina aperta con l'account del progetto è emerso che:

- con `music_web_enable_server_queues` la coda è **conservata sul server**, come una
  playlist i cui elementi hanno un `playlistSetVideoId`, condivisa tra i dispositivi;
  con `music_web_enable_server_queue_restoration`, per gli abbonati, una pagina nuova la
  **ripristina** (è ricomparsa identica in un browser diverso, senza riprodurre nulla);
- con `music_enable_responsive_radio_client` la «radio reattiva» (`isRaarEnabled`), a
  ogni cambio di brano, chiede al server la continuazione e applica `UPDATE_ITEMS`: il
  riduttore conserva gli elementi fino al brano corrente e **sostituisce tutti quelli
  successivi** con la versione del server;
- quando l'utente toglie o sposta un brano, l'interfaccia aggiorna **tre cose**: lo store
  (`REMOVE_ITEM` / `MOVE_ITEM`), il server (`DELETE` / `REORDER`, cioè
  `ACTION_REMOVE_VIDEO` / `ACTION_MOVE_VIDEO_AFTER`) e la radio reattiva, che spegne
  (`SET_IS_RAAR_ENABLED false`), oltre a un eventuale dispositivo Cast.

La versione 1.2 aggiornava solo lo store. Un brano tolto restava quindi sul server e, al
primo cambio di brano (per esempio *Riproduci ora* dalla ricerca), la radio reattiva
riscriveva la coda dalla versione del server: i brani tolti «ricomparivano». Dalla 1.3 il
ponte usa le stesse due vie dell'interfaccia:

- **spostamento**: il metodo `onDrop` del componente `<ytmusic-player-queue>`, lo stesso
  che riceve il trascinamento. Il ponte gli passa un evento con `dragEl.data` uguale al
  renderer dell'elemento da spostare e `dragEl` collocato, tra i "fratelli", nella
  posizione d'arrivo. Il componente legge la coda da `this.data`, che lo store aggiorna
  dopo ogni azione: prima di ogni mossa il ponte attende che sia lo stesso array dello
  store (al più ~1 s), altrimenti si ferma senza toccare nulla;
- **rimozione**: `queue.removeItem(chiave)`, dove la chiave è
  `navigationEndpoint.watchEndpoint.index` dell'elemento, che toglie il *primo* elemento
  con quella chiave. Le chiavi non sono sempre uniche: dopo un inserimento o uno SHUF il
  server assegna ai nuovi elementi numeri che possono coincidere con quelli già presenti
  (nella 1.3.1 il brano con la chiave doppia non veniva tolto: «0 operazioni su 1»). Con
  una chiave ambigua il ponte esegue per **indice** le stesse tre operazioni del metodo:
  `serverQueueApi.removeItem(elemento)`, `REMOVE_ITEM`, `SET_IS_RAAR_ENABLED false`.
- **SHUF** su una coda del server (`serverQueuesEnabled`) non rimescola la coda locale:
  chiede al server una sequenza rimescolata nuova (`serverQueueApi.shuffle()` →
  `RESET_SHUFFLE_ITEMS`, con un nuovo gettone). Per una radio significa **una radio
  nuova** di 50 brani, che rimpiazza la coda potata a mano. È il comportamento della
  pagina; BiaAmp lo lascia com'è.

Se queste vie mancassero (una futura versione della pagina), il ponte ricorre alle azioni
dello store e spegne comunque la radio reattiva; il registro riporta allora
`modifiche della coda: via locale` invece di `via nativa`. Anche l'inserimento dalla
ricerca spegne la radio reattiva prima del salto al brano: una coda costruita a mano non
deve essere riscritta.

**La radio (continuazione).** Una coda nata da una radio o da un Mix porta con sé un
*gettone di continuazione*, `queue.continuation`, sull'oggetto della coda (non nello
store). Nel codice di YouTube Music, alla sottoscrizione sul cambio del brano corrente:

```
f = currentIndex + 5 >= items.length + automixItems.length
f = !!queue.continuation && f && !pendingFetchItemsRequest && !cast
f && enb(queue, endpoint)         // chiede la continuazione al server, poi UPDATE_ITEMS
```

cioè: **a ogni cambio di brano, se dopo il corrente restano meno di 5 brani e il gettone
c'è, YouTube Music chiede altri brani al server** e sostituisce tutto ciò che segue il
corrente con la risposta. È quanto si è visto nella 1.3.0: tolti 48 brani su 52, al
primo *Riproduci ora* dalla ricerca la coda è risalita a 54 brani, **diversi** da quelli
tolti (quindi le rimozioni sul server avevano funzionato). Le rimozioni native non
toccano il gettone; lo azzera solo YouTube Music stesso quando ricrea la coda
(`continuation = undefined`). Dalla 1.3.1 ogni rimozione fatta da BiaAmp fa lo stesso:
una coda potata a mano non si riempie più da sola (avviso «radio fermata»); `⌘Z` sulla
rimozione rimette anche il gettone. `isInfinite`, nello store, non è un indicatore
affidabile (era `false` con il gettone presente): il piede della playlist mostra
«radio» quando il gettone c'è. La voce *Ferma la radio* del menu toglie il gettone
senza rimuovere nulla. Restano le impostazioni di YouTube Music: con la *riproduzione
automatica* attiva, finita la coda partono i brani suggeriti (`automixItems`).

**Spostamento a blocco.** Trascinare k righe selezionate S = {s₁ < … < sₖ} nel *varco* g
(lo spazio davanti alla riga g; g = n in fondo) deve dare:

> (non selezionati con indice < g) ‖ s₁ … sₖ ‖ (non selezionati con indice ≥ g)

come il controllore di torre che sposta un gruppo di *strip* di volo nella rastrelliera
conservandone l'ordine. Lo store offre solo la mossa elementare (da, a), cioè
`insert(a, pop(da))`. `core.mosse_blocco()` usa al più k mosse:

1. i selezionati sopra il varco, a₁ < … < aₚ, dal più vicino al varco: aₚ₋ⱼ → g−1−j;
2. quelli sotto, b₁ < … < b_q, in ordine crescente: bⱼ → g+j.

*Dimostrazione.* `pop(i)` seguito da `insert(t)` con i ≤ t sposta di un posto **solo**
gli indici in (i, t]; con t ≤ i solo quelli in [t, i). Nella fase 1 il j-esimo passo usa
i = aₚ₋ⱼ e t = g−1−j: restano fermi gli a ancora da trattare (indici < i) e il blocco già
formato (indici > t). Nella fase 2, t = g+j ≤ bⱼ perché i b sono distinti e ≥ g: restano
fermi i b successivi (> bⱼ) e il blocco (< t). Ogni selezionato si muove quindi una volta
sola, nella posizione voluta; gli altri conservano l'ordine relativo. ∎

L'autotest lo verifica anche **in modo esaustivo**: per ogni n ≤ 7, ogni sottoinsieme S e
ogni varco (Σ 2ⁿ(n+1) = 1 793 casi) controlla l'ordine finale, il numero di mosse ≤ k, la nuova
selezione e che le mosse inverse ripristinino l'ordine iniziale.

**La convenzione di `toIndex`.** Nella 1.2 il significato di `toIndex` non era noto e il
ponte lo ricavava alla prima mossa verso il basso. Il codice di YouTube Music lo chiarisce:
il riduttore esegue `splice(from, 1)` e poi `splice(to, 0, elemento)`, quindi `toIndex` è
la **posizione finale**, la stessa convenzione di `core.mosse_blocco()`. Con la via
nativa la mossa elementare è comunque quella di YouTube Music; il ponte verifica dopo
ogni mossa che l'elemento atteso sia nella posizione voluta.

**Identità degli elementi.** Ogni mossa e ogni rimozione portano, oltre all'indice, il
`videoId` atteso in quel punto (calcolato simulando le mosse precedenti). Se nel frattempo
la coda è cambiata (avanzamento automatico, un brano aggiunto dalla ricerca), il ponte
cerca l'occorrenza più vicina di quel `videoId`; se non la trova si ferma.

**Aggiornamento immediato e generazioni.** Per non far "saltare" la lista, la copia locale
si aggiorna subito. C'è però una corsa: una lettura della coda partita *prima* della
modifica viene eseguita prima di questa (WebKit esegue i messaggi in ordine) e descrive
la coda vecchia, ma la sua risposta arriva *dopo* l'aggiornamento locale. È il problema
della telemetria con contatore di sequenza: ogni modifica incrementa un **numero di
generazione** e le risposte di una generazione precedente si scartano. La rilettura
successiva alla modifica conferma lo stato reale. Se la pagina conferma meno operazioni
del previsto, la rilettura lo ripristina e il piede della playlist lo segnala.

**Annullamento (`⌘Z`, un livello).** Gli spostamenti si annullano con le mosse inverse in
ordine opposto: se M = mₖ ∘ … ∘ m₁, allora M⁻¹ = m₁⁻¹ ∘ … ∘ mₖ⁻¹, e l'inversa di
(da → a) è (a → da). Le rimozioni avvengono per indici **decrescenti**, quindi ogni indice
registrato è quello originale. Per annullarle il ponte rimette ogni brano per indici
**crescenti**, con le vie dell'interfaccia (così anche il server lo registra): lo
inserisce in fondo con `get_queue` e lo porta al suo posto con `onDrop`. Quando si
reinserisce r_{j+1}, tutti i rimossi con indice minore sono già al loro posto, quindi le
prime r_{j+1} posizioni coincidono con quelle originali e la collocazione in r_{j+1} è
esatta (induzione su j). L'annullamento vale solo finché la lunghezza della coda non è
cambiata per altre vie.

**Il brano in riproduzione** non viene tolto da BiaAmp. YouTube Music lo consentirebbe
(`removeItem` passa prima al brano successivo), ma da una lista in cui si seleziona con
il mouse è facile farlo per sbaglio. Per toglierlo basta passare al brano seguente. Si
può invece spostare.

---

## 4. Costruzione senza Xcode

`build.sh` esegue, nell'ordine:

1. **autotest del motore** (`core.py`) e **controlli statici** (`test/verifica_pyobjc.py`:
   regole PyObjC, selettori dei menu, risorse dichiarate in `setup.py`); se falliscono, la
   costruzione si ferma;
2. ambiente virtuale `.venv` e dipendenze (ruote PyObjC già compilate: nessun compilatore);
3. **pulizia completa** di `build/` e `dist/`;
4. `py2app` → `dist/BiaAmp.app` (registro in `build.log`);
5. **firma ad hoc** (`codesign -s -`), necessaria su Apple Silicon, e stampa delle
   architetture ottenute;
6. `BiaAmp-x.y.z-macOS.zip` e `BiaAmp-x.y.z-macOS.dmg`.

Tutti gli strumenti usati (`codesign`, `ditto`, `hdiutil`, `file`) fanno parte di macOS.

**Quale Python.** Lo script sceglie, nell'ordine: l'interprete indicato con `PYTHON=…`; il
Python di python.org più recente tra 3.14 e 3.10; il `python3` del PATH, purché sia una
build *framework* (requisito di py2app per un'app autonoma). Scarta i Python non framework
(tipicamente pyenv) e non interroga mai `/usr/bin/python3`, che senza gli strumenti di
Apple aprirebbe una finestra di installazione. Se il Python di base cambia, `.venv` viene
ricreato.

**Python di Homebrew: verifica di portabilità.** La versione 1.0.0 è stata costruita con
Python 3.14.7 di Homebrew, le cui librerie contengono percorsi assoluti (`/opt/homebrew/…`).
Leggendo i comandi di caricamento delle 82 intestazioni Mach-O del pacchetto:

- dipendenze dirette (`LC_LOAD_DYLIB`) fuori dal pacchetto e dal sistema: **nessuna**; le
  librerie esterne sono caricate come `@executable_path/../Frameworks/…` da copie interne;
- caricamenti `@rpath/…`: **nessuno**, quindi i 58 percorsi di ricerca
  `LC_RPATH = /opt/homebrew/lib` non vengono mai consultati;
- le altre occorrenze di `/opt/homebrew` sono valori predefiniti compilati che BiaAmp non usa.

Il limite di Homebrew è l'architettura: solo **arm64**. Per i Mac Intel serve il Python
*universal2* di python.org.

**Autenticazione (notarizzazione).** Senza un certificato Developer ID (Apple Developer
Program), Gatekeeper blocca il primo avvio di un'app scaricata: si consente da
*Impostazioni di Sistema ▸ Privacy e sicurezza ▸ Apri comunque* (da macOS 15 il "clic
destro ▸ Apri" non basta più) o rimuovendo la quarantena con `xattr`. `installa.sh` lo fa
per la copia locale.

### Regole PyObjC rispettate

- `@objc.python_method` su ogni metodo Python "puro" delle classi Objective-C;
- azioni (selettori) in camelCase con il solo `_` finale (`comandoMiPiace_`); il metodo
  delegato `control_textView_doCommandBySelector_` ha tre `_` perché ha tre argomenti;
- `NSApplication.sharedApplication()` solo a runtime;
- nessuna esclusione di moduli della libreria standard in `setup.py`;
- **nessuna dichiarazione `protocols=`** per i delegati: i loro metodi sono già noti a
  PyObjC come protocolli informali su `NSObject`. Dichiarandoli, PyObjC 12 confronta le
  firme alla lettera e per i metodi che restituiscono `BOOL` trova `Z` nei metadati e `B`
  nel protocollo di sistema: `TypeError` all'avvio (riscontrato al primo collaudo, ora
  intercettato dal banco di prova);
- **ogni risorsa caricata dall'app è dichiarata in `setup.py`**, altrimenti py2app non la
  copia e l'app non parte (controllato da `verifica_pyobjc.py` dentro `build.sh`).

---

## 5. Verifiche eseguite

| Livello | Strumento | Esito |
|---|---|---|
| Motore | `python3 core.py` | 573 verifiche superate, di cui una esaustiva su 1 793 spostamenti a blocco |
| Presa audio | `node test/test_presa.js` | 19 prove: intervalli, MP4/WebM, `esds`, `trun`, ADTS, FFT contro DFT, `mdhd`/`tfdt` a 32 e 64 bit, MP4 rifiutato → ADTS, separazione tra brani, segmenti in pezzi da 7 byte con `timestampOffset`, buffer video ignorati; sessioni lunghe (1.4.1): `abort()` a metà segmento, pezzo rifiutato e ripetuto, riaggancio (dimensione impossibile, tipo ignoto, firma spezzata), secondo `<video>` nel documento, limite dei blocchi, blocchi sovrapposti e `remove()`, diagnosi, errore passeggero. Ognuna delle 12 difese è verificata per mutazione: togliendola, almeno una prova fallisce |
| Ponte | `node test/test_ponte.js` | 16 prove (gettone di continuazione compreso) contro una simulazione delle vie native ricavate dal codice di YouTube Music: stato, dati della presa, coda, comandi, ricerca, riproduci ora / in coda (radio reattiva spenta), coda vuota; segnaposto e radio, spostamento nativo (`REORDER` al server), componente in ritardo sullo store, ricerca per `videoId`, ripiego locale, rimozione nativa (`DELETE`), ripristino, fine della radio |
| Codice di YouTube Music | lettura del pacchetto `music_polymer_inlined_html.js` (`af273e12`) in una pagina aperta con l'account del progetto, **senza modificare la coda** | riduttore di `MOVE_ITEM` (posizione finale), `removeItem` e `onDrop` con le chiamate al server, `UPDATE_ITEMS` della radio reattiva, `SET_IS_RAAR_ENABLED`, `SET_IS_INFINITE`; coda del server ripristinata in una pagina nuova |
| Ricerca su dati reali | righe autentiche di una risposta di YouTube Music salvata da `ytmusicapi` | 11 righe su 11 riconosciute (titolo, artista, durata, identificativo) |
| Interfaccia | `python3 test/banco.py` (AppKit simulato) | 142 verifiche: avvio, eventi, agganci, playlist modificabile (selezione, trascinamento, tastiera, menu, annullamento, letture superate, pagina che non conferma), riduzione a icona, pannello di ricerca, diagnosi, tre scale, menu; anteprime PNG |
| Regole PyObjC e risorse | `python3 test/verifica_pyobjc.py` | 7 classi, 184 metodi conformi; risorse dichiarate |
| Nomi delle API | metadati ufficiali di PyObjC 12.2.2 | costanti, firme dei blocchi e di `callAsyncJavaScript` confermate |

**Verificato sul Mac.** 1.0.0: costruzione completa, portabilità del pacchetto, avvio,
lettura dello stato, coda, controlli. 1.1–1.2: analizzatore alimentato dalla presa audio
con i segmenti reali di YouTube Music, ricerca e inserimento in coda, selezione e
spostamenti nella playlist. 1.3.0: vie native (`onDrop`, `removeItem`) dentro
WKWebView, registro «via nativa»; rimozioni persistenti sul server (i brani ricomparsi
erano altri: la continuazione della radio). 1.3.1: gettone di continuazione tolto
(la coda non si riempie più), spostamenti e «riproduci dopo» dalla ricerca. **Non ancora
verificato (1.3.2):** la rimozione per indice con chiavi doppie, l'annullamento di una
rimozione, la riduzione a icona. **Da verificare (1.4.1):** l'analisi in una sessione
lunga con salti di brano. Il guasto della 1.4.0 compariva a caso dopo molti brani; la
causa è stata ricostruita e riprodotta in simulazione, non osservata nel registro.
La riga di stato, il piede della playlist e il registro sono predisposti per mostrare
esattamente questi dati.

---

## 6. Risoluzione dei problemi

| Sintomo | Causa probabile | Rimedio |
|---|---|---|
| L'app si chiude all'avvio | eccezione Python | avviarla dal Terminale (`./dist/BiaAmp.app/Contents/MacOS/BiaAmp`) e leggere il traceback; vedi anche *Aiuto ▸ Mostra il registro* |
| Google rifiuta l'accesso | riconoscimento dei browser incorporati | riprovare; se persiste, aggiornare `USER_AGENT` in `core.py` |
| `analisi n/d: nessun flusso audio intercettato` | la presa non vede i `SourceBuffer` (pagina caricata prima della presa, o MSE in un *worker*) | `⌘R` ricarica la pagina; se resta, segnalare la riga |
| `analisi n/d: audio/… · seg N · dec 0 · decodifica…` | WebKit non decodifica il formato ricevuto | segnalare la riga: indica il formato e l'errore esatti |
| `analisi n/d: … · seg 0` | i segmenti arrivano ma non vengono ricomposti | segnalare la riga e il formato |
| `analisi n/d: … · nessun blocco` | nessun segmento decodificato per il brano in ascolto; se `seg` resta fermo, i segmenti non arrivano più alla presa | attendere qualche secondo (riaggancio automatico); se resta, `⌘R` e segnalare le righe `presa audio:` del registro |
| `analisi n/d: … · blocchi N fuori tempo` | i blocchi ci sono ma non coprono l'istante di ascolto | segnalare le righe `presa audio:` del registro con l'ora |
| `… · riagganci N` (con l'analisi attiva) | la ricomposizione ha perso il passo N volte e si è riagganciata da sola | nessuno: è informativo |
| Ricerca: `errore: …` | pagina non pronta o API cambiata | attendere il caricamento della Libreria; poi Web Inspector |
| Brani tolti che ricompaiono | modifica rimasta locale: il server ha ancora la coda vecchia | nel registro deve comparire `via nativa`; con `via locale` l'interfaccia di YouTube Music è cambiata: console, `typeof document.querySelector('#queue').onDrop` e `typeof document.querySelector('#queue').queue.removeItem` |
| La coda si allunga da sola dopo un cambio di brano | è una radio (piede: «radio») e dopo il corrente restano meno di 5 brani | togliere un brano la ferma; oppure menu della playlist ▸ *Ferma la radio* |
| Playlist: `coda non modificabile` | la coda è letta dal DOM (store non raggiungibile) | `⌘R`; se resta, l'API interna è cambiata: console, `__bia.coda('')` |
| Playlist: `…: YouTube Music non ha confermato` | lo store ha rifiutato l'azione o l'elemento non c'è più | la coda viene riletta da sola; il registro riporta quante operazioni sono riuscite e, nella riga `pagina: …`, il motivo dato dal ponte |
| Dopo SHUF la coda è di nuovo piena | per una radio, SHUF chiede al server una sequenza nuova | è YouTube Music; per una coda stabile non usare SHUF sulle radio |
| Pulsanti senza effetto | YouTube ha cambiato le API interne | *Safari ▸ Sviluppo ▸ (Mac) ▸ BiaAmp*, console: `__bia.stato('')`, `__bia.cmd('toggle')`, `__biaPresa.diagnosi()` |

Per il Web Inspector occorre attivare in Safari *Impostazioni ▸ Avanzate ▸ Mostra
funzionalità per sviluppatori web*. BiaAmp dichiara la propria pagina ispezionabile.

---

## 7. Struttura del progetto

```
biaamp/
├── core.py              motore (dominio, nessuna grafica) + autotest
├── app.py               interfaccia Cocoa (PyObjC)
├── setup.py             configurazione py2app
├── build.sh             costruzione completa senza Xcode
├── installa.sh          installazione locale in /Applicazioni
├── requirements.txt
├── risorse/
│   ├── presa.js         presa audio (inizio pagina): segmenti, decodifica, FFT
│   ├── ponte.js         ponte con la pagina (fine pagina): stato, comandi, ricerca, coda e sua modifica
│   ├── BiaAmp.icns      icona dell'app
│   └── BiaAmp.png       icona 1024 px
├── strumenti/
│   └── genera_icona.py  rigenera l'icona (Pillow)
├── test/
│   ├── banco.py         banco di prova di app.py con AppKit simulato
│   ├── finto/           moduli AppKit, Foundation, WebKit, MediaPlayer simulati
│   ├── test_presa.js    prova della presa audio (Node.js)
│   ├── test_ponte.js    prova del ponte (Node.js)
│   └── verifica_pyobjc.py  controllo statico delle regole PyObjC e delle risorse
├── docs/anteprima.png
├── README.md · README-progetto.md · CHANGELOG.md · LICENSE · .gitignore
```

Comandi di verifica:

```bash
python3 core.py                   # motore
python3 test/verifica_pyobjc.py   # regole PyObjC e risorse
node test/test_presa.js           # presa audio
node test/test_ponte.js           # ponte
python3 test/banco.py             # interfaccia (anteprime in test/anteprime)
```

---

## 8. Sviluppi possibili

- **Modalità compatta** (solo barra del titolo con tempo e marquee) con doppio clic.
- **Skin intercambiabili**: la palette e il layout sono già dati, non codice.
- **Equalizzatore**: con MSE in WebKit l'audio non può passare per Web Audio (§2.1), quindi
  un equalizzatore richiederebbe di sostituire la riproduzione stessa: non è previsto.
- **Versione per Linux**: `core.py`, `presa.js` e `ponte.js` sono indipendenti dal sistema;
  basterebbe riscrivere `app.py` con GTK4 e WebKitGTK.
