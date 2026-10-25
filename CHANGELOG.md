# Registro delle modifiche

Formato: [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) — versioni secondo
[Semantic Versioning](https://semver.org/lang/it/).

## [1.4.1] — 2026-10-25

### Corretto
- **L'analizzatore si fermava all'improvviso** («analisi n/d: audio/mp4 · seg N · dec N»,
  senza errori) e tornava solo riavviando l'app. YouTube Music riusa lo stesso buffer
  audio da un brano all'altro. Un segmento interrotto da un salto di brano (`abort()`) o
  rifiutato per buffer pieno e poi ripetuto faceva perdere il passo alla ricomposizione
  dei riquadri MP4, che restava in attesa di un riquadro inesistente. Ora la presa:
  - si azzera con `abort()` e con gli errori del buffer;
  - conta solo i dati accettati dal browser;
  - riconosce un'intestazione incoerente (tipo ignoto o dimensione oltre 4 MB) e si
    riaggancia al riquadro successivo.
- La potatura dei blocchi usava il primo `<video>` del documento invece di quello del
  lettore. Inoltre poteva scartare i blocchi del brano in ascolto a vantaggio di quelli
  di brani precedenti.
- Un errore passeggero restava per sempre nella diagnosi.

### Aggiunto
- La diagnosi dice perché manca l'analisi (`nessun blocco`, `blocchi N fuori tempo`) e
  quante volte la presa si è riagganciata (`riagganci N`).

## [1.4.0] — 2026-10-24

### Aggiunto
- **Primo piano solidale**: quando una finestra del deck riceve l'input, le compagne
  agganciate (lettore, playlist, ricerca) salgono con lei sopra le finestre delle altre
  applicazioni, come un'unica finestra.

## [1.3.2] — 2026-10-24

### Corretto
- **Un brano non si toglieva** («YouTube Music non ha confermato», `0 operazioni su 1`)
  quando la sua chiave interna coincideva con quella di un altro elemento, cosa che accade
  dopo un inserimento o uno SHUF. Con chiave ambigua la rimozione avviene per indice, con
  le stesse operazioni del metodo di YouTube Music (server, store, radio reattiva).
- Il registro riporta il motivo dato dalla pagina per un'operazione fallita (`pagina: …`).

### Documentato
- SHUF su una radio chiede al server una sequenza nuova di 50 brani: non è un difetto di
  BiaAmp, ma rimpiazza una coda potata a mano.

## [1.3.1] — 2026-10-24

### Corretto
- **La coda si riempiva di nuovo** (con brani diversi da quelli tolti) al primo cambio di
  brano dopo una rimozione: è la continuazione della radio di YouTube Music, che scatta
  quando dopo il brano corrente restano meno di 5 brani e la coda ha un gettone di
  continuazione (`queue.continuation`). Ora ogni rimozione toglie il gettone, come fa
  YouTube Music quando ricrea la coda; `⌘Z` lo rimette.
- «radio» nel piede e *Ferma la radio* si basano sul gettone, non su `isInfinite`.

## [1.3.0] — 2026-10-24

### Corretto
- **I brani tolti dalla coda ricomparivano** dopo l'inserimento di un nuovo brano. YouTube
  Music conserva la coda anche sul server e la sua «radio reattiva», a ogni cambio di brano,
  riscrive i brani successivi dalla versione del server; BiaAmp modificava solo la copia
  nella pagina. Ora spostamenti e rimozioni passano per le stesse vie dell'interfaccia di
  YouTube Music (`onDrop` del componente della coda, `queue.removeItem`), che aggiornano
  anche il server e spengono la radio reattiva.
- L'annullamento di una rimozione rimette i brani con la stessa via dell'inserimento,
  quindi anche sul server.

### Aggiunto
- Il piede della playlist indica «radio» quando la coda si allunga da sola.
- Menu della playlist: *Ferma la radio (la coda finisce qui)*. Lo stesso avviene da sé
  quando si tolgono tutti i brani dopo quello in riproduzione.
- Registro: `modifiche della coda: via nativa | locale`.

### Modificato
- L'inserimento dalla ricerca spegne la radio reattiva prima del salto al brano.
- Verificato sul codice di YouTube Music che `toIndex` di `MOVE_ITEM` è la posizione
  finale: tolto il riconoscimento della convenzione introdotto nella 1.2.0.

## [1.2.0] — 2026-10-24

### Aggiunto
- **Playlist modificabile.** Selezione multipla (clic, `⇧`-clic, `⌘`-clic, `⇧↑` `⇧↓`, `⌘A`);
  riordino trascinando le righe, con linea d'inserimento e scorrimento automatico ai bordi;
  `⌥↑` `⌥↓` spostano la selezione; `⌫` toglie i brani dalla coda (non quello in
  riproduzione); `⌘Z` annulla l'ultima modifica. Menu contestuale con *Riproduci dopo il
  brano attuale*, *Sposta in fondo alla coda*, *Rimuovi i brani successivi*.
- Ponte: `sposta`, `rimuovi`, `ripristina` sulle azioni dello store della coda
  (`MOVE_ITEM`, `REMOVE_ITEM`, `ADD_ITEMS`). Ogni operazione porta anche il `videoId`
  atteso; la convenzione di `toIndex` viene riconosciuta alla prima mossa verso il basso.
- **Riduzione a icona solidale**: playlist e ricerca seguono il lettore nel Dock e tornano
  agganciate al ripristino (pulsante del deck, `⌘M`, `⌘1`, clic sull'icona nel Dock).

### Corretto
- Una lettura della coda partita prima di una modifica non può più riportare indietro la
  lista (numero di generazione delle letture).
- La lista non torna al brano corrente a ogni aggiornamento della coda, ma solo quando il
  brano cambia.
- Gli elementi della coda che il ponte non riconosce non vengono più saltati (avrebbero
  sfasato gli indici di `SET_INDEX`): compaiono come «—».
- Il messaggio di coda vuota rimandava ancora a `⌘3` per la Libreria.

### Modificato
- `⌘M` con la playlist o la ricerca in primo piano riduce il lettore con le sue compagne.

## [1.1.0] — 2026-10-24

### Aggiunto
- **Pannello Ricerca** agganciato a destra del lettore: campo nativo, filtri Brani / Album /
  Playlist, risultati con *riproduci ora* (doppio clic, `⏎`), *riproduci dopo* (`⇧⏎`) e
  *in fondo alla coda* (`⌥⏎`), menu contestuale; album e playlist inseriti interi.
  Il tasto ⏏ e il tasto `L` portano al campo di ricerca.
- **Scala** 1× / 1,5× / 2× (*Vista ▸ Dimensione*); predefinita 1,5×, pari alle dimensioni
  fisiche del deck classico sui monitor dell'epoca.
- **Presa audio** (`risorse/presa.js`): intercetta i segmenti audio dei `SourceBuffer`,
  li decodifica (direttamente o rincapsulando l'AAC in ADTS) e calcola FFT e forma d'onda
  replicando `AnalyserNode`. Segmenti consegnati a pezzi ricomposti; istanti da `tfdt`.
- Diagnosi della presa sulla riga di stato e nel registro quando l'analisi non è disponibile.
- `test/test_presa.js`, `test/verifica_pyobjc.py` (eseguito anche da `build.sh`).

### Corretto
- Analizzatore e oscilloscopio sempre vuoti: in WebKit `createMediaElementSource` restituisce
  solo zeri con lo streaming MSE (bug WebKit 180696). La via Web Audio è stata rimossa.
- `build.sh` trova da solo un Python framework adatto e ricrea `.venv` se cambia interprete.
- Avvio: rimossa la dichiarazione `protocols=` che faceva fallire PyObjC 12.

### Modificato
- Il pulsante LIB del lettore diventa RIC (ricerca); la Libreria resta in *Finestra* (`⌘4`)
  e nel pannello di ricerca.

## [1.0.0] — 2026-10-24

Prima versione, derivata da *youtube-music-macos* (Swift) e riscritta in Python + PyObjC.

### Aggiunto
- Finestra **Lettore** senza cornice, disegnata in Core Graphics: display a sette segmenti
  (tempo trascorso / rimanente, lampeggio in pausa), marquee, volume, posizione, trasporto,
  interruttori casuale / ripetizione / mi piace / playlist / libreria.
- **Analizzatore di spettro** a 22 bande logaritmiche (40 Hz – 16 kHz) dall'FFT di Web Audio,
  con compensazione dello spettro rosa, barre del 1° ordine e picchi in caduta grave;
  **oscilloscopio** con trigger sul fronte di salita.
- Finestra **Playlist** con la coda di YouTube Music, ridimensionabile, agganciabile.
- Finestra **Libreria** (WKWebView) con music.youtube.com; i link esterni si aprono nel browser.
- Ponte JavaScript `ponte.js` a interrogazione (pull), robusto al rallentamento delle pagine nascoste.
- Integrazione con il Centro di Controllo (titolo, artista, album, copertina, avanzamento)
  e con i tasti multimediali.
- Scorciatoie da tastiera dei lettori classici (Z X C V B) e menu completi.
- Impostazioni persistenti con scrittura atomica; registro in `~/Library/Logs/BiaAmp.log`.
- `build.sh` (autotest → py2app → firma ad hoc → ZIP + DMG) e `installa.sh`; nessun Xcode richiesto.
- Banco di prova per Linux/macOS senza display (`test/`): AppKit simulato e anteprime rasterizzate.

### Rimosso rispetto all'originale
- Discord Rich Presence.
