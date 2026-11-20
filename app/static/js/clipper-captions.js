// Live captions over a short's <video> (shorts grid and editor). The design table is the server's
// (app/core/clipper/captions.py STYLES, passed as JSON) and `group` mirrors captions.group exactly, so what the
// preview shows is what the download burns in.
(() => {
  const group = (words, maxWords, maxChars, maxGap) => {
    const out = []; let cur = [];
    for (const w of words) {
      if (cur.length) {
        const len = cur.reduce((n, x) => n + x[2].length + 1, 0) + w[2].length;
        const ends = /[.?!,;:]$/.test(cur[cur.length - 1][2]);
        if (cur.length >= maxWords || len > maxChars || w[0] - cur[cur.length - 1][1] > maxGap || (ends && cur.length >= 2)) { out.push(cur); cur = []; }
      }
      cur.push(w);
    }
    if (cur.length) out.push(cur);
    return out;
  };
  const wordText = (w, upper) => { w = w.trim(); return upper ? w.replace(/[,;:]$/, '').toUpperCase() : w; };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const rgba = (hex, a = 1) => {
    const h = String(hex || '#000000').replace('#', '');
    return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
  };
  // Caption Studio designs (st.custom): CSS variables + classes that mirror studio.ass exactly.
  function customVars(st) {
    const sd = st.shadow_d / 100;
    const shadow = sd > 0 ? `${sd}em ${sd}em 0 ${rgba(st.shadow, st.shadow_a / 100)}` : '0 0 0 transparent';
    const glow = st.glow && st.box !== 'line' ? `0 0 ${0.035 * st.glow_s}em ${rgba(st.glow_color, 0.95)}, 0 0 ${0.08 * st.glow_s}em ${rgba(st.glow_color, 0.6)}, ` : '';
    return [`--cx-font:"${st.css_font}"`, `--cx-text:${st.text}`, `--cx-active:${st.active}`, `--cx-oc:${st.outline}`,
      `--cx-ow:${st.outline_w / 100}`, `--cx-shadow:${glow}${shadow}`, `--cx-bc:${rgba(st.box_color, st.box_a / 100)}`,
      `--cx-sp:${st.spacing / 100}em`, `--cx-tilt:${st.tilt}deg`, `--cx-style:${st.italic ? 'italic' : 'normal'}`].join(';');
  }
  function customLine(st, words, k, fresh) {
    const cls = ['vc-cap', 'vc-cx', `fx-${st.word_fx}`, `box-${st.box}`, `in-${st.enter}`];
    if (fresh && st.enter !== 'none') cls.push('enter');
    return `<div class="${cls.join(' ')}" style="${customVars(st)}">${words.map((w, j) => {
      const c = [];
      if (j === k) c.push('on', 'lit');
      else if (st.mode === 'so_far' && j < k) c.push('lit');
      if (st.mode === 'reveal' && j > k) c.push('hide');
      return `<span class="${c.join(' ')}">${esc(w)}</span>`;
    }).join(' ')}</div>`;
  }

  function lines(words, st, vertical) {
    const gs = group(words, st.words, vertical ? st.chars : st.chars + 10, st.gap);
    return gs.map((g, n) => {
      const nxt = n + 1 < gs.length ? gs[n + 1][0][0] : null;
      let end = g[g.length - 1][1];
      end = nxt !== null && nxt - end < 0.35 ? nxt : end + 0.25;
      return { start: g[0][0], end, words: g, n };
    });
  }

  // attach(video, {words, width, height}, styleId, styles, {time, host}) -> {set(styleId|null), setWords(meta), draw(), destroy()}
  // `time` gives the short's own clock when the video plays a longer source (the editor).
  function attach(video, meta, styleId, styles, extra = {}) {
    const host = extra.host || video.parentElement;
    const layer = document.createElement('div');
    layer.className = 'vc-cap-layer';
    layer.setAttribute('aria-hidden', 'true');
    host.appendChild(layer);
    let vertical = (meta.height || 1920) > (meta.width || 1080);
    let st = null, ls = [], shown = '', raf = 0;

    const size = () => {
      if (!st) return;
      const r = host.getBoundingClientRect();
      const short = Math.min(r.width, r.height);
      const sz = st.custom ? st.size_share : st.size, ps = st.custom ? st.pos_share : st.pos;
      layer.style.setProperty('--cap-size', `${short * sz * (vertical ? 1 : 0.8)}px`);
      layer.style.setProperty('--cap-bottom', `${r.height * ps * (vertical ? 1 : 0.55)}px`);
    };
    let peek = null;          // a design shown while the pointer is on its button (not saved)
    const draw = () => {
      if (!st) { if (shown) { layer.innerHTML = ''; shown = ''; } return; }
      let t = extra.time ? extra.time() : video.currentTime;
      let line = ls.find((l) => t >= l.start && t < l.end);
      if (!line && peek) {        // paused between lines or before the first: show the next line, so there is a look
        line = ls.find((l) => l.start >= t) || ls[0];
        if (line) t = line.words.length > 1 ? line.words[1][0] : line.start;
      }
      if (!line) { if (shown) { layer.innerHTML = ''; shown = ''; } return; }
      let k = 0;
      line.words.forEach((w, j) => { if (t >= (j === 0 ? line.start : w[0])) k = j; });
      const key = `${line.n}:${k}`;
      if (st.custom && st.exit === 'fade' && layer.firstChild && key === shown) {
        layer.firstChild.style.opacity = String(Math.max(0, Math.min(1, (line.end - t) / 0.14)));
      }
      if (key === shown) return;
      const fresh = !shown.startsWith(`${line.n}:`);
      shown = key;
      if (st.custom) {
        layer.innerHTML = customLine(st, line.words.map((w) => wordText(w[2], st.upper)), k, fresh);
        return;
      }
      const words = styleId === 'clean' ? [line.words.map((w) => w[2].trim()).join(' ')] : line.words.map((w) => wordText(w[2], st.upper));
      layer.innerHTML = `<div class="vc-cap vc-cap-${styleId}${fresh ? ' enter' : ''}${styleId === 'one' && line.n % 2 ? ' alt' : ''}">${
        words.map((w, j) => `<span class="${styleId !== 'clean' && j === k ? 'on' : ''}">${esc(w)}</span>`).join(' ')}</div>`;
    };
    const loop = () => { draw(); raf = video.paused ? 0 : requestAnimationFrame(loop); };
    const play = () => { if (!raf) raf = requestAnimationFrame(loop); };
    const ro = new ResizeObserver(size);
    ro.observe(host);
    ['play', 'playing'].forEach((e) => video.addEventListener(e, play));
    ['seeked', 'timeupdate', 'pause'].forEach((e) => video.addEventListener(e, draw));

    const api = {
      set(id) {
        styleId = id && styles[id] ? id : null;
        st = styleId ? styles[styleId] : null;
        ls = st ? lines(meta.words || [], st, vertical) : [];
        shown = ''; size(); draw();
      },
      setWords(m) { meta = m; vertical = (meta.height || 1920) > (meta.width || 1080); api.set(styleId); },
      // preview(id): show that design for a moment; preview(null): back to the saved one.
      preview(id) {
        if (id && styles[id]) {
          if (peek === null) peek = styleId || '';
          const keep = peek; api.set(id); peek = keep;
        } else if (peek !== null) {
          const back = peek || null; peek = null; api.set(back);
        }
      },
      draw() { draw(); },
      destroy() { cancelAnimationFrame(raf); ro.disconnect(); layer.remove(); },
    };
    api.set(styleId);
    return api;
  }

  // A design button that shows the design itself: a sample line on a dark tile, then its name.
  const SAMPLE = { one: ['GRACE'], clean: ['His grace is enough'] };
  function chip(id, st, pressed) {
    if (st.custom) {
      const ws = (st.words === 1 ? ['GRACE'] : ['His', 'grace', 'wins']).map((w) => wordText(w, st.upper));
      return `<button type="button" class="vc-capchip" data-cap="${esc(id)}" aria-pressed="${pressed}" title="${esc(st.hint)}">`
        + `<span class="vc-chip-tile" aria-hidden="true">${customLine({ ...st, enter: 'none' }, ws, ws.length > 1 ? 1 : 0, false)}</span>`
        + `<span class="vc-chip-name">${esc(st.label)}</span></button>`;
    }
    const words = SAMPLE[id] || (st.upper ? ['HIS', 'GRACE', 'WINS'] : ['His', 'grace', 'wins']);
    const on = id === 'clean' ? -1 : (words.length > 1 ? 1 : 0);
    return `<button type="button" class="vc-capchip" data-cap="${esc(id)}" aria-pressed="${pressed}" title="${esc(st.hint)}">`
      + `<span class="vc-chip-tile" aria-hidden="true"><span class="vc-cap vc-cap-${esc(id)}">${words.map((w, j) =>
        `<span class="${j === on ? 'on' : ''}">${esc(w)}</span>`).join(' ')}</span></span>`
      + `<span class="vc-chip-name">${esc(st.label)}</span></button>`;
  }

  window.SwmCaptions = { attach, group, chip, customLine, wordText };
})();
