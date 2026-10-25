/*
 * ponte.js — Ponte tra BiaAmp (Cocoa) e la pagina di YouTube Music.
 *
 * Viene iniettato da WKWebView alla fine del caricamento della pagina
 * (WKUserScriptInjectionTimeAtDocumentEnd, solo frame principale) ed
 * espone l'oggetto window.__bia con tre funzioni che il lato nativo
 * interroga con evaluateJavaScript:
 *
 *   __bia.stato(modo)  → JSON con lo stato del lettore (+ dati dell'analisi
 *                        audio se modo = 'f' spettro o 'w' forma d'onda,
 *                        forniti dalla presa audio di presa.js)
 *   __bia.coda(firma)  → JSON con la coda, oppure {uguale:1} se invariata
 *   __bia.cmd(n, arg)  → esegue un comando, restituisce true/false
 *
 * e altre operazioni, invocate con callAsyncJavaScript:
 *
 *   __bia.cerca(testo, parametri)       → Promise<JSON> risultati di ricerca
 *   __bia.accoda(bersaglio, pos, salta) → Promise<testo> brani inseriti
 *   __bia.sposta(mosse)                 → Promise<numero> spostamenti riusciti
 *   __bia.rimuovi(voci)                 → numero di brani tolti dalla coda
 *   __bia.ripristina()                  → Promise<numero> brani rimessi (annulla)
 *
 * Modello "a interrogazione" (pull): è il lato nativo a chiedere i dati a
 * cadenza fissa. Non si usano timer della pagina, che WebKit rallenta
 * quando la finestra della Libreria è nascosta.
 *
 * I punti di accesso alla pagina (#movie_player, ytmusic-player-bar,
 * #queue) sono API interne di YouTube Music: possono cambiare senza
 * preavviso. Per questo ogni accesso è protetto e ha un ripiego.
 *
 * Le regole CSS delle barre di scorrimento derivano da youtube-music-macos
 * di jem (0xjemm), licenza MIT.
 *
 * © 2026 B. Raucci — Licenza MIT
 */
(function () {
  'use strict';
  if (window.__bia) { return; }

  // ── Barre di scorrimento sottili in stile macOS ─────────────────────────
  try {
    var stile = document.createElement('style');
    stile.textContent =
      '*,*::before,*::after{scrollbar-width:thin!important;scrollbar-color:rgba(255,255,255,.15) transparent!important}' +
      '::-webkit-scrollbar{width:14px!important;height:14px!important}' +
      '::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:transparent!important}' +
      '::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15)!important;border-radius:100px!important;' +
      'border-right:3px solid transparent!important;background-clip:padding-box!important}' +
      '::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.25)!important}';
    (document.head || document.documentElement).appendChild(stile);
  } catch (e) { /* non essenziale */ }

  // ── Accesso agli elementi della pagina ──────────────────────────────────
  function q(sel, radice) { return (radice || document).querySelector(sel); }
  function testo(el) { return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
  function giri(o) {                       // testo di un oggetto {runs:[{text}]}
    if (!o || !o.runs) { return ''; }
    var s = '';
    for (var i = 0; i < o.runs.length; i++) { s += o.runs[i].text || ''; }
    return s;
  }
  function api() { return q('#movie_player'); }
  function barra() { return q('ytmusic-player-bar'); }
  function video() { return q('video.html5-main-video') || q('video'); }
  function numero(x) { return (typeof x === 'number' && isFinite(x)) ? x : 0; }
  function prova(f, ripiego) { try { return f(); } catch (e) { return ripiego; } }
  function clic(sel, radice) {
    var el = q(sel, radice);
    if (el) { el.click(); return true; }
    return false;
  }

  // ── Analisi audio: dati dalla presa (presa.js) ──────────────────────────
  // In WebKit il nodo createMediaElementSource restituisce solo zeri con lo
  // streaming MSE di YouTube (bug WebKit 180696): la presa intercetta invece
  // i segmenti audio e calcola FFT e forma d'onda da sé.
  function base64(u8) {
    var s = '', BLOCCO = 0x8000;
    for (var i = 0; i < u8.length; i += BLOCCO) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + BLOCCO));
    }
    return btoa(s);
  }

  function presa() { return window.__biaPresa || null; }

  // ── Volume: una sola scala, quella del cursore di YouTube Music ─────────
  // updateVolume(a) della barra salva il valore del cursore a nello store
  // (player.volume) e passa al lettore il guadagno esponenziale
  //   g(a) = 2^(0,03917·a + 2,82823) − 7,13431   (limitato a 0…100)
  // (funzione Kfb, opzione music_web_enable_exponential_volume_control).
  // Rileggere getVolume() darebbe g(a): il cursore di BiaAmp tornerebbe
  // indietro (42 → 15). Si legge e si scrive quindi sullo stesso livello: lo
  // store se è leggibile, altrimenti il lettore (setVolume/getVolume, lineari).
  function volumeCursore(b) {
    var a = prova(function () { return b.getState().player.volume; }, null);
    return (typeof a === 'number' && isFinite(a)) ? a : null;
  }

  function leggiViz(modo, v) {
    var p = presa();
    if ((modo !== 'f' && modo !== 'w') || !v || v.paused || !p) { return ''; }
    var dati = modo === 'w' ? p.onda(v) : p.spettro(v);
    return dati ? base64(dati) : '';
  }

  // ── Stato del lettore ───────────────────────────────────────────────────
  function stato(modo) {
    var p = api(), b = barra(), v = video();
    var s = { pronto: !!(p && p.getPlayerState) };
    var st = prova(function () { return p.getPlayerState(); }, -1);
    var cur = prova(function () { return p.getCurrentTime(); }, 0);
    var dur = prova(function () { return p.getDuration(); }, 0);
    var vol = volumeCursore(b);
    if (vol === null) { vol = prova(function () { return p.getVolume(); }, -1); }
    var muto = prova(function () { return p.isMuted(); }, false);
    if (v) {
      if (!numero(cur)) { cur = v.currentTime; }
      if (!(numero(dur) > 0)) { dur = v.duration; }
      if (vol < 0) { vol = Math.round(v.volume * 100); }
      if (st < 0 && v.currentSrc) { st = v.paused ? 2 : 1; }
    }
    s.st = st;
    s.cur = numero(cur);
    s.dur = numero(dur);
    s.vol = vol < 0 ? 100 : vol;
    s.muto = !!muto;

    s.titolo = testo(b && (q('.content-info-wrapper .title', b) || q('.title.ytmusic-player-bar', b)));
    s.byline = testo(b && (q('.content-info-wrapper .byline', b) || q('.byline.ytmusic-player-bar', b)));
    var img = b && (q('.thumbnail-image-wrapper img.image', b) || q('img.image', b));
    s.cop = img ? (img.src || '') : '';
    var vd = prova(function () { return p.getVideoData(); }, null);
    s.vid = (vd && vd.video_id) ? vd.video_id : '';
    if (!s.titolo && vd) { s.titolo = vd.title || ''; s.byline = vd.author || ''; }

    s.rip = prova(function () { return b.getState().queue.repeatMode; }, '');
    s.shuf = !!(b && b.hasAttribute('shuffle-on'));
    var mp = (b && q('#like-button-renderer', b)) || q('#like-button-renderer');
    s.like = mp ? (mp.getAttribute('like-status') || '') : '';

    var viz = prova(function () { return leggiViz(modo, v); }, '');
    s.vk = viz ? modo : '';
    s.viz = viz;
    var p = presa();
    s.fs = p ? p.fs : 0;
    s.nfft = p ? p.nfft : 0;
    s.ad = p ? prova(function () { return p.diagnosi(); }, 'diagnosi non disponibile') : 'presa audio assente';
    return s;
  }

  // ── Coda di riproduzione ────────────────────────────────────────────────
  function firmaDi(stringa) {               // hash djb2: basta a rilevare i cambiamenti
    var h = 5381;
    for (var i = 0; i < stringa.length; i++) { h = ((h << 5) + h + stringa.charCodeAt(i)) | 0; }
    return (h >>> 0).toString(16);
  }

  function vociDaDati() {
    var c = q('#queue');
    var dati = prova(function () { return c.queue.getItems(); }, null);
    if (!dati || !dati.length) { return null; }
    var voci = [];
    for (var i = 0; i < dati.length; i++) {
      var r = rendererCoda(dati[i]);
      // Nessun elemento viene saltato: la posizione nella lista deve restare
      // uguale all'indice interno, usato da SET_INDEX, MOVE_ITEM, REMOVE_ITEM.
      if (!r) { voci.push({ t: '', a: '', d: '', s: false, id: '', x: 1 }); continue; }
      voci.push({ t: giri(r.title), a: giri(r.shortBylineText), d: giri(r.lengthText),
                  s: !!r.selected, id: r.videoId || '' });
    }
    return voci;
  }

  function vociDaDom() {                    // ripiego: lettura degli elementi visibili
    var el = document.querySelectorAll(
      '#queue #contents > ytmusic-player-queue-item,' +
      '#queue #contents > ytmusic-playlist-panel-video-wrapper-renderer > #primary-renderer > ytmusic-player-queue-item');
    var voci = [];
    for (var i = 0; i < el.length; i++) {
      voci.push({ t: testo(q('.song-title', el[i])), a: testo(q('.byline', el[i])),
                  d: testo(q('.duration', el[i])), s: el[i].hasAttribute('selected'), id: '' });
    }
    return voci;
  }

  function coda(firmaPrecedente) {
    var voci = prova(vociDaDati, null);
    // Modificabile solo se letta dallo store: gli indici del DOM non sono affidabili.
    var modificabile = !!voci && !!prova(function () { return q('#queue').dispatch; }, null);
    if (!voci) { voci = prova(vociDaDom, []) || []; }
    var impronta = '';
    for (var i = 0; i < voci.length; i++) {
      impronta += voci[i].id + voci[i].t + (voci[i].s ? '*' : '') + (voci[i].x ? '?' : '') + '|';
    }
    var radio = !!prova(function () { return radioAttiva(q('#queue')); }, false);
    var firma = voci.length + '-' + firmaDi(impronta + ultimoErrore) + (modificabile ? 'm' : '') + (radio ? 'r' : '') + viaUsata.charAt(0);
    if (firma === firmaPrecedente) { return { firma: firma, uguale: 1 }; }
    return { firma: firma, voci: voci, m: modificabile ? 1 : 0, inf: radio ? 1 : 0, via: viaUsata, err: ultimoErrore };
  }

  // ── Comandi ─────────────────────────────────────────────────────────────
  function cmd(nome, arg) {
    var p = api(), b = barra();
    var suona = prova(function () { return p.getPlayerState() === 1; }, false);
    switch (nome) {
      case 'toggle':   if (!p) { return false; } if (suona) { p.pauseVideo(); } else { p.playVideo(); } return true;
      case 'riprendi': if (!p) { return false; } p.playVideo(); return true;
      case 'sospendi': if (!p) { return false; } p.pauseVideo(); return true;
      case 'play':     // come sui lettori classici: Play durante la riproduzione riparte da capo
        if (!p) { return false; } if (suona) { p.seekTo(0, true); } else { p.playVideo(); } return true;
      case 'stop':     if (!p) { return false; } p.pauseVideo(); p.seekTo(0, true); return true;
      case 'next':     return clic('.next-button.ytmusic-player-bar') || !!prova(function () { p.nextVideo(); return true; }, false);
      case 'prev':     return clic('.previous-button.ytmusic-player-bar') || !!prova(function () { p.previousVideo(); return true; }, false);
      case 'seek':     if (!p) { return false; } p.seekTo(Math.max(0, Number(arg) || 0), true); return true;
      case 'volume': {
        var x = Math.max(0, Math.min(100, Math.round(Number(arg) || 0)));
        if (b && b.updateVolume && volumeCursore(b) !== null) { b.updateVolume(x); }    // scala del cursore
        else if (p) { p.setVolume(x); } else { return false; }                          // scala del lettore
        if (x > 0 && p && p.isMuted && p.isMuted()) { p.unMute(); }
        return true;
      }
      case 'shuffle':
        if (prova(function () { b.queue.shuffle(); return true; }, false)) { return true; }
        return clic('.shuffle.ytmusic-player-bar');
      case 'repeat':
        if (prova(function () { b.onRepeatButtonClick(); return true; }, false)) { return true; }
        return clic('.repeat.ytmusic-player-bar');
      case 'like': {
        var mp = (b && q('#like-button-renderer', b)) || q('#like-button-renderer');
        if (!mp) { return false; }
        if (clic('#button-shape-like button', mp)) { return true; }
        return !!prova(function () { mp.updateLikeStatus('LIKE'); return true; }, false);
      }
      case 'indice': {
        var c = q('#queue');
        if (!c || !c.dispatch) { return false; }
        c.dispatch({ type: 'SET_INDEX', payload: Math.max(0, arg | 0) });
        return true;
      }
      case 'ferma_radio': {                 // la coda non si estende più da sola
        var cq = q('#queue');
        if (!cq || !cq.dispatch) { return false; }
        fermaRadio(cq);
        return true;
      }
      case 'cerca': {
        var sb = q('ytmusic-search-box');
        if (!sb) { return false; }
        prova(function () { sb.open(); }, null);
        var campo = q('input', sb);
        if (campo) { campo.focus(); campo.select(); return true; }
        return false;
      }
      default: return false;
    }
  }

  // ── Ricerca e inserimento in coda (asincroni) ───────────────────────────
  // Stesse richieste che fa l'interfaccia di YouTube Music: '/search' per la
  // ricerca, '/music/get_queue' + ADD_ITEMS per l'inserimento in coda.
  function rete() {
    var app = q('ytmusic-app');
    return app && app.networkManager && typeof app.networkManager.fetch === 'function' ? app.networkManager : null;
  }

  function cerca(testo, parametri) {
    var nm = rete();
    if (!nm) { return Promise.reject(new Error('YouTube Music non è ancora pronto')); }
    var corpo = { query: String(testo || '').slice(0, 200) };
    if (parametri) { corpo.params = String(parametri); }
    var statistiche = prova(function () { return q('ytmusic-search-box').getSearchboxStats(); }, null);
    if (statistiche) { corpo.suggestStats = statistiche; }
    return Promise.resolve(nm.fetch('/search', corpo)).then(function (r) {
      return JSON.stringify({ contents: (r && r.contents) || null });
    });
  }

  function rendererCoda(it) {
    it = it || {};
    return it.playlistPanelVideoRenderer ||
      (it.playlistPanelVideoWrapperRenderer && it.playlistPanelVideoWrapperRenderer.primaryRenderer &&
       it.playlistPanelVideoWrapperRenderer.primaryRenderer.playlistPanelVideoRenderer) || null;
  }

  // Inserimento: la stessa richiesta '/music/get_queue' dell'interfaccia (con
  // queueContextParams e posizione, così anche la coda sul server lo registra),
  // poi ADD_ITEMS nello store. Restituisce {indice, n} o lancia un errore.
  function inserisci(c, store, nm, bersaglio, posizione) {
    var statoCoda = store.getState().queue;
    var corpo = { queueContextParams: statoCoda.queueContextParams, queueInsertPosition: posizione };
    if (bersaglio.videoId) { corpo.videoIds = [String(bersaglio.videoId)]; }
    else { corpo.playlistId = String(bersaglio.playlistId || ''); }
    return Promise.resolve(nm.fetch('/music/get_queue', corpo)).then(function (r) {
      var voci = ((r && r.queueDatas) || []).map(function (x) { return x && x.content; }).filter(Boolean);
      if (!voci.length) { throw new Error('YouTube Music non ha restituito brani'); }
      var attuale = store.getState().queue, el = attuale.items || [], corrente = -1;
      for (var i = 0; i < el.length; i++) {
        if (inCorso(el[i])) { corrente = i; break; }
      }
      var indice = (posizione === 'INSERT_AFTER_CURRENT_VIDEO' && corrente >= 0) ? corrente + 1 : el.length;
      c.dispatch({ type: 'ADD_ITEMS', payload: { nextQueueItemId: attuale.nextQueueItemId, index: indice,
                                                 items: voci, shuffleEnabled: false, shouldAssignIds: true } });
      return { indice: indice, n: voci.length };
    });
  }

  function accoda(bersaglio, posizione, salta) {
    bersaglio = bersaglio || {};
    var c = q('#queue'), nm = rete(), store = negozio();
    var statoCoda = prova(function () { return store.getState().queue; }, null);
    var vuota = !statoCoda || !statoCoda.items || !statoCoda.items.length;
    if (!nm || !c || !c.dispatch || vuota) {
      // Nessuna coda attiva: si apre direttamente il brano o la raccolta.
      location.assign(bersaglio.videoId ? '/watch?v=' + encodeURIComponent(bersaglio.videoId)
                                        : '/watch?list=' + encodeURIComponent(bersaglio.playlistId || ''));
      return Promise.resolve('navigazione');
    }
    return inserisci(c, store, nm, bersaglio, posizione).then(function (esito) {
      // Una coda scelta a mano non va più riscritta dalla radio reattiva
      // (prima del salto: il cambio di brano la farebbe intervenire).
      spegniRadioReattiva(c);
      if (salta) { c.dispatch({ type: 'SET_INDEX', payload: esito.indice }); }
      return String(esito.n);
    });
  }

  // ── Modifica della coda: spostamento, rimozione, annullamento ──────────
  // YouTube Music tiene la coda anche sul SERVER (condivisa tra i dispositivi e
  // ripristinata all'apertura per gli abbonati) e, con la «radio reattiva»
  // (isRaarEnabled), a ogni cambio di brano svuota ciò che segue il brano
  // corrente e lo riempie con la versione del server (azione UPDATE_ITEMS).
  // Le modifiche fatte dalla sua interfaccia passano quindi per due vie che
  // aggiornano insieme store, server e radio reattiva; il ponte usa le stesse:
  //   spostamento: <ytmusic-player-queue>.onDrop(evento) → MOVE_ITEM, REORDER
  //                sul server, SET_IS_RAAR_ENABLED false, Cast;
  //   rimozione:   queue.removeItem(chiave) → DELETE sul server, REMOVE_ITEM,
  //                SET_IS_RAAR_ENABLED false, Cast.
  // La chiave di un elemento è navigationEndpoint.watchEndpoint.index, che
  // YouTube Music assegna come identificativo unico. Se le vie native mancano
  // si usano le sole azioni dello store e la diagnosi riporta «locale».
  // Verificato sul codice di music.youtube.com (pacchetto af273e12, ottobre
  // 2026): MOVE_ITEM esegue splice(from, 1) e poi splice(to, 0, elemento),
  // quindi toIndex è la posizione finale.
  function negozio() { var c = q('#queue'); return prova(function () { return c.queue.store.store; }, null); }

  function elementi() {                     // lo store è sincrono: si legge subito l'esito
    var s = negozio();
    var el = prova(function () { return s.getState().queue.items; }, null);
    if (!el) { el = prova(function () { return q('#queue').queue.getItems(); }, null); }
    return el || [];
  }

  function idDi(it) { var r = rendererCoda(it); return r ? (r.videoId || '') : ''; }
  function inCorso(it) { var r = rendererCoda(it); return !!(r && r.selected); }
  function chiaveDi(it) {
    var r = rendererCoda(it), w = r && r.navigationEndpoint && r.navigationEndpoint.watchEndpoint;
    return (w && typeof w.index === 'number') ? String(w.index) : '';
  }

  function risolvi(el, indice, id) {        // indice atteso, altrimenti occorrenza più vicina
    if (indice >= 0 && indice < el.length && (!id || idDi(el[indice]) === id)) { return indice; }
    if (!id) { return -1; }
    var migliore = -1;
    for (var i = 0; i < el.length; i++) {
      if (idDi(el[i]) === id && (migliore < 0 || Math.abs(i - indice) < Math.abs(migliore - indice))) { migliore = i; }
    }
    return migliore;
  }

  var viaUsata = '';                        // 'nativa' | 'locale': per la diagnosi

  function spegniRadioReattiva(c) { c.dispatch({ type: 'SET_IS_RAAR_ENABLED', payload: false }); }

  // La «radio»: una coda nata da una radio o da un Mix porta con sé un gettone
  // di continuazione (queue.continuation, sull'oggetto della coda, non nello
  // store). A ogni cambio di brano, se dopo il corrente restano meno di 5
  // brani e il gettone c'è, YouTube Music chiede al server la continuazione e
  // SOSTITUISCE tutto ciò che segue il corrente con la risposta (UPDATE_ITEMS).
  // Le rimozioni native non toccano il gettone; lo azzera solo YouTube Music
  // stesso quando ricrea la coda (continuation = undefined): qui si fa lo
  // stesso. isInfinite, nello store, non è un indicatore affidabile (false
  // anche con il gettone presente).
  function gettone(c) { return prova(function () { return c.queue.continuation; }, undefined); }

  function radioAttiva(c) {
    return !!gettone(c) || !!prova(function () { return negozio().getState().queue.isInfinite; }, false);
  }

  var radioSospesa;                         // gettone tolto da una rimozione, per l'annullamento

  function fermaRadio(c, sospendi) {        // la coda finisce dove finisce l'elenco
    var g = gettone(c);
    if (g) { radioSospesa = sospendi ? g : undefined; }
    prova(function () { c.queue.continuation = undefined; }, null);
    c.dispatch({ type: 'SET_IS_INFINITE', payload: false });
    spegniRadioReattiva(c);
  }

  function pausa(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }

  // onDrop legge la coda da this.data del componente: prima di ogni mossa
  // deve coincidere con l'elenco dello store (stesso array).
  function allineato(c) {
    var tentativi = 0;
    function prova_() {
      if (c.data === elementi()) { return Promise.resolve(true); }
      if (++tentativi > 40) { return Promise.resolve(false); }
      return (tentativi <= 5 ? Promise.resolve() : pausa(25)).then(prova_);
    }
    return prova_();
  }

  function muoviNativo(c, el, da, a) {
    var it = el[da] || {};
    var dati = it.playlistPanelVideoWrapperRenderer || it.playlistPanelVideoRenderer;
    if (!dati || c.data !== el) { return false; }
    // Lo stesso evento del trascinamento: onDrop cerca dragEl.data in this.data
    // (partenza) e la posizione di dragEl tra i suoi fratelli (arrivo).
    var trascinato = { data: dati }, fratelli = [];
    fratelli[a] = trascinato;
    trascinato.parentElement = { children: fratelli };
    c.onDrop({ detail: { dragEl: trascinato } });
    return true;
  }

  function sposta(mosse) {
    var c = q('#queue');
    if (!c || !c.dispatch || !mosse || !mosse.length) { return Promise.resolve(0); }
    var nativa = typeof c.onDrop === 'function', fatte = 0, k = 0;
    function passo() {
      if (k >= mosse.length) { return Promise.resolve(fatte); }
      return (nativa ? allineato(c) : Promise.resolve(true)).then(function (pronto) {
        var m = mosse[k++] || [], el = elementi();
        var id = String(m[2] || ''), a = m[1] | 0, da = risolvi(el, m[0] | 0, id);
        if (!pronto) { ultimoErrore = 'spostamento: componente non allineato allo store'; return fatte; }
        if (da < 0 || a < 0 || a >= el.length) { ultimoErrore = 'spostamento: ' + m[0] + '→' + a + ' (' + id + ') fuori dalla coda'; return fatte; }
        if (da !== a) {
          if (nativa) {
            if (!muoviNativo(c, el, da, a)) { return fatte; }
            viaUsata = 'nativa';
          } else {
            c.dispatch({ type: 'MOVE_ITEM', payload: { fromIndex: da, toIndex: a } });
            spegniRadioReattiva(c);
            viaUsata = 'locale';
          }
          if (idDi(elementi()[a]) !== id) { ultimoErrore = 'spostamento: ' + id + ' non è arrivato in ' + a; return fatte; }
        }
        fatte++;
        return passo();
      });
    }
    return passo();
  }

  var cestino = [];                         // ultima rimozione: [[indice, videoId], …]

  // f_.prototype.removeItem(chiave) toglie il PRIMO elemento con quella chiave
  // (watchEndpoint.index). Dopo un inserimento o uno SHUF il server può
  // assegnare ai nuovi elementi chiavi già presenti (anche quella del brano
  // corrente): con una chiave ambigua si eseguono per indice le stesse tre
  // operazioni di removeItem: avviso al server (DELETE), REMOVE_ITEM nello
  // store, radio reattiva spenta. Il dispositivo Cast non è gestito.
  function togliNativo(c, el, i) {
    var chiave = chiaveDi(el[i]), unica = !!chiave;
    for (var j = 0; unica && j < el.length; j++) {
      if (j !== i && chiaveDi(el[j]) === chiave) { unica = false; }
    }
    if (unica && typeof c.queue.removeItem === 'function') { c.queue.removeItem(chiave); return true; }
    var sq = c.queue.serverQueueApi;
    if (c.queue.serverQueuesEnabled && !(sq && typeof sq.removeItem === 'function')) { return false; }
    if (c.queue.serverQueuesEnabled) { prova(function () { sq.removeItem(el[i]); }, null); }
    c.dispatch({ type: 'REMOVE_ITEM', payload: i });
    spegniRadioReattiva(c);
    return true;
  }

  var ultimoErrore = '';                    // motivo dell'ultima operazione fallita (diagnosi)

  function rimuovi(voci) {
    var c = q('#queue');
    if (!c || !c.dispatch || !voci || !voci.length) { return 0; }
    var nativa = !!(c.queue && (typeof c.queue.removeItem === 'function' || c.queue.serverQueueApi));
    voci = voci.slice().sort(function (x, y) { return (y[0] | 0) - (x[0] | 0); });   // decrescente
    var tolti = [];
    for (var k = 0; k < voci.length; k++) {
      var el = elementi(), id = String(voci[k][1] || ''), i = risolvi(el, voci[k][0] | 0, id);
      if (i < 0) { ultimoErrore = 'rimozione: elemento ' + voci[k][0] + ' (' + id + ') non trovato'; continue; }
      if (inCorso(el[i])) { continue; }                 // il brano in riproduzione resta
      var n = el.length, idTolto = idDi(el[i]);
      if (nativa) {
        if (!togliNativo(c, el, i)) { ultimoErrore = 'rimozione: via nativa non disponibile per ' + id; break; }
        viaUsata = 'nativa';
      } else {
        c.dispatch({ type: 'REMOVE_ITEM', payload: i });
        spegniRadioReattiva(c);
        viaUsata = 'locale';
      }
      if (elementi().length !== n - 1) { ultimoErrore = 'rimozione: lo store non ha tolto ' + id + ' (indice ' + i + ')'; break; }
      tolti.push([i, idTolto]);
    }
    if (tolti.length) {
      cestino = tolti;
      fermaRadio(c, true);                  // una coda potata a mano non si riempie più da sola
    }
    return tolti.length;
  }

  // Annullamento della rimozione: ogni brano torna con la stessa via
  // dell'inserimento (così il server lo registra) e poi al suo posto con la
  // stessa mossa del trascinamento. Indici crescenti: ogni posto è esatto.
  function ripristina() {
    var c = q('#queue'), s = negozio(), nm = rete();
    if (!c || !c.dispatch || !s || !nm || !cestino.length) { return Promise.resolve(0); }
    var voci = cestino.slice().sort(function (x, y) { return x[0] - y[0]; });
    cestino = [];
    var fatti = 0, k = 0;
    function passo() {
      if (k >= voci.length) { return Promise.resolve(fatti); }
      var v = voci[k++];
      return inserisci(c, s, nm, { videoId: v[1] }, 'INSERT_AT_END').then(function (esito) {
        return sposta([[esito.indice, Math.min(v[0], elementi().length - 1), v[1]]]);
      }).then(function (mossi) {
        if (mossi !== 1) { return fatti; }
        fatti++;
        return passo();
      });
    }
    return passo().then(function (n) {
      if (n === voci.length && radioSospesa) {   // rimozione annullata per intero: la radio riprende
        prova(function () { c.queue.continuation = radioSospesa; }, null);
      }
      radioSospesa = undefined;
      spegniRadioReattiva(c);
      return n;
    });
  }

  window.__bia = {
    versione: '1.4.2',
    stato: function (modo) { return JSON.stringify(stato(modo)); },
    coda: function (firma) { return JSON.stringify(coda(firma)); },
    cmd: function (nome, arg) { try { return !!cmd(nome, arg); } catch (e) { return false; } },
    cerca: function (testo, parametri) { return cerca(testo, parametri); },
    accoda: function (bersaglio, posizione, salta) { return accoda(bersaglio, posizione, !!salta); },
    sposta: function (mosse) {
      try { return sposta(mosse).catch(function () { return -1; }); } catch (e) { return Promise.resolve(-1); }
    },
    rimuovi: function (voci) { try { return rimuovi(voci); } catch (e) { return -1; } },
    ripristina: function () {
      try { return ripristina().catch(function () { return -1; }); } catch (e) { return Promise.resolve(-1); }
    }
  };
})();
