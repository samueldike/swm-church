/* Video Clipper pages (admin/clipper-form.html, admin/clipper-show.html).
   Inline scripts re-run on Turbo visits, so every listener hangs off one AbortController per page. */
(() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // "Settings → AI drafting" / "Video Clipper → Channels" in a message become links (same map as the server's
  // `directions` filter in app/core/template.py). Applied to already-escaped text.
  const TABS = { 'Website content': 'website', 'Admin Appearance': 'appearance', 'Giving gateway': 'giving', 'Church accounts': 'accounts',
    'Admin security': 'security', 'Email delivery': 'email', 'AI drafting': 'ai', 'Social publishing': 'social',
    'Audio distribution': 'distribution', 'Lists': 'lists', 'Legal pages': 'legal', 'General': 'general' };
  const PAGES = { 'YouTube access': '/admin/clipper/settings#youtube-access', 'Channels': '/admin/clipper/channels', 'Watermark': '/admin/clipper/settings#watermark' };
  const directions = (html) => html
    .replace(new RegExp(`((?:Admin\\s*→\\s*)?Settings\\s*→\\s*)(${Object.keys(TABS).join('|')})`, 'g'),
      (m, a, k) => `<a class="underline font-semibold" href="/admin/settings#${TABS[k]}">${m}</a>`)
    .replace(new RegExp(`Video Clipper\\s*→\\s*(${Object.keys(PAGES).join('|')})`, 'g'),
      (m, k) => `<a class="underline font-semibold" href="${PAGES[k]}">${m}</a>`);
  const fresh = () => { window.__swmClipAbort?.abort(); window.__swmClipTimer && clearTimeout(window.__swmClipTimer); const c = new AbortController(); window.__swmClipAbort = c; return c; };

  async function post(url, data, csrf) {
    const body = data instanceof FormData ? data : new URLSearchParams(data || {});
    const res = await fetch(url, { method: 'POST', body, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } });
    let json = {};
    try { json = await res.json(); } catch (e) { json = { ok: false, error: 'The server answered unexpectedly. Please try again.' }; }
    if (!res.ok && json.ok !== false) json.ok = false;
    return json;
  }

  /* ── New video ─────────────────────────────────────────────── */
  function form() {
    const ctl = fresh(); const opt = { signal: ctl.signal };
    const f = document.getElementById('vc-form');
    if (!f) return;
    const csrf = f.querySelector('[name=csrf_token]').value;
    const msg = document.getElementById('vc-msg');
    const go = document.getElementById('vc-go');
    const fileInput = document.getElementById('vc-file');
    const drop = document.getElementById('vc-drop');
    const maxBytes = Number(f.dataset.maxMb || 4000) * 1024 * 1024;
    let file = null;
    const say = (text, bad) => { msg.textContent = text || ''; msg.classList.toggle('bad', !!bad); };

    f.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
      const kind = b.dataset.tab;
      f.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
      f.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== kind; });
      f.source_kind.value = kind;
      say('');
    }, opt));

    // Captions are opt-in: the style choice is only sent (enabled) while the switch is on.
    const capOn = document.getElementById('vc-cap-on');
    const capStyles = document.getElementById('vc-cap-styles');
    if (capOn && capStyles) {
      const sync = () => {
        capStyles.hidden = !capOn.checked;
        capStyles.querySelectorAll('input').forEach((i) => { i.disabled = !capOn.checked; });
      };
      capOn.addEventListener('change', sync, opt);
      sync();
    }

    const pick = (fl) => {
      if (!fl) return;
      if (fl.size > maxBytes) { say(`That file is larger than ${Math.round(maxBytes / 1048576)} MB.`, true); return; }
      file = fl;
      document.getElementById('vc-file-name').textContent = `${fl.name} (${(fl.size / 1048576).toFixed(0)} MB)`;
      if (!f.title.value) f.title.value = fl.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').slice(0, 200);
      say('');
      f.dispatchEvent(new CustomEvent('vc-file', { detail: fl }));
    };
    drop.addEventListener('click', () => fileInput.click(), opt);
    drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } }, opt);
    fileInput.addEventListener('change', () => pick(fileInput.files[0]), opt);
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }, opt));
    ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }, opt));
    drop.addEventListener('drop', (e) => pick(e.dataTransfer.files[0]), opt);

    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const kind = f.source_kind.value;
      if (kind === 'upload' && !file) { say('Choose the video file first.', true); return; }
      if (kind === 'url' && !/^https?:\/\//i.test(f.url.value.trim())) { say('Paste the full link to the video (it starts with https://).', true); return; }
      go.disabled = true;
      say('Starting…');
      const data = new FormData(f);
      if (kind === 'upload') data.set('file_name', file.name);
      const start = await post(f.dataset.create, data, csrf);
      if (!start.ok) { say(start.error, true); go.disabled = false; return; }
      if (kind === 'url') { window.location.href = start.url; return; }

      // Upload: the raw file as the request body, so the server can stream it straight to disk.
      const bar = document.getElementById('vc-upbar'); const fill = document.getElementById('vc-upfill');
      bar.classList.add('on');
      const xhr = new XMLHttpRequest();
      xhr.open('POST', start.upload);
      xhr.setRequestHeader('X-CSRF-Token', csrf);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      const t0 = Date.now();
      xhr.upload.onprogress = (ev) => {
        if (!ev.lengthComputable) return;
        const pct = ev.loaded / ev.total;
        fill.style.width = `${(pct * 100).toFixed(1)}%`;
        const secs = (Date.now() - t0) / 1000;
        const left = pct > 0.02 ? Math.max(0, secs / pct - secs) : 0;
        say(`Uploading… ${(pct * 100).toFixed(0)}%${left > 5 ? `, about ${left > 90 ? Math.round(left / 60) + ' min' : Math.round(left) + ' s'} left` : ''}. Keep this page open.`);
      };
      xhr.onload = () => {
        let j = {}; try { j = JSON.parse(xhr.responseText); } catch (err) { j = {}; }
        if (xhr.status === 200 && j.ok) { say('Uploaded. Opening the project…'); window.location.href = j.url; }
        else { say(j.error || 'The upload failed. Please try again.', true); go.disabled = false; bar.classList.remove('on'); }
      };
      xhr.onerror = () => { say('The upload was interrupted. Check the connection and try again.', true); go.disabled = false; bar.classList.remove('on'); };
      ctl.signal.addEventListener('abort', () => { if (xhr.readyState !== 4) xhr.abort(); });
      xhr.send(file);
    }, opt);
  }

  /* ── Project page ──────────────────────────────────────────── */
  function show() {
    const ctl = fresh(); const opt = { signal: ctl.signal };
    const root = document.getElementById('vc-show');
    if (!root) return;
    const csrf = root.dataset.csrf;
    const base = root.dataset.base;
    const opts = JSON.parse(document.getElementById('vc-opts').textContent);
    const capStyles = JSON.parse(document.getElementById('vc-capstyles')?.textContent || '{}');
    const capWords = {};      // short id -> {words, width, height}
    const capLayers = {};     // short id -> overlay api
    const frameLib = JSON.parse(document.getElementById('vc-frames')?.textContent || '{"items":[],"default":{}}');
    const frameSelect = (s) => {
      const mine = frameLib.items.filter((f) => f.format === (s.format || 'vertical'));
      const def = mine.find((f) => f.id === frameLib.default[s.format || 'vertical']);
      if (!mine.length) return '';
      const opt = (v, label) => `<option value="${esc(v)}" ${String(s.frame || 'default') === v ? 'selected' : ''}>${esc(label)}</option>`;
      return `<label class="vc-framesel">Frame <select data-frame aria-label="Frame">${opt('default', `Default${def ? ` (${def.name})` : ' (none)'}`)}${opt('none', 'No frame')}${mine.map((f) => opt(f.id, f.name)).join('')}</select></label>`;
    };
    const musicSelect = (s) => {
      const list = frameLib.tracks || [];
      if (!list.length) return '';
      const d = frameLib.music_default;
      const dname = d === 'random' ? 'Random' : (list.find((t) => t.id === d) || {}).name || 'none';
      const opt = (v, label) => `<option value="${esc(v)}" ${String(s.music || 'default') === v ? 'selected' : ''}>${esc(label)}</option>`;
      return `<label class="vc-framesel">Music <select data-music aria-label="Background music">${opt('default', `Default (${dname})`)}${opt('none', 'No music')}${opt('random', 'Random')}${list.map((t) => opt(t.id, t.name)).join('')}</select></label>`;
    };
    const markSelect = (s) => {
      const lib = frameLib.marks || { items: [] };
      const def = lib.items.find((m) => m.id === lib.default);
      const opt = (v, label) => `<option value="${esc(v)}" ${String(s.watermark || 'default') === v ? 'selected' : ''}>${esc(label)}</option>`;
      return `<label class="vc-framesel">Watermark <select data-watermark aria-label="Watermark">${opt('default', `Default (${def ? def.name : 'none'})`)}${opt('none', 'No watermark')}${lib.items.map((m) => opt(m.id, m.name)).join('')}</select></label>`;
    };
    const markHtml = (m) => (m ? `<img class="vc-wm-ovl ${esc(m.position)} ${m.tone === 'white' ? 'white' : ''}" src="${esc(m.url)}" alt="" style="width:${Number(m.size)}%;opacity:${Number(m.opacity) / 100}">` : '');
    const outroSelect = (s) => {
      const list = frameLib.outros || [];
      if (!list.length) return '';
      const def = list.find((o) => o.id === frameLib.outro_default);
      const opt = (v, label) => `<option value="${esc(v)}" ${String(s.outro || 'default') === v ? 'selected' : ''}>${esc(label)}</option>`;
      return `<label class="vc-framesel">Outro <select data-outro aria-label="Outro">${opt('default', `Default${def ? ` (${def.name}, ${def.seconds} s)` : ' (none)'}`)}${opt('none', 'No outro')}${list.map((o) => opt(o.id, `${o.name} (${o.seconds} s)`)).join('')}</select></label>`;
    };
    let data = JSON.parse(document.getElementById('vc-data').textContent);
    const grid = document.getElementById('vc-shorts');
    const sortBar = document.getElementById('vc-sortbar'), sortSel = document.getElementById('vc-sort');
    const sortNote = document.getElementById('vc-sort-note');
    let sortBy = 'best', ranks = {}, rankedCount = 0;
    try { sortBy = localStorage.getItem('vc-sort') || 'best'; } catch (e) { /* private window */ }
    if (sortSel) {
      sortSel.value = sortBy;
      sortSel.addEventListener('change', () => {
        sortBy = sortSel.value;
        try { localStorage.setItem('vc-sort', sortBy); } catch (e) { /* private window */ }
        delete grid.dataset.key; drawShorts(true);
      });
    }
    const toastEl = document.getElementById('vc-toast');
    let toastTimer;
    const toast = (text) => { toastEl.textContent = text; toastEl.classList.add('on'); clearTimeout(toastTimer); toastTimer = setTimeout(() => toastEl.classList.remove('on'), 2600); };

    const statusPill = (d) => {
      if (d.status === 'ready') return '<span class="vc-pill ok"><i class="ri-check-line"></i>Ready</span>';
      if (d.status === 'failed') return '<span class="vc-pill bad"><i class="ri-error-warning-line"></i>Needs attention</span>';
      if (d.status === 'stalled') return '<span class="vc-pill bad"><i class="ri-pause-line"></i>Stopped</span>';
      return '<span class="vc-pill run"><i class="ri-loader-4-line vc-spin"></i>Working</span>';
    };
    const select = (name, table, value) => `<select name="${name}">${Object.entries(table).map(([k, v]) =>
      `<option value="${esc(k)}" ${String(k) === String(value) ? 'selected' : ''}>${esc(typeof v === 'string' ? v : v.label)}</option>`).join('')}</select>`;
    const secs = (n) => `${n > 0 ? '+' : ''}${Number(n).toFixed(1)}s`;

    const POST_LABEL = { queued: 'Sending', uploading: 'Sending', scheduled: 'Scheduled', published: 'Published', partial: 'Partly published', failed: 'Not published' };
    const postsHtml = (s) => (s.posts && s.posts.length ? `<div class="vc-posts">${s.posts.map((x) => {
      const tone = x.status === 'published' ? 'ok' : (x.status === 'failed' || x.status === 'partial' ? 'bad' : 'run');
      const when = x.when ? `<span class="vc-muted">${esc(new Date(x.when).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }))}</span>` : '';
      const err = x.error ? `<span class="vc-muted" title="${esc(x.error)}">${directions(esc(x.error.slice(0, 90)))}</span>` : '';
      return `<div class="row"><span class="vc-pill ${tone}">${esc(POST_LABEL[x.status] || x.status)}</span>${x.targets.map((t) => `<i class="${esc(t.icon)}" title="${esc(t.label)}${t.name ? ': ' + esc(t.name) : ''}"></i>`).join('')}${when}${err}</div>`;
    }).join('')}</div>` : '');

    function card(s) {
      const fmt = s.format || 'vertical';
      let media;
      if (s.video) {
        media = `<video src="${esc(s.video)}" poster="${esc(s.thumb)}" controls preload="none" playsinline></video>${s.frame_url ? `<img class="vc-frame-ovl" src="${esc(s.frame_url)}" alt="">` : ''}${markHtml(s.mark)}`;
      } else if (s.status === 'failed') {
        media = `<div class="vc-wait"><i class="ri-error-warning-line"></i><span>${directions(esc(s.error || 'This short could not be cut.'))}</span></div>`;
      } else {
        media = `<div class="vc-wait"><i class="ri-loader-4-line vc-spin"></i><span>${s.status === 'rendering' ? 'Cutting this short…' : 'Waiting to be cut…'}</span></div>`;
      }
      const r = ranks[s.id];
      const tier = s.score >= 85 ? 'Top pick' : s.score >= 70 ? 'Strong' : 'Good';
      const badges = `<div class="vc-badges">${s.score != null
        ? `<span class="vc-pill gold vc-rank ${r === 1 ? 'first' : ''}" title="${esc(`Ranked ${r} of ${rankedCount} by how valuable and shareable it is. Score ${s.score} of 100.${s.why ? ' ' + s.why : ''}`)}">#${r} · ${esc(tier)} ${s.score}</span>`
        : (s.origin === 'manual' ? '<span class="vc-pill gold">Your clip</span>' : '<span></span>')}${s.length ? `<span class="vc-pill gold">${Math.round(s.length)} s</span>` : ''}</div>`;
      return `<article class="vc-panel vc-short ${s.starred ? 'starred' : ''}" data-sid="${s.id}">
        <div class="vc-player ${esc(fmt)}">${media}${badges}</div>
        <div class="vc-short-body">
          <div class="vc-title" contenteditable="true" spellcheck="true" role="textbox" aria-label="Title" data-field="title">${esc(s.title)}</div>
          ${s.why ? `<div class="vc-why" title="Why it ranks here"><i class="ri-fire-line"></i><span>${esc(s.why)}</span></div>` : ''}
          ${s.value ? `<div class="vc-value"><i class="ri-lightbulb-flash-line"></i><span>${esc(s.value)}</span></div>` : ''}
          ${s.effects && s.effects.length ? `<div class="vc-fx" title="Effects the editor chose for this short"><i class="ri-magic-line"></i><span>${esc(s.effects.join(' · '))}</span></div>` : ''}
          <div class="vc-meta"><span>${esc(s.from)}–${esc(s.to)}</span>${s.pieces > 1 ? `<span>${s.pieces} parts joined</span>` : ''}${s.cold_open ? '<span>opens with a teaser</span>' : ''}</div>
          <div class="vc-capbar">
            <label class="vc-switch" title="${s.words ? 'Show captions on this short (preview, download and post)' : 'Re-cut this short once to add captions'}"><input type="checkbox" data-cap-on ${s.captions_on ? 'checked' : ''} ${s.words ? '' : 'disabled'}><span aria-hidden="true"></span><b>Captions</b></label>
            <div class="vc-capchips" role="group" aria-label="Caption design (point at one to see it on the video)" aria-disabled="${!s.captions_on}">${Object.entries(capStyles).map(([k, v]) =>
              window.SwmCaptions ? window.SwmCaptions.chip(k, v, k === s.caption_style) : `<button type="button" data-cap="${esc(k)}" aria-pressed="${k === s.caption_style}" title="${esc(v.hint)}">${esc(v.label)}</button>`).join('')}</div>
          </div>
          <div class="vc-actions">
            ${s.video ? `<a class="btn btn-primary btn-sm" href="${esc(base)}/shorts/${s.id}/publish"><i class="ri-send-plane-line"></i>Publish</a>` : ''}
            ${s.video && !data.source_gone ? `<a class="btn btn-secondary btn-sm" href="${esc(base)}/shorts/${s.id}/edit"><i class="ri-film-line"></i>Edit</a>` : ''}
            ${s.download ? `<a class="btn btn-secondary btn-sm" href="${esc(s.download)}" data-turbo="false" download data-dl><i class="ri-download-2-line"></i>Download</a>` : ''}
            <details class="vc-more"><summary class="btn btn-ghost btn-sm" aria-label="More actions"><i class="ri-more-2-fill"></i>More</summary>
              <div class="vc-more-menu" role="menu">
                <button type="button" role="menuitem" data-act="copy"><i class="ri-file-copy-line"></i>Copy post caption</button>
                ${s.srt ? `<a role="menuitem" href="${esc(s.srt)}" data-turbo="false"><i class="ri-closed-captioning-line"></i>Caption file (.srt)</a>` : ''}
                <button type="button" role="menuitem" data-act="star" aria-pressed="${s.starred}"><i class="${s.starred ? 'ri-star-fill' : 'ri-star-line'}"></i>${s.starred ? 'Remove from favourites' : 'Add to favourites'}</button>
                <button type="button" role="menuitem" data-act="delete" class="bad"><i class="ri-delete-bin-6-line"></i>Delete this short</button>
              </div>
            </details>
          </div>
          ${postsHtml(s)}
          <details><summary><i class="ri-chat-quote-line"></i>Post caption</summary>
            <div class="vc-caption"><textarea data-field="caption" data-richtext="off" rows="5" aria-label="Post caption" placeholder="Post caption and hashtags">${esc(s.caption)}</textarea></div></details>
          ${s.video ? `<details><summary><i class="ri-palette-line"></i>Watermark, frame, music and outro</summary>
            <div class="vc-look">${markSelect(s)}${frameSelect(s)}${musicSelect(s)}${outroSelect(s)}</div></details>` : ''}
          <details><summary><i class="ri-text"></i>What is said</summary><div class="vc-transcript">${esc(s.transcript)}</div></details>
          ${data.source_gone ? '' : `<details><summary><i class="ri-equalizer-line"></i>Adjust and re-cut</summary>
            <form class="vc-recut" data-recut>
              <label>Shape ${select('format', opts.formats, s.format)}</label>
              <label>Framing ${select('framing', opts.framings, s.framing)}</label>
              <label>Quality ${select('quality', opts.qualities, s.quality)}</label>
              <label>Noise reduction ${select('denoise', opts.denoise, s.denoise || 'natural')}</label>
              <div class="wide vc-fx-toggles">
                <label><input type="hidden" name="flashes" value="0"><input type="checkbox" name="flashes" value="1" ${s.flashes ? 'checked' : ''}> Light transitions + whoosh (on new points)</label>
                <label><input type="hidden" name="zooms" value="0"><input type="checkbox" name="zooms" value="1" ${s.zooms ? 'checked' : ''}> Smart zoom (punch-ins, push-ins)</label>
                <label><input type="hidden" name="touches" value="0"><input type="checkbox" name="touches" value="1" ${s.touches ? 'checked' : ''}> Finishing touches (hook pop, scripture glow, sharpen, fade out)</label>
              </div>
              <label>Start <span class="vc-nudge"><button type="button" data-n="nudge_start" data-d="-1" aria-label="Start one second earlier">−</button><output data-o="nudge_start">${secs(s.nudge_start)}</output><button type="button" data-n="nudge_start" data-d="1" aria-label="Start one second later">+</button></span></label>
              <label>End <span class="vc-nudge"><button type="button" data-n="nudge_end" data-d="-1" aria-label="End one second earlier">−</button><output data-o="nudge_end">${secs(s.nudge_end)}</output><button type="button" data-n="nudge_end" data-d="1" aria-label="End one second later">+</button></span></label>
              <input type="hidden" name="nudge_start" value="${s.nudge_start}"><input type="hidden" name="nudge_end" value="${s.nudge_end}">
              <div class="wide"><button type="submit" class="btn btn-secondary btn-sm"><i class="ri-refresh-line"></i>Re-cut this short</button>
              <span class="vc-muted text-[12px]">Start and end move by whole words.</span></div>
            </form></details>`}
        </div>
      </article>`;
    }

    // Keep what the person is editing or playing when a poll redraws the grid.
    function drawShorts(force) {
      const busyEl = document.activeElement && grid.contains(document.activeElement);
      const playing = [...grid.querySelectorAll('video')].some((v) => !v.paused);
      const openDetails = new Set([...grid.querySelectorAll('details[open]')].map((d) => `${d.closest('[data-sid]').dataset.sid}:${[...d.parentNode.querySelectorAll(':scope > details')].indexOf(d)}`));
      const keyNow = JSON.stringify([sortBy, data.shorts.map((s) => [s.id, s.status, s.video, s.starred, s.score, s.watermark, (s.posts || []).map((x) => x.status).join()])]);
      if (grid.dataset.key === keyNow && grid.children.length) return;
      if (!force && (busyEl || playing) && grid.children.length) return;   // redraw later
      grid.dataset.key = keyNow;
      // Rank = place among the scored shorts of this video, best first (ties: the one made first).
      const best = (a, b) => ((b.score ?? -1) - (a.score ?? -1)) || (a.position - b.position) || (a.id - b.id);
      const scored = data.shorts.filter((s) => s.score != null).sort(best);
      ranks = {}; scored.forEach((s, i) => { ranks[s.id] = i + 1; }); rankedCount = scored.length;
      const order = {
        best,
        video: (a, b) => (a.at - b.at) || (a.id - b.id),
        starred: (a, b) => (b.starred - a.starred) || best(a, b),
      }[sortBy] || best;
      const sorted = [...data.shorts].sort(order);
      if (sortBar) {
        sortBar.hidden = data.shorts.length < 2;
        sortNote.textContent = { best: 'Best first: the strongest short is top left.', video: 'In the order they are said in the video.', starred: 'Your favourites first, then the best.' }[sortBy];
      }
      grid.innerHTML = sorted.length ? sorted.map(card).join('') :
        (data.busy ? '' : '<div class="vc-panel vc-empty" style="grid-column:1/-1"><i class="ri ri-film-line"></i><h2>No shorts yet</h2><p>Use “Find more shorts” or clip a stretch by its times.</p></div>');
      grid.querySelectorAll('details').forEach((d) => {
        const key = `${d.closest('[data-sid]').dataset.sid}:${[...d.parentNode.querySelectorAll(':scope > details')].indexOf(d)}`;
        if (openDetails.has(key)) d.open = true;
      });
      Object.keys(capLayers).forEach((k) => { capLayers[k].destroy(); delete capLayers[k]; });
      data.shorts.forEach((s) => { if (s.captions_on) captionsOn(s); });
    }

    // Live captions: the words are fetched once per short, the design is drawn over the video.
    async function captionsOn(s) {
      const v = grid.querySelector(`[data-sid="${s.id}"] video`);
      if (!v || !s.words || !window.SwmCaptions) return;
      if (!capWords[s.id]) {
        try { const r = await fetch(s.words, { credentials: 'same-origin', signal: ctl.signal }); if (!r.ok) return; capWords[s.id] = await r.json(); }
        catch (e) { return; }
      }
      if (capLayers[s.id]) { capLayers[s.id].set(s.captions_on ? s.caption_style : null); return; }
      if (v.isConnected) capLayers[s.id] = window.SwmCaptions.attach(v, capWords[s.id], s.captions_on ? s.caption_style : null, capStyles);
    }
    // Point at (or tab to) a design: it shows on that short's video at once; moving away puts the saved one back.
    async function peekCaption(chipEl, on) {
      const sid = chipEl.closest('[data-sid]')?.dataset.sid;
      const s = data.shorts.find((x) => String(x.id) === sid);
      if (!s || !s.words) return;
      if (on && !capLayers[s.id]) await captionsOn(s);
      if (capLayers[s.id]) capLayers[s.id].preview(on ? chipEl.dataset.cap : null);
    }
    grid.addEventListener('pointerover', (e) => { const c = e.target.closest('[data-cap]'); if (c && !c.contains(e.relatedTarget)) peekCaption(c, true); }, opt);
    grid.addEventListener('pointerout', (e) => { const c = e.target.closest('[data-cap]'); if (c && !c.contains(e.relatedTarget)) peekCaption(c, false); }, opt);
    grid.addEventListener('focusin', (e) => { const c = e.target.closest('[data-cap]'); if (c) peekCaption(c, true); }, opt);
    grid.addEventListener('focusout', (e) => { const c = e.target.closest('[data-cap]'); if (c) peekCaption(c, false); }, opt);
    function syncCaptionControls(s) {
      const cardEl = grid.querySelector(`[data-sid="${s.id}"]`);
      if (!cardEl) return;
      cardEl.querySelector('[data-cap-on]').checked = !!s.captions_on;
      cardEl.querySelector('.vc-capchips').setAttribute('aria-disabled', String(!s.captions_on));
      cardEl.querySelectorAll('[data-cap]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cap === s.caption_style)));
      const dl = cardEl.querySelector('[data-dl]');
      if (dl && s.download) dl.href = s.download;
      captionsOn(s);
    }

    function drawHead() {
      document.getElementById('vc-status').innerHTML = statusPill(data);
      const prog = document.getElementById('vc-progress');
      const problem = document.getElementById('vc-problem');
      const tools = document.getElementById('vc-tools');
      prog.hidden = !data.busy;
      if (data.busy) {
        const order = { queued: 0, fetching: 1, transcribing: 2, composing: 3, rendering: 4 };
        const step = order[data.status] ?? 0;
        const overall = Math.min(0.99, (step + (data.progress || 0)) / 5);
        document.getElementById('vc-stage').textContent = `${data.stage || 'Working'}…`;
        document.getElementById('vc-pct').textContent = `Step ${step + 1} of 5`;
        document.getElementById('vc-fill').style.width = `${(overall * 100).toFixed(1)}%`;
      }
      if (data.status === 'failed' || data.status === 'stalled') {
        problem.hidden = false;
        problem.innerHTML = `<div class="vc-alert bad"><i class="ri-error-warning-line"></i><span>${directions(esc(data.status === 'stalled'
          ? 'The work stopped (the server may have restarted). Retry carries on from where it stopped.'
          : (data.error || 'Something went wrong.')))}</span><span class="flex-grow"></span>
          ${data.speaker_refused ? '<button type="button" class="btn btn-secondary btn-sm" data-act="anyway"><i class="ri-user-voice-line"></i>Clip anyway</button>' : '<button type="button" class="btn btn-secondary btn-sm" data-act="retry"><i class="ri-refresh-line"></i>Retry</button>'}</div>`;
      } else if (data.source_gone) {
        problem.hidden = false;
        problem.innerHTML = '<div class="vc-alert info"><i class="ri-information-line"></i><span>The long source video was removed to save space. The shorts are kept, but they can no longer be re-cut.</span></div>';
      } else {
        problem.hidden = true;
      }
      tools.hidden = data.busy;
      tools.querySelectorAll('.vc-add').forEach((x) => { x.hidden = data.source_gone; });
    }

    function draw() { drawHead(); drawShorts(); }

    async function poll() {
      try {
        const res = await fetch(`${base}/status.json`, { credentials: 'same-origin', signal: ctl.signal });
        if (res.ok) { data = await res.json(); draw(); }
      } catch (e) { if (ctl.signal.aborted) return; }
      if (!ctl.signal.aborted) window.__swmClipTimer = setTimeout(poll, data.busy ? 3000 : 20000);
    }

    const rerankBtn = document.getElementById('vc-rerank');
    if (rerankBtn) rerankBtn.addEventListener('click', async () => {
      rerankBtn.disabled = true;
      const label = rerankBtn.querySelector('span'); label.textContent = 'Ranking…';
      const r = await post(`${base}/rank`, {}, csrf);
      rerankBtn.disabled = false; label.textContent = 'Rank again';
      if (!r.ok) { toast(r.error || 'Could not rank the shorts.'); return; }
      const res = await fetch(`${base}/status.json`, { credentials: 'same-origin' });
      if (res.ok) { data = await res.json(); sortBy = 'best'; if (sortSel) sortSel.value = 'best'; delete grid.dataset.key; drawHead(); drawShorts(true); }
      toast(`Ranked ${r.ranked} shorts, best first`);
    }, { signal: ctl.signal });

    async function save(sid, fields) {
      const r = await post(`${base}/shorts/${sid}/update`, fields, csrf);
      if (!r.ok) { toast(r.error || 'Could not save.'); return null; }
      const at = data.shorts.findIndex((s) => s.id === r.short.id);
      if (at >= 0) data.shorts[at] = r.short;
      return r;
    }

    grid.addEventListener('focusout', async (e) => {
      const el = e.target.closest('[data-field]');
      if (!el) return;
      const sid = el.closest('[data-sid]').dataset.sid;
      const s = data.shorts.find((x) => String(x.id) === sid);
      const value = el.dataset.field === 'title' ? el.textContent.trim() : el.value;
      if (!s || value === (s[el.dataset.field] || '')) return;
      if (await save(sid, { [el.dataset.field]: value })) toast('Saved');
    }, opt);
    document.addEventListener('click', (e) => {
      document.querySelectorAll('.vc-more[open]').forEach((m) => { if (!m.contains(e.target)) m.open = false; });
    }, opt);
    grid.addEventListener('change', async (e) => {
      const msel = e.target.closest('[data-music]');
      if (msel) {
        const r = await save(msel.closest('[data-sid]').dataset.sid, { music: msel.value });
        if (r) toast(r.short.music_name ? `Music: ${r.short.music_name}, softly under the voice in the download and the post` : 'No music on this short');
        return;
      }
      const wsel = e.target.closest('[data-watermark]');
      if (wsel) {
        const r = await save(wsel.closest('[data-sid]').dataset.sid, { watermark: wsel.value });
        if (r) { grid.dataset.key = ''; drawShorts(true); toast(r.short.mark ? `Watermark: ${r.short.mark.name} on the download and the post` : 'No watermark on this short'); }
        return;
      }
      const osel = e.target.closest('[data-outro]');
      if (osel) {
        const r = await save(osel.closest('[data-sid]').dataset.sid, { outro: osel.value });
        if (r) toast(r.short.outro_name ? `Outro: ${r.short.outro_name} ends the download and the post` : 'No outro on this short');
        return;
      }
      const fsel = e.target.closest('[data-frame]');
      if (fsel) {
        const cardEl = fsel.closest('[data-sid]');
        const r = await save(cardEl.dataset.sid, { frame: fsel.value });
        if (!r) return;
        const player = cardEl.querySelector('.vc-player');
        let ovl = player.querySelector('.vc-frame-ovl');
        if (r.short.frame_url) {
          if (!ovl) { ovl = document.createElement('img'); ovl.className = 'vc-frame-ovl'; ovl.alt = ''; player.querySelector('video').after(ovl); }
          ovl.src = r.short.frame_url;
        } else if (ovl) ovl.remove();
        toast(r.short.frame_url ? 'Frame on: the download and the post carry it' : 'No frame on this short');
        return;
      }
      const box = e.target.closest('[data-cap-on]');
      if (!box) return;
      const sid = box.closest('[data-sid]').dataset.sid;
      const r = await save(sid, { captions_on: box.checked ? '1' : '0' });
      if (r) { syncCaptionControls(r.short); toast(box.checked ? 'Captions on: the download and the post carry them' : 'Captions off'); }
      else box.checked = !box.checked;
    }, opt);
    grid.addEventListener('keydown', (e) => {
      if (e.target.matches('.vc-title') && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
    }, opt);

    grid.addEventListener('click', async (e) => {
      const nudge = e.target.closest('[data-n]');
      if (nudge) {
        const form = nudge.closest('form');
        const input = form.querySelector(`[name=${nudge.dataset.n}]`);
        const v = Math.max(-30, Math.min(30, Number(input.value || 0) + Number(nudge.dataset.d)));
        input.value = v;
        form.querySelector(`[data-o=${nudge.dataset.n}]`).textContent = secs(v);
        return;
      }
      const menu = e.target.closest('.vc-more');
      if (menu && e.target.closest('[data-act]')) menu.open = false;
      const chip = e.target.closest('[data-cap]');
      if (chip) {
        const sid = chip.closest('[data-sid]').dataset.sid;
        const r = await save(sid, { caption_style: chip.dataset.cap, captions_on: '1' });
        if (r) { syncCaptionControls(r.short); toast(`Captions: ${capStyles[chip.dataset.cap]?.label || ''}`); }
        return;
      }
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const sid = btn.closest('[data-sid]')?.dataset.sid;
      const s = data.shorts.find((x) => String(x.id) === sid);
      if (btn.dataset.act === 'copy') {
        const text = btn.closest('[data-sid]').querySelector('textarea').value;
        try { await navigator.clipboard.writeText(text); toast('Caption copied'); }
        catch (err) { const ta = btn.closest('[data-sid]').querySelector('textarea'); ta.select(); document.execCommand('copy'); toast('Caption copied'); }
      } else if (btn.dataset.act === 'star') {
        if (await save(sid, { starred: s.starred ? '0' : '1' })) { grid.dataset.key = ''; drawShorts(); }
      } else if (btn.dataset.act === 'delete') {
        if (!confirm('Delete this short?')) return;
        const r = await post(`${base}/shorts/${sid}/delete`, {}, csrf);
        if (!r.ok) { toast(r.error || 'Could not delete.'); return; }
        data.shorts = data.shorts.filter((x) => String(x.id) !== sid);
        grid.dataset.key = ''; drawShorts(); toast('Deleted');
      }
    }, opt);

    grid.addEventListener('submit', async (e) => {
      const form = e.target.closest('[data-recut]');
      if (!form) return;
      e.preventDefault();
      const sid = form.closest('[data-sid]').dataset.sid;
      const r = await save(sid, new FormData(form));
      if (!r) return;
      if (r.rerender) { toast('Re-cutting this short…'); data.busy = true; data.status = 'rendering'; document.activeElement?.blur(); grid.dataset.key = ''; draw(); clearTimeout(window.__swmClipTimer); window.__swmClipTimer = setTimeout(poll, 1500); }
      else toast('Nothing changed');
    }, opt);

    document.getElementById('vc-problem').addEventListener('click', async (e) => {
      const anyway = e.target.closest('[data-act=anyway]');
      if (!anyway && !e.target.closest('[data-act=retry]')) return;
      const r = await post(`${base}/${anyway ? 'clip-anyway' : 'retry'}`, {}, csrf);
      if (!r.ok) { toast(r.error || 'Could not retry.'); return; }
      data.busy = true; data.status = 'queued'; data.stage = 'Starting again'; draw();
      clearTimeout(window.__swmClipTimer); window.__swmClipTimer = setTimeout(poll, 1500);
    }, opt);

    document.getElementById('vc-more').addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await post(`${base}/more`, new FormData(e.target), csrf);
      if (!r.ok) { toast(r.error || 'Could not start.'); return; }
      data.busy = true; data.status = 'composing'; data.stage = 'Looking for more moments'; draw();
      clearTimeout(window.__swmClipTimer); window.__swmClipTimer = setTimeout(poll, 1500);
    }, opt);

    document.getElementById('vc-manual').addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await post(`${base}/manual`, new FormData(e.target), csrf);
      if (!r.ok) { toast(r.error || 'Could not make that clip.'); return; }
      e.target.reset();
      data.busy = true; data.status = 'rendering'; data.stage = 'Cutting the shorts'; draw();
      clearTimeout(window.__swmClipTimer); window.__swmClipTimer = setTimeout(poll, 1500);
    }, opt);

    root.querySelectorAll('form[data-confirm]').forEach((f) => f.addEventListener('submit', (e) => {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    }, opt));

    draw();
    window.__swmClipTimer = setTimeout(poll, data.busy ? 2000 : 20000);
  }

  window.SWMClipper = { form, show };
})();
