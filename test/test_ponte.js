/*
 * test_ponte.js — Prova di risorse/ponte.js su un DOM simulato.
 *
 * Verifica: stato del lettore, lettura dei dati dalla presa audio, coda,
 * comandi, ricerca e inserimento in coda contro una rete simulata di
 * YouTube Music; spostamento, rimozione e ripristino nella coda contro una
 * simulazione delle vie native di music.youtube.com (onDrop, removeItem,
 * chiamate al server, radio reattiva), ricavate dal suo codice.
 *
 * Uso:   node test/test_ponte.js [risorse/ponte.js] [stato.json da scrivere]
 * Serve: Node.js, nessuna dipendenza.
 *
 * © 2026 B. Raucci — Licenza MIT
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const sorgente = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'risorse', 'ponte.js'), 'utf8');

let chiaveProssima = 1000;
const voceCoda = (titolo, artista, durata, selezionata, id, avvolta) => {
  const r = { title: { runs: [{ text: titolo }] }, shortBylineText: { runs: [{ text: artista }] },
              lengthText: { runs: [{ text: durata }] }, selected: selezionata, videoId: id,
              navigationEndpoint: { watchEndpoint: { videoId: id, index: chiaveProssima++ } } };
  return avvolta ? { playlistPanelVideoWrapperRenderer: { primaryRenderer: { playlistPanelVideoRenderer: r } } }
                 : { playlistPanelVideoRenderer: r };
};

function crea(opzioni = {}) {
  const log = [];
  const server = [];                        // chiamate che YouTube Music farebbe al server
  const videoEl = { paused: !!opzioni.inPausa, currentTime: 42, duration: 200, volume: 0.5, currentSrc: 'blob:x' };
  // Volume come in YouTube Music: la barra tiene nello store il valore del cursore
  // e passa al lettore il guadagno esponenziale Kfb (funzione copiata dal pacchetto
  // music_polymer_inlined_html.js, opzione music_web_enable_exponential_volume_control).
  const Kfb = (a) => Math.min(100, Math.max(0, Math.pow(2, 0.03917 * a + 2.82823) - 7.13431));
  const volume = { cursore: 70, guadagno: Kfb(70) };
  const player = {
    stato: opzioni.inPausa ? 2 : 1,
    getPlayerState() { return this.stato; }, getCurrentTime() { return 42.5; }, getDuration() { return 245; },
    getVolume() { return volume.guadagno; }, isMuted() { return false; }, getVideoData() { return { video_id: 'abc123', title: 'T', author: 'A' }; },
    playVideo() { log.push('playVideo'); }, pauseVideo() { log.push('pauseVideo'); }, seekTo(t) { log.push('seekTo:' + t); },
    setVolume(v) { log.push('setVolume:' + v); volume.guadagno = v; }, unMute() {}, nextVideo() { log.push('nextVideo'); }, previousVideo() {},
  };
  const el = (txt) => ({ textContent: txt });
  const barraEl = {
    hasAttribute: (n) => n === 'shuffle-on',
    getState: () => (opzioni.senzaVolumeStore ? { queue: { repeatMode: 'ALL' } }
                                              : { queue: { repeatMode: 'ALL' }, player: { volume: volume.cursore } }),
    updateVolume(v) { log.push('updateVolume:' + v); volume.cursore = v; volume.guadagno = Kfb(v); },
    onRepeatButtonClick() { log.push('repeat'); },
    queue: { shuffle() { log.push('shuffle'); } },
    querySelector(sel) {
      if (sel.includes('.title')) return el('  Wish You  Were Here ');
      if (sel.includes('.byline')) return el('Pink Floyd • Wish You Were Here • 1975');
      if (sel.includes('img.image')) return { src: 'https://lh3.googleusercontent.com/x=w60-h60-l90-rj' };
      if (sel === '#like-button-renderer') return { getAttribute: () => 'LIKE', querySelector: () => null, updateLikeStatus() {} };
      return null;
    },
  };
  const iniziali = opzioni.ids
    ? opzioni.ids.map((id, i) => (id === '?' ? { altro: {} } : voceCoda('T' + id, 'A', '1:00', id === opzioni.corrente, id, i % 2 === 1)))
    : [voceCoda('Uno', 'X', '3:00', false, 'a'), voceCoda('Due', 'Y', '4:30', true, 'b'), voceCoda('Tre', 'Z', '5:00', false, 'c')];
  // Come nella pagina vera: il gettone di continuazione sta sull'oggetto della
  // coda e isInfinite nello store può essere false anche con il gettone.
  const statoCoda = { items: opzioni.codaVuota ? [] : iniziali, queueContextParams: 'CONTESTO', nextQueueItemId: 99,
                      isInfinite: false, isRaarEnabled: true };
  const rend = (it) => it.playlistPanelVideoRenderer ||
    (it.playlistPanelVideoWrapperRenderer && it.playlistPanelVideoWrapperRenderer.primaryRenderer.playlistPanelVideoRenderer) || {};
  const g_ = (it) => { const w = (rend(it).navigationEndpoint || {}).watchEndpoint || {}; return typeof w.index === 'number' ? '' + w.index : ''; };
  // Riduttore come quello di music.youtube.com (pacchetto af273e12): ogni
  // azione produce un NUOVO array; il componente riceve this.data subito,
  // oppure in ritardo (opzione dataInRitardo) per provare l'attesa del ponte.
  const coda = {
    data: null,
    dispatch(a) {
      const p = a.payload;
      log.push('dispatch:' + a.type + ':' + (a.type === 'ADD_ITEMS' ? p.index + '+' + p.items.length
        : a.type === 'MOVE_ITEM' ? p.fromIndex + '>' + p.toIndex : p));
      let it = statoCoda.items;
      if (a.type === 'ADD_ITEMS') { it = it.slice(0); it.splice(p.index, 0, ...p.items); }
      if (a.type === 'REMOVE_ITEM') { it = it.slice(0); it.splice(p, 1); }
      if (a.type === 'MOVE_ITEM') { it = it.slice(0); const c = it.splice(p.fromIndex, 1); it.splice(p.toIndex, 0, ...c); }
      if (a.type === 'SET_IS_RAAR_ENABLED') statoCoda.isRaarEnabled = p;
      if (a.type === 'SET_IS_INFINITE') statoCoda.isInfinite = p;
      if (it !== statoCoda.items) {
        statoCoda.items = it;
        if (opzioni.dataInRitardo) setTimeout(() => { coda.data = statoCoda.items; }, 30);
        else coda.data = it;
      }
    },
    // k.onDrop del componente <ytmusic-player-queue> → Fnb: MOVE_ITEM, radio reattiva spenta, REORDER sul server.
    onDrop(ev) {
      const b = ev.detail.dragEl;
      const da = this.data.findIndex((d) => (b.data.primaryRenderer ? d.playlistPanelVideoWrapperRenderer === b.data
                                                                    : d.playlistPanelVideoRenderer === b.data));
      const a = Array.from(b.parentElement.children).indexOf(b);
      const mosso = this.data[da];
      this.dispatch({ type: 'MOVE_ITEM', payload: { fromIndex: da, toIndex: a } });
      this.dispatch({ type: 'SET_IS_RAAR_ENABLED', payload: false });
      server.push('REORDER:' + rend(mosso).videoId + '>' + (a > 0 ? rend(statoCoda.items[a - 1]).videoId : '^'));
    },
  };
  coda.data = statoCoda.items;
  if (opzioni.chiaviDoppie) {               // come dopo un inserimento o uno SHUF: chiavi ripetute
    for (const it of statoCoda.items) { const w = rend(it).navigationEndpoint; if (w) w.watchEndpoint.index = 7; }
  }
  coda.queue = {
    continuation: opzioni.radio === false ? undefined : 'GETTONE',
    serverQueuesEnabled: true,
    serverQueueApi: { removeItem(it) { server.push('DELETE:' + rend(it).videoId); } },
    getItems: () => statoCoda.items, store: { store: { getState: () => ({ queue: statoCoda }) } },
    // f_.prototype.removeItem: indice della chiave, DELETE sul server, REMOVE_ITEM, radio reattiva spenta.
    removeItem(chiave) {
      const i = statoCoda.items.findIndex((c) => g_(c) === chiave);
      if (i < 0) return;
      coda.queue.serverQueueApi.removeItem(statoCoda.items[i]);
      coda.dispatch({ type: 'REMOVE_ITEM', payload: i });
      coda.dispatch({ type: 'SET_IS_RAAR_ENABLED', payload: false });
    },
  };
  if (opzioni.senzaNativi) { delete coda.onDrop; delete coda.queue.removeItem; delete coda.queue.serverQueueApi; coda.queue.serverQueuesEnabled = false; }
  if (opzioni.senzaDispatch) { delete coda.dispatch; }
  const richieste = [];
  const app = { networkManager: { fetch(percorso, corpo) {
    richieste.push({ percorso, corpo });
    if (percorso === '/search') return Promise.resolve({ responseContext: { grande: 'x'.repeat(1000) }, contents: { esito: corpo.query } });
    if (percorso === '/music/get_queue') {
      server.push('GET_QUEUE:' + corpo.queueInsertPosition + ':' + (corpo.videoIds || [corpo.playlistId]).join(','));
      const ids = corpo.videoIds || ['n0', 'n1', 'n2'];
      return Promise.resolve({ queueDatas: ids.map((id) => ({ content: voceCoda('T' + id, 'W', '1:00', false, id) })) });
    }
    return Promise.reject(new Error('percorso sconosciuto'));
  } } };
  const location = { assegnato: null, assign(u) { this.assegnato = u; } };
  const document = {
    head: { appendChild() {} }, documentElement: { appendChild() {} },
    createElement: () => ({}),
    querySelector(sel) {
      if (sel === '#movie_player') return player;
      if (sel === 'ytmusic-player-bar') return barraEl;
      if (sel.startsWith('video')) return videoEl;
      if (sel === '#queue') return coda;
      if (sel === 'ytmusic-app') return app;
      if (sel === 'ytmusic-search-box') return { getSearchboxStats: () => ({ s: 1 }) };
      if (sel === '.next-button.ytmusic-player-bar') return { click() { log.push('click-next'); } };
      return null;
    },
    querySelectorAll() { return []; },
  };
  const presa = opzioni.senzaPresa ? undefined : {
    fs: 48000, nfft: 2048, chiamate: [],
    spettro(v) { this.chiamate.push('spettro'); return opzioni.presaVuota ? null : Uint8Array.from({ length: 1024 }, (_, i) => i % 256); },
    onda(v) { this.chiamate.push('onda'); return new Uint8Array(2048).fill(128); },
    diagnosi() { return 'audio/mp4 · seg 3 · dec 3 · adts'; },
  };
  const window = { __biaPresa: presa };
  const btoa = (s) => Buffer.from(s, 'binary').toString('base64');
  new Function('window', 'document', 'btoa', 'location', sorgente)(window, document, btoa, location);
  return { bia: window.__bia, log, presa, richieste, location, statoCoda, server, coda, player };
}

(async () => {
  // 1. Stato e dati dalla presa audio.
  let a = crea();
  let s = JSON.parse(a.bia.stato('f'));
  assert.strictEqual(s.titolo, 'Wish You Were Here');
  assert.strictEqual(s.rip, 'ALL'); assert.strictEqual(s.shuf, true); assert.strictEqual(s.like, 'LIKE');
  assert.strictEqual(s.cur, 42.5); assert.strictEqual(s.dur, 245); assert.strictEqual(s.vol, 70);
  assert.strictEqual(Buffer.from(s.viz, 'base64').length, 1024); assert.strictEqual(s.vk, 'f');
  assert.strictEqual(s.fs, 48000); assert.strictEqual(s.nfft, 2048); assert.ok(s.ad.includes('adts'));
  s = JSON.parse(a.bia.stato('w'));
  assert.strictEqual(Buffer.from(s.viz, 'base64').length, 2048); assert.strictEqual(s.vk, 'w');
  console.log('✓ stato e dati della presa (FFT 1024 bin, onda 2048 campioni), diagnosi:', s.ad);

  // 2. In pausa o senza modo: nessuna analisi; presa assente o vuota: diagnosi.
  a = crea({ inPausa: true });
  s = JSON.parse(a.bia.stato('f'));
  assert.strictEqual(s.viz, ''); assert.deepStrictEqual(a.presa.chiamate, []);
  a = crea();
  s = JSON.parse(a.bia.stato(''));
  assert.strictEqual(s.viz, ''); assert.deepStrictEqual(a.presa.chiamate, []);
  a = crea({ senzaPresa: true });
  s = JSON.parse(a.bia.stato('f'));
  assert.strictEqual(s.viz, ''); assert.strictEqual(s.ad, 'presa audio assente');
  a = crea({ presaVuota: true });
  s = JSON.parse(a.bia.stato('f'));
  assert.strictEqual(s.viz, ''); assert.strictEqual(s.vk, '');
  console.log('✓ nessuna analisi in pausa o se non richiesta; presa assente o senza dati gestite');

  // 3. Coda: firma stabile.
  a = crea();
  const c1 = JSON.parse(a.bia.coda(''));
  assert.strictEqual(c1.voci.length, 3); assert.strictEqual(c1.voci[1].s, true);
  assert.strictEqual(JSON.parse(a.bia.coda(c1.firma)).uguale, 1);
  console.log('✓ coda: voci lette, firma invariata →', c1.firma);

  // 4. Comandi.
  assert.strictEqual(a.bia.cmd('toggle'), true); assert.ok(a.log.includes('pauseVideo'));
  a.bia.cmd('play'); assert.ok(a.log.includes('seekTo:0'));
  a.bia.cmd('volume', 150); assert.ok(a.log.includes('updateVolume:100'));
  a.bia.cmd('indice', 3.7); assert.ok(a.log.includes('dispatch:SET_INDEX:3'));
  a.bia.cmd('next'); assert.ok(a.log.includes('click-next'));
  a.bia.cmd('shuffle'); a.bia.cmd('repeat'); assert.ok(a.log.includes('shuffle') && a.log.includes('repeat'));
  assert.strictEqual(a.bia.cmd('inesistente'), false);
  console.log('✓ comandi');

  // 4 bis. Volume: si rilegge sulla stessa scala su cui si scrive (cursore di
  // YouTube Music), non il guadagno esponenziale del lettore.
  a = crea();
  for (const x of [42, 59, 17, 47, 0, 100]) {
    a.bia.cmd('volume', x);
    assert.strictEqual(JSON.parse(a.bia.stato('')).vol, x, 'volume ' + x + ' riletto come ' + JSON.parse(a.bia.stato('')).vol);
  }
  a.bia.cmd('volume', 42);
  assert.ok(Math.abs(a.player.getVolume() - 15.08) < 0.05, 'guadagno del lettore ' + a.player.getVolume());
  a = crea({ senzaVolumeStore: true });            // store non leggibile: scrittura e lettura sul lettore
  a.bia.cmd('volume', 42);
  assert.ok(a.log.includes('setVolume:42') && !a.log.some((x) => x.startsWith('updateVolume')), a.log.join());
  assert.strictEqual(JSON.parse(a.bia.stato('')).vol, 42);
  console.log('✓ volume: 42 → 42 (guadagno del lettore 15,1); senza store, setVolume/getVolume');

  // 5. Ricerca: richiesta '/search' con parametri; risposta ridotta a 'contents'.
  a = crea();
  const r = JSON.parse(await a.bia.cerca('pink floyd', 'EgWKAQIIAWoMEA4QChADEAQQCRAF'));
  assert.deepStrictEqual(r, { contents: { esito: 'pink floyd' } });
  assert.strictEqual(a.richieste[0].percorso, '/search');
  assert.strictEqual(a.richieste[0].corpo.params, 'EgWKAQIIAWoMEA4QChADEAQQCRAF');
  assert.deepStrictEqual(a.richieste[0].corpo.suggestStats, { s: 1 });
  console.log('✓ ricerca: /search con filtro, risposta ridotta a "contents"');

  // 6. "Riproduci ora": dopo il brano corrente (indice 1) e salto.
  a = crea();
  assert.strictEqual(await a.bia.accoda({ videoId: 'zzz' }, 'INSERT_AFTER_CURRENT_VIDEO', true), '1');
  assert.deepStrictEqual(a.richieste[0].corpo, { queueContextParams: 'CONTESTO', queueInsertPosition: 'INSERT_AFTER_CURRENT_VIDEO', videoIds: ['zzz'] });
  assert.deepStrictEqual(a.log, ['dispatch:ADD_ITEMS:2+1', 'dispatch:SET_IS_RAAR_ENABLED:false', 'dispatch:SET_INDEX:2']);
  assert.deepStrictEqual(a.server, ['GET_QUEUE:INSERT_AFTER_CURRENT_VIDEO:zzz']);
  console.log('✓ riproduci ora: get_queue (registrato dal server) → ADD_ITEMS 2 → radio reattiva spenta → SET_INDEX 2');

  // 7. Album in coda alla fine, senza salto.
  a = crea();
  assert.strictEqual(await a.bia.accoda({ playlistId: 'OLAK5uy_x' }, 'INSERT_AT_END', false), '3');
  assert.strictEqual(a.richieste[0].corpo.playlistId, 'OLAK5uy_x');
  assert.deepStrictEqual(a.log, ['dispatch:ADD_ITEMS:3+3', 'dispatch:SET_IS_RAAR_ENABLED:false']);
  console.log('✓ album in coda alla fine: playlistId → 3 brani all\'indice 3, nessun salto');

  // 8. Coda vuota: apertura diretta del brano.
  a = crea({ codaVuota: true });
  assert.strictEqual(await a.bia.accoda({ videoId: 'q w' }, 'INSERT_AFTER_CURRENT_VIDEO', true), 'navigazione');
  assert.strictEqual(a.location.assegnato, '/watch?v=q%20w');
  console.log('✓ coda vuota: si apre direttamente', a.location.assegnato);

  // 9. Coda con un elemento illeggibile: segnaposto, indici allineati; stato della radio.
  const ids = (st) => st.items.map((it) => (it.altro ? '?' : (it.playlistPanelVideoRenderer
    || it.playlistPanelVideoWrapperRenderer.primaryRenderer.playlistPanelVideoRenderer).videoId));
  a = crea({ ids: ['a', '?', 'b'], corrente: 'b' });
  let c9 = JSON.parse(a.bia.coda(''));
  assert.strictEqual(c9.m, 1); assert.strictEqual(c9.inf, 1); assert.strictEqual(c9.via, '');
  assert.deepStrictEqual(c9.voci.map((v) => v.x ? '?' : v.id), ['a', '?', 'b']);
  assert.strictEqual(c9.voci[2].s, true);
  a = crea({ senzaDispatch: true });
  assert.strictEqual(JSON.parse(a.bia.coda('')).m, 0);
  assert.strictEqual(await a.bia.sposta([[0, 1, 'a']]), 0); assert.strictEqual(a.bia.rimuovi([[0, 'a']]), 0);
  console.log('✓ coda: segnaposto al posto giusto, radio segnalata; senza dispatch non modificabile');

  // 10. Spostamenti per la via nativa (onDrop): server e radio reattiva aggiornati.
  //     Mosse di mosse_blocco(7, (0, 2, 5), 4) dal motore Python: b d a c f e g.
  const mosseBlocco = [[2, 3, 'c'], [0, 2, 'a'], [5, 4, 'f']];
  a = crea({ ids: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], corrente: 'b' });
  assert.strictEqual(await a.bia.sposta(mosseBlocco), 3);
  assert.deepStrictEqual(ids(a.statoCoda), ['b', 'd', 'a', 'c', 'f', 'e', 'g']);
  assert.deepStrictEqual(a.server, ['REORDER:c>d', 'REORDER:a>d', 'REORDER:f>c']);
  assert.strictEqual(a.statoCoda.isRaarEnabled, false);
  assert.strictEqual(JSON.parse(a.bia.coda('')).via, 'nativa');
  console.log('✓ spostamento a blocco per la via nativa: 3 REORDER al server, radio reattiva spenta');

  // 11. this.data del componente aggiornato in ritardo: il ponte attende prima di ogni mossa.
  a = crea({ ids: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], corrente: 'b', dataInRitardo: true });
  assert.strictEqual(await a.bia.sposta(mosseBlocco), 3);
  assert.deepStrictEqual(ids(a.statoCoda), ['b', 'd', 'a', 'c', 'f', 'e', 'g']);
  console.log('✓ componente in ritardo sullo store: il ponte aspetta l\'allineamento, esito identico');

  // 12. Indici superati: l'elemento si ritrova per videoId; ripiego senza vie native.
  a = crea({ ids: ['a', 'b', 'c', 'x', 'd'], corrente: 'b' });
  assert.strictEqual(await a.bia.sposta([[3, 0, 'd']]), 1);      // 'd' è ormai in 4
  assert.deepStrictEqual(ids(a.statoCoda), ['d', 'a', 'b', 'c', 'x']);
  assert.strictEqual(await a.bia.sposta([[1, 0, 'zz']]), 0);    // videoId sconosciuto: nessuna mossa
  a = crea({ ids: ['a', 'b', 'c', 'd'], corrente: 'b', senzaNativi: true });
  assert.strictEqual(await a.bia.sposta([[3, 1, 'd']]), 1);
  assert.strictEqual(a.bia.rimuovi([[0, 'a']]), 1);
  assert.deepStrictEqual(ids(a.statoCoda), ['d', 'b', 'c']);
  assert.deepStrictEqual(a.server, []); assert.strictEqual(a.statoCoda.isRaarEnabled, false);
  assert.strictEqual(JSON.parse(a.bia.coda('')).via, 'locale');
  console.log('✓ ritrovamento per videoId; senza vie native: azioni locali, radio reattiva spenta, via «locale»');

  // 13. Rimozione nativa (removeItem con la chiave): DELETE al server, corrente esclusa;
  //     il gettone di continuazione viene tolto (la coda non si riempie più da sola).
  a = crea({ ids: ['a', 'b', 'c', 'd', 'e'], corrente: 'b' });
  assert.strictEqual(JSON.parse(a.bia.coda('')).inf, 1);        // gettone presente, isInfinite false
  assert.strictEqual(a.bia.rimuovi([[1, 'b'], [3, 'd'], [0, 'a']]), 2);
  assert.deepStrictEqual(ids(a.statoCoda), ['b', 'c', 'e']);
  assert.deepStrictEqual(a.server, ['DELETE:d', 'DELETE:a']);
  assert.strictEqual(a.coda.queue.continuation, undefined);
  assert.strictEqual(JSON.parse(a.bia.coda('')).inf, 0);
  console.log('✓ rimozione nativa: DELETE al server, il brano in riproduzione resta, gettone di continuazione tolto');

  // 14. Annullamento: get_queue in fondo (il server lo registra) e onDrop al posto originale.
  a.server.length = 0;
  assert.strictEqual(await a.bia.ripristina(), 2);
  assert.deepStrictEqual(ids(a.statoCoda), ['a', 'b', 'c', 'd', 'e']);
  assert.deepStrictEqual(a.server, ['GET_QUEUE:INSERT_AT_END:a', 'REORDER:a>^', 'GET_QUEUE:INSERT_AT_END:d', 'REORDER:d>c']);
  assert.strictEqual(a.coda.queue.continuation, 'GETTONE');     // rimozione annullata: la radio riprende
  assert.strictEqual(await a.bia.ripristina(), 0);             // cestino vuoto
  console.log('✓ ripristino per le stesse vie dell\'interfaccia: brani e gettone rimessi al loro posto');

  // 14 bis. Chiavi ripetute: rimozione per indice con le stesse tre operazioni di removeItem.
  a = crea({ ids: ['a', 'b', 'c', 'd'], corrente: 'b', chiaviDoppie: true });
  assert.strictEqual(a.bia.rimuovi([[3, 'd'], [0, 'a']]), 2);
  assert.deepStrictEqual(ids(a.statoCoda), ['b', 'c']);
  assert.deepStrictEqual(a.server, ['DELETE:d', 'DELETE:a']);
  assert.strictEqual(a.statoCoda.isRaarEnabled, false);
  assert.strictEqual(JSON.parse(a.bia.coda('')).err, '');
  a = crea({ ids: ['a', 'b', 'c'], corrente: 'b' });
  assert.strictEqual(a.bia.rimuovi([[2, 'zz']]), 0);
  assert.ok(JSON.parse(a.bia.coda('')).err.includes('non trovato'));
  console.log('✓ chiavi ripetute: rimozione per indice (DELETE al server); diagnosi dell\'errore nella coda');

  // 15. Comando «ferma radio»: gettone tolto, isInfinite e radio reattiva spente; non si riprende con ⌘Z.
  a = crea({ ids: ['a', 'b', 'c', 'd'], corrente: 'a' });
  assert.strictEqual(a.bia.cmd('ferma_radio'), true);
  assert.strictEqual(a.coda.queue.continuation, undefined);
  assert.strictEqual(a.statoCoda.isInfinite, false); assert.strictEqual(a.statoCoda.isRaarEnabled, false);
  assert.strictEqual(a.bia.rimuovi([[3, 'd']]), 1);
  assert.strictEqual(await a.bia.ripristina(), 1);
  assert.strictEqual(a.coda.queue.continuation, undefined);
  a = crea({ ids: ['a', 'b'], corrente: 'a', radio: false });
  assert.strictEqual(JSON.parse(a.bia.coda('')).inf, 0);
  console.log('✓ «ferma radio»: gettone tolto per sempre; coda senza gettone: nessuna radio segnalata');

  if (process.argv[3]) {          // JSON reale del ponte, per provare core.StatoLettore.da_json
    fs.writeFileSync(process.argv[3], crea().bia.stato('f'));
  }
  console.log('Tutte le prove del ponte superate.');
})().catch((e) => { console.error(e); process.exit(1); });
