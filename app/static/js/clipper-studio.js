// Caption Studio (/admin/clipper/captions): make and edit the church's caption designs with a live preview.
// The preview is clipper-captions.js (customLine), which mirrors app/core/clipper/studio.py:ass; "Check the real video"
// asks the server for one frame drawn by ffmpeg + libass, the same path as the downloads.
(() => {
  const root = document.getElementById('cs');
  if (!root || !window.SwmCaptions) return;
  const ctl = new AbortController(); const o = { signal: ctl.signal };
  document.addEventListener('turbo:before-visit', () => ctl.abort(), { once: true });
  const B = JSON.parse(document.getElementById('cs-boot').textContent);
  const csrf = root.dataset.csrf;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const q = `?church=${B.church}`;
  const toastEl = $('vc-toast'); let tt;
  const toast = (t) => { toastEl.textContent = t; toastEl.classList.add('on'); clearTimeout(tt); tt = setTimeout(() => toastEl.classList.remove('on'), 2600); };

  // ── the design being edited ──
  let designs = B.designs.slice();
  let D = null;            // working copy
  let saved = '';          // JSON of the saved version ('' = never saved)
  const keys = Object.keys(B.base);
  const plain = (d) => Object.fromEntries(keys.map((k) => [k, d[k]]));
  const dirty = () => JSON.stringify(plain(D)) !== saved;
  const pub = (d) => ({
    ...d, custom: true, label: d.name, hint: B.fonts[d.font]?.label || '', chars: 6 + 6 * d.words, gap: 0.6,
    size_share: B.base_size * d.size / 100, pos_share: d.pos / 100, css_font: B.fonts[d.font]?.css || 'SWM Poppins Extra Bold',
  });

  // ── preview ──
  const SIZES = { vertical: [1080, 1920], square: [1080, 1080], landscape: [1920, 1080] };
  let format = 'vertical', bg = B.photo ? 'photo' : 'dark', playing = true, clock = 0, last = 0;
  const stage = $('cs-stage');
  const words = () => {
    const out = []; let t = 0.3;
    for (const w of ($('cs-text').value.trim() || B.sample).split(/\s+/).slice(0, 40)) {
      const dur = 0.18 + 0.045 * w.length;
      out.push([+t.toFixed(3), +(t + dur).toFixed(3), w]);
      t += dur + (/[.?!,]$/.test(w) ? 0.32 : 0.06);
    }
    return out;
  };
  let meta = { words: [], width: 1080, height: 1920 };
  const styles = {};
  const stub = { paused: true, currentTime: 0, addEventListener() {}, parentElement: stage };
  const api = window.SwmCaptions.attach(stub, meta, null, styles, { host: stage, time: () => clock });
  const total = () => (meta.words.length ? meta.words[meta.words.length - 1][1] + 0.9 : 3);
  const refreshWords = () => { meta = { words: words(), width: SIZES[format][0], height: SIZES[format][1] }; api.setWords(meta); };
  const refreshStyle = () => { styles.__s = pub(D); api.set('__s'); };
  const frame = (now) => {
    if (ctl.signal.aborted) return;
    if (playing) { clock += Math.min(0.1, (now - (last || now)) / 1000); if (clock > total()) clock = 0; }
    last = now;
    api.draw();
    requestAnimationFrame(frame);
  };
  const paintStage = () => {
    stage.className = `cs-stage ${format} bg-${bg}`;
    stage.style.backgroundImage = bg === 'photo' && B.photo ? `url("${B.photo}")` : '';
    root.querySelectorAll('#cs-format [data-format]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.format === format)));
    root.querySelectorAll('#cs-bg [data-bg]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.bg === bg)));
  };
  if (!B.photo) root.querySelector('#cs-bg [data-bg=photo]').hidden = true;

  // ── the controls ──
  const SWATCHES = ['#FFFFFF', '#FFE03A', '#F0C837', '#FF5A36', '#FF4FD8', '#7CE7FF', '#3CFF8A', '#000000'];
  const GROUPS = [
    { title: 'Font', fields: [{ k: 'font', t: 'font' }] },
    { title: 'Text', fields: [{ k: 'size', t: 'range', label: 'Size', unit: '%' }, { k: 'upper', t: 'check', label: 'CAPITAL LETTERS' },
      { k: 'italic', t: 'check', label: 'Italic' }, { k: 'spacing', t: 'range', label: 'Letter spacing' }, { k: 'tilt', t: 'range', label: 'Tilt', unit: '°' }] },
    { title: 'Colours', fields: [{ k: 'text', t: 'color', label: 'Words' }, { k: 'active', t: 'color', label: 'Spoken word' }] },
    { title: 'Spoken word', fields: [{ k: 'mode', t: 'select', label: 'Highlight' }, { k: 'word_fx', t: 'select', label: 'Effect' }] },
    { title: 'Outline and shadow', fields: [{ k: 'outline', t: 'color', label: 'Outline', show: (d) => d.box !== 'line' },
      { k: 'outline_w', t: 'range', label: 'Outline thickness', show: (d) => d.box !== 'line' },
      { k: 'shadow', t: 'color', label: 'Shadow', show: (d) => d.box !== 'line' }, { k: 'shadow_d', t: 'range', label: 'Shadow distance', show: (d) => d.box !== 'line' },
      { k: 'shadow_a', t: 'range', label: 'Shadow strength', unit: '%', show: (d) => d.box !== 'line' && d.shadow_d > 0 }] },
    { title: 'Glow', fields: [{ k: 'glow', t: 'check', label: 'Glow behind the words', show: (d) => d.box !== 'line' },
      { k: 'glow_color', t: 'color', label: 'Glow colour', show: (d) => d.glow && d.box !== 'line' },
      { k: 'glow_s', t: 'range', label: 'Glow size', show: (d) => d.glow && d.box !== 'line' }] },
    { title: 'Box', fields: [{ k: 'box', t: 'select', label: 'Box' }, { k: 'box_color', t: 'color', label: 'Box colour', show: (d) => d.box !== 'none' },
      { k: 'box_a', t: 'range', label: 'Box strength', unit: '%', show: (d) => d.box !== 'none' }] },
    { title: 'Motion', fields: [{ k: 'enter', t: 'select', label: 'Line comes in' }, { k: 'exit', t: 'select', label: 'Line goes out' }] },
    { title: 'Layout', fields: [{ k: 'words', t: 'range', label: 'Words per line' }, { k: 'pos', t: 'range', label: 'Height from the bottom', unit: '%' }] },
  ];
  const kinds = {};
  Object.entries(B.fonts).forEach(([id, f]) => { (kinds[f.kind] = kinds[f.kind] || []).push([id, f]); });

  function fieldHtml(f) {
    const v = D[f.k];
    const id = `cs-f-${f.k}`;
    if (f.t === 'font') {
      return `<div class="cs-fonts" role="radiogroup" aria-label="Font">${Object.entries(kinds).map(([kind, list]) =>
        `<div class="cs-font-kind">${esc(kind)}</div>${list.map(([fid, ff]) =>
          `<button type="button" role="radio" aria-checked="${fid === v}" data-font="${esc(fid)}" title="${esc(ff.label)}" style="font-family:'${esc(ff.css)}',sans-serif">Grace<small>${esc(ff.label)}</small></button>`).join('')}`).join('')}</div>`;
    }
    if (f.t === 'range') {
      const [lo, hi] = B.ranges[f.k];
      return `<label class="cs-row" for="${id}"><span>${esc(f.label)}</span><input type="range" id="${id}" data-k="${f.k}" min="${lo}" max="${hi}" step="1" value="${v}"><output>${v}${f.unit || ''}</output></label>`;
    }
    if (f.t === 'check') {
      return `<label class="cs-row cs-check"><input type="checkbox" data-k="${f.k}" ${v ? 'checked' : ''}><span>${esc(f.label)}</span></label>`;
    }
    if (f.t === 'color') {
      return `<div class="cs-row"><span>${esc(f.label)}</span><span class="cs-swatches">${SWATCHES.map((c) =>
        `<button type="button" data-swatch="${f.k}" data-c="${c}" style="background:${c}" aria-label="${c}" aria-pressed="${c === String(v).toUpperCase()}"></button>`).join('')}
        <input type="color" data-k="${f.k}" value="${esc(v)}" aria-label="${esc(f.label)}, any colour"></span></div>`;
    }
    const table = B.choices[f.k];
    return `<label class="cs-row" for="${id}"><span>${esc(f.label)}</span><select id="${id}" data-k="${f.k}">${Object.entries(table).map(([k, l]) =>
      `<option value="${esc(k)}" ${k === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`;
  }
  const openGroups = new Set(['Font', 'Colours', 'Spoken word', 'Motion']);
  function drawFields() {
    $('cs-fields').innerHTML = GROUPS.map((g) => {
      const shown = g.fields.filter((f) => !f.show || f.show(D));
      if (!shown.length) return '';
      return `<details class="cs-group" data-group="${esc(g.title)}" ${openGroups.has(g.title) ? 'open' : ''}><summary>${esc(g.title)}</summary>${shown.map(fieldHtml).join('')}</details>`;
    }).join('');
    $('cs-name').value = D.name;
    paintState();
  }
  function paintState() {
    const st = $('cs-state');
    const isNew = !D.id;
    st.textContent = isNew ? 'New, not saved' : (dirty() ? 'Changed, not saved' : 'Saved');
    st.className = `vc-pill ${!isNew && !dirty() ? 'ok' : 'run'}`;
    root.querySelector('[data-cmd=delete]').hidden = isNew || !B.can_edit;
    root.querySelector('[data-cmd=copy]').hidden = isNew || !B.can_edit;
    root.querySelector('[data-cmd=revert]').disabled = !dirty();
    root.querySelector('[data-cmd=save]').hidden = !B.can_edit;
    $('cs-readonly').hidden = !!B.can_edit;
    root.querySelectorAll('.cs-card').forEach((c) => c.setAttribute('aria-current', String(!!D.id && c.dataset.id === D.id)));
  }
  // A change: redraw what depends on it (some fields show or hide others).
  function change(k, v, redrawFields) {
    D[k] = v;
    refreshStyle();
    if (redrawFields) drawFields(); else paintState();
    drawCards();
  }
  const fields = $('cs-fields');
  fields.addEventListener('toggle', (e) => { const g = e.target.closest('details.cs-group'); if (g) { if (g.open) openGroups.add(g.dataset.group); else openGroups.delete(g.dataset.group); } }, { ...o, capture: true });
  fields.addEventListener('input', (e) => {
    const el = e.target.closest('[data-k]'); if (!el) return;
    const k = el.dataset.k;
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.type === 'range') { v = +v; const out = el.parentElement.querySelector('output'); const f = GROUPS.flatMap((g) => g.fields).find((x) => x.k === k); out.textContent = `${v}${f?.unit || ''}`; }
    if (el.type === 'color') v = v.toUpperCase();
    if (el.type === 'checkbox' || el.tagName === 'SELECT') return;      // handled on "change" (they show/hide others)
    change(k, v, false);
    if (el.type === 'color') el.closest('.cs-row').querySelectorAll('[data-swatch]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.c === v)));
  }, o);
  fields.addEventListener('change', (e) => {
    const el = e.target.closest('select[data-k], input[type=checkbox][data-k]');
    if (el) { change(el.dataset.k, el.type === 'checkbox' ? el.checked : el.value, true); return; }
    if (e.target.dataset?.k === 'shadow_d') drawFields();            // shows or hides "Shadow strength"
  }, o);
  fields.addEventListener('click', (e) => {
    const font = e.target.closest('[data-font]');
    if (font) { fields.querySelectorAll('[data-font]').forEach((b) => b.setAttribute('aria-checked', String(b === font))); change('font', font.dataset.font, false); return; }
    const sw = e.target.closest('[data-swatch]');
    if (sw) {
      const row = sw.closest('.cs-row');
      row.querySelectorAll('[data-swatch]').forEach((b) => b.setAttribute('aria-pressed', String(b === sw)));
      row.querySelector('input[type=color]').value = sw.dataset.c.toLowerCase();
      change(sw.dataset.swatch, sw.dataset.c, false);
    }
  }, o);
  $('cs-name').addEventListener('input', (e) => { D.name = e.target.value; paintState(); drawCards(); }, o);

  // ── the design cards ──
  const SAMPLE_WORDS = ['His', 'grace', 'wins'];
  const card = (d, id, isPreset) => {
    const st = pub(d);
    const ws = (st.words === 1 ? ['GRACE'] : SAMPLE_WORDS).map((w) => window.SwmCaptions.wordText(w, st.upper));
    return `<button type="button" class="cs-card" data-${isPreset ? 'preset' : 'id'}="${esc(id)}" ${isPreset ? '' : `aria-current="${D && D.id === id}"`}>
      <span class="cs-card-tile" aria-hidden="true">${window.SwmCaptions.customLine({ ...st, enter: 'none' }, ws, ws.length > 1 ? 1 : 0, false)}</span>
      <span class="cs-card-name">${esc(d.name)}${!isPreset && D && D.id === id && dirty() ? ' •' : ''}</span></button>`;
  };
  function drawCards() {
    const mine = designs.map((d) => (D && D.id === d.id ? card(D, d.id, false) : card(d, d.id, false)));
    $('cs-mine').innerHTML = mine.join('');
    $('cs-mine-empty').hidden = designs.length > 0;
  }
  $('cs-presets').innerHTML = Object.entries(B.presets).map(([k, d]) => card(d, k, true)).join('');

  let origin = null;       // what "Undo changes" goes back to
  function open(d, isSaved) {
    origin = { d, isSaved };
    D = plain(d); D.id = isSaved ? d.id : null;
    saved = JSON.stringify(plain(d));            // untouched = not changed, even for a new design from a preset
    drawFields(); refreshStyle(); drawCards(); clock = 0;
  }
  const leaveOk = () => !D || !dirty() || confirm('Your changes to this design are not saved. Leave them?');
  $('cs-mine').addEventListener('click', (e) => {
    const c = e.target.closest('[data-id]'); if (!c || (D && c.dataset.id === D.id) || !leaveOk()) return;
    open(designs.find((d) => d.id === c.dataset.id), true);
  }, o);
  $('cs-presets').addEventListener('click', (e) => {
    const c = e.target.closest('[data-preset]'); if (!c || !leaveOk()) return;
    open(B.presets[c.dataset.preset], false);
    toast(`Starting from "${B.presets[c.dataset.preset].name}". Change anything, then Save.`);
  }, o);

  // ── save, copy, delete ──
  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body || {}) });
    let j = {}; try { j = await r.json(); } catch (e) { j = { ok: false, error: 'The server answered unexpectedly.' }; }
    if (!r.ok) j.ok = false;
    return j;
  }
  async function save(asNew) {
    const body = { ...plain(D), id: asNew ? null : D.id };
    if (asNew) body.name = `${D.name} (copy)`.slice(0, 40);
    const r = await post(`/admin/clipper/captions/save${q}`, { design: body });
    if (!r.ok) { toast(r.error || 'Could not save.'); return; }
    const at = designs.findIndex((d) => d.id === r.design.id);
    if (at >= 0) designs[at] = r.design; else designs.push(r.design);
    open(r.design, true);
    toast(asNew ? 'Saved as a new design' : 'Saved. Pick it on any short under Captions.');
  }
  $('cs-actions').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cmd]'); if (!b) return;
    if (b.dataset.cmd === 'save') save(false);
    else if (b.dataset.cmd === 'copy') save(true);
    else if (b.dataset.cmd === 'revert') open(origin.d, origin.isSaved);
    else if (b.dataset.cmd === 'delete') {
      if (!confirm(`Delete the design "${D.name}"? Shorts that use it go back to the Pop design.`)) return;
      const r = await post(`/admin/clipper/captions/${encodeURIComponent(D.id)}/delete${q}`);
      if (!r.ok) { toast(r.error || 'Could not delete.'); return; }
      designs = designs.filter((d) => d.id !== D.id);
      saved = ''; open(designs[0] || B.presets.pop, !!designs[0]);
      toast(r.moved ? `Deleted. ${r.moved} short${r.moved > 1 ? 's' : ''} went back to Pop.` : 'Deleted.');
    }
  }, o);
  window.addEventListener('beforeunload', (e) => { if (D && dirty() && B.can_edit) { e.preventDefault(); e.returnValue = ''; } }, o);

  // ── preview controls ──
  $('cs-format').addEventListener('click', (e) => { const b = e.target.closest('[data-format]'); if (!b) return; format = b.dataset.format; paintStage(); refreshWords(); }, o);
  $('cs-bg').addEventListener('click', (e) => { const b = e.target.closest('[data-bg]'); if (!b) return; bg = b.dataset.bg; paintStage(); }, o);
  const playBtn = $('cs-play');
  const setPlaying = (p) => { playing = p; playBtn.innerHTML = `<i class="${p ? 'ri-pause-fill' : 'ri-play-fill'}"></i>`; playBtn.setAttribute('aria-label', p ? 'Pause' : 'Play'); };
  playBtn.addEventListener('click', () => setPlaying(!playing), o);
  $('cs-text').addEventListener('input', () => { refreshWords(); clock = 0; }, o);
  $('cs-real').addEventListener('click', async () => {
    setPlaying(false);
    const btn = $('cs-real'); btn.disabled = true; btn.querySelector('span').textContent = 'Drawing…';
    try {
      const r = await fetch(`/admin/clipper/captions/sample${q}`, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ design: plain(D), format, text: $('cs-text').value, at: clock, bg, photo: B.photo }) });
      if (!r.ok) throw new Error();
      const img = $('cs-real-img');
      if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
      img.src = URL.createObjectURL(await r.blob());
      $('cs-real-box').hidden = false;
      $('cs-real-box').className = `cs-real ${format}`;
    } catch (e) { toast('The real video sample could not be made right now.'); }
    btn.disabled = false; btn.querySelector('span').textContent = 'Check the real video';
  }, o);
  $('cs-real-close').addEventListener('click', () => { $('cs-real-box').hidden = true; }, o);

  // ── start ──
  $('cs-text').value = B.sample;
  paintStage();
  open(designs[0] || B.presets.pop, !!designs[0]);
  refreshWords();
  requestAnimationFrame(frame);
})();
