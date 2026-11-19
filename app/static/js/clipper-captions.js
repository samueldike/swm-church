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
      layer.style.setProperty('--cap-size', `${short * st.size * (vertical ? 1 : 0.8)}px`);
      layer.style.setProperty('--cap-bottom', `${r.height * st.pos * (vertical ? 1 : 0.55)}px`);
    };
    const draw = () => {
      if (!st) { if (shown) { layer.innerHTML = ''; shown = ''; } return; }
      const t = extra.time ? extra.time() : video.currentTime;
      const line = ls.find((l) => t >= l.start && t < l.end);
      if (!line) { if (shown) { layer.innerHTML = ''; shown = ''; } return; }
      let k = 0;
      line.words.forEach((w, j) => { if (t >= (j === 0 ? line.start : w[0])) k = j; });
      const key = `${line.n}:${k}`;
      if (key === shown) return;
      const fresh = !shown.startsWith(`${line.n}:`);
      shown = key;
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
      draw() { draw(); },
      destroy() { cancelAnimationFrame(raf); ro.disconnect(); layer.remove(); },
    };
    api.set(styleId);
    return api;
  }

  window.SwmCaptions = { attach, group };
})();
