// Video Clipper: the visual editor for one short (admin/clipper-editor.html).
// The short is a list of clips (stretches of the long source, in playing order). The browser plays the source
// through them; the server turns them into the exact cut (pauses removed, timeline.py) for both this preview and
// the render, so what plays here is what gets cut. Undo/redo, a local backup and an unsaved-changes guard keep
// work safe; every action has a key (Space, S, Delete, arrows, Ctrl+Z, Ctrl+S).
(() => {
  const root = document.getElementById('ve');
  if (!root || root.dataset.ready) return;
  root.dataset.ready = '1';
  const ctl = new AbortController(); const o = { signal: ctl.signal };
  document.addEventListener('turbo:before-visit', () => ctl.abort(), { once: true });

  const B = JSON.parse(document.getElementById('ve-boot').textContent);
  const csrf = root.dataset.csrf;
  const $ = (id) => document.getElementById(id);
  const video = $('ve-video'), stage = $('ve-stage'), scroll = $('ve-scroll'), track = $('ve-track');
  const ruler = $('ve-ruler'), playhead = $('ve-playhead'), lane = $('ve-words'), map = $('ve-map');
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtT = (t) => { t = Math.max(0, t); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, '0')}`; };
  const clock = (t) => { t = Math.max(0, Math.round(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60; return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`; };
  const toastEl = $('vc-toast'); let toastT;
  const toast = (t) => { toastEl.textContent = t; toastEl.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('on'), 2600); };
  const SIZES = { vertical: [1080, 1920], square: [1080, 1080], landscape: [1920, 1080] };

  // ── state ──
  let S = {
    clips: B.edit.clips.map((c) => ({ start: +c.start, end: +c.end })), tighten: B.edit.tighten !== false,
    frame_x: B.edit.frame_x ?? null, format: B.format, framing: B.framing, title: B.title,
    captions_on: B.captions_on, caption_style: B.caption_style, frame: B.frame || 'default',
  };
  const saved = JSON.stringify(S);
  const past = [], future = [];
  let sel = 0, T = 0, playing = false, k = 0, pps = 40;
  let cut = null;                          // {segments:[[s,e,clip]], words, length}
  let words = (B.words || []).slice();     // [[start, end, text]] on the project's clock
  let focus = null;                        // words lane window when not following the selected clip
  let anchor = null, pick = null;          // word selection (indices into `words`)

  const snapshot = () => JSON.stringify(S);
  function commit(mutate, label) {
    past.push(snapshot()); if (past.length > 200) past.shift(); future.length = 0;
    mutate(); changed(label);
  }
  function changed(label) {
    S.clips = S.clips.filter((c) => c.end - c.start >= 0.2);
    if (!S.clips.length) S.clips = [{ start: 0, end: Math.min(B.duration, 20) }];
    sel = Math.max(0, Math.min(sel, S.clips.length - 1));
    try { localStorage.setItem(`ve:${B.short}`, JSON.stringify({ at: Date.now(), S })); } catch (e) {}
    $('ve-state').textContent = snapshot() === saved ? '' : 'Unsaved changes';
    localCut(); draw(); requestCut();
    if (label) toast(label);
  }

  // ── the cut: local guess at once, the server's exact cut a moment later ──
  function localCut() {
    const segs = S.clips.map((c, i) => [c.start, c.end, i]);
    cut = { segments: segs, words: cut ? cut.words : [], length: segs.reduce((n, s) => n + s[1] - s[0], 0), local: true };
  }
  let cutTimer = 0, cutSeq = 0;
  function requestCut() {
    clearTimeout(cutTimer);
    cutTimer = setTimeout(async () => {
      const seq = ++cutSeq;
      try {
        const r = await fetch(`${B.base}/shorts/${B.short}/timeline/preview`, {
          method: 'POST', credentials: 'same-origin', signal: ctl.signal,
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ clips: S.clips, tighten: S.tighten }),
        });
        const j = await r.json();
        if (seq !== cutSeq) return;
        if (!j.ok) { toast(j.error || 'Could not work out the cut.'); return; }
        // Which clip each played segment belongs to (segments stay inside the clip they came from).
        let ci = 0;
        const segs = j.segments.map(([s, e]) => {
          while (ci < S.clips.length - 1 && !(s >= S.clips[ci].start - 0.05 && e <= S.clips[ci].end + 0.05)) ci++;
          return [s, e, ci];
        });
        cut = { segments: segs, words: j.words, length: j.length };
        T = Math.min(T, cut.length);
        draw(); captions.setWords({ words: cut.words, width: SIZES[S.format][0], height: SIZES[S.format][1] });
      } catch (e) { /* offline: the local cut keeps working */ }
    }, 250);
  }
  const cum = () => { const out = []; let n = 0; for (const s of cut.segments) { out.push(n); n += s[1] - s[0]; } return out; };
  function locate(t) {          // short time -> {k, src}
    const c = cum();
    for (let i = 0; i < cut.segments.length; i++) {
      const [s, e] = cut.segments[i];
      if (t < c[i] + (e - s) || i === cut.segments.length - 1) return { k: i, src: Math.min(e, s + Math.max(0, t - c[i])) };
    }
    return { k: 0, src: S.clips[0].start };
  }
  const clipTimes = () => {      // per clip: [timeline start, played length]
    const c = cum(); const out = S.clips.map(() => [null, 0]);
    cut.segments.forEach(([s, e, ci], i) => { if (!out[ci]) return; if (out[ci][0] === null) out[ci][0] = c[i]; out[ci][1] += e - s; });
    let n = 0; out.forEach((x) => { if (x[0] === null) x[0] = n; n = x[0] + x[1]; });
    return out;
  };

  // ── playback ──
  function seek(t) {
    T = Math.max(0, Math.min(t, cut.length)); const L = locate(T); k = L.k;
    video.currentTime = L.src + B.offset; drawHead(); captions.draw();
  }
  function play() {
    if (playing) { video.pause(); playing = false; drawHead(); return; }
    if (T >= cut.length - 0.05) seek(0);
    playing = true; video.play().catch(() => { playing = false; drawHead(); }); tick();
  }
  function tick() {
    if (!playing) return;
    const seg = cut.segments[k];
    const src = video.currentTime - B.offset;
    if (seg) {
      if (src >= seg[1] - 0.025) {
        if (k + 1 < cut.segments.length) { k += 1; video.currentTime = cut.segments[k][0] + B.offset; }
        else { video.pause(); playing = false; T = cut.length; drawHead(); return; }
      } else if (src < seg[0] - 0.5 || src > seg[1] + 0.5) {
        video.currentTime = seg[0] + B.offset;
      } else {
        T = cum()[k] + Math.max(0, src - seg[0]);
      }
    }
    drawHead(); requestAnimationFrame(tick);
  }
  video.addEventListener('loadedmetadata', () => { $('ve-loading').textContent = ''; seek(0); }, o);
  video.addEventListener('waiting', () => { $('ve-loading').textContent = 'Loading…'; }, o);
  video.addEventListener('playing', () => { $('ve-loading').textContent = ''; }, o);
  video.addEventListener('error', () => { $('ve-loading').textContent = 'The video could not be loaded.'; }, o);

  // ── drawing ──
  function drawHead() {
    $('ve-now').textContent = fmtT(T); $('ve-len').textContent = fmtT(cut.length);
    playhead.style.transform = `translateX(${T * pps}px)`;
    root.querySelector('[data-cmd=play] i').className = playing ? 'ri-pause-fill' : 'ri-play-fill';
    if (playing) {
      const x = T * pps;
      if (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth - 40) scroll.scrollLeft = x - 60;
    }
  }
  function textOf(c) {
    const ws = words.filter((w) => w[0] >= c.start - 0.05 && w[1] <= c.end + 0.05).map((w) => w[2]);
    return ws.length ? ws.join(' ') : '(no words)';
  }
  function draw() {
    const times = clipTimes();
    const width = Math.max(scroll.clientWidth - 2, (cut.length + 2) * pps);
    track.style.width = ruler.style.width = `${width}px`;
    track.innerHTML = S.clips.map((c, i) => {
      const [t0, len] = times[i];
      return `<div class="ve-clip ${i === sel ? 'sel' : ''}" role="option" aria-selected="${i === sel}" tabindex="${i === sel ? 0 : -1}" data-i="${i}"
        style="left:${t0 * pps}px;width:${Math.max(14, len * pps)}px" title="${esc(clock(c.start))}–${esc(clock(c.end))} in the video">
        <span class="ve-h l" data-h="l" aria-label="Trim the start"></span>
        <span class="ve-clip-text">${esc(textOf(c))}</span><span class="ve-clip-len">${len.toFixed(1)} s</span>
        <span class="ve-h r" data-h="r" aria-label="Trim the end"></span></div>`;
    }).join('');
    const step = pps > 60 ? 1 : pps > 25 ? 2 : pps > 10 ? 5 : 10;
    let marks = '';
    for (let t = 0; t <= cut.length + 2; t += step) marks += `<span style="left:${t * pps}px">${clock(t)}</span>`;
    ruler.innerHTML = marks;
    drawHead(); drawLane(); drawMap(); drawInspector();
  }
  function drawLane() {
    const c = S.clips[sel];
    const [a, b] = focus || [c.start - 15, c.end + 15];
    ensureWords(a, b);
    const inAny = (w) => S.clips.some((x) => (w[0] + w[1]) / 2 >= x.start && (w[0] + w[1]) / 2 <= x.end);
    const lo = pick ? Math.min(pick[0], pick[1]) : -1, hi = pick ? Math.max(pick[0], pick[1]) : -1;
    lane.innerHTML = words.map((w, i) => (w[1] < a || w[0] > b ? '' :
      `<span data-w="${i}" class="${inAny(w) ? 'in' : 'out'}${(w[0] + w[1]) / 2 >= c.start && (w[0] + w[1]) / 2 <= c.end ? ' cur' : ''}${i >= lo && i <= hi ? ' pick' : ''}">${esc(w[2])}</span>`)).join(' ')
      || '<span class="vc-muted">No words here.</span>';
    root.querySelector('[data-cmd=cut-words]').disabled = !pick;
    root.querySelector('[data-cmd=add-words]').disabled = !pick;
  }
  function drawMap() {
    const D = B.duration || 1;
    map.querySelectorAll('.ve-map-clip').forEach((x) => x.remove());
    S.clips.forEach((c, i) => {
      const m = document.createElement('span'); m.className = `ve-map-clip${i === sel ? ' sel' : ''}`;
      m.style.left = `${(c.start / D) * 100}%`; m.style.width = `${Math.max(0.3, ((c.end - c.start) / D) * 100)}%`;
      map.appendChild(m);
    });
    const [a, b] = focus || [S.clips[sel].start - 15, S.clips[sel].end + 15];
    const win = $('ve-map-win'); win.style.left = `${(Math.max(0, a) / D) * 100}%`; win.style.width = `${(Math.min(D, b) - Math.max(0, a)) / D * 100}%`;
    $('ve-source-read').textContent = `Clips from ${clock(Math.min(...S.clips.map((c) => c.start)))} to ${clock(Math.max(...S.clips.map((c) => c.end)))} of ${clock(D)}. Click anywhere to read what is said there.`;
  }
  function drawInspector() {
    $('ve-format').innerHTML = Object.entries(B.formats).map(([k2, v]) => `<button type="button" data-format="${esc(k2)}" aria-pressed="${k2 === S.format}">${esc(String(v).split(' ')[0])}</button>`).join('');
    const fr = $('ve-framing');
    if (!fr.options.length) fr.innerHTML = Object.entries(B.framings).map(([k2, v]) => `<option value="${esc(k2)}">${esc(v)}</option>`).join('');
    fr.value = S.framing; fr.disabled = S.frame_x !== null;
    $('ve-manual').checked = S.frame_x !== null;
    const fx = $('ve-frame-x'); fx.hidden = S.frame_x === null; if (S.frame_x !== null) fx.value = S.frame_x;
    $('ve-tighten').checked = S.tighten;
    $('ve-cap-on').checked = S.captions_on;
    const chips = $('ve-cap-styles');
    chips.setAttribute('aria-disabled', String(!S.captions_on));
    chips.innerHTML = Object.entries(B.styles).map(([k2, v]) => `<button type="button" data-cap="${esc(k2)}" aria-pressed="${k2 === S.caption_style}" title="${esc(v.hint)}">${esc(v.label)}</button>`).join('');
    stage.className = `ve-stage ${S.format}${S.framing === 'fit' && S.frame_x === null ? ' fit' : ''}`;
    video.style.objectPosition = S.frame_x !== null ? `${S.frame_x * 100}% 50%` : '50% 50%';
    $('ve-frame-hint').hidden = !(S.framing === 'speaker' && S.frame_x === null && S.format !== 'landscape');
    captions.set(S.captions_on ? S.caption_style : null);
    // Frame: the choices for this shape, and the one that shows (the church default unless chosen).
    const mine = (B.frames || []).filter((f) => f.format === S.format);
    const def = mine.find((f) => f.id === (B.frame_defaults || {})[S.format]);
    const fsel = $('ve-frame');
    fsel.innerHTML = [['default', `Default${def ? ` (${def.name})` : ' (none)'}`], ['none', 'No frame'], ...mine.map((f) => [f.id, f.name])]
      .map(([v, l]) => `<option value="${esc(v)}" ${v === S.frame ? 'selected' : ''}>${esc(l)}</option>`).join('');
    if (![...fsel.options].some((x) => x.selected)) fsel.value = 'default';
    const shown = S.frame === 'none' ? null : (S.frame === 'default' ? def : mine.find((f) => f.id === S.frame) || def);
    const ovl = $('ve-frame-ovl');
    ovl.hidden = !shown;
    if (shown && ovl.getAttribute('src') !== shown.url) ovl.src = shown.url;
  }

  // ── words ──
  const fetched = [[B.window[0], B.window[1]]];
  async function ensureWords(a, b) {
    a = Math.max(0, a); b = Math.min(B.duration || b, b);
    if (fetched.some(([x, y]) => x <= a && y >= b)) return;
    fetched.push([a - 60, b + 60]);
    try {
      const r = await fetch(`${B.base}/words.json?from=${(a - 60).toFixed(1)}&to=${(b + 60).toFixed(1)}`, { credentials: 'same-origin', signal: ctl.signal });
      const j = await r.json();
      if (!j.ok) return;
      const have = new Set(words.map((w) => w[0].toFixed(3)));
      j.words.forEach((w) => { if (!have.has(w[0].toFixed(3))) words.push(w); });
      words.sort((x, y) => x[0] - y[0]); anchor = pick = null;
      draw();
    } catch (e) {}
  }
  const snapStart = (t) => { const w = words.find((x) => Math.abs(x[0] - t) < 0.25); return w ? Math.max(0, w[0] - 0.06) : t; };
  const snapEnd = (t) => { const w = words.find((x) => Math.abs(x[1] - t) < 0.25); return w ? w[1] + 0.14 : t; };

  // ── commands ──
  function split() {
    const L = locate(T); const ci = cut.segments[L.k] ? cut.segments[L.k][2] : sel; const c = S.clips[ci];
    if (!c || L.src <= c.start + 0.15 || L.src >= c.end - 0.15) { toast('Move the playhead inside a clip to split it.'); return; }
    commit(() => { S.clips.splice(ci, 1, { start: c.start, end: L.src }, { start: L.src, end: c.end }); sel = ci + 1; }, 'Split');
  }
  function del() {
    if (S.clips.length < 2) { toast('A short needs at least one clip.'); return; }
    commit(() => { S.clips.splice(sel, 1); }, 'Clip deleted');
  }
  function shift(d) {
    const lo = Math.min(...S.clips.map((c) => c.start)), hi = Math.max(...S.clips.map((c) => c.end));
    d = Math.max(-lo, Math.min(d, (B.duration || hi) - hi));
    if (!d) return;
    commit(() => { S.clips = S.clips.map((c) => ({ start: c.start + d, end: c.end + d })); }, `Moved ${d > 0 ? 'later' : 'earlier'} by ${Math.abs(d)} s`);
  }
  function undo() { if (!past.length) return; future.push(snapshot()); S = JSON.parse(past.pop()); changed('Undone'); }
  function redo() { if (!future.length) return; past.push(snapshot()); S = JSON.parse(future.pop()); changed('Redone'); }
  async function save(reset) {
    const btn = root.querySelector('[data-cmd=save]'); btn.disabled = true;
    try {
      const r = await fetch(`${B.base}/shorts/${B.short}/timeline`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ ...S, title: $('ve-title').value, reset: !!reset }),
      });
      const j = await r.json();
      if (!j.ok) { toast(j.error || 'Could not save.'); btn.disabled = false; return; }
      try { localStorage.removeItem(`ve:${B.short}`); } catch (e) {}
      leaving = true; window.location.href = j.url;
    } catch (e) { toast('Could not reach the server. Your edit is kept on this device.'); btn.disabled = false; }
  }

  // ── pointer: trim, reorder, seek ──
  let drag = null;
  track.addEventListener('pointerdown', (e) => {
    const el = e.target.closest('.ve-clip'); if (!el) return;
    const i = +el.dataset.i; const h = e.target.closest('[data-h]');
    sel = i; focus = null; anchor = pick = null;
    drag = { i, h: h ? h.dataset.h : null, x0: e.clientX, orig: { ...S.clips[i] }, before: snapshot(), moved: false };
    el.setPointerCapture(e.pointerId); draw();
  }, o);
  track.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x0; if (Math.abs(dx) > 4) drag.moved = true;
    if (!drag.moved) return;
    const c = S.clips[drag.i], ds = dx / pps;
    if (drag.h === 'l') c.start = Math.max(0, Math.min(drag.orig.start + ds, c.end - 0.3));
    else if (drag.h === 'r') c.end = Math.min(B.duration || 1e9, Math.max(drag.orig.end + ds, c.start + 0.3));
    else {
      const times = clipTimes(); const x = (e.clientX - track.getBoundingClientRect().left) / pps;
      drag.to = times.findIndex(([t0, len]) => x < t0 + len / 2); if (drag.to < 0) drag.to = S.clips.length - 1;
      track.querySelectorAll('.ve-clip').forEach((n) => n.classList.toggle('drop', +n.dataset.i === drag.to && drag.to !== drag.i));
      return;
    }
    localCut(); draw();
  }, o);
  track.addEventListener('pointerup', (e) => {
    if (!drag) return;
    const d = drag; drag = null;
    if (!d.moved) { const times = clipTimes(); seek(times[d.i][0] + 0.001); return; }
    const c = S.clips[d.i];
    if (d.h) {
      if (!e.altKey) { if (d.h === 'l') c.start = snapStart(c.start); else c.end = snapEnd(c.end); }
      past.push(d.before); future.length = 0; changed(d.h === 'l' ? 'Start trimmed' : 'End trimmed');
    } else if (d.to !== undefined && d.to !== d.i) {
      past.push(d.before); future.length = 0;
      const [m] = S.clips.splice(d.i, 1); S.clips.splice(d.to, 0, m); sel = d.to; changed('Clip moved');
    } else draw();
  }, o);
  const seekAt = (e) => { const x = e.clientX - ruler.getBoundingClientRect().left; seek(x / pps); };
  ruler.addEventListener('pointerdown', (e) => { seekAt(e); const mv = (ev) => seekAt(ev); window.addEventListener('pointermove', mv, o); window.addEventListener('pointerup', () => window.removeEventListener('pointermove', mv), { once: true }); }, o);
  map.addEventListener('click', (e) => {
    const r = map.getBoundingClientRect(); const t = ((e.clientX - r.left) / r.width) * (B.duration || 0);
    focus = [Math.max(0, t - 30), t + 30]; anchor = pick = null; draw();
  }, o);

  // ── words lane ──
  lane.addEventListener('click', (e) => {
    const el = e.target.closest('[data-w]'); if (!el) return;
    const i = +el.dataset.w, w = words[i], c = S.clips[sel];
    if (e.shiftKey && anchor !== null) { pick = [anchor, i]; drawLane(); return; }
    anchor = i; pick = [i, i];
    const mid = (w[0] + w[1]) / 2;
    if (!focus && mid < c.start && c.start - w[0] < 60) { commit(() => { c.start = Math.max(0, w[0] - 0.06); }, 'Clip starts earlier'); return; }
    if (!focus && mid > c.end && w[1] - c.end < 60) { commit(() => { c.end = w[1] + 0.14; }, 'Clip ends later'); return; }
    const L = cut.segments.findIndex(([s, e2]) => w[0] >= s - 0.05 && w[0] <= e2);
    if (L >= 0) seek(cum()[L] + Math.max(0, w[0] - cut.segments[L][0]));
    drawLane();
  }, o);
  function cutWords() {
    if (!pick) return;
    const a = words[Math.min(...pick)], b = words[Math.max(...pick)];
    const ci = S.clips.findIndex((c) => a[0] >= c.start - 0.05 && b[1] <= c.end + 0.05);
    if (ci < 0) { toast('Select words inside one clip to cut them out.'); return; }
    const c = S.clips[ci];
    commit(() => {
      const parts = [{ start: c.start, end: a[0] - 0.02 }, { start: b[1] + 0.02, end: c.end }].filter((x) => x.end - x.start >= 0.2);
      S.clips.splice(ci, 1, ...parts); sel = Math.min(ci, S.clips.length - 1); anchor = pick = null;
    }, 'Words cut out');
  }
  function addWords() {
    if (!pick) return;
    const a = words[Math.min(...pick)], b = words[Math.max(...pick)];
    commit(() => { S.clips.splice(sel + 1, 0, { start: Math.max(0, a[0] - 0.06), end: b[1] + 0.14 }); sel += 1; anchor = pick = null; focus = null; }, 'Clip added');
  }

  // ── inspector ──
  root.addEventListener('click', (e) => {
    const f = e.target.closest('[data-format]'); if (f) { commit(() => { S.format = f.dataset.format; }, ''); return; }
    const cs = e.target.closest('[data-cap]'); if (cs) { commit(() => { S.caption_style = cs.dataset.cap; S.captions_on = true; }, ''); return; }
    const sh = e.target.closest('[data-shift]'); if (sh) { shift(+sh.dataset.shift); return; }
    const c = e.target.closest('[data-cmd]'); if (!c) return;
    ({
      play, split, delete: del, undo, redo, save: () => save(false), 'cut-words': cutWords, 'add-words': addWords,
      reset: () => { if (confirm('Throw away your edit and go back to the AI\'s cut?')) save(true); },
      'zoom-in': () => { pps = Math.min(200, pps * 1.5); draw(); }, 'zoom-out': () => { pps = Math.max(4, pps / 1.5); draw(); },
    }[c.dataset.cmd] || (() => {}))();
  }, o);
  $('ve-framing').addEventListener('change', (e) => commit(() => { S.framing = e.target.value; }, ''), o);
  $('ve-manual').addEventListener('change', (e) => commit(() => { S.frame_x = e.target.checked ? 0.5 : null; }, ''), o);
  let fxBefore = null;
  $('ve-frame-x').addEventListener('pointerdown', () => { fxBefore = snapshot(); }, o);
  $('ve-frame-x').addEventListener('input', (e) => { S.frame_x = +e.target.value; video.style.objectPosition = `${S.frame_x * 100}% 50%`; }, o);
  $('ve-frame-x').addEventListener('change', () => { if (fxBefore) { past.push(fxBefore); future.length = 0; fxBefore = null; } changed(''); }, o);
  $('ve-frame').addEventListener('change', (e) => commit(() => { S.frame = e.target.value; }, ''), o);
  $('ve-tighten').addEventListener('change', (e) => commit(() => { S.tighten = e.target.checked; }, e.target.checked ? 'Pauses removed' : 'Pauses kept'), o);
  $('ve-cap-on').addEventListener('change', (e) => commit(() => { S.captions_on = e.target.checked; }, ''), o);
  $('ve-title').addEventListener('change', (e) => { S.title = e.target.value; changed(''); }, o);

  // ── keys ──
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea, select')) { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save(false); } return; }
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === ' ') { e.preventDefault(); play(); }
    else if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
    else if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(false); }
    else if (e.key === 's' || e.key === 'S') { split(); }
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); if (pick) cutWords(); else del(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(T - (e.shiftKey ? 1 : 0.1)); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seek(T + (e.shiftKey ? 1 : 0.1)); }
    else if (e.key === '+' || e.key === '=') { pps = Math.min(200, pps * 1.5); draw(); }
    else if (e.key === '-') { pps = Math.max(4, pps / 1.5); draw(); }
  }, o);

  // ── safety ──
  let leaving = false;
  window.addEventListener('beforeunload', (e) => { if (!leaving && snapshot() !== saved) { e.preventDefault(); e.returnValue = ''; } }, o);
  document.addEventListener('turbo:before-visit', (e) => { if (!leaving && snapshot() !== saved && !confirm('Leave without saving your edit? It stays on this device.')) e.preventDefault(); }, o);

  // ── start ──
  const captions = window.SwmCaptions.attach(video, { words: [], width: SIZES[S.format][0], height: SIZES[S.format][1] },
    null, B.styles, { time: () => T, host: stage });
  localCut();
  pps = Math.max(8, Math.min(80, (scroll.clientWidth - 40) / Math.max(10, cut.length)));
  try {
    const d = JSON.parse(localStorage.getItem(`ve:${B.short}`) || 'null');
    if (d && JSON.stringify(d.S) !== saved && confirm('You have an unsaved edit of this short on this device. Carry on with it?')) { past.push(snapshot()); S = d.S; }
  } catch (e) {}
  changed('');
})();
