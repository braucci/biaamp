/*
 * presa.js — Presa audio per l'analizzatore di BiaAmp.
 *
 * PERCHÉ ESISTE. In WebKit, quando l'audio arriva da Media Source
 * Extensions (lo streaming usato da YouTube Music), il nodo Web Audio che
 * attinge all'elemento <video> (createMediaElementSource) produce solo zeri:
 * la musica suona, ma l'analizzatore riceve silenzio (WebKit bug 180696,
 * ancora aperto). Nessuna impostazione lo aggira.
 *
 * COME FUNZIONA. Questo script viene iniettato all'INIZIO della pagina, prima
 * del lettore di YouTube, e intercetta in modo passivo i segmenti audio che
 * il lettore carica nel SourceBuffer:
 *   1. copia ogni segmento e ne ricava l'istante t0: per l'MP4 frammentato
 *      dal riquadro 'tfdt' (con i byte accumulati finché il riquadro è
 *      completo, perché il lettore può consegnare un segmento a pezzi); per
 *      il WebM dall'intervallo di tempo appena bufferizzato;
 *   2. lo decodifica a parte con decodeAudioData (un OfflineAudioContext che
 *      non suona nulla); se WebKit rifiuta l'MP4 frammentato, ne estrae i
 *      fotogrammi AAC e li rincapsula in ADTS, formato che decodifica sempre;
 *   3. su richiesta calcola l'FFT dei 2048 campioni che terminano
 *      all'istante di riproduzione, replicando AnalyserNode (finestra di
 *      Blackman, modulo / N, scala in dB tra minDecibels e maxDecibels).
 * L'audio udibile non viene toccato in alcun modo.
 *
 * SESSIONI LUNGHE (1.4.1). YouTube Music riusa lo stesso SourceBuffer da un
 * brano all'altro. Se un segmento viene interrotto (salto di brano: abort()) o
 * rifiutato (buffer pieno: appendBuffer lancia un'eccezione e il lettore lo
 * ripete), i byte in sospeso non devono saldarsi ai successivi: la presa si
 * azzera come l'analizzatore del browser, ignora i dati rifiutati e, se
 * comunque perde l'allineamento, si riaggancia al riquadro 'moof' successivo.
 *
 * © 2026 B. Raucci — Licenza MIT
 */
(function () {
  'use strict';
  if (window.__biaPresa) { return; }

  var FS = 48000, N = 2048, MIN_DB = -90, MAX_DB = -20;
  var MAX_BLOCCHI = 40;
  var stato = { mime: '', segmenti: 0, decodificati: 0, errore: '', metodo: '', blocchi: [],
                riagganci: 0, manca: '' };
  var presi = new WeakMap();          // SourceBuffer audio → stato della presa
  var urlSorgenti = {};               // blob URL → MediaSource
  var ordineUrl = [];
  var decoder = null;
  var ultimoVideo = null;             // il <video> che il ponte fa analizzare
  var sequenza = 0;                   // ordine di arrivo dei blocchi

  // ── utilità ──────────────────────────────────────────────────────────────
  function contestoDecodifica() {
    if (!decoder) {
      var O = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!O) { throw new Error('OfflineAudioContext assente'); }
      decoder = new O(1, 1, FS);
    }
    return decoder;
  }

  function copia(dati) {
    if (dati instanceof ArrayBuffer) { return new Uint8Array(dati.slice(0)); }
    if (ArrayBuffer.isView(dati)) {
      return new Uint8Array(dati.buffer.slice(dati.byteOffset, dati.byteOffset + dati.byteLength));
    }
    return null;
  }

  function intervalli(tr) {
    var r = [];
    try { for (var i = 0; i < tr.length; i++) { r.push([tr.start(i), tr.end(i)]); } } catch (e) { /* SourceBuffer rimosso */ }
    return r;
  }

  // Inizio della porzione di 'dopo' non coperta da 'prima' (la più lunga).
  function inizioNuovo(prima, dopo) {
    var migliore = null, durata = 0;
    for (var i = 0; i < dopo.length; i++) {
      var pezzi = [[dopo[i][0], dopo[i][1]]];
      for (var j = 0; j < prima.length; j++) {
        var p0 = prima[j][0], p1 = prima[j][1], nuovi = [];
        for (var k = 0; k < pezzi.length; k++) {
          var x0 = pezzi[k][0], x1 = pezzi[k][1];
          if (p1 <= x0 || p0 >= x1) { nuovi.push([x0, x1]); continue; }
          if (p0 > x0) { nuovi.push([x0, p0]); }
          if (p1 < x1) { nuovi.push([p1, x1]); }
        }
        pezzi = nuovi;
      }
      for (var m = 0; m < pezzi.length; m++) {
        var d = pezzi[m][1] - pezzi[m][0];
        if (d > durata) { durata = d; migliore = pezzi[m][0]; }
      }
    }
    return durata > 0.01 ? migliore : null;
  }

  function u32(u, p) { return ((u[p] << 24) >>> 0) + (u[p + 1] << 16) + (u[p + 2] << 8) + u[p + 3]; }
  function tipo4(u, p) { return String.fromCharCode(u[p], u[p + 1], u[p + 2], u[p + 3]); }
  function eWebm(u) { return u.length >= 4 && u[0] === 0x1A && u[1] === 0x45 && u[2] === 0xDF && u[3] === 0xA3; }

  function cercaSequenza(u, seq, da) {
    outer: for (var i = da || 0; i + seq.length <= u.length; i++) {
      for (var k = 0; k < seq.length; k++) { if (u[i + k] !== seq[k]) { continue outer; } }
      return i;
    }
    return -1;
  }

  // Separa l'eventuale segmento di inizializzazione dai dati multimediali.
  function separa(u) {
    if (eWebm(u)) {
      var c = cercaSequenza(u, [0x1F, 0x43, 0xB6, 0x75]);          // primo Cluster
      return c < 0 ? { init: u, media: null, contenitore: 'webm' }
                   : { init: u.subarray(0, c), media: u.subarray(c), contenitore: 'webm' };
    }
    if (u.length >= 8 && /^(ftyp|moov)$/.test(tipo4(u, 4))) {
      var p = 0;
      while (p + 8 <= u.length) {
        var t = tipo4(u, p + 4), dim = u32(u, p);
        if (t === 'moof' || t === 'styp' || t === 'sidx') {
          return { init: u.subarray(0, p), media: u.subarray(p), contenitore: 'mp4' };
        }
        if (dim < 8) { break; }
        p += dim;
      }
      return { init: u, media: null, contenitore: 'mp4' };
    }
    return { init: null, media: u, contenitore: '' };
  }

  // ── MP4 frammentato → ADTS (solo AAC) ────────────────────────────────────
  // Riquadri ISO-BMFF: [dimensione 32 bit][tipo 4 caratteri][contenuto].
  function figli(u, inizio, fine, cb) {
    var p = inizio;
    while (p + 8 <= fine) {
      var dim = u32(u, p), t = tipo4(u, p + 4), testa = 8;
      if (dim === 1) { dim = u32(u, p + 8) * 4294967296 + u32(u, p + 12); testa = 16; }
      else if (dim === 0) { dim = fine - p; }
      if (dim < testa || p + dim > fine) { return; }
      if (cb(t, p, p + testa, p + dim) === false) { return; }
      p += dim;
    }
  }

  // Lunghezza dei descrittori MPEG-4 (fino a 4 byte, 7 bit utili ciascuno).
  function descrittore(u, p) {
    var tag = u[p], lung = 0, i = p + 1;
    for (var k = 0; k < 4; k++) { var b = u[i++]; lung = (lung << 7) | (b & 0x7F); if (!(b & 0x80)) { break; } }
    return { tag: tag, inizio: i, fine: i + lung };
  }

  // AudioSpecificConfig da 'esds' → { profilo, indiceFreq, canali }.
  function configurazioneAac(init) {
    var e = cercaSequenza(init, [0x65, 0x73, 0x64, 0x73]);          // 'esds'
    if (e < 4) { return null; }
    var fine = e - 4 + u32(init, e - 4), p = e + 4 + 4;             // salta versione e flag
    var es = descrittore(init, p);
    if (es.tag !== 0x03) { return null; }
    p = es.inizio + 2;
    var flag = init[p++];
    if (flag & 0x80) { p += 2; }
    if (flag & 0x40) { p += 1 + init[p]; }
    if (flag & 0x20) { p += 2; }
    var dc = descrittore(init, p);
    if (dc.tag !== 0x04) { return null; }
    var dsi = descrittore(init, dc.inizio + 13);
    if (dsi.tag !== 0x05 || dsi.fine > fine) { return null; }
    var a = init[dsi.inizio], b = init[dsi.inizio + 1];
    var aot = a >> 3, indice = ((a & 7) << 1) | (b >> 7), canali = (b >> 3) & 0x0F;
    if (aot === 0 || aot === 31 || indice === 15) { return null; }
    return { profilo: Math.min(aot, 4) - 1, indiceFreq: indice, canali: canali };
  }

  // Fotogrammi AAC di un frammento (moof + mdat) secondo 'tfhd' e 'trun'.
  function fotogrammi(u) {
    var uscita = [];
    figli(u, 0, u.length, function (t, p, c0, c1) {
      if (t !== 'moof') { return true; }
      var inizioMoof = p;
      figli(u, c0, c1, function (t2, p2, d0, d1) {
        if (t2 !== 'traf') { return true; }
        var base = inizioMoof, dimPredef = 0;
        figli(u, d0, d1, function (t3, p3, e0) {
          if (t3 === 'tfhd') {
            var f = (u[e0 + 1] << 16) | (u[e0 + 2] << 8) | u[e0 + 3], q = e0 + 8;
            if (f & 0x01) { base = u32(u, q) * 4294967296 + u32(u, q + 4); q += 8; }
            if (f & 0x02) { q += 4; }
            if (f & 0x08) { q += 4; }
            if (f & 0x10) { dimPredef = u32(u, q); }
          } else if (t3 === 'trun') {
            var g = (u[e0 + 1] << 16) | (u[e0 + 2] << 8) | u[e0 + 3];
            var n = u32(u, e0 + 4), r = e0 + 8, dati = base;
            if (g & 0x01) { dati = base + (u32(u, r) | 0); r += 4; }
            if (g & 0x04) { r += 4; }
            for (var i = 0; i < n; i++) {
              if (g & 0x100) { r += 4; }
              var dim = dimPredef;
              if (g & 0x200) { dim = u32(u, r); r += 4; }
              if (g & 0x400) { r += 4; }
              if (g & 0x800) { r += 4; }
              if (dim > 0 && dati + dim <= u.length) { uscita.push(u.subarray(dati, dati + dim)); }
              dati += dim;
            }
          }
          return true;
        });
        return true;
      });
      return true;
    });
    return uscita;
  }

  function adts(cfg, frames) {
    var totale = 0, i;
    for (i = 0; i < frames.length; i++) { totale += 7 + frames[i].length; }
    var out = new Uint8Array(totale), p = 0;
    for (i = 0; i < frames.length; i++) {
      var len = 7 + frames[i].length;
      out[p] = 0xFF;
      out[p + 1] = 0xF1;
      out[p + 2] = (cfg.profilo << 6) | (cfg.indiceFreq << 2) | (cfg.canali >> 2);
      out[p + 3] = ((cfg.canali & 3) << 6) | (len >> 11);
      out[p + 4] = (len >> 3) & 0xFF;
      out[p + 5] = ((len & 7) << 5) | 0x1F;
      out[p + 6] = 0xFC;
      out.set(frames[i], p + 7);
      p += len;
    }
    return out;
  }

  // ── decodifica ───────────────────────────────────────────────────────────
  function accoda(ms, t0, ab) {
    var n = ab.length, canali = ab.numberOfChannels, mono = new Float32Array(n);
    for (var c = 0; c < canali; c++) {
      var d = ab.getChannelData(c);
      for (var i = 0; i < n; i++) { mono[i] += d[i] / canali; }
    }
    stato.blocchi.push({ ms: ms, t0: t0, fs: ab.sampleRate, dati: mono, n: ++sequenza, arrivo: Date.now() });
    stato.decodificati++;
    stato.errore = '';                // la diagnosi descrive il presente, non un guasto passato
    pota();
  }

  function decodifica(buffer, cb, errore) {
    var ctx;
    try { ctx = contestoDecodifica(); } catch (e) { errore(e); return; }
    var chiuso = false;
    var ok = function (ab) { if (!chiuso) { chiuso = true; cb(ab); } };
    var ko = function (e) { if (!chiuso) { chiuso = true; errore(e); } };
    try {
      var p = ctx.decodeAudioData(buffer, ok, ko);
      if (p && typeof p.then === 'function') { p.then(ok, ko); }
    } catch (e) { ko(e); }
  }

  function nomeErrore(e) { return (e && (e.name || e.message)) || String(e); }

  function decodificaAdts(st, media, t0) {
    var cfg = st.aac || (st.aac = configurazioneAac(st.init));
    if (!cfg) { stato.errore = 'esds AAC non trovato'; return; }
    var fr = fotogrammi(media);
    if (!fr.length) { stato.errore = 'nessun fotogramma AAC nel frammento'; return; }
    decodifica(adts(cfg, fr).buffer, function (ab) { stato.metodo = 'adts'; accoda(st.ms, t0, ab); },
      function (e) { stato.errore = 'decodifica ADTS: ' + nomeErrore(e); });
  }

  function elabora(st, media, t0) {
    stato.segmenti++;
    if (st.contenitore === 'mp4' && st.metodo === 'adts') { decodificaAdts(st, media, t0); return; }
    var file = new Uint8Array(st.init.length + media.length);
    file.set(st.init, 0);
    file.set(media, st.init.length);
    decodifica(file.buffer, function (ab) { st.metodo = 'diretto'; stato.metodo = 'diretto'; accoda(st.ms, t0, ab); },
      function (e) {
        if (st.contenitore === 'mp4') { st.metodo = 'adts'; decodificaAdts(st, media, t0); }
        else { stato.errore = 'decodifica: ' + nomeErrore(e); }
      });
  }

  function unisci(a, b) {
    var u = new Uint8Array(a.length + b.length);
    u.set(a, 0);
    u.set(b, a.length);
    return u;
  }

  // Scala dei tempi della traccia ('mdhd': versione 0 → offset 12, 1 → offset 20).
  function scalaTempi(init) {
    var m = cercaSequenza(init, [0x6D, 0x64, 0x68, 0x64]);          // 'mdhd'
    if (m < 0) { return 0; }
    var c = m + 4;
    return u32(init, c + (init[c] === 1 ? 20 : 12));
  }

  // Istante di decodifica del frammento ('tfdt' in moof/traf).
  function tempoBase(frammento) {
    var risultato = null;
    figli(frammento, 0, frammento.length, function (t, p, c0, c1) {
      if (t !== 'moof') { return true; }
      figli(frammento, c0, c1, function (t2, p2, d0, d1) {
        if (t2 !== 'traf') { return true; }
        figli(frammento, d0, d1, function (t3, p3, e0) {
          if (t3 !== 'tfdt') { return true; }
          risultato = frammento[e0] === 1 ? u32(frammento, e0 + 4) * 4294967296 + u32(frammento, e0 + 8)
                                          : u32(frammento, e0 + 4);
          return false;
        });
        return risultato === null;
      });
      return risultato === null;
    });
    return risultato;
  }

  // MP4: i byte si accumulano e si elaborano solo i riquadri completi, quindi
  // un segmento consegnato in più pezzi (protocollo SABR) viene ricomposto.
  // Il tempo viene da tfdt / scala + timestampOffset, non dal buffer.
  //
  // Un'intestazione è credibile solo se il tipo è uno dei riquadri di primo
  // livello di un MP4 frammentato e la dimensione è plausibile per l'audio (un
  // frammento di 10 s a 256 kbit/s occupa circa 320 kB). Altrimenti
  // l'allineamento è perso: senza questo controllo quattro byte qualsiasi letti
  // come «dimensione» (fino a 4 GB) facevano attendere la presa per sempre.
  var ACCUMULO_MAX = 16 * 1024 * 1024;
  var RIQUADRO_MAX = 4 * 1024 * 1024;
  var TIPI_MP4 = /^(ftyp|styp|moov|moof|mdat|sidx|ssix|emsg|prft|free|skip|uuid|mfra|meta|pdin)$/;

  function azzera(st) { st.accumulo = null; st.moof = null; st.attesa = null; st.prima = null; }

  // Primo riquadro 'moof', 'ftyp', 'styp' o 'moov' con dimensione plausibile
  // che inizia da 'da' in poi; -1 se non c'è.
  function riaggancio(a, da) {
    for (var i = da + 4; i + 4 <= a.length; i++) {
      var c = a[i];
      if (c !== 0x6D && c !== 0x66 && c !== 0x73) { continue; }       // m, f, s
      var t = tipo4(a, i);
      if (t !== 'moof' && t !== 'ftyp' && t !== 'styp' && t !== 'moov') { continue; }
      var dim = u32(a, i - 4);
      if (dim >= 8 && dim <= RIQUADRO_MAX) { return i - 4; }
    }
    return -1;
  }

  function riceviMp4(st, sb, u) {
    var a = st.accumulo ? unisci(st.accumulo, u) : u, p = 0;
    if (a.length > ACCUMULO_MAX) { azzera(st); st.perso = true; return; }
    while (p + 8 <= a.length) {
      var dim = u32(a, p), t = tipo4(a, p + 4), testa = 8;
      if (dim === 1) {
        if (p + 16 > a.length) { break; }
        dim = u32(a, p + 8) * 4294967296 + u32(a, p + 12);
        testa = 16;
      }
      if (!TIPI_MP4.test(t) || dim < testa || dim > RIQUADRO_MAX) {   // allineamento perso
        if (!st.perso) { st.perso = true; stato.riagganci++; }
        st.moof = null;
        var q = riaggancio(a, p + 1);
        if (q < 0) {                  // tengo la coda: la firma può essere a cavallo di due pezzi
          st.accumulo = a.slice(Math.max(p + 1, a.length - 7));
          return;
        }
        p = q;
        continue;
      }
      if (p + dim > a.length) { break; }                      // incompleto: aspetto altri byte
      st.perso = false;
      var r = a.subarray(p, p + dim);
      if (t === 'ftyp') {
        st.ftyp = new Uint8Array(r);
      } else if (t === 'moov') {
        st.init = st.ftyp ? unisci(st.ftyp, r) : new Uint8Array(r);
        st.scala = scalaTempi(st.init);
        st.contenitore = 'mp4';
        st.aac = null;
      } else if (t === 'moof') {
        st.moof = new Uint8Array(r);
      } else if (t === 'mdat') {
        if (st.moof && st.init && st.scala) {
          var frammento = unisci(st.moof, r), base = tempoBase(frammento);
          if (base !== null) {
            var offset = 0;
            try { offset = sb.timestampOffset || 0; } catch (e) { offset = 0; }
            elabora(st, frammento, base / st.scala + offset);
          }
        }
        st.moof = null;
      }
      p += dim;
    }
    st.accumulo = p < a.length ? a.slice(p) : null;
  }

  function concluso(sb) {
    var st = presi.get(sb);
    if (!st || !st.attesa) { return; }
    var dati = st.attesa, prima = st.prima || [];
    st.attesa = null;
    st.prima = null;
    var parti = separa(dati);
    if (parti.init && parti.init.length) {
      st.init = new Uint8Array(parti.init);
      st.contenitore = parti.contenitore;
      st.aac = null;
    }
    if (!parti.media || !parti.media.length || !st.init) { return; }
    var t0 = inizioNuovo(prima, intervalli(sb.buffered));
    if (t0 === null) { return; }
    elabora(st, new Uint8Array(parti.media), t0);
  }

  // Il <video> del lettore: quello che il ponte fa analizzare o, prima della
  // prima analisi, quello principale di YouTube. Non il primo del documento,
  // che può essere un'anteprima o un annuncio con un istante tutto suo.
  function videoLettore() {
    return ultimoVideo || document.querySelector('video.html5-main-video') || document.querySelector('video');
  }

  // Tiene i blocchi utili. Del brano in ascolto (MediaSource collegato al video)
  // si scartano quelli finiti da più di 15 s, salvo se appena arrivati: il
  // lettore può accodare un brano e solo dopo spostare l'istante di ascolto.
  // Quelli di altri brani restano finché c'è posto. Oltre MAX_BLOCCHI si
  // sacrificano prima i blocchi di altri brani (i più vecchi), poi quelli più
  // lontani dall'istante di ascolto.
  function pota() {
    var v = videoLettore(), t = v ? v.currentTime : 0, ms = v ? sorgenteDi(v) : null, ora = Date.now();
    var corrente = function (b) { return !ms || b.ms === ms; };
    stato.blocchi = stato.blocchi.filter(function (b) {
      if (!corrente(b)) { return true; }
      return b.t0 + b.dati.length / b.fs > t - 15 || ora - b.arrivo < 20000;
    });
    if (stato.blocchi.length > MAX_BLOCCHI) {
      var peso = function (b) {
        if (!corrente(b)) { return 1e12 + (sequenza - b.n); }
        var fine = b.t0 + b.dati.length / b.fs;
        return Math.max(0, b.t0 - t, t - fine);
      };
      stato.blocchi.sort(function (a, b) { return peso(a) - peso(b); });
      stato.blocchi.length = MAX_BLOCCHI;
    }
    stato.blocchi.sort(function (a, b) { return a.t0 - b.t0; });
  }

  // ── intercettazione (passiva) di MediaSource e SourceBuffer ─────────────
  function avvolgi(proto, nome, fabbrica) {
    if (!proto || typeof proto[nome] !== 'function' || proto[nome].__bia) { return; }
    var nuovo = fabbrica(proto[nome]);
    nuovo.__bia = true;
    proto[nome] = nuovo;
  }

  function suAddSourceBuffer(originale) {
    return function (tipo) {
      var sb = originale.apply(this, arguments);
      try {
        if (/^audio\//i.test(String(tipo)) && !presi.has(sb)) {
          presi.set(sb, { ms: this, init: null, attesa: null, prima: null, contenitore: '', metodo: '', aac: null,
                          mp4: /^audio\/mp4/i.test(String(tipo)), accumulo: null, ftyp: null, moof: null, scala: 0,
                          perso: false });
          stato.mime = String(tipo);
          sb.addEventListener('updateend', function () {
            try { concluso(sb); } catch (e) { stato.errore = 'updateend: ' + nomeErrore(e); }
          });
          // Errore di decodifica nel browser: il suo analizzatore si azzera, la presa pure.
          sb.addEventListener('error', function () { var st = presi.get(sb); if (st) { azzera(st); } });
        }
      } catch (e) { stato.errore = 'addSourceBuffer: ' + nomeErrore(e); }
      return sb;
    };
  }

  // I dati entrano nella presa solo se il browser li ha accettati: un
  // appendBuffer che lancia (buffer pieno) verrà ripetuto dal lettore, e
  // contarlo due volte salderebbe un pezzo a se stesso.
  function suAppendBuffer(originale) {
    return function (dati) {
      var st = presi.get(this), copiati = null, prima = null;
      if (st) {
        try {
          copiati = copia(dati);
          if (!st.mp4) { prima = intervalli(this.buffered); }
        } catch (e) { copiati = null; stato.errore = 'appendBuffer: ' + nomeErrore(e); }
        if (!st.mp4) { st.attesa = copiati; st.prima = prima; }    // WebM: elaborato a updateend
      }
      var risultato;
      try { risultato = originale.apply(this, arguments); }
      catch (e) { if (st) { st.attesa = null; st.prima = null; } throw e; }
      if (st && st.mp4 && copiati) {                                // MP4: subito, tempo da tfdt
        try { riceviMp4(st, this, copiati); }
        catch (e) { azzera(st); stato.errore = 'appendBuffer: ' + nomeErrore(e); }
      }
      return risultato;
    };
  }

  // abort() azzera l'analizzatore del browser (salto di brano, ricerca nel
  // brano): i byte di un segmento interrotto non arriveranno più.
  function suAbort(originale) {
    return function () {
      var st = presi.get(this);
      if (st) { azzera(st); }
      return originale.apply(this, arguments);
    };
  }

  // remove(inizio, fine): il lettore libera un tratto del buffer; i blocchi
  // interamente compresi non verranno più riprodotti.
  function suRemove(originale) {
    return function (inizio, fine) {
      var risultato = originale.apply(this, arguments);
      var st = presi.get(this);
      if (st && typeof inizio === 'number' && typeof fine === 'number') {
        stato.blocchi = stato.blocchi.filter(function (b) {
          return b.ms !== st.ms || b.t0 < inizio || b.t0 + b.dati.length / b.fs > fine;
        });
      }
      return risultato;
    };
  }

  // changeType(): stesso SourceBuffer, formato nuovo; seguirà un nuovo init.
  function suChangeType(originale) {
    return function (tipo) {
      var risultato = originale.apply(this, arguments);
      var st = presi.get(this);
      if (st) {
        azzera(st);
        st.init = null; st.ftyp = null; st.scala = 0; st.aac = null; st.metodo = ''; st.contenitore = '';
        st.mp4 = /^audio\/mp4/i.test(String(tipo));
        stato.mime = String(tipo);
      }
      return risultato;
    };
  }

  function suCreateObjectURL(originale) {
    return function (oggetto) {
      var url = originale.apply(this, arguments);
      try {
        if (window.MediaSource && oggetto instanceof window.MediaSource) {
          urlSorgenti[url] = oggetto;
          ordineUrl.push(url);
          while (ordineUrl.length > 50) { delete urlSorgenti[ordineUrl.shift()]; }
        }
      } catch (e) { /* non essenziale */ }
      return url;
    };
  }

  try {
    if (window.MediaSource) { avvolgi(window.MediaSource.prototype, 'addSourceBuffer', suAddSourceBuffer); }
    if (window.ManagedMediaSource) { avvolgi(window.ManagedMediaSource.prototype, 'addSourceBuffer', suAddSourceBuffer); }
    [window.SourceBuffer, window.ManagedSourceBuffer].forEach(function (C) {
      if (!C) { return; }
      avvolgi(C.prototype, 'appendBuffer', suAppendBuffer);
      avvolgi(C.prototype, 'abort', suAbort);
      avvolgi(C.prototype, 'remove', suRemove);
      avvolgi(C.prototype, 'changeType', suChangeType);
    });
    if (window.URL) { avvolgi(window.URL, 'createObjectURL', suCreateObjectURL); }
  } catch (e) { stato.errore = 'installazione: ' + nomeErrore(e); }

  // ── finestra di campioni e analisi ──────────────────────────────────────
  function sorgenteDi(v) {
    try { if (v.srcObject && window.MediaSource && v.srcObject instanceof window.MediaSource) { return v.srcObject; } }
    catch (e) { /* proseguo */ }
    return urlSorgenti[v.currentSrc || v.src] || null;
  }

  function finestra(v, n) {
    ultimoVideo = v;
    var t = v.currentTime, ms = sorgenteDi(v), blocco = null, propri = 0;
    for (var i = stato.blocchi.length - 1; i >= 0; i--) {
      var b = stato.blocchi[i];
      if (ms && b.ms !== ms) { continue; }
      propri++;
      // a parità di copertura vale il blocco arrivato per ultimo (brano nuovo
      // caricato sugli stessi istanti di uno vecchio)
      if (t >= b.t0 && t < b.t0 + b.dati.length / b.fs && (!blocco || b.n > blocco.n)) { blocco = b; }
    }
    // Perché manca l'analisi: nessun blocco del brano, o blocchi che non coprono l'istante.
    stato.manca = blocco ? '' : (propri ? 'blocchi ' + propri + ' fuori tempo' : 'nessun blocco');
    if (!blocco) { return null; }
    var fine = Math.floor((t - blocco.t0) * blocco.fs), inizio = fine - n + 1, x = new Float32Array(n);
    for (var k = 0; k < n; k++) {
      var j = inizio + k;
      x[k] = (j >= 0 && j < blocco.dati.length) ? blocco.dati[j] : 0;
    }
    return x;
  }

  var finestraBlackman = null, inverso = null, coseni = null, seni = null;

  function preparaFFT(n) {
    var bit = Math.round(Math.log(n) / Math.LN2), i;
    finestraBlackman = new Float64Array(n);
    for (i = 0; i < n; i++) {
      finestraBlackman[i] = 0.42 - 0.5 * Math.cos(2 * Math.PI * i / n) + 0.08 * Math.cos(4 * Math.PI * i / n);
    }
    inverso = new Uint32Array(n);
    for (i = 0; i < n; i++) {
      var r = 0, x = i;
      for (var b = 0; b < bit; b++) { r = (r << 1) | (x & 1); x >>= 1; }
      inverso[i] = r;
    }
    coseni = new Float64Array(n / 2);
    seni = new Float64Array(n / 2);
    for (i = 0; i < n / 2; i++) { coseni[i] = Math.cos(-2 * Math.PI * i / n); seni[i] = Math.sin(-2 * Math.PI * i / n); }
  }

  // FFT radix-2 iterativa (Cooley–Tukey), sul posto.
  function fft(re, im) {
    var n = re.length, i, t;
    if (!inverso || inverso.length !== n) { preparaFFT(n); }
    for (i = 0; i < n; i++) {
      var j = inverso[i];
      if (j > i) { t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (var dim = 2; dim <= n; dim <<= 1) {
      var meta = dim >> 1, passo = n / dim;
      for (i = 0; i < n; i += dim) {
        for (var k = 0; k < meta; k++) {
          var wr = coseni[k * passo], wi = seni[k * passo], a = i + k, b = a + meta;
          var xr = re[b] * wr - im[b] * wi, xi = re[b] * wi + im[b] * wr;
          re[b] = re[a] - xr; im[b] = im[a] - xi;
          re[a] += xr; im[a] += xi;
        }
      }
    }
  }

  // Replica di AnalyserNode.getByteFrequencyData (smoothingTimeConstant = 0).
  function spettroByte(x) {
    var n = x.length, i;
    if (!finestraBlackman || finestraBlackman.length !== n) { preparaFFT(n); }
    var re = new Float64Array(n), im = new Float64Array(n);
    for (i = 0; i < n; i++) { re[i] = x[i] * finestraBlackman[i]; }
    fft(re, im);
    var out = new Uint8Array(n / 2), k = 255 / (MAX_DB - MIN_DB);
    for (i = 0; i < n / 2; i++) {
      var modulo = Math.sqrt(re[i] * re[i] + im[i] * im[i]) / n;
      var v = modulo > 0 ? Math.floor((20 * Math.log(modulo) / Math.LN10 - MIN_DB) * k) : 0;
      out[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
    }
    return out;
  }

  // Replica di AnalyserNode.getByteTimeDomainData.
  function ondaByte(x) {
    var out = new Uint8Array(x.length);
    for (var i = 0; i < x.length; i++) {
      var v = Math.floor(128 * (1 + x[i]));
      out[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
    }
    return out;
  }

  window.__biaPresa = {
    fs: FS,
    nfft: N,
    spettro: function (v) { var x = finestra(v, N); return x ? spettroByte(x) : null; },
    onda: function (v) { var x = finestra(v, N); return x ? ondaByte(x) : null; },
    diagnosi: function () {
      if (!stato.mime) { return 'nessun flusso audio intercettato'; }
      var d = stato.mime.split(';')[0] + ' · seg ' + stato.segmenti + ' · dec ' + stato.decodificati;
      if (stato.metodo) { d += ' · ' + stato.metodo; }
      if (stato.riagganci) { d += ' · riagganci ' + stato.riagganci; }
      if (stato.manca) { d += ' · ' + stato.manca; }
      if (stato.errore) { d += ' · ' + stato.errore; }
      return d;
    },
    _prova: { inizioNuovo: inizioNuovo, separa: separa, fft: fft, spettroByte: spettroByte, ondaByte: ondaByte,
              configurazioneAac: configurazioneAac, fotogrammi: fotogrammi, adts: adts, stato: stato,
              scalaTempi: scalaTempi, tempoBase: tempoBase, riaggancio: riaggancio }
  };
})();
