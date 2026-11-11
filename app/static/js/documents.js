/* Document library (Admin -> Documents). Talks to /admin/documents/*; the server checks every permission. */
(() => {
  'use strict';

  const KIND_ICON = { pdf: 'ri-file-pdf-2-fill', word: 'ri-file-word-2-fill', sheet: 'ri-file-excel-2-fill', slides: 'ri-file-ppt-2-fill',
    image: 'ri-image-2-fill', audio: 'ri-music-2-fill', video: 'ri-film-fill', text: 'ri-file-text-fill', archive: 'ri-file-zip-fill', other: 'ri-file-3-fill' };
  const VIS = {
    private: ['ri-lock-line', 'Only me', 'Only you and the people or offices you share with'],
    church: ['ri-building-4-line', 'My church', 'Everyone in the console at this church'],
    everyone: ['ri-earth-line', 'Every church', 'Everyone in the console at every church'],
  };
  const EMPTY = {
    library: ['ri-folder-open-line', 'This folder is empty', 'Drag files here, or use New → Upload files.'],
    received: ['ri-inbox-archive-line', 'Nothing received yet', 'Files sent to you or your office appear here.'],
    sent: ['ri-send-plane-line', 'You have not sent anything', 'Open a file and press Send to deliver it to a person or an office.'],
    shared: ['ri-team-line', 'Nothing shared with you', 'Files other people share with you or your office appear here.'],
    starred: ['ri-star-line', 'No starred files', 'Star the files you use often to find them quickly.'],
    recent: ['ri-time-line', 'No recent files', 'Files you can see appear here, newest first.'],
    mine: ['ri-user-line', 'You have not uploaded anything', 'Use New → Upload files.'],
    trash: ['ri-delete-bin-6-line', 'Trash is empty', 'Files moved to the Trash can be restored or deleted for good.'],
  };

  function init() {
    window.__swmDocsAbort?.abort();
    const root = document.getElementById('dl');
    if (!root) return;
    const ctl = new AbortController(); window.__swmDocsAbort = ctl;
    const sig = { signal: ctl.signal };
    document.addEventListener('turbo:before-visit', () => ctl.abort(), { once: true });
    const $ = (id) => document.getElementById(id);
    const csrf = root.dataset.csrf, me = Number(root.dataset.me), maxMb = Number(root.dataset.maxMb) || 100;
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const enc = encodeURIComponent;
    const when = (iso) => { if (!iso) return ''; const d = new Date(iso), now = new Date();
      if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' }); };
    const full = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
    const initials = (n) => String(n || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();

    // ── fit to the window ──
    const fit = () => { const top = root.getBoundingClientRect().top; if (innerWidth > 860) root.style.setProperty('--dl-h', Math.max(520, innerHeight - top - 24) + 'px'); };
    fit(); window.addEventListener('resize', fit, sig);

    // ── http ──
    async function getJSON(url) {
      const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: ctl.signal });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `Error ${r.status}`); return j;
    }
    async function post(url, data) {
      const fd = data instanceof FormData ? data : new FormData();
      if (!(data instanceof FormData)) Object.entries(data || {}).forEach(([k, v]) => (Array.isArray(v) ? v.forEach((x) => fd.append(k, x)) : fd.append(k, v)));
      fd.append('csrf_token', csrf);
      const r = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf }, signal: ctl.signal });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `Error ${r.status}`); return j;
    }
    function toast(text) { const t = document.createElement('div'); t.className = 'dl-toast'; t.textContent = text; document.body.append(t); setTimeout(() => t.remove(), 3000); }

    // ── state ──
    const S = { view: 'library', folder: null, q: '', kind: '', sort: 'updated', layout: localStorage.getItem('dl-layout') || 'grid',
      church: Number(root.dataset.church), items: [], folders: [], crumbs: [], sel: null, detail: null, tab: 'details' };
    const lib = () => (S.church ? `&church=${S.church}` : '');

    // ── menus and dialogs ──
    let menu = null;
    const closeMenu = () => { menu?.remove(); menu = null; };
    function openMenu(anchor, items) {
      closeMenu();
      const m = document.createElement('div'); m.className = 'dl-pop'; m.setAttribute('role', 'menu'); m.style.position = 'fixed';
      items.filter(Boolean).forEach((it) => {
        if (it === '-') { const s = document.createElement('div'); s.className = 'sep'; m.append(s); return; }
        const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'menuitem'); if (it.danger) b.className = 'danger';
        b.innerHTML = `<i class="${it.icon}"></i>${esc(it.label)}`; b.addEventListener('click', () => { closeMenu(); it.run(); }); m.append(b);
      });
      document.body.append(m);
      const r = anchor.getBoundingClientRect(); let x = r.right - m.offsetWidth, y = r.bottom + 4;
      if (x < 8) x = 8; if (y + m.offsetHeight > innerHeight - 8) y = r.top - m.offsetHeight - 4;
      m.style.left = x + 'px'; m.style.top = y + 'px'; menu = m; m.querySelector('button')?.focus();
    }
    document.addEventListener('click', (e) => { if (menu && !menu.contains(e.target)) closeMenu();
      if (!$('dl-new-menu').hidden && !e.target.closest('.dl-new-wrap')) $('dl-new-menu').hidden = true; }, { ...sig, capture: true });

    function modal(html, onOpen) {
      return new Promise((resolve) => {
        const w = document.createElement('div'); w.className = 'dl-modal-wrap';
        w.innerHTML = `<div class="dl-modal" role="dialog" aria-modal="true">${html}</div>`;
        const done = (v) => { w.remove(); document.removeEventListener('keydown', key, true); resolve(v); };
        const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
        w.addEventListener('click', (e) => { if (e.target === w) done(null); const b = e.target.closest('[data-close]'); if (b) done(null); });
        document.addEventListener('keydown', key, true); document.body.append(w);
        onOpen?.(w.querySelector('.dl-modal'), done);
        w.querySelector('input, textarea, select, button.primary')?.focus();
      });
    }
    async function confirmBox(title, text, ok = 'OK', danger = false) {
      return modal(`<h3>${esc(title)}</h3><p class="sub">${esc(text)}</p><div class="btns"><button type="button" class="dl-btn" data-close>Cancel</button><button type="button" class="dl-btn ${danger ? 'danger' : 'primary'}" data-ok>${esc(ok)}</button></div>`,
        (m, done) => m.querySelector('[data-ok]').addEventListener('click', () => done(true)));
    }
    async function prompt(title, label, value = '') {
      return modal(`<h3>${esc(title)}</h3><label class="dl-field"><span>${esc(label)}</span><input id="dl-pr" maxlength="200" value="${esc(value)}"></label>
        <div class="btns"><button type="button" class="dl-btn" data-close>Cancel</button><button type="button" class="dl-btn primary" data-ok>Save</button></div>`, (m, done) => {
        const i = m.querySelector('#dl-pr'); i.select();
        const go = () => { if (i.value.trim()) done(i.value.trim()); };
        m.querySelector('[data-ok]').addEventListener('click', go); i.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
      });
    }

    // ── people & offices picker (Share / Send) ──
    let targets = null;
    async function loadTargets() { if (!targets) { const t = await getJSON('/admin/documents/targets.json'); targets = [...t.offices, ...t.people]; } return targets; }
    const rank = (list, q) => { const n = q.trim().toLowerCase(); if (!n) return list.slice(0, 12);
      return list.filter((t) => (t.label + ' ' + (t.sub || '')).toLowerCase().split(/\s+/).some((w) => w.startsWith(n)) || t.label.toLowerCase().includes(n)).slice(0, 30); };
    function picker(box, { multi, onPick }) {
      box.innerHTML = `<div class="dl-chosen"></div><div class="dl-picker"><input type="search" placeholder="Name of a person or office (e.g. HQ Finance)" autocomplete="off"><div class="dl-sugg" hidden></div></div>`;
      const input = box.querySelector('input'), sugg = box.querySelector('.dl-sugg'), chosen = box.querySelector('.dl-chosen');
      const picked = new Map(); let rows = [], active = 0;
      const drawChosen = () => { chosen.innerHTML = [...picked.values()].map((t) => `<span>${t.kind === 'office' ? '<i class="ri-building-2-line"></i>' : ''}${esc(t.label)}<button type="button" data-un="${esc(t.target)}" aria-label="Remove"><i class="ri-close-line"></i></button></span>`).join(''); };
      const draw = async () => {
        const all = await loadTargets(); rows = rank(all.filter((t) => !picked.has(t.target)), input.value); active = 0;
        sugg.innerHTML = rows.map((t, i) => `<button type="button" data-i="${i}" class="${i === 0 ? 'is-active' : ''}"><span class="dl-av ${t.kind === 'office' ? 'office' : ''}">${t.kind === 'office' ? '<i class="ri-building-2-line"></i>' : esc(initials(t.label))}</span><span>${esc(t.label)}<small>${esc(t.sub || (t.kind === 'office' ? 'Office' : ''))}</small></span></button>`).join('') || '<p style="padding:10px;color:var(--color-ink-3);font-size:13px">No one matches.</p>';
        sugg.hidden = false;
      };
      const pick = (i) => { const t = rows[i]; if (!t) return; if (multi) { picked.set(t.target, t); drawChosen(); input.value = ''; draw(); } else { input.value = ''; sugg.hidden = true; } onPick?.(t, picked); };
      input.addEventListener('focus', draw); input.addEventListener('input', draw);
      input.addEventListener('keydown', (e) => {
        const btns = [...sugg.querySelectorAll('[data-i]')];
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); active = (active + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % Math.max(1, btns.length); btns.forEach((b, i) => b.classList.toggle('is-active', i === active)); }
        else if (e.key === 'Enter') { e.preventDefault(); pick(active); }
        else if (e.key === 'Backspace' && !input.value && picked.size) { picked.delete([...picked.keys()].pop()); drawChosen(); }
      });
      sugg.addEventListener('mousedown', (e) => { const b = e.target.closest('[data-i]'); if (b) { e.preventDefault(); pick(Number(b.dataset.i)); } });
      input.addEventListener('blur', () => setTimeout(() => { sugg.hidden = true; }, 150));
      chosen.addEventListener('click', (e) => { const b = e.target.closest('[data-un]'); if (b) { picked.delete(b.dataset.un); drawChosen(); } });
      return picked;
    }

    // ── listing ──
    async function load() {
      const body = $('dl-body');
      let url = `/admin/documents/list.json?view=${S.view}&q=${enc(S.q)}&kind=${S.kind}&sort=${S.sort}${lib()}`;
      if (S.view === 'library' && S.folder) url += `&folder=${S.folder}`;
      try { const j = await getJSON(url); S.items = j.items; S.folders = j.folders || []; S.crumbs = j.crumbs || []; }
      catch (e) { body.innerHTML = `<div class="dl-empty"><i class="ri-error-warning-line"></i><b>Could not load</b>${esc(e.message)}</div>`; return; }
      render(); refreshUnread();
    }
    function crumbs() {
      const c = $('dl-crumbs'), names = { received: 'Received', sent: 'Sent', shared: 'Shared with me', starred: 'Starred', recent: 'Recent', mine: 'My files', trash: 'Trash' };
      if (S.view !== 'library') { c.innerHTML = `<span class="last">${names[S.view]}</span>`; return; }
      const parts = [{ id: null, name: `${$('dl-lib-name').textContent} library` }, ...S.crumbs];
      c.innerHTML = parts.map((p, i) => i === parts.length - 1 ? `<span class="last">${esc(p.name)}</span>` : `<button type="button" data-crumb="${p.id ?? ''}">${esc(p.name)}</button><i class="ri-arrow-right-s-line"></i>`).join('');
    }
    function thumb(it) {
      if (it.kind === 'image' && it.inline) return `<img src="/admin/documents/${it.id}/file?inline=1" alt="" loading="lazy">`;
      return `<i class="${KIND_ICON[it.kind] || KIND_ICON.other}"></i>`;
    }
    function render() {
      crumbs();
      const body = $('dl-body'), sel = S.sel;
      let html = '';
      if (S.view === 'library' && S.folders.length && !S.q && !S.kind) {
        html += '<p class="dl-sec">Folders</p><div class="dl-folders">' + S.folders.map((f) => `<div class="dl-folder" role="button" tabindex="0" data-folder="${f.id}" style="${f.color ? `--fc:${esc(f.color)}` : ''}">
          <i class="ri-folder-3-fill"></i><span style="min-width:0"><b>${esc(f.name)}</b><small>${f.count} file${f.count === 1 ? '' : 's'}</small></span>
          <button type="button" class="dl-ic dl-more" data-folder-menu="${f.id}" aria-label="Folder options"><i class="ri-more-2-fill"></i></button></div>`).join('') + '</div>';
      }
      if (!S.items.length) {
        if (!html) { const [ic, t, s] = S.q || S.kind ? ['ri-search-line', 'No files match', 'Try another word or type.'] : EMPTY[S.view]; html = `<div class="dl-empty"><i class="${ic}"></i><b>${t}</b>${s}</div>`; }
        body.innerHTML = html; return;
      }
      const isDelivery = S.view === 'received' || S.view === 'sent';
      html += `<p class="dl-sec">${isDelivery ? (S.view === 'received' ? 'Sent to you or your office' : 'Sent by you') : 'Files'}</p>`;
      if (S.layout === 'grid' && !isDelivery) {
        html += '<div class="dl-grid">' + S.items.map((it) => `<button type="button" class="dl-card k-${it.kind} ${sel === it.id ? 'is-sel' : ''}" data-id="${it.id}">
          <span class="dl-thumb">${thumb(it)}<span class="dl-badge">${esc(it.ext)}</span>${it.starred ? '<span class="dl-star"><i class="ri-star-fill"></i></span>' : ''}</span>
          <span class="dl-meta"><b title="${esc(it.title)}">${esc(it.title)}</b>
          <small><i class="${VIS[it.visibility][0]}" title="${VIS[it.visibility][1]}"></i>${esc(it.owner_id === me ? 'You' : it.owner)} · ${when(it.updated)} · ${esc(it.size_h)}</small></span></button>`).join('') + '</div>';
      } else {
        html += `<table class="dl-table"><thead><tr><th>Name</th>${isDelivery ? `<th>${S.view === 'received' ? 'From' : 'To'}</th><th>Note</th><th>Sent</th>` : '<th>Owner</th><th>Who can see</th><th>Modified</th><th>Size</th>'}</tr></thead><tbody>` +
          S.items.map((it) => `<tr class="k-${it.kind} ${sel === it.id ? 'is-sel' : ''}" data-id="${it.id}" tabindex="0">
            <td><span class="nm"><span class="dl-ticon"><i class="${KIND_ICON[it.kind] || KIND_ICON.other}"></i></span>${it.unread ? '<span class="dl-unread-dot" title="Not opened yet"></span>' : ''}<span style="${it.unread ? 'font-weight:700' : ''}">${esc(it.title)}</span>${it.starred ? '<i class="ri-star-fill" style="color:#c99a1d"></i>' : ''}</span></td>
            ${isDelivery ? `<td>${esc(S.view === 'received' ? it.delivery.from : it.delivery.to)}</td><td class="dl-row-msg">${esc((it.delivery.message || '').slice(0, 90))}</td><td>${when(it.delivery.at)}</td>`
              : `<td>${esc(it.owner_id === me ? 'You' : it.owner)}</td><td><i class="${VIS[it.visibility][0]}"></i> ${VIS[it.visibility][1]}</td><td>${when(it.updated)}</td><td>${esc(it.size_h)}</td>`}</tr>`).join('') + '</tbody></table>';
      }
      body.innerHTML = html;
    }
    async function refreshUnread() {
      try { const j = await getJSON('/admin/documents/list.json?view=received'); const n = j.items.filter((x) => x.unread).length;
        $('dl-unread').hidden = !n; $('dl-unread').textContent = n; } catch (_) {}
    }

    // view switching
    function go(view) {
      S.view = view; S.folder = null; S.sel = null; closeSide();
      root.querySelectorAll('.dl-views button').forEach((b) => b.classList.toggle('is-on', b.dataset.view === view));
      load();
    }
    root.querySelectorAll('.dl-views button').forEach((b) => b.addEventListener('click', () => go(b.dataset.view), sig));
    $('dl-church')?.addEventListener('change', (e) => { S.church = Number(e.target.value); $('dl-lib-name').textContent = e.target.selectedOptions[0].textContent; go('library'); }, sig);
    root.querySelectorAll('.dl-chip').forEach((b) => b.addEventListener('click', () => { S.kind = b.dataset.kind; root.querySelectorAll('.dl-chip').forEach((x) => x.classList.toggle('is-on', x === b)); load(); }, sig));
    let qTimer; $('dl-q').addEventListener('input', (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = e.target.value; load(); }, 250); }, sig);
    $('dl-sort').addEventListener('change', (e) => { S.sort = e.target.value; load(); }, sig);
    root.querySelectorAll('[data-layout]').forEach((b) => b.addEventListener('click', () => { S.layout = b.dataset.layout; localStorage.setItem('dl-layout', S.layout);
      root.querySelectorAll('[data-layout]').forEach((x) => x.classList.toggle('is-on', x === b)); render(); }, sig));
    root.querySelectorAll('[data-layout]').forEach((x) => x.classList.toggle('is-on', x.dataset.layout === S.layout));
    $('dl-crumbs').addEventListener('click', (e) => { const b = e.target.closest('[data-crumb]'); if (b) { S.folder = b.dataset.crumb ? Number(b.dataset.crumb) : null; S.sel = null; closeSide(); load(); } }, sig);

    // clicks in the list
    $('dl-body').addEventListener('click', (e) => {
      const fm = e.target.closest('[data-folder-menu]'); if (fm) { e.stopPropagation(); folderMenu(fm, Number(fm.dataset.folderMenu)); return; }
      const f = e.target.closest('[data-folder]'); if (f) { S.folder = Number(f.dataset.folder); S.sel = null; closeSide(); load(); return; }
      const it = e.target.closest('[data-id]'); if (it) select(Number(it.dataset.id));
    }, sig);
    $('dl-body').addEventListener('dblclick', (e) => { const it = e.target.closest('[data-id]'); if (it) openFile(Number(it.dataset.id)); }, sig);
    $('dl-body').addEventListener('keydown', (e) => { const it = e.target.closest('[data-id]'); if (it && e.key === 'Enter') select(Number(it.dataset.id));
      const f = e.target.closest('[data-folder]'); if (f && e.key === 'Enter') { S.folder = Number(f.dataset.folder); load(); } }, sig);
    $('dl-body').addEventListener('contextmenu', (e) => { const it = e.target.closest('[data-id]'); if (it) { e.preventDefault(); select(Number(it.dataset.id)); } }, sig);

    const itemById = (id) => S.items.find((x) => x.id === id) || S.detail?.item;
    const openFile = (id) => { const it = itemById(id); window.open(`/admin/documents/${id}/file${it && it.inline ? '?inline=1' : ''}`, '_blank', 'noopener'); };
    const download = (id, v) => { const a = document.createElement('a'); a.href = `/admin/documents/${id}/file${v ? `?v=${v}` : ''}`; a.download = ''; document.body.append(a); a.click(); a.remove(); };

    // ── folders ──
    async function newFolder() {
      const name = await prompt('New folder', 'Folder name'); if (!name) return;
      try { await post('/admin/documents/folders', { name, parent_id: S.view === 'library' && S.folder ? S.folder : '', church: S.church }); toast('Folder created'); if (S.view !== 'library') go('library'); else load(); }
      catch (e) { toast(e.message); }
    }
    function folderMenu(anchor, id) {
      const f = S.folders.find((x) => x.id === id);
      openMenu(anchor, [
        { icon: 'ri-folder-open-line', label: 'Open', run: () => { S.folder = id; load(); } },
        { icon: 'ri-edit-line', label: 'Rename', run: async () => { const n = await prompt('Rename folder', 'Folder name', f.name); if (!n) return;
          try { await post(`/admin/documents/folders/${id}/rename`, { name: n }); load(); } catch (e) { toast(e.message); } } },
        '-',
        { icon: 'ri-delete-bin-6-line', label: 'Delete folder', danger: true, run: async () => {
          if (!await confirmBox(`Delete “${f.name}”?`, 'Only an empty folder can be deleted.', 'Delete', true)) return;
          try { await post(`/admin/documents/folders/${id}/delete`, {}); load(); } catch (e) { toast(e.message); } } },
      ]);
    }

    // ── uploading ──
    $('dl-new').addEventListener('click', () => { $('dl-new-menu').hidden = !$('dl-new-menu').hidden; }, sig);
    $('dl-new-menu').addEventListener('click', (e) => { const b = e.target.closest('[data-new]'); if (!b) return; $('dl-new-menu').hidden = true;
      if (b.dataset.new === 'upload') $('dl-file').click(); else newFolder(); }, sig);
    $('dl-file').addEventListener('change', (e) => { const files = [...e.target.files]; e.target.value = ''; if (files.length) uploadDialog(files); }, sig);
    const here = () => (S.view === 'library' && S.crumbs.length ? S.crumbs[S.crumbs.length - 1].name : `${$('dl-lib-name').textContent} library`);
    async function uploadDialog(files) {
      const big = files.filter((f) => f.size > maxMb * 1048576);
      const ok = files.filter((f) => f.size <= maxMb * 1048576);
      const opts = await modal(`<h3>Upload ${ok.length} file${ok.length === 1 ? '' : 's'}</h3><p class="sub">To <b>${esc(here())}</b></p>
        <div class="files">${ok.map((f) => `<div><i class="ri-file-3-line"></i><span>${esc(f.name)}</span><small>${(f.size / 1048576).toFixed(1)} MB</small></div>`).join('')}</div>
        ${big.length ? `<p class="err">Larger than ${maxMb} MB, left out: ${esc(big.map((f) => f.name).join(', '))}</p>` : ''}
        <div class="dl-field"><span>Who can see ${ok.length === 1 ? 'it' : 'them'}</span><div class="dl-vis">${Object.entries(VIS).map(([k, [ic, t, s]]) => `<label><input type="radio" name="vis" value="${k}" ${k === 'church' ? 'checked' : ''}><span><b><i class="${ic}"></i> ${t}</b><small>${s}</small></span></label>`).join('')}</div></div>
        <label class="dl-field"><span>Note (optional)</span><textarea id="dl-up-desc" rows="2" maxlength="5000" placeholder="What is this? e.g. Minutes of the March council meeting"></textarea></label>
        <label class="dl-field"><span>Tags (optional)</span><input id="dl-up-tags" maxlength="400" placeholder="e.g. minutes, 2026, council"></label>
        <p class="sub" style="margin-top:10px">You can also share or send them to people and offices afterwards.</p>
        <div class="btns"><button type="button" class="dl-btn" data-close>Cancel</button><button type="button" class="dl-btn primary" data-ok ${ok.length ? '' : 'disabled'}><i class="ri-upload-cloud-2-line"></i>Upload</button></div>`,
      (m, done) => m.querySelector('[data-ok]').addEventListener('click', () => done({ vis: m.querySelector('[name=vis]:checked').value, desc: m.querySelector('#dl-up-desc').value, tags: m.querySelector('#dl-up-tags').value })));
      if (!opts || !ok.length) return;
      const folder = S.view === 'library' ? S.folder : null;
      $('dl-uploads').hidden = false; $('dl-up-title').textContent = `Uploading ${ok.length} file${ok.length === 1 ? '' : 's'}`;
      let done = 0;
      for (const f of ok) {
        const li = document.createElement('li');
        li.innerHTML = `<i class="st ri-loader-4-line dl-spin"></i><span class="nm">${esc(f.name)}</span><span class="pc">0%</span><span class="bar"><i></i></span>`;
        $('dl-up-list').prepend(li);
        await new Promise((resolve) => {
          const fd = new FormData(); fd.append('file', f); fd.append('visibility', opts.vis); fd.append('description', opts.desc); fd.append('tags', opts.tags);
          if (folder) fd.append('folder_id', folder); fd.append('church', S.church); fd.append('csrf_token', csrf);
          const x = new XMLHttpRequest(); x.open('POST', '/admin/documents/upload'); x.setRequestHeader('X-CSRF-Token', csrf);
          x.upload.onprogress = (ev) => { if (ev.lengthComputable) { const p = Math.round(100 * ev.loaded / ev.total); li.querySelector('.bar i').style.width = p + '%'; li.querySelector('.pc').textContent = p + '%'; } };
          x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (_) {}
            if (x.status < 300) { li.classList.add('ok'); li.querySelector('.st').className = 'st ri-checkbox-circle-fill'; li.querySelector('.pc').textContent = ''; done++; }
            else { li.classList.add('bad'); li.querySelector('.st').className = 'st ri-error-warning-fill'; li.querySelector('.pc').textContent = ''; li.insertAdjacentHTML('beforeend', `<small>${esc(j.error || 'Upload failed')}</small>`); }
            resolve(); };
          x.onerror = () => { li.classList.add('bad'); li.insertAdjacentHTML('beforeend', '<small>Network error</small>'); resolve(); };
          x.send(fd);
        });
      }
      $('dl-up-title').textContent = `${done} of ${ok.length} uploaded`;
      if (S.view !== 'library' && S.view !== 'mine' && S.view !== 'recent') go('library'); else load();
    }
    $('dl-up-x').addEventListener('click', () => { $('dl-uploads').hidden = true; $('dl-up-list').innerHTML = ''; }, sig);
    // drag and drop
    let dragDepth = 0;
    const main = $('dl-main');
    main.addEventListener('dragenter', (e) => { if (![...(e.dataTransfer?.types || [])].includes('Files')) return; e.preventDefault(); dragDepth++; $('dl-drop-to').textContent = here(); $('dl-drop').hidden = false; }, sig);
    main.addEventListener('dragover', (e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) e.preventDefault(); }, sig);
    main.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('dl-drop').hidden = true; }, sig);
    main.addEventListener('drop', (e) => { e.preventDefault(); dragDepth = 0; $('dl-drop').hidden = true; const files = [...(e.dataTransfer?.files || [])]; if (files.length) uploadDialog(files); }, sig);

    // ── details drawer ──
    const side = $('dl-side');
    function closeSide() { side.hidden = true; S.detail = null; }
    async function select(id) {
      S.sel = id; render();
      side.hidden = false; side.innerHTML = '<div class="dl-empty"><i class="ri-loader-4-line dl-spin"></i></div>';
      try { S.detail = await getJSON(`/admin/documents/${id}.json`); } catch (e) { side.innerHTML = `<div class="dl-empty"><b>Not available</b>${esc(e.message)}</div>`; return; }
      const it = S.items.find((x) => x.id === id); if (it && it.unread) { it.unread = false; render(); refreshUnread(); }
      drawSide();
    }
    function preview(it) {
      const src = `/admin/documents/${it.id}/file?inline=1&v=${it.version}`;
      if (!it.inline) return `<div class="dl-preview k-${it.kind}"><i class="${KIND_ICON[it.kind] || KIND_ICON.other}"></i></div>`;
      if (it.kind === 'image') return `<div class="dl-preview k-${it.kind}"><img src="${src}" alt=""></div>`;
      if (it.kind === 'audio') return `<div class="dl-preview k-${it.kind}"><audio controls preload="none" src="${src}"></audio></div>`;
      if (it.kind === 'video') return `<div class="dl-preview k-${it.kind}"><video controls preload="metadata" src="${src}"></video></div>`;
      return `<div class="dl-preview k-${it.kind}"><iframe src="${src}" title="Preview" ${it.kind === 'pdf' ? '' : 'sandbox'} loading="lazy"></iframe></div>`;
    }
    function drawSide() {
      const d = S.detail, it = d.item, can = it.access, edit = can === 'edit' || can === 'manage', manage = can === 'manage';
      const rec = S.view === 'received' ? S.items.find((x) => x.id === it.id)?.delivery : null;
      const tabs = [['details', 'Details'], ['sharing', 'Sharing'], ['versions', `Versions (${d.versions.length})`], ['activity', 'Activity']];
      let tab = '';
      if (S.tab === 'details') {
        tab = `<dl class="dl-facts">
          <div><dt>Owner</dt><dd>${esc(it.owner_id === me ? 'You' : it.owner)}</dd></div>
          <div><dt>Library</dt><dd>${esc(it.church)}${it.folder ? ' / ' + esc(it.folder) : ''}</dd></div>
          <div><dt>Who can see</dt><dd><i class="${VIS[it.visibility][0]}"></i> ${VIS[it.visibility][1]}</dd></div>
          <div><dt>Your access</dt><dd>${manage ? 'Owner' : edit ? 'Can edit' : 'Can view'}</dd></div>
          <div><dt>File</dt><dd>${esc(it.filename)}</dd></div>
          <div><dt>Size</dt><dd>${esc(it.size_h)} · version ${it.version}</dd></div>
          <div><dt>Added</dt><dd>${full(it.created)}</dd></div>
          <div><dt>Modified</dt><dd>${full(it.updated)}</dd></div></dl>
          ${edit ? `<label class="dl-field"><span>Note</span><textarea id="dl-desc" rows="3" maxlength="5000" placeholder="Add a note about this file">${esc(it.description)}</textarea></label>
            <label class="dl-field"><span>Tags</span><input id="dl-tags" value="${esc(it.tags.join(', '))}" placeholder="minutes, 2026"></label>
            <div style="margin-top:10px"><button type="button" class="dl-btn" id="dl-save-meta"><i class="ri-check-line"></i>Save</button></div>`
            : `${it.description ? `<p class="dl-field"><span>Note</span>${esc(it.description)}</p>` : ''}${it.tags.length ? `<div class="dl-field"><span>Tags</span><div class="dl-tags">${it.tags.map((t) => `<span class="dl-tag">${esc(t)}</span>`).join('')}</div></div>` : ''}`}`;
      } else if (S.tab === 'sharing') {
        const visBlock = `<div class="dl-field"><span>Who can see it</span><div class="dl-vis">${Object.entries(VIS).map(([k, [ic, t, s]]) => `<label><input type="radio" name="dl-vis" value="${k}" ${it.visibility === k ? 'checked' : ''}><span><b><i class="${ic}"></i> ${t}</b><small>${s}</small></span></label>`).join('')}</div></div>`;
        const people = `<div class="dl-people">${d.shares.length ? d.shares.map((s) => `<div class="dl-person"><span class="dl-av ${s.target.startsWith('user:') ? '' : 'office'}">${s.target.startsWith('user:') ? esc(initials(s.label)) : '<i class="ri-building-2-line"></i>'}</span>
          <span class="who"><b>${esc(s.label)}</b><small>${s.target.startsWith('user:') ? 'Person' : 'Office'}</small></span>
          ${manage ? `<select data-share-access="${esc(s.target)}"><option value="view" ${s.access === 'view' ? 'selected' : ''}>Can view</option><option value="edit" ${s.access === 'edit' ? 'selected' : ''}>Can edit</option><option value="">Remove</option></select>` : `<small>${s.access === 'edit' ? 'Can edit' : 'Can view'}</small>`}</div>`).join('') : '<p class="dl-field" style="color:var(--color-ink-3);margin-top:4px">Not shared with anyone in particular yet.</p>'}</div>`;
        // Like Google Drive: add people first (its suggestions open downwards into free space), then the general access.
        tab = (manage ? `<div class="dl-field" style="margin-top:0"><span>Share with people and offices</span><div id="dl-share-pick"></div>
            <select id="dl-share-access" style="margin-top:6px"><option value="view">Can view</option><option value="edit">Can edit (rename, notes, new versions)</option></select></div>`
          : '<p class="dl-field" style="margin-top:0">Only the owner can change who sees this file.</p>') + people + (manage ? visBlock : '');
        if (d.deliveries.length) tab += `<p class="dl-sec">Sent</p><div class="dl-timeline">${d.deliveries.map((x) => `<div class="dl-ev"><i class="ri-send-plane-line"></i><div>To <b>${esc(x.to)}</b>${x.message ? ` · “${esc(x.message.slice(0, 80))}”` : ''}<small>${esc(x.from)} · ${full(x.at)}${x.reads != null ? ` · opened by ${x.reads}` : ''}</small></div></div>`).join('')}</div>`;
      } else if (S.tab === 'versions') {
        tab = (edit ? `<div style="margin-bottom:8px"><button type="button" class="dl-btn" id="dl-new-version"><i class="ri-upload-2-line"></i>Upload a new version</button></div>` : '') +
          d.versions.map((v) => `<div class="dl-ver"><span class="v">v${v.version}</span><div><b>${esc(v.filename)}</b><small>${esc(v.by)} · ${full(v.at)} · ${esc(v.size_h)}${v.note ? ` · ${esc(v.note)}` : ''}</small></div>
            <button type="button" class="dl-ic" data-dl-version="${v.version}" aria-label="Download version ${v.version}"><i class="ri-download-2-line"></i></button></div>`).join('');
      } else {
        tab = `<div class="dl-timeline">${d.activity.map((a) => `<div class="dl-ev"><span class="dl-av" style="width:28px;height:28px;font-size:11px">${esc(initials(a.who))}</span><div><b>${esc(a.who)}</b> ${esc(a.what)}${a.detail ? ` ${esc(a.detail)}` : ''}<small>${full(a.at)}</small></div></div>`).join('') || '<p class="dl-field" style="color:var(--color-ink-3)">No activity yet.</p>'}</div>`;
      }
      side.innerHTML = `<header class="dl-side-head k-${it.kind}"><span class="dl-ticon"><i class="${KIND_ICON[it.kind] || KIND_ICON.other}"></i></span><h2>${esc(it.title)}</h2>
          <button type="button" class="dl-ic" id="dl-side-more" aria-label="More"><i class="ri-more-2-fill"></i></button><button type="button" class="dl-ic" id="dl-side-x" aria-label="Close"><i class="ri-close-line"></i></button></header>
        <div class="dl-side-body">
          ${rec ? `<div class="dl-note"><b>From ${esc(rec.from)} · ${full(rec.at)}</b>${rec.message ? esc(rec.message) : 'No note.'}</div>` : ''}
          ${it.trashed ? '' : preview(it)}
          <div class="dl-actions">
            ${it.trashed ? (manage ? `<button type="button" class="dl-btn primary" data-act="restore"><i class="ri-arrow-go-back-line"></i>Restore</button><button type="button" class="dl-btn danger" data-act="delete"><i class="ri-delete-bin-7-line"></i>Delete for good</button>` : '')
              : `<button type="button" class="dl-btn primary" data-act="open"><i class="ri-external-link-line"></i>Open</button>
                 <button type="button" class="dl-btn" data-act="download"><i class="ri-download-2-line"></i>Download</button>
                 ${edit ? '<button type="button" class="dl-btn" data-act="send"><i class="ri-send-plane-line"></i>Send</button>' : ''}
                 <button type="button" class="dl-btn" data-act="star"><i class="${it.starred ? 'ri-star-fill' : 'ri-star-line'}" style="${it.starred ? 'color:#c99a1d' : ''}"></i>${it.starred ? 'Starred' : 'Star'}</button>`}
          </div>
          ${it.trashed ? '' : `<nav class="dl-tabs">${tabs.map(([k, l]) => `<button type="button" data-tab="${k}" class="${S.tab === k ? 'is-on' : ''}">${l}</button>`).join('')}</nav>${tab}`}
        </div>`;
      if (S.tab === 'sharing' && manage) {
        picker($('dl-share-pick'), { multi: false, onPick: async (t) => {
          try { await post(`/admin/documents/${it.id}/share`, { target: t.target, access: $('dl-share-access').value }); toast(`Shared with ${t.label}`); await select(it.id); }
          catch (e) { toast(e.message); } } });
      }
    }
    side.addEventListener('click', async (e) => {
      const d = S.detail; if (!d) return; const it = d.item;
      if (e.target.closest('#dl-side-x')) { S.sel = null; closeSide(); render(); return; }
      const t = e.target.closest('[data-tab]'); if (t) { S.tab = t.dataset.tab; drawSide(); return; }
      const v = e.target.closest('[data-dl-version]'); if (v) { download(it.id, Number(v.dataset.dlVersion)); return; }
      if (e.target.closest('#dl-new-version')) { $('dl-file-version').click(); return; }
      if (e.target.closest('#dl-save-meta')) {
        try { await post(`/admin/documents/${it.id}/update`, { description: $('dl-desc').value, tags: $('dl-tags').value }); toast('Saved'); await select(it.id); load(); } catch (err) { toast(err.message); }
        return;
      }
      if (e.target.closest('#dl-side-more')) { moreMenu(e.target.closest('#dl-side-more'), it); return; }
      const a = e.target.closest('[data-act]'); if (!a) return;
      const act = a.dataset.act;
      if (act === 'open') openFile(it.id);
      else if (act === 'download') download(it.id);
      else if (act === 'send') sendDialog(it);
      else if (act === 'star') { try { await post(`/admin/documents/${it.id}/star`, { on: it.starred ? '0' : '1' }); await select(it.id); load(); } catch (err) { toast(err.message); } }
      else if (act === 'restore') { try { await post(`/admin/documents/${it.id}/trash`, { restore: '1' }); toast('Restored'); closeSide(); load(); } catch (err) { toast(err.message); } }
      else if (act === 'delete') {
        if (!await confirmBox(`Delete “${it.title}” for good?`, 'Every version is deleted. This cannot be undone.', 'Delete for good', true)) return;
        try { await post(`/admin/documents/${it.id}/delete`, {}); toast('Deleted'); closeSide(); load(); } catch (err) { toast(err.message); }
      }
    }, sig);
    side.addEventListener('change', async (e) => {
      const d = S.detail; if (!d) return; const it = d.item;
      if (e.target.name === 'dl-vis') {
        try { await post(`/admin/documents/${it.id}/update`, { visibility: e.target.value }); toast(`Now visible to: ${VIS[e.target.value][1]}`); await select(it.id); load(); } catch (err) { toast(err.message); }
      }
      const sa = e.target.closest('[data-share-access]');
      if (sa) {
        try {
          if (!sa.value) { await post(`/admin/documents/${it.id}/unshare`, { target: sa.dataset.shareAccess }); toast('Access removed'); }
          else { await post(`/admin/documents/${it.id}/share`, { target: sa.dataset.shareAccess, access: sa.value }); toast('Access changed'); }
          await select(it.id);
        } catch (err) { toast(err.message); }
      }
    }, sig);
    $('dl-file-version').addEventListener('change', async (e) => {
      const f = e.target.files[0]; e.target.value = ''; const it = S.detail?.item; if (!f || !it) return;
      if (f.size > maxMb * 1048576) { toast(`The file is larger than ${maxMb} MB.`); return; }
      const note = await prompt('New version', 'What changed? (optional)', '');
      if (note === null) return;
      const fd = new FormData(); fd.append('file', f); fd.append('note', note || '');
      toast('Uploading the new version…');
      try { await post(`/admin/documents/${it.id}/version`, fd); toast('New version saved'); S.tab = 'versions'; await select(it.id); load(); } catch (err) { toast(err.message); }
    }, sig);

    function moreMenu(anchor, it) {
      const edit = it.access === 'edit' || it.access === 'manage', manage = it.access === 'manage';
      openMenu(anchor, [
        edit && { icon: 'ri-edit-line', label: 'Rename', run: async () => { const t = await prompt('Rename', 'Title', it.title); if (!t) return;
          try { await post(`/admin/documents/${it.id}/update`, { title: t }); await select(it.id); load(); } catch (e) { toast(e.message); } } },
        edit && { icon: 'ri-folder-transfer-line', label: 'Move to folder', run: () => moveDialog(it) },
        edit && { icon: 'ri-upload-2-line', label: 'Upload a new version', run: () => $('dl-file-version').click() },
        edit && { icon: 'ri-send-plane-line', label: 'Send to a person or office', run: () => sendDialog(it) },
        manage && { icon: 'ri-user-shared-line', label: 'Share settings', run: () => { S.tab = 'sharing'; drawSide(); } },
        '-',
        manage && { icon: 'ri-delete-bin-6-line', label: 'Move to Trash', danger: true, run: async () => {
          try { await post(`/admin/documents/${it.id}/trash`, {}); toast('Moved to Trash'); S.sel = null; closeSide(); load(); } catch (e) { toast(e.message); } } },
      ]);
    }
    async function moveDialog(it) {
      let folders = [];
      try { folders = (await getJSON(`/admin/documents/folders.json${lib().replace('&', '?')}`)).folders; } catch (e) { toast(e.message); return; }
      const kids = (pid) => folders.filter((f) => (f.parent_id || null) === pid);
      const tree = (pid, depth) => kids(pid).map((f) => `<button type="button" data-to="${f.id}" style="padding-left:${10 + depth * 18}px" class="${it.folder_id === f.id ? 'is-on' : ''}"><i class="ri-folder-3-fill" style="color:var(--color-gold-text)"></i>${esc(f.name)}</button>` + tree(f.id, depth + 1)).join('');
      const to = await modal(`<h3>Move “${esc(it.title)}”</h3><p class="sub">Choose a folder in ${esc($('dl-lib-name').textContent)} library.</p>
        <div class="dl-tree"><button type="button" data-to="" class="${!it.folder_id ? 'is-on' : ''}"><i class="ri-building-4-line"></i>Library (top)</button>${tree(null, 1)}</div>
        <div class="btns"><button type="button" class="dl-btn" data-close>Cancel</button></div>`, (m, done) => m.addEventListener('click', (e) => { const b = e.target.closest('[data-to]'); if (b) done({ id: b.dataset.to }); }));
      if (!to) return;
      try { await post(`/admin/documents/${it.id}/update`, { folder_id: to.id }); toast('Moved'); await select(it.id); load(); } catch (e) { toast(e.message); }
    }
    async function sendDialog(it) {
      let picked;
      const res = await modal(`<h3>Send “${esc(it.title)}”</h3><p class="sub">They get view access and find it under Received, with your note.</p>
        <div class="dl-field" style="margin-top:0"><span>To</span><div id="dl-send-pick"></div></div>
        <label class="dl-field"><span>Note (optional)</span><textarea id="dl-send-msg" rows="3" maxlength="2000" placeholder="e.g. Please review before Sunday"></textarea></label>
        <p class="err" id="dl-send-err" hidden></p>
        <div class="btns"><button type="button" class="dl-btn" data-close>Cancel</button><button type="button" class="dl-btn primary" data-ok><i class="ri-send-plane-fill"></i>Send</button></div>`, (m, done) => {
        picked = picker(m.querySelector('#dl-send-pick'), { multi: true });
        m.querySelector('[data-ok]').addEventListener('click', () => {
          if (!picked.size) { const er = m.querySelector('#dl-send-err'); er.hidden = false; er.textContent = 'Choose at least one person or office.'; return; }
          done({ targets: [...picked.keys()], message: m.querySelector('#dl-send-msg').value });
        });
      });
      if (!res) return;
      try { const r = await post(`/admin/documents/${it.id}/send`, { targets: res.targets, message: res.message }); toast(`Sent to ${r.sent} recipient${r.sent === 1 ? '' : 's'}`); await select(it.id); }
      catch (e) { toast(e.message); }
    }

    // keyboard
    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select') || document.querySelector('.dl-modal-wrap')) return;
      if (e.key === 'Escape' && !side.hidden) { S.sel = null; closeSide(); render(); }
      if (e.key === 'Delete' && S.detail && S.detail.item.access === 'manage' && !S.detail.item.trashed) {
        post(`/admin/documents/${S.detail.item.id}/trash`, {}).then(() => { toast('Moved to Trash'); S.sel = null; closeSide(); load(); }).catch((err) => toast(err.message));
      }
    }, sig);

    // start (#received opens the Received box, e.g. from the email notice)
    const start = (location.hash || '').slice(1);
    go(EMPTY[start] ? start : 'library');
  }

  window.SWMDocuments = { init };
})();
