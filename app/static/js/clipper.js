/* Video Clipper pages (admin/clipper-form.html, admin/clipper-show.html).
   Inline scripts re-run on Turbo visits, so every listener hangs off one AbortController per page. */
(() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
    let data = JSON.parse(document.getElementById('vc-data').textContent);
    const grid = document.getElementById('vc-shorts');
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

    function card(s) {
      const fmt = s.format || 'vertical';
      let media;
      if (s.video) {
        media = `<video src="${esc(s.video)}" poster="${esc(s.thumb)}" controls preload="none" playsinline></video>`;
      } else if (s.status === 'failed') {
        media = `<div class="vc-wait"><i class="ri-error-warning-line"></i><span>${esc(s.error || 'This short could not be cut.')}</span></div>`;
      } else {
        media = `<div class="vc-wait"><i class="ri-loader-4-line vc-spin"></i><span>${s.status === 'rendering' ? 'Cutting this short…' : 'Waiting to be cut…'}</span></div>`;
      }
      const badges = `<div class="vc-badges">${s.score != null ? `<span class="vc-pill gold" title="How strongly the editor rates it">${s.score}</span>` : (s.origin === 'manual' ? '<span class="vc-pill gold">Your clip</span>' : '<span></span>')}${s.length ? `<span class="vc-pill gold">${Math.round(s.length)} s</span>` : ''}</div>`;
      return `<article class="vc-panel vc-short ${s.starred ? 'starred' : ''}" data-sid="${s.id}">
        <div class="vc-player ${esc(fmt)}">${media}${badges}</div>
        <div class="vc-short-body">
          <div class="vc-title" contenteditable="true" spellcheck="true" role="textbox" aria-label="Title" data-field="title">${esc(s.title)}</div>
          ${s.value ? `<div class="vc-value"><i class="ri-lightbulb-flash-line"></i><span>${esc(s.value)}</span></div>` : ''}
          <div class="vc-meta"><span>${esc(s.from)}–${esc(s.to)}</span>${s.pieces > 1 ? `<span>${s.pieces} parts joined</span>` : ''}${s.cold_open ? '<span>opens with a teaser</span>' : ''}</div>
          <div class="vc-caption"><textarea data-field="caption" aria-label="Post caption" placeholder="Post caption and hashtags">${esc(s.caption)}</textarea></div>
          <div class="vc-actions">
            ${s.download ? `<a class="btn btn-primary btn-sm" href="${esc(s.download)}" data-turbo="false" download><i class="ri-download-2-line"></i>Download</a>` : ''}
            <button type="button" class="btn btn-secondary btn-sm" data-act="copy"><i class="ri-file-copy-line"></i>Copy caption</button>
            ${s.srt ? `<a class="btn btn-ghost btn-sm" href="${esc(s.srt)}" data-turbo="false" title="Caption file for YouTube or Facebook"><i class="ri-closed-captioning-line"></i>.srt</a>` : ''}
            <button type="button" class="btn btn-ghost btn-sm" data-act="star" aria-pressed="${s.starred}" title="Mark as a favourite"><i class="${s.starred ? 'ri-star-fill' : 'ri-star-line'}"></i></button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="delete" title="Delete this short"><i class="ri-delete-bin-6-line"></i></button>
          </div>
          <details><summary><i class="ri-text"></i>What is said</summary><div class="vc-transcript">${esc(s.transcript)}</div></details>
          ${data.source_gone ? '' : `<details><summary><i class="ri-equalizer-line"></i>Adjust and re-cut</summary>
            <form class="vc-recut" data-recut>
              <label>Shape ${select('format', opts.formats, s.format)}</label>
              <label>Framing ${select('framing', opts.framings, s.framing)}</label>
              <label>Captions ${select('caption_style', opts.captions, s.caption_style)}</label>
              <label>Quality ${select('quality', opts.qualities, s.quality)}</label>
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
    function drawShorts() {
      const busyEl = document.activeElement && grid.contains(document.activeElement);
      const playing = [...grid.querySelectorAll('video')].some((v) => !v.paused);
      const openDetails = new Set([...grid.querySelectorAll('details[open]')].map((d) => `${d.closest('[data-sid]').dataset.sid}:${[...d.parentNode.querySelectorAll(':scope > details')].indexOf(d)}`));
      const keyNow = JSON.stringify(data.shorts.map((s) => [s.id, s.status, s.video, s.starred]));
      if (grid.dataset.key === keyNow && grid.children.length) return;
      if ((busyEl || playing) && grid.children.length) return;   // redraw later
      grid.dataset.key = keyNow;
      const sorted = [...data.shorts].sort((a, b) => (b.starred - a.starred) || 0);
      grid.innerHTML = sorted.length ? sorted.map(card).join('') :
        (data.busy ? '' : '<div class="vc-panel vc-empty" style="grid-column:1/-1"><i class="ri ri-film-line"></i><h2>No shorts yet</h2><p>Use “Find more shorts” or clip a stretch by its times.</p></div>');
      grid.querySelectorAll('details').forEach((d) => {
        const key = `${d.closest('[data-sid]').dataset.sid}:${[...d.parentNode.querySelectorAll(':scope > details')].indexOf(d)}`;
        if (openDetails.has(key)) d.open = true;
      });
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
        problem.innerHTML = `<div class="vc-alert bad"><i class="ri-error-warning-line"></i><span>${esc(data.status === 'stalled'
          ? 'The work stopped (the server may have restarted). Retry carries on from where it stopped.'
          : (data.error || 'Something went wrong.'))}</span><span class="flex-grow"></span>
          <button type="button" class="btn btn-secondary btn-sm" data-act="retry"><i class="ri-refresh-line"></i>Retry</button></div>`;
      } else if (data.source_gone) {
        problem.hidden = false;
        problem.innerHTML = '<div class="vc-alert info"><i class="ri-information-line"></i><span>The long source video was removed to save space. The shorts are kept, but they can no longer be re-cut.</span></div>';
      } else {
        problem.hidden = true;
      }
      tools.hidden = data.busy;
      tools.querySelectorAll('#vc-more, #vc-manual').forEach((x) => { x.hidden = data.source_gone; });
    }

    function draw() { drawHead(); drawShorts(); }

    async function poll() {
      try {
        const res = await fetch(`${base}/status.json`, { credentials: 'same-origin', signal: ctl.signal });
        if (res.ok) { data = await res.json(); draw(); }
      } catch (e) { if (ctl.signal.aborted) return; }
      if (!ctl.signal.aborted) window.__swmClipTimer = setTimeout(poll, data.busy ? 3000 : 20000);
    }

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
      if (!e.target.closest('[data-act=retry]')) return;
      const r = await post(`${base}/retry`, {}, csrf);
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
