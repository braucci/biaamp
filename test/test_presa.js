/*
 * test_presa.js — Prova di risorse/presa.js con MediaSource, SourceBuffer e
 * decodificatore simulati.
 *
 * Il decodificatore finto si comporta come il caso peggiore plausibile di
 * WebKit: RIFIUTA l'MP4 frammentato e accetta solo ADTS (o WebM). Così si
 * collauda il percorso di ripiego, non solo quello fortunato.
 *
 * Uso:   node test/test_presa.js [risorse/presa.js]
 * © 2026 B. Raucci — Licenza MIT
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const sorgente = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'risorse', 'presa.js'), 'utf8');

// ── costruzione di riquadri ISO-BMFF ────────────────────────────────────────
const be32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const box = (tipo, ...contenuti) => {
  const corpo = contenuti.flatMap((c) => Array.from(c));
  return Uint8Array.from([...be32(8 + corpo.length), ...Buffer.from(tipo, 'ascii'), ...corpo]);
};
const lungDesc = (n, lunga) => (lunga ? [0x80, 0x80, 0x80, n] : [n]);
function esds(lunga) {
  const asc = [0x12, 0x10];                                    // AAC-LC, 44,1 kHz (indice 4), stereo
  const dsi = [0x05, ...lungDesc(asc.length, lunga), ...asc];
  const dcd = [0x40, 0x15, 0, 0, 0, 0, 1, 0xF4, 0, 0, 1, 0xF4, 0, ...dsi];
  const dc = [0x04, ...lungDesc(dcd.length, lunga), ...dcd];
  const es = [0, 1, 0x00, ...dc, 0x06, 0x01, 0x02];
  return box('esds', [0, 0, 0, 0], [0x03, ...lungDesc(es.length, lunga), ...es]);
}
const SCALA = 44100;
const mdhd = box('mdhd', [0, 0, 0, 0], be32(0), be32(0), be32(SCALA), be32(0), [0x55, 0xC4, 0, 0]);
const initMp4 = (lunga) => Uint8Array.from([...box('ftyp', Buffer.from('iso6mp41')),
  ...box('moov', box('trak', box('mdia', mdhd, box('minf', box('stbl', box('stsd', esds(lunga)))))))]);
// Frammento moof + mdat con tfdt = secondi · SCALA (versione 0 a 32 bit o 1 a 64 bit).
function frammento(campioni, secondi = 0, v1 = false) {
  const base = Math.round(secondi * SCALA);
  const tfdt = v1 ? box('tfdt', [1, 0, 0, 0], be32(Math.floor(base / 4294967296)), be32(base >>> 0))
                  : box('tfdt', [0, 0, 0, 0], be32(base));
  const costruisci = (offset) => box('moof', box('mfhd', [0, 0, 0, 0, 0, 0, 0, 1]),
    box('traf', box('tfhd', [0, 2, 0, 0], be32(1)), tfdt,
      box('trun', [0, 0, 2, 1], be32(campioni.length), be32(offset), ...campioni.map((c) => be32(c.length)))));
  const moof = costruisci(costruisci(0).length + 8);
  return Uint8Array.from([...moof, ...box('mdat', ...campioni)]);
}
const CAMPIONI = [Uint8Array.from({ length: 10 }, (_, i) => i), new Uint8Array(12).fill(7)];

// ── ambiente simulato ───────────────────────────────────────────────────────
function ambiente(opzioni) {
  const listener = new Map();
  class TimeRanges { constructor(r) { this.r = r; } get length() { return this.r.length; }
    start(i) { return this.r[i][0]; } end(i) { return this.r[i][1]; } }
  class SourceBuffer {
    constructor() { this.intervalli = []; this.ev = []; }
    get buffered() { return new TimeRanges(this.intervalli.map((x) => x.slice())); }
    addEventListener(n, f) { if (n === 'updateend') this.ev.push(f); }
    appendBuffer() {
      const d = ambienteCorrente.prossimaDurata.shift();
      if (d) {
        const fine = this.intervalli.length ? this.intervalli[0][1] : ambienteCorrente.t0Iniziale;
        if (this.intervalli.length) this.intervalli[0][1] = fine + d; else this.intervalli.push([fine, fine + d]);
      }
      this.ev.forEach((f) => f());
    }
  }
  class MediaSource { addSourceBuffer() { return new SourceBuffer(); } }
  const decodificati = [];
  class OfflineAudioContext {
    constructor(c, l, fs) { this.sampleRate = fs; }
    decodeAudioData(buf, ok, ko) {
      const u = new Uint8Array(buf);
      const eAdts = u[0] === 0xFF && (u[1] & 0xF0) === 0xF0;
      const eWebm = u[0] === 0x1A && u[1] === 0x45;
      decodificati.push(eAdts ? 'adts' : eWebm ? 'webm' : 'mp4');
      if (!eAdts && !eWebm && opzioni.rifiutaMp4) { ko({ name: 'EncodingError' }); return; }
      const durata = 4, n = durata * this.sampleRate, f = ambienteCorrente.frequenza;
      const dati = Float32Array.from({ length: n }, (_, i) => 0.5 * Math.sin(2 * Math.PI * f * i / this.sampleRate));
      ok({ length: n, numberOfChannels: 2, sampleRate: this.sampleRate, getChannelData: () => dati });
    }
  }
  const video = { currentTime: 0, src: '', currentSrc: '', srcObject: null };
  let contatoreUrl = 0;
  const window = { MediaSource, SourceBuffer, OfflineAudioContext,
                   URL: { createObjectURL: () => 'blob:biaamp/' + (++contatoreUrl) } };
  const document = { querySelector: (s) => (s === 'video' ? video : null) };
  const ambienteCorrente = { prossimaDurata: [], t0Iniziale: 0, frequenza: 1000, window, video, decodificati, listener };
  new Function('window', 'document', sorgente)(window, document);
  return ambienteCorrente;
}

const picco = (b) => b.reduce((m, v, i) => (v > b[m] ? i : m), 0);

// 1. Intervalli: porzione nuova del buffer.
let A = ambiente({});
let P = A.window.__biaPresa._prova;
assert.strictEqual(P.inizioNuovo([[0, 10]], [[0, 15]]), 10);
assert.strictEqual(P.inizioNuovo([], [[30, 35]]), 30);
assert.strictEqual(P.inizioNuovo([[0, 10]], [[0, 10], [40, 45]]), 40);
assert.strictEqual(P.inizioNuovo([[0, 10]], [[0, 10.005]]), null);
console.log('✓ porzione nuova del buffer');

// 2. Separazione init / dati, MP4 e WebM.
const init = initMp4(false), fr = frammento(CAMPIONI);
let s = P.separa(Uint8Array.from([...init, ...fr]));
assert.strictEqual(s.init.length, init.length); assert.strictEqual(s.media.length, fr.length); assert.strictEqual(s.contenitore, 'mp4');
s = P.separa(fr); assert.strictEqual(s.init, null);
const webm = Uint8Array.from([0x1A, 0x45, 0xDF, 0xA3, 1, 2, 3, 0x1F, 0x43, 0xB6, 0x75, 9, 9]);
s = P.separa(webm); assert.strictEqual(s.init.length, 7); assert.strictEqual(s.media.length, 6);
console.log('✓ separazione init/dati (MP4 e WebM)');

// 3. AAC: AudioSpecificConfig, fotogrammi del trun, intestazioni ADTS.
for (const lunga of [false, true]) {
  const cfg = P.configurazioneAac(initMp4(lunga));
  assert.deepStrictEqual(cfg, { profilo: 1, indiceFreq: 4, canali: 2 }, 'esds lunga=' + lunga);
}
const frames = P.fotogrammi(fr);
assert.deepStrictEqual(frames.map((f) => f.length), [10, 12]);
assert.strictEqual(frames[1][0], 7);
const adts = P.adts({ profilo: 1, indiceFreq: 4, canali: 2 }, frames);
assert.deepStrictEqual(Array.from(adts.subarray(0, 7)), [0xFF, 0xF1, 0x50, 0x80, 0x02, 0x3F, 0xFC]);
assert.strictEqual(adts.length, 7 + 10 + 7 + 12);
assert.strictEqual(((adts[17 + 3] & 3) << 11) | (adts[17 + 4] << 3) | (adts[17 + 5] >> 5), 19);
console.log('✓ esds (lunghezze corte e lunghe), trun → 2 fotogrammi, intestazioni ADTS');

// 4. FFT contro DFT diretta.
const n = 64, re = Float64Array.from({ length: n }, (_, i) => Math.sin(i * 1.3) + 0.2 * Math.cos(i * 0.7));
const im = new Float64Array(n), re0 = Float64Array.from(re);
P.fft(re, im);
let errMax = 0;
for (let k = 0; k < n; k++) {
  let a = 0, b = 0;
  for (let t = 0; t < n; t++) { a += re0[t] * Math.cos(-2 * Math.PI * k * t / n); b += re0[t] * Math.sin(-2 * Math.PI * k * t / n); }
  errMax = Math.max(errMax, Math.abs(a - re[k]), Math.abs(b - im[k]));
}
assert.ok(errMax < 1e-9, 'errore FFT ' + errMax);
console.log('✓ FFT radix-2 coincide con la DFT (errore max ' + errMax.toExponential(1) + ')');

// 4 bis. Tempi MP4: scala da mdhd, istante da tfdt (32 e 64 bit).
assert.strictEqual(P.scalaTempi(init), SCALA);
assert.strictEqual(P.tempoBase(frammento(CAMPIONI, 12.5)), 12.5 * SCALA);
assert.strictEqual(P.tempoBase(frammento(CAMPIONI, 200000, true)), 200000 * SCALA);
console.log('✓ scala dei tempi (mdhd) e istante del frammento (tfdt a 32 e 64 bit)');

// 5. Flusso completo: MP4 rifiutato → ripiego ADTS → spettro al tempo giusto.
A = ambiente({ rifiutaMp4: true });
let W = A.window;
let ms = new W.MediaSource();
A.video.src = A.video.currentSrc = W.URL.createObjectURL(ms);
let sb = W.MediaSource.prototype.addSourceBuffer.call(ms, 'audio/mp4; codecs="mp4a.40.2"');
sb.appendBuffer(init);                                         // solo init
sb.appendBuffer(frammento(CAMPIONI, 0));                       // [0, 4)
sb.appendBuffer(frammento(CAMPIONI, 4));                       // [4, 8)
assert.deepStrictEqual(A.decodificati, ['mp4', 'adts', 'adts']);
assert.ok(/seg 2 · dec 2 · adts/.test(W.__biaPresa.diagnosi()), W.__biaPresa.diagnosi());
A.video.currentTime = 5.0;
let spettro = W.__biaPresa.spettro(A.video);
assert.strictEqual(spettro.length, 1024);
assert.ok(Math.abs(picco(spettro) - 1000 / (48000 / 2048)) <= 1, 'picco a ' + picco(spettro));
assert.ok(spettro[picco(spettro)] > 200 && spettro[600] < 40, 'dinamica ' + spettro[picco(spettro)] + ' / ' + spettro[600]);
let onda = W.__biaPresa.onda(A.video);
assert.strictEqual(onda.length, 2048); assert.ok(Math.max(...onda) > 180 && Math.min(...onda) < 76);
A.video.currentTime = 9.0;
assert.strictEqual(W.__biaPresa.spettro(A.video), null, 'oltre i dati: nessuna analisi');
console.log('✓ MP4 rifiutato → ADTS; picco a 1 kHz nel bin', picco(spettro), '·', W.__biaPresa.diagnosi());

// 6. Nuovo brano (nuovo MediaSource, stessi tempi): niente confusione tra brani.
A.frequenza = 3000;
const ms2 = new W.MediaSource();
A.video.src = A.video.currentSrc = W.URL.createObjectURL(ms2);
const sb2 = W.MediaSource.prototype.addSourceBuffer.call(ms2, 'audio/mp4; codecs="mp4a.40.2"');
sb2.appendBuffer(init);
sb2.appendBuffer(frammento(CAMPIONI, 0));
A.video.currentTime = 2.0;
spettro = W.__biaPresa.spettro(A.video);
assert.ok(Math.abs(picco(spettro) - 3000 / (48000 / 2048)) <= 1, 'secondo brano: picco a ' + picco(spettro));
console.log('✓ brano successivo: analisi del MediaSource collegato al video (3 kHz nel bin', picco(spettro) + ')');

// 6 bis. Segmenti consegnati a pezzi di 7 byte (SABR) e timestampOffset.
A = ambiente({ rifiutaMp4: true });
W = A.window;
ms = new W.MediaSource();
A.video.src = A.video.currentSrc = W.URL.createObjectURL(ms);
sb = W.MediaSource.prototype.addSourceBuffer.call(ms, 'audio/mp4; codecs="mp4a.40.2"');
sb.timestampOffset = 100;
const flusso = Uint8Array.from([...init, ...frammento(CAMPIONI, 0), ...frammento(CAMPIONI, 4)]);
for (let i = 0; i < flusso.length; i += 7) { sb.appendBuffer(flusso.subarray(i, i + 7)); }
const blocchi = W.__biaPresa._prova.stato.blocchi.map((b) => b.t0);
assert.deepStrictEqual(blocchi, [100, 104], 'istanti ' + blocchi);
A.video.currentTime = 106;
assert.ok(W.__biaPresa.spettro(A.video), 'analisi a 106 s');
console.log('✓ ricomposizione di ' + Math.ceil(flusso.length / 7) + ' pezzi da 7 byte; istanti', blocchi.join(' e '), 's con timestampOffset');

// 7. WebM (Opus): decodifica diretta di init + Cluster.
A = ambiente({ rifiutaMp4: true });
W = A.window;
ms = new W.MediaSource();
A.video.src = A.video.currentSrc = W.URL.createObjectURL(ms);
sb = W.MediaSource.prototype.addSourceBuffer.call(ms, 'audio/webm; codecs="opus"');
A.prossimaDurata.push(4);
sb.appendBuffer(webm);
A.video.currentTime = 1.0;
assert.ok(W.__biaPresa.spettro(A.video), 'webm');
assert.deepStrictEqual(A.decodificati, ['webm']);
console.log('✓ WebM: decodifica diretta ·', W.__biaPresa.diagnosi());

// 8. I SourceBuffer video non vengono toccati.
const sbv = W.MediaSource.prototype.addSourceBuffer.call(ms, 'video/mp4; codecs="avc1"');
A.prossimaDurata.push(4);
sbv.appendBuffer(Uint8Array.from([...init, ...fr]));
assert.deepStrictEqual(A.decodificati, ['webm']);
console.log('✓ i SourceBuffer video sono ignorati');
console.log('Tutte le prove della presa audio superate.');
