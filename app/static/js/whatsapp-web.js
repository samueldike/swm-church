/* SWM console: the church's WhatsApp, working like the official WhatsApp Web.
   Data comes from /admin/whatsapp/* (relayed to the church's gateway session); nothing is stored by SWM
   except scheduled posts. init() runs on every (Turbo) visit; the previous run is aborted first. */
(() => {
  'use strict';

  const EMOJI = {
    'ri-emotion-line': ['Smileys', '😀 😃 😄 😁 😆 😅 😂 🤣 🥲 ☺️ 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😗 😙 😚 😋 😛 😝 😜 🤪 🤨 🧐 🤓 😎 🥸 🤩 🥳 😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥 😓 🤗 🤔 🫣 🤭 🫢 🤫 🤥 😶 😐 😑 😬 🙄 😯 😦 😧 😮 😲 🥱 😴 🤤 😪 😵 🤐 🥴 🤢 🤮 🤧 😷 🤒 🤕 🤑 🤠 😈 👿 👻 💀 🤡 💩 👽 🤖 🎃'],
    'ri-hand-heart-line': ['People', '👋 🤚 🖐️ ✋ 🖖 👌 🤌 🤏 ✌️ 🤞 🫰 🤟 🤘 🤙 👈 👉 👆 🖕 👇 ☝️ 👍 👎 ✊ 👊 🤛 🤜 👏 🙌 🫶 👐 🤲 🤝 🙏 ✍️ 💅 💪 🦾 🦵 🦶 👂 👃 🧠 👀 👁️ 👅 👄 💋 👶 🧒 👦 👧 🧑 👱 👨 🧔 👩 🧓 👴 👵 🙍 🙎 🙅 🙆 💁 🙋 🧏 🙇 🤦 🤷 👮 🧑‍⚕️ 🧑‍🎓 🧑‍🏫 🧑‍🍳 🧑‍💻 🤵 👰 🤰 🤱 👼 🎅 🧑‍🤝‍🧑 👪 💃 🕺 🚶 🏃'],
    'ri-heart-line': ['Symbols', '❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 💟 ✝️ ☦️ ⛪ 🕊️ 🙏 ✨ ⭐ 🌟 💫 🔥 💯 ✅ ☑️ ✔️ ❌ ❎ ➕ ➖ ❗ ❓ ‼️ ⁉️ ⚠️ 🚫 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟥 🟩 🟦 ▶️ ⏸️ ⏹️ 🔁 🔔 🔕 📢 📣 💬 💭 🗯️ ♻️ 🆗 🆕 🆓 🔝 ⬆️ ⬇️ ➡️ ⬅️ 🔟 1️⃣ 2️⃣ 3️⃣'],
    'ri-leaf-line': ['Nature', '🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🐔 🐧 🐦 🐤 🦆 🦅 🦉 🐺 🐴 🦄 🐝 🦋 🐌 🐞 🐢 🐍 🐙 🐠 🐟 🐬 🐳 🐋 🦈 🐊 🐅 🐆 🦓 🐘 🦒 🐑 🐐 🐪 🌵 🎄 🌲 🌳 🌴 🌱 🌿 ☘️ 🍀 🍃 🍂 🍁 🌾 🌺 🌻 🌹 🥀 🌷 🌼 🌸 💐 🌍 🌙 ☀️ 🌤️ ⛅ 🌧️ ⛈️ 🌈 ❄️ 💧 🌊'],
    'ri-cake-2-line': ['Food', '🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🍆 🥔 🥕 🌽 🌶️ 🥒 🥬 🥦 🧄 🧅 🍄 🥜 🍞 🥐 🥖 🥨 🧀 🥚 🍳 🥞 🥓 🍗 🍖 🌭 🍔 🍟 🍕 🥪 🌮 🌯 🥗 🍝 🍜 🍲 🍛 🍣 🍱 🍚 🍙 🍘 🍥 🥮 🍢 🍡 🍧 🍨 🍦 🥧 🧁 🍰 🎂 🍮 🍭 🍬 🍫 🍿 🍩 🍪 ☕ 🍵 🥤 🧃 🥛 🍷 🥂 🍾'],
    'ri-football-line': ['Activity', '⚽ 🏀 🏈 ⚾ 🎾 🏐 🏉 🎱 🏓 🏸 🥅 🏒 🏏 ⛳ 🏹 🎣 🥊 🥋 🎽 ⛸️ 🎿 🏆 🥇 🥈 🥉 🏅 🎖️ 🎗️ 🎫 🎟️ 🎪 🎭 🎨 🎬 🎤 🎧 🎼 🎹 🥁 🎷 🎺 🎸 🎻 🎲 🧩 🎯 🎳 🎮 🚗 🚕 🚌 🚑 🚒 🚓 🏍️ 🚲 ✈️ 🚀 🛳️ 🚂 🗺️ 🏠 🏡 🏫 🏥 ⛪ 🕌 🏛️ 🌆 🌉 🎉 🎊 🎈 🎁 🎀 🪅'],
    'ri-lightbulb-line': ['Objects', '⌚ 📱 💻 ⌨️ 🖥️ 🖨️ 🖱️ 💾 💿 📷 📸 📹 🎥 📞 ☎️ 📺 📻 🎙️ ⏰ ⏳ 📡 🔋 🔌 💡 🔦 🕯️ 💸 💵 💰 💳 💎 ⚖️ 🔧 🔨 🛠️ ⚙️ 🔑 🗝️ 🚪 🛏️ 🛋️ 🧸 🎁 ✉️ 📩 📨 📧 💌 📦 🏷️ 📝 📄 📃 📑 📊 📈 📉 📅 📆 🗓️ 📁 📂 📌 📍 📎 🖇️ 📏 ✂️ 🖊️ ✏️ 🔍 🔎 🔒 🔓 📖 📚 📰 🗞️ 🧾'],
  };
  const QUICK = ['👍', '❤️', '😂', '😮', '😢', '🙏'];
  const NAME_COLORS = ['#e542a3', '#1f7aec', '#d42a66', '#00a884', '#a62c71', '#fc9775', '#8b7add', '#c7861d', '#06cf9c', '#ff2e74', '#53bdeb', '#5e47de'];

  function init() {
    window.__swmWaAbort?.abort();
    clearInterval(window.__swmWaTimer); clearInterval(window.__swmWaPresence);
    const root = document.getElementById('wa');
    if (!root) return;
    const ctl = new AbortController(); window.__swmWaAbort = ctl;
    const sig = { signal: ctl.signal };
    const stopAll = () => { ctl.abort(); clearInterval(window.__swmWaTimer); clearInterval(window.__swmWaPresence); document.title = baseTitle; };
    document.addEventListener('turbo:before-visit', stopAll, { once: true });
    const baseTitle = document.title.replace(/^\(\d+\)\s*/, '');

    const branch = root.dataset.branch, csrf = root.dataset.csrf;
    const $ = (id) => document.getElementById(id);
    const app = $('wa-app');
    const q = (p) => `/admin/whatsapp${p}${p.includes('?') ? '&' : '?'}branch=${branch}`;
    const enc = encodeURIComponent;
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const initials = (n) => (String(n || '').replace(/[^\p{L}\p{N} ]/gu, '').trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join('') || '').toUpperCase();
    const phoneOf = (jid) => String(jid || '').split('@')[0].split(':')[0];
    const num = (jid) => (/@(c\.us|s\.whatsapp\.net)$/.test(String(jid || '')) ? '+' + phoneOf(jid) : '');
    const nameColor = (s) => { let h = 0; for (const ch of String(s)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return NAME_COLORS[h % NAME_COLORS.length]; };
    // Search like WhatsApp: every typed word must start a word of the name (any order), or match its initials
    // ("swm" finds "Scent of Water Ministry", "swm nigeria" finds "Scent of Water Ministry Nigeria"); numbers
    // match anywhere in the phone. Results are ranked best first.
    const norm = (v) => String(v || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim();
    const subseq = (hay, needle) => { let i = 0; for (const ch of hay) if (ch === needle[i]) i++; return i === needle.length; };
    function score(text, query) {
      const t = norm(text), qn = norm(query);
      if (!qn) return 1; if (!t) return 0;
      const qd = String(query).replace(/\D/g, '');
      if (qd.length >= 3 && /^[+\d\s()-]+$/.test(String(query).trim())) return String(text).replace(/\D/g, '').includes(qd) ? 70 : 0;
      if (t === qn) return 100;
      if (t.startsWith(qn)) return 90;
      const words = t.split(' '), qs = qn.split(' '), ini = words.map((w) => w[0]).join('');
      if (qs.every((w) => words.some((x) => x.startsWith(w)))) return 80;
      if (t.includes(qn)) return 60;
      const qc = qn.replace(/ /g, '');
      if (qc.length >= 2 && ini.startsWith(qc)) return 50;
      if (qs.every((w) => words.some((x) => x.startsWith(w)) || (w.length >= 2 && subseq(ini, w)))) return 40;
      if (qs.every((w) => t.includes(w))) return 30;
      return 0;
    }
    // Lookalikes: when nothing matches every word, fall back to names matching SOME of the words (best first).
    function partialScore(text, query) {
      const words = norm(text).split(' '), qs = norm(query).split(' ').filter((w) => w.length >= 2);
      if (!qs.length) return 0;
      const ini = words.map((w) => w[0]).join('');
      const hit = qs.filter((w) => words.some((x) => x.startsWith(w)) || (w.length >= 2 && subseq(ini, w)) || (w.length >= 4 && words.some((x) => x.startsWith(w.slice(0, 3))))).length;
      return hit ? Math.round(20 * hit / qs.length) : 0;
    }
    let lastPartial = false;
    function rank(list, query, fields, limit = 300) {
      lastPartial = false;
      if (!String(query || '').trim()) return list.slice(0, limit);
      const run = (fn) => list.map((x, i) => [x, Math.max(0, ...fields(x).map((f) => fn(f, query))), i]).filter((r) => r[1] > 0)
        .sort((a, b) => (b[1] - a[1]) || (a[2] - b[2])).slice(0, limit).map((r) => r[0]);
      const strict = run(score);
      if (strict.length || /^[+\d\s()-]+$/.test(String(query).trim())) return strict;
      const loose = run(partialScore); lastPartial = loose.length > 0; return loose;
    }
    // highlight the matched word starts in a name
    function hl(name, query) {
      const qs = norm(query).split(' ').filter(Boolean); if (!qs.length) return esc(name);
      return esc(name).replace(/(^|[\s(\-~])([\p{L}\p{N}]+)/gu, (m, pre, w) => {
        const q1 = qs.find((x) => norm(w).startsWith(x)); return q1 ? `${pre}<mark>${w.slice(0, q1.length)}</mark>${w.slice(q1.length)}` : m; });
    }
    const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
      set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} } };
    const K = (k) => `swm-wa-${k}-b${branch}`;

    // WhatsApp text formatting: *bold* _italic_ ~strike~ ```mono``` and links
    const fmt = (s) => esc(s)
      .replace(/```([\s\S]+?)```/g, '<code>$1</code>')
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, '$1<b>$2</b>')
      .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, '$1<i>$2</i>')
      .replace(/(^|[\s(])~([^~\n]+)~(?=[\s).,!?:;]|$)/g, '$1<s>$2</s>')
      .replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    const clock = (t) => t ? new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const sameDay = (a, b) => a.toDateString() === b.toDateString();
    const listTime = (t) => {
      if (!t) return ''; const d = new Date(t * 1000), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
      if (sameDay(d, now)) return clock(t); if (sameDay(d, y)) return 'Yesterday';
      if (now - d < 6 * 864e5) return d.toLocaleDateString([], { weekday: 'long' });
      return d.toLocaleDateString();
    };
    const dayLabel = (t) => {
      const d = new Date(t * 1000), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
      if (sameDay(d, now)) return 'Today'; if (sameDay(d, y)) return 'Yesterday';
      if (now - d < 6 * 864e5) return d.toLocaleDateString([], { weekday: 'long' });
      return d.toLocaleDateString([], { day: 'numeric', month: 'long', year: 'numeric' });
    };
    const tick = (ack) => ack >= 3 ? '<i class="ri-check-double-line wx-tick read" aria-label="Read"></i>' : ack === 2 ? '<i class="ri-check-double-line wx-tick" aria-label="Delivered"></i>'
      : ack === 1 ? '<i class="ri-check-line wx-tick" aria-label="Sent"></i>' : '<i class="ri-time-line wx-tick" aria-label="Sending"></i>';
    const kindIcon = { image: 'ri-camera-fill', video: 'ri-video-fill', ptt: 'ri-mic-fill', audio: 'ri-headphone-fill', document: 'ri-file-fill', sticker: 'ri-sticky-note-fill',
      location: 'ri-map-pin-fill', vcard: 'ri-user-fill', poll_creation: 'ri-bar-chart-horizontal-fill', revoked: 'ri-forbid-line' };

    // ── HTTP ──
    async function getJSON(path) {
      const r = await fetch(q(path), { credentials: 'same-origin', cache: 'no-store', signal: ctl.signal });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `Error ${r.status}`); return j;
    }
    async function post(path, data) {
      const fd = data instanceof FormData ? data : new FormData();
      if (!(data instanceof FormData)) Object.entries(data || {}).forEach(([k, v]) => fd.append(k, v));
      fd.append('csrf_token', csrf);
      const r = await fetch(q(path), { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf }, signal: ctl.signal });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `Error ${r.status}`); return j;
    }
    async function api(path, body) {
      const opt = { credentials: 'same-origin', cache: 'no-store', signal: ctl.signal };
      if (body !== undefined) Object.assign(opt, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } });
      const r = await fetch(q('/api/' + path), opt);
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `Error ${r.status}`); return j;
    }
    const chatAction = (jid, action, value) => api(`chats/${enc(jid)}/action`, { action, value });
    const msgAction = (action, id, value, chat) => api('messages/action', { action, id, value, chat });

    // ── small UI helpers: toast, menus, dialogs ──
    function toast(text) {
      const t = document.createElement('div'); t.className = 'wx-toast'; t.textContent = text; app.append(t);
      setTimeout(() => t.remove(), 3200);
    }
    let menuEl = null;
    function closeMenu() { menuEl?.remove(); menuEl = null; }
    function openMenu(anchor, items, opts = {}) {
      closeMenu();
      const m = document.createElement('div'); m.className = 'wx-menu'; m.setAttribute('role', 'menu');
      if (opts.react) {
        const bar = document.createElement('div'); bar.className = 'wx-emobar';
        [...QUICK, '+'].forEach((e) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = e; b.setAttribute('aria-label', e === '+' ? 'More reactions' : 'React ' + e);
          b.addEventListener('click', () => { closeMenu(); opts.react(e); }); bar.append(b); });
        m.append(bar);
      }
      items.filter(Boolean).forEach((it) => {
        if (it === '-') { const s = document.createElement('div'); s.className = 'sep'; m.append(s); return; }
        const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'menuitem'); if (it.danger) b.className = 'danger';
        b.innerHTML = `${it.icon ? `<i class="${it.icon}"></i>` : ''}<span>${esc(it.label)}</span>`;
        b.addEventListener('click', () => { closeMenu(); it.run(); }); m.append(b);
      });
      document.body.append(m);
      const r = anchor.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight;
      let x = opts.alignLeft ? r.left : r.right - w, y = r.bottom + 4;
      if (x < 8) x = 8; if (x + w > innerWidth - 8) x = innerWidth - w - 8;
      if (y + h > innerHeight - 8) y = Math.max(8, r.top - h - 4);
      m.style.left = x + 'px'; m.style.top = y + 'px'; menuEl = m;
      m.querySelector('button')?.focus();
    }
    document.addEventListener('click', (e) => { if (menuEl && !menuEl.contains(e.target)) closeMenu(); }, { ...sig, capture: true });
    window.addEventListener('resize', closeMenu, sig);

    function dialog({ title, html = '', buttons }) {
      return new Promise((resolve) => {
        const wrap = document.createElement('div'); wrap.className = 'wx-modal';
        wrap.innerHTML = `<div class="wx-dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}">${title ? `<h3>${esc(title)}</h3>` : ''}<div class="body">${html}</div><div class="btns"></div></div>`;
        const done = (v) => { wrap.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
        const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
        buttons.forEach((b) => { const el = document.createElement('button'); el.type = 'button'; el.className = 'wx-btn' + (b.ghost ? ' ghost' : '') + (b.danger ? ' danger' : ''); el.textContent = b.label;
          el.addEventListener('click', () => done(b.value === undefined ? b.label : (typeof b.value === 'function' ? b.value(wrap) : b.value))); wrap.querySelector('.btns').append(el); });
        wrap.addEventListener('click', (e) => { if (e.target === wrap) done(null); });
        document.addEventListener('keydown', onKey, true);
        document.body.append(wrap); (wrap.querySelector('input, .btns button:last-child'))?.focus();
      });
    }
    const confirmBox = (title, text, ok = 'OK', danger = false) => dialog({ title, html: `<p>${esc(text)}</p>`, buttons: [{ label: 'Cancel', ghost: true, value: false }, { label: ok, danger, value: true }] });

    // ── avatars: fetched once per jid, lazily as rows scroll into view ──
    const avatarCache = new Map();
    function paintAvatar(el, jid, name, kind) {
      el.innerHTML = kind === 'group' ? '<i class="ri-group-fill"></i>' : kind === 'channel' ? '<i class="ri-broadcast-fill"></i>' : '<i class="ri-user-fill"></i>';
      if (!jid) return;
      if (!avatarCache.has(jid)) avatarCache.set(jid, getJSON('/avatar.json?jid=' + enc(jid)).then((j) => j.url).catch(() => null));
      avatarCache.get(jid).then((url) => { if (url && el.isConnected) el.innerHTML = `<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`; });
    }
    const avIO = 'IntersectionObserver' in window ? new IntersectionObserver((list) => list.forEach((en) => {
      if (en.isIntersecting) { avIO.unobserve(en.target); paintAvatar(en.target, en.target.dataset.av, en.target.dataset.name, en.target.dataset.kind); }
    }), { rootMargin: '150px' }) : null;
    const lazyAv = (box) => box.querySelectorAll('[data-av]').forEach((el) => {
      if (el.dataset.done) return; el.dataset.done = '1';
      el.innerHTML = el.dataset.kind === 'group' ? '<i class="ri-group-fill"></i>' : el.dataset.kind === 'channel' ? '<i class="ri-broadcast-fill"></i>' : '<i class="ri-user-fill"></i>';
      avIO ? avIO.observe(el) : paintAvatar(el, el.dataset.av, el.dataset.name, el.dataset.kind);
    });
    const av = (jid, name, kind, cls = '') => `<span class="wx-av ${cls}" data-av="${esc(jid)}" data-name="${esc(name)}" data-kind="${esc(kind || '')}"></span>`;

    function decorateSearch(scope) {
      scope.querySelectorAll('.wx-search').forEach((lab) => {
        if (lab.querySelector('.wx-clear')) return;
        const input = lab.querySelector('input'); if (!input) return;
        const b = document.createElement('button'); b.type = 'button'; b.className = 'wx-clear'; b.hidden = true; b.setAttribute('aria-label', 'Clear search'); b.innerHTML = '<i class="ri-close-line"></i>';
        b.addEventListener('mousedown', (e) => e.preventDefault());
        b.addEventListener('click', () => { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); input.focus(); });
        input.addEventListener('input', () => { b.hidden = !input.value; });
        lab.append(b);
      });
    }
    decorateSearch(app);
    const searchMO = new MutationObserver(() => decorateSearch(document.body));
    searchMO.observe(document.body, { childList: true });
    ctl.signal.addEventListener('abort', () => searchMO.disconnect());

    // ── layout: fill the window under whatever is above ──
    function fit() {
      if (app.hidden) return;
      const top = Math.max(0, app.getBoundingClientRect().top);
      root.style.setProperty('--wx-h', Math.max(420, innerHeight - top) + 'px');
    }
    window.addEventListener('resize', fit, sig);
    document.getElementById('admin-sidebar-rail')?.addEventListener('click', () => setTimeout(fit, 220), sig);
    document.querySelectorAll('[data-church-pick]').forEach((s) => s.addEventListener('change', (e) => { window.location.href = `/admin/whatsapp?branch=${e.target.value}`; }, sig));

    // ── connection ──
    let state = {}, lastActivity = -1, mode = '', lastAuto = 0;
    async function refreshState() {
      try { state = await getJSON('/state.json'); } catch (e) { if (mode !== 'app') showConnect(e.message); return; }
      if (state.status === 'connected') { showApp(); if (state.activity !== lastActivity) { lastActivity = state.activity; onActivity(); } }
      else { showConnect(state.lastError); autoStart(); }
    }
    async function autoStart() {
      if (state.status !== 'disconnected' && state.status) return;
      if (document.hidden || Date.now() - lastAuto < 60000 || !$('wa-phone-form').hidden) return;
      lastAuto = Date.now();
      try { await post('/connect', {}); state.status = 'connecting'; state.lastError = null; showConnect(); } catch (e) { showConnect(e.message); }
    }
    function showConnect(err) {
      if (mode !== 'connect') { mode = 'connect'; app.hidden = true; root.classList.remove('wa-full'); $('wa-connect').hidden = false; }
      const e = $('wa-connect-error'); e.hidden = !err; e.textContent = err || '';
      const qr = $('wa-qr'), code = $('wa-code'), wait = $('wa-qr-wait');
      if (state.linkCode) { code.textContent = state.linkCode; code.hidden = false; qr.hidden = true; wait.textContent = 'On the phone: Linked devices, Link a device, Link with phone number instead, then enter this code.'; }
      else if (state.status === 'qr' && state.qr) { if (qr.src !== state.qr) qr.src = state.qr; qr.hidden = false; code.hidden = true; wait.textContent = 'Scan with WhatsApp on the phone. The code refreshes by itself.'; }
      else { qr.hidden = true; code.hidden = true; wait.textContent = state.status === 'connecting' ? 'Loading the QR code… (up to a minute the first time)' : (err ? 'The code could not load.' : 'Loading the QR code…'); }
      $('wa-start').hidden = !err;
    }
    function showApp() {
      if (mode === 'app') return;
      mode = 'app'; $('wa-connect').hidden = true; app.hidden = false; root.classList.add('wa-full');
      requestAnimationFrame(fit);
      $('wx-set-phone').textContent = state.phone ? '+' + state.phone : '—';
      $('wx-set-since').textContent = state.connectedAt ? new Date(state.connectedAt).toLocaleString() : '—';
      loadChats(); loadMe();
    }
    $('wa-start').addEventListener('click', async () => { try { await post('/connect', {}); state.status = 'connecting'; showConnect(); } catch (e) { showConnect(e.message); } }, sig);
    $('wa-phone-toggle').addEventListener('click', () => { $('wa-phone-form').hidden = !$('wa-phone-form').hidden; }, sig);
    $('wa-phone-form').addEventListener('submit', async (ev) => { ev.preventDefault(); try { await post('/connect', new FormData(ev.target)); state.status = 'connecting'; showConnect(); } catch (e) { showConnect(e.message); } }, sig);
    $('wa-disconnect').addEventListener('click', async () => {
      if (!await confirmBox('Log out?', 'This church\'s WhatsApp will be unlinked from the console. Scheduled posts will wait until it is linked again.', 'Log out', true)) return;
      try { await post('/disconnect', {}); mode = ''; state = {}; refreshState(); } catch (e) { toast(e.message); }
    }, sig);

    // ── settings (this browser) ──
    const prefs = Object.assign({ notify: false, sound: true, enter: true }, store.get('swm-wa-prefs', {}));
    const savePrefs = () => store.set('swm-wa-prefs', prefs);
    const paintPrefs = () => { $('wx-set-notify').setAttribute('aria-checked', String(prefs.notify && 'Notification' in window && Notification.permission === 'granted'));
      $('wx-set-sound').setAttribute('aria-checked', String(prefs.sound)); $('wx-set-enter').setAttribute('aria-checked', String(prefs.enter)); };
    paintPrefs();
    $('wx-set-notify').addEventListener('click', async () => {
      if (!('Notification' in window)) { toast('This browser does not support notifications.'); return; }
      if (!prefs.notify) { const p = await Notification.requestPermission(); prefs.notify = p === 'granted'; if (p !== 'granted') toast('Notifications are blocked for this site in the browser settings.'); }
      else prefs.notify = false;
      savePrefs(); paintPrefs();
    }, sig);
    function paintSafetySub() {
      const el = $('wx-set-safety-sub'); if (!el || !guard) return;
      el.textContent = guard.pausedUntil ? `Paused until ${new Date(guard.pausedUntil).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}: ${guard.pauseReason}` : `On · today ${guard.used.msg.day} of ${guard.limits.msg.day} messages`;
      el.style.color = guard.pausedUntil ? '#ea0038' : '';
    }
    $('wx-set-safety').addEventListener('click', async () => {
      await loadGuard(); if (!guard) { toast('Could not load the safety limits.'); return; }
      const order = ['msg', 'bulk', 'new', 'cold', 'forward', 'status', 'group_create', 'group_add'];
      const rows = order.map((k) => Object.entries(guard.limits[k]).map(([w, max]) => {
        const n = guard.used[k][w], pct = Math.min(100, Math.round(100 * n / max));
        return `<tr><td>${esc(guard.labels[k])}</td><td style="white-space:nowrap">per ${w}</td><td style="width:34%"><div class="wx-meter"><i class="${pct >= 100 ? 'full' : pct >= 75 ? 'hot' : ''}" style="width:${pct}%"></i></div></td><td style="white-space:nowrap;text-align:right">${n} / ${max}</td></tr>`;
      }).join('')).join('');
      const html = `<p style="margin-bottom:10px">WhatsApp bans numbers that behave like bulk senders. These limits keep this account looking like a person. They cover everything sent from the console.</p>
        ${guard.pausedUntil ? `<p class="wx-err" style="margin-bottom:10px">Paused until ${new Date(guard.pausedUntil).toLocaleString()}: ${esc(guard.pauseReason)}. Bulk sending and first messages wait; replies to existing chats still work.</p>` : ''}
        <table class="wx-limits" style="width:100%">${rows}</table>
        <p style="margin-top:12px;font-size:13px;color:var(--wx-ink3)">Also: Post to many sends a few chats at a time with pauses, at whatever time you choose, takes at most 50 chats (25 people), never messages numbers that are not saved contacts and never chatted, and stops by itself if WhatsApp pushes back.</p>`;
      const v = await dialog({ title: 'Safety limits', html, buttons: guard.pausedUntil ? [{ label: 'Resume now', ghost: true, value: 'resume' }, { label: 'OK', value: null }] : [{ label: 'OK', value: null }] });
      if (v === 'resume' && await confirmBox('Resume sending now?', 'Only do this if you know why WhatsApp pushed back. Resuming too early can get the number banned.', 'Resume', true)) {
        try { await api('guard/resume', {}); toast('Sending resumed'); loadGuard(); } catch (e) { toast(e.message); }
      }
    }, sig);
    $('wx-set-sound').addEventListener('click', () => { prefs.sound = !prefs.sound; savePrefs(); paintPrefs(); }, sig);
    $('wx-set-enter').addEventListener('click', () => { prefs.enter = !prefs.enter; savePrefs(); paintPrefs(); }, sig);
    let audioCtx = null;
    function ding() {
      if (!prefs.sound) return;
      try { audioCtx ||= new (window.AudioContext || window.webkitAudioContext)(); const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.type = 'sine'; o.frequency.setValueAtTime(880, audioCtx.currentTime); o.frequency.setValueAtTime(1320, audioCtx.currentTime + .09);
        g.gain.setValueAtTime(.0001, audioCtx.currentTime); g.gain.exponentialRampToValueAtTime(.18, audioCtx.currentTime + .02); g.gain.exponentialRampToValueAtTime(.0001, audioCtx.currentTime + .3);
        o.connect(g).connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + .32); } catch (_) {}
    }

    // ── side views ──
    const sideViews = () => [...app.querySelectorAll('.wx-side > .wx-view')];
    const railOf = { chats: 'chats', archived: 'chats', newchat: 'chats', newgroup: 'chats', status: 'status', statusnew: 'status', channels: 'channels', post: 'post', scheduled: 'scheduled', starred: 'starred', settings: 'settings', profile: 'profile' };
    let view = 'chats';
    function go(v) {
      view = v;
      sideViews().forEach((el) => { el.hidden = el.dataset.view !== v; });
      app.querySelectorAll('.wx-rail [data-go]').forEach((b) => b.classList.toggle('is-on', b.dataset.go === railOf[v]));
      app.classList.remove('reading');
      if (v === 'archived') loadArchived();
      if (v === 'newchat') openNewChat();
      if (v === 'status') loadStatuses();
      if (v === 'channels') loadChannels();
      if (v === 'post') loadTargets();
      if (v === 'scheduled') loadPosts();
      if (v === 'starred') loadStarred();
      if (v === 'settings' || v === 'profile') loadMe();
      if (v === 'settings') loadGuard();
    }
    app.addEventListener('click', (e) => { const b = e.target.closest('[data-go]'); if (b && app.contains(b)) { e.preventDefault(); go(b.dataset.go); } }, sig);

    // ── chat list ──
    let chats = [], archivedCount = 0, filter = 'all', openJid = null;
    const favs = new Set(store.get(K('favs'), []));
    const drafts = new Map(Object.entries(store.get(K('drafts'), {})));
    const known = new Map();                 // names for chats not in the list yet (new chats, starred, contacts)
    const prevUnread = new Map();
    const chatOf = (jid) => chats.find((c) => c.jid === jid) || channels.find((c) => c.jid === jid) || archived.find((c) => c.jid === jid);
    const people = new Map();               // @lid and unknown ids resolved by the gateway: jid -> { label, name, pushname, phone }
    const nameOf = (jid) => chatOf(jid)?.name || known.get(jid) || (contacts || []).find((c) => c.jid === jid)?.name || people.get(jid)?.label || num(jid) || 'Unknown';
    let peopleBusy = false;
    async function resolvePeople(ids, then) {
      const want = [...new Set(ids.filter((j) => j && !people.has(j) && !(contacts || []).some((c) => c.jid === j)))].slice(0, 300);
      if (!want.length || peopleBusy) return;
      peopleBusy = true;
      try { const r = await api('people', { ids: want }); Object.entries(r.people || {}).forEach(([j, v]) => people.set(j, v)); then?.(); } catch (_) { /* optional */ } finally { peopleBusy = false; }
    }
    const kindOf = (jid) => jid.endsWith('@g.us') ? 'group' : jid.endsWith('@newsletter') ? 'channel' : 'user';
    const muted = (c) => c && c.muted;

    async function loadChats() {
      try {
        const j = await getJSON('/chats.json?filter=' + (filter === 'unread' ? 'unread' : filter === 'groups' ? 'groups' : ''));
        const fresh = (j.chats || []).filter((c) => c.kind !== 'channel');
        notifyNew(fresh);
        chats = fresh;
        archivedCount = j.archivedCount || 0;
        renderChats();
      } catch (e) { $('wx-chatlist').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; }
    }
    function notifyNew(fresh) {
      let total = 0;
      fresh.forEach((c) => {
        const n = c.unread || 0, before = prevUnread.get(c.jid);
        if (n || c.markedUnread) total++;
        if (before !== undefined && n > before && !muted(c) && (document.hidden || c.jid !== openJid)) {
          ding();
          if (prefs.notify && 'Notification' in window && Notification.permission === 'granted') {
            const nt = new Notification(c.name, { body: c.last ? (c.last.who ? c.last.who + ': ' : '') + c.last.text : 'New message', tag: c.jid, icon: '/static/brand/swm-logo-128.png' });
            nt.onclick = () => { window.focus(); openChat(c.jid); nt.close(); };
          }
        }
        prevUnread.set(c.jid, n);
      });
      if (filter === 'all') {
        document.title = (total ? `(${total}) ` : '') + baseTitle;
        $('wx-rail-unread').hidden = !total; $('wx-rail-unread').textContent = total;
      }
    }
    function rowHTML(c, opts = {}) {
      const draft = drafts.get(c.jid);
      let last = '';
      if (opts.sub !== undefined) last = esc(opts.sub);
      else if (draft && c.jid !== openJid) last = `<span class="draft">Draft:</span> ${esc(draft)}`;
      else if (c.last) {
        const icon = kindIcon[c.last.type] ? `<i class="${kindIcon[c.last.type]}"></i> ` : '';
        last = (c.last.fromMe ? tick(c.last.ack) + ' ' : '') + (c.last.who && !c.last.fromMe ? esc(c.last.who) + ': ' : '') + icon + esc(c.last.text || '');
      }
      const unread = c.unread > 0 || c.markedUnread;
      const shown = opts.hl ? hl(c.name, opts.hl) : esc(c.name);
      return `<div class="wx-row ${c.jid === openJid ? 'is-on' : ''}" role="button" tabindex="0" data-jid="${esc(c.jid)}">
        ${av(c.jid, c.name, c.kind)}
        <div class="wx-row-mid">
          <div class="wx-row-top"><span class="wx-row-name">${shown}</span><span class="wx-row-time ${unread && !opts.sub ? 'unread' : ''}">${opts.sub !== undefined ? '' : listTime(c.t)}</span></div>
          <div class="wx-row-bot"><span class="wx-row-last">${last}</span>
            <span class="wx-row-end">${c.muted ? '<i class="ri-volume-mute-fill" aria-label="Muted"></i>' : ''}${c.pinned ? '<i class="ri-pushpin-2-fill" aria-label="Pinned"></i>' : ''}${favs.has(c.jid) && filter !== 'favourites' ? '' : ''}
              ${c.unread > 0 ? `<span class="wx-badge ${c.muted ? 'muted' : ''}">${c.unread}</span>` : c.markedUnread ? '<span class="wx-badge dot"></span>' : ''}
              ${opts.noMenu ? '' : '<button type="button" class="wx-chev" data-chatmenu aria-label="Chat options"><i class="ri-arrow-down-s-line"></i></button>'}</span></div>
        </div></div>`;
    }
    function renderChats() {
      const box = $('wx-chatlist'), needle = $('wx-q').value.trim().toLowerCase();
      let list = chats;
      if (filter === 'favourites') list = list.filter((c) => favs.has(c.jid));
      let similar = false;
      if (needle) { list = rank(list, needle, (c) => [c.name, num(c.jid)]); similar = lastPartial; }
      let html = '';
      if (!needle && filter === 'all' && archivedCount) html += `<button type="button" class="wx-archived" data-go="archived"><i class="ri-inbox-archive-line"></i><span>Archived</span><b>${archivedCount}</b></button>`;
      if (needle) html += list.length ? `<p class="wx-sec">${similar ? `No exact match · similar chats` : 'Chats'}</p>` : '';
      html += list.map((c) => rowHTML(c, needle ? { hl: needle } : {})).join('');
      if (needle && contacts) {
        const inList = new Set(list.map((c) => c.jid));
        const more = rank(contacts.filter((c) => !inList.has(c.jid)), needle, (c) => [c.name, c.pushname, c.phone], 40);
        if (more.length) html += `<p class="wx-sec">${lastPartial ? 'Similar contacts' : 'Contacts'}</p>` + more.map((c) => rowHTML({ jid: c.jid, name: c.name, kind: 'user' }, { sub: c.pushname && c.pushname !== c.name ? '~' + c.pushname : '+' + c.phone, noMenu: true, hl: needle })).join('');
      }
      if (needle && !contacts) html += '<p class="wx-note">Searching contacts…</p>';
      if (!list.length && !html) html = `<p class="wx-note">${needle ? 'No chats, contacts or messages found' : filter === 'unread' ? 'No unread chats' : filter === 'favourites' ? 'Add chats to Favourites from the chat menu (˅).' : filter === 'groups' ? 'No groups' : 'No chats yet'}</p>`;
      box.innerHTML = html; lazyAv(box);
    }
    $('wx-q').addEventListener('input', renderChats, sig);
    $('wx-q').addEventListener('focus', () => { loadContacts().then(() => { if ($('wx-q').value.trim()) renderChats(); }).catch(() => {}); }, sig);
    app.querySelectorAll('.wx-chip').forEach((b) => b.addEventListener('click', () => {
      filter = b.dataset.filter; app.querySelectorAll('.wx-chip').forEach((x) => x.classList.toggle('is-on', x === b));
      if (filter === 'favourites') renderChats(); else { $('wx-chatlist').innerHTML = '<p class="wx-note">Loading…</p>'; loadChats(); }
    }, sig));
    function onListClick(e) {
      const menuBtn = e.target.closest('[data-chatmenu]'), row = e.target.closest('.wx-row');
      if (!row) return;
      if (menuBtn) { e.stopPropagation(); chatMenu(menuBtn, row.dataset.jid); return; }
      openChat(row.dataset.jid);
    }
    ['wx-chatlist', 'wx-archlist', 'wx-chanlist'].forEach((id) => {
      $(id).addEventListener('click', onListClick, sig);
      $(id).addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('wx-row')) { e.preventDefault(); openChat(e.target.dataset.jid); } }, sig);
      $(id).addEventListener('contextmenu', (e) => { const row = e.target.closest('.wx-row'); const btn = row && row.querySelector('[data-chatmenu]'); if (btn) { e.preventDefault(); chatMenu(btn, row.dataset.jid); } }, sig);
    });

    async function act(promise, okText) {
      try { await promise; if (okText) toast(okText); await loadChats(); if (view === 'archived') loadArchived(); return true; }
      catch (e) { toast(e.message); return false; }
    }
    async function muteDialog(jid) {
      const v = await dialog({ title: 'Mute notifications for…', html: `<p style="margin-bottom:8px">Other members will not see that you muted this chat.</p>
          <label class="wx-radio"><input type="radio" name="mute" value="28800" checked> 8 hours</label>
          <label class="wx-radio"><input type="radio" name="mute" value="604800"> 1 week</label>
          <label class="wx-radio"><input type="radio" name="mute" value="always"> Always</label>`,
        buttons: [{ label: 'Cancel', ghost: true, value: null }, { label: 'Mute', value: (w) => w.querySelector('input[name=mute]:checked').value }] });
      if (v) act(chatAction(jid, 'mute', v === 'always' ? 'always' : Number(v)), 'Chat muted');
    }
    async function deleteChat(jid) {
      const c = chatOf(jid), isGroup = kindOf(jid) === 'group';
      if (!await confirmBox(`Delete this chat?`, isGroup ? 'Messages will be removed from this device. Exit the group first to stop receiving them.' : 'Messages will only be removed from this device and your devices on the newer versions of WhatsApp.', 'Delete chat', true)) return;
      if (await act(chatAction(jid, 'delete'), 'Chat deleted') && openJid === jid) closeChat();
      void c;
    }
    async function exitGroup(jid) {
      if (!await confirmBox(`Exit "${nameOf(jid)}" group?`, 'Only group admins will be notified that you left the group.', 'Exit group', true)) return;
      act(chatAction(jid, 'leave'), 'You left the group');
    }
    async function blockChat(jid, block) {
      if (block && !await confirmBox(`Block ${nameOf(jid)}?`, 'Blocked contacts will no longer be able to call you or send you messages.', 'Block', true)) return;
      await act(chatAction(jid, block ? 'block' : 'unblock'), block ? 'Contact blocked' : 'Contact unblocked');
      if (openJid === jid) loadInfoState(jid);
    }
    async function clearChat(jid) {
      if (!await confirmBox('Clear this chat?', 'Messages will be removed from this chat. Starred messages are kept.', 'Clear chat', true)) return;
      if (await act(chatAction(jid, 'clear'), 'Chat cleared') && openJid === jid) openChat(jid, { quiet: true, force: true });
    }
    function chatMenu(anchor, jid) {
      const c = chatOf(jid) || { jid, kind: kindOf(jid) };
      const archivedRow = !!archived.find((x) => x.jid === jid) || c.archived;
      openMenu(anchor, [
        { icon: 'ri-inbox-archive-line', label: archivedRow ? 'Unarchive chat' : 'Archive chat', run: () => act(chatAction(jid, 'archive', !archivedRow), archivedRow ? 'Chat unarchived' : 'Chat archived') },
        c.muted ? { icon: 'ri-volume-up-line', label: 'Unmute notifications', run: () => act(chatAction(jid, 'mute', 0), 'Chat unmuted') }
          : { icon: 'ri-volume-mute-line', label: 'Mute notifications', run: () => muteDialog(jid) },
        !archivedRow && (c.pinned ? { icon: 'ri-unpin-line', label: 'Unpin chat', run: () => act(chatAction(jid, 'pin', false)) }
          : { icon: 'ri-pushpin-line', label: 'Pin chat', run: () => act(chatAction(jid, 'pin', true)) }),
        c.unread > 0 || c.markedUnread ? { icon: 'ri-chat-check-line', label: 'Mark as read', run: () => act(chatAction(jid, 'read')) }
          : { icon: 'ri-chat-unread-line', label: 'Mark as unread', run: () => act(chatAction(jid, 'unread')) },
        favs.has(jid) ? { icon: 'ri-heart-3-line', label: 'Remove from favourites', run: () => { favs.delete(jid); store.set(K('favs'), [...favs]); renderChats(); } }
          : { icon: 'ri-heart-3-line', label: 'Add to favourites', run: () => { favs.add(jid); store.set(K('favs'), [...favs]); renderChats(); toast('Added to favourites'); } },
        '-',
        c.kind === 'group' ? { icon: 'ri-logout-box-r-line', label: 'Exit group', danger: true, run: () => exitGroup(jid) } : null,
        c.kind === 'user' ? { icon: 'ri-forbid-line', label: 'Block', danger: true, run: () => blockChat(jid, true) } : null,
        { icon: 'ri-delete-bin-6-line', label: 'Delete chat', danger: true, run: () => deleteChat(jid) },
      ]);
    }
    $('wx-chats-menu').addEventListener('click', (e) => openMenu(e.currentTarget, [
      { icon: 'ri-group-line', label: 'New group', run: () => { go('newgroup'); openNewGroup(); } },
      { icon: 'ri-star-line', label: 'Starred messages', run: () => go('starred') },
      { icon: 'ri-chat-check-line', label: 'Mark all as read', run: async () => { await Promise.all(chats.filter((c) => c.unread > 0 || c.markedUnread).map((c) => chatAction(c.jid, 'read').catch(() => {}))); loadChats(); } },
      { icon: 'ri-settings-3-line', label: 'Settings', run: () => go('settings') },
      '-',
      { icon: 'ri-logout-box-r-line', label: 'Log out', danger: true, run: () => $('wa-disconnect').click() },
    ]), sig);

    // archived / channels
    let archived = [], channels = [];
    async function loadArchived() {
      try { archived = (await getJSON('/chats.json?archived=1')).chats || []; }
      catch (e) { $('wx-archlist').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; return; }
      archived.forEach((c) => { c.archived = true; });
      $('wx-archlist').innerHTML = archived.length ? '<p class="wx-note" style="padding:14px 24px;text-align:left">These chats stay archived when new messages are received.</p>' + archived.map((c) => rowHTML(c)).join('') : '<p class="wx-note">No archived chats</p>';
      lazyAv($('wx-archlist'));
    }
    async function loadChannels() {
      try { channels = ((await getJSON('/chats.json?filter=channels')).chats || []); }
      catch (e) { $('wx-chanlist').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; return; }
      $('wx-chanlist').innerHTML = channels.length ? channels.map((c) => rowHTML(c, { noMenu: true })).join('') : '<p class="wx-note">Channels you follow appear here.</p>';
      lazyAv($('wx-chanlist'));
    }

    // ── contacts (New chat, search, forward, add member, contact card) ──
    let contacts = null, contactsLoading = null;
    function loadContacts() {
      if (contacts) return Promise.resolve(contacts);
      contactsLoading ||= getJSON('/contacts.json').then((j) => (contacts = j.contacts || [])).catch((e) => { contactsLoading = null; throw e; });
      return contactsLoading;
    }
    const digitsOf = (v) => String(v || '').replace(/\D/g, '');
    const contactRow = (c, extra = '', end = '', query = '') => `<div class="wx-row" role="button" tabindex="0" data-pick="${esc(c.jid)}" data-name="${esc(c.name)}" ${extra}>${av(c.jid, c.name, c.kind || 'user')}
      <div class="wx-row-mid"><div class="wx-row-top"><span class="wx-row-name">${query ? hl(c.name, query) : esc(c.name)}</span></div><div class="wx-row-bot"><span class="wx-row-last">${esc(c.sub ?? (c.pushname && c.pushname !== c.name ? '~' + c.pushname : '+' + (c.phone || phoneOf(c.jid))))}</span>${end}</div></div></div>`;
    const iconRow = (attr, icon, title) => `<div class="wx-row" role="button" tabindex="0" ${attr}><span class="wx-av" style="background:var(--wx-green)"><i class="${icon}"></i></span>
      <div class="wx-row-mid"><div class="wx-row-top"><span class="wx-row-name">${esc(title)}</span></div></div></div>`;
    let checkTimer = null, checkSeq = 0;
    function renderNewChat() {
      const raw = $('wx-nc-q').value.trim(), ql = raw.toLowerCase(), d = digitsOf(raw), box = $('wx-nc-list');
      let html = '';
      if (!ql) {
        html += iconRow('data-ncgroup', 'ri-group-fill', 'New group');
        if (state.phone) html += contactRow({ jid: state.phone + '@c.us', name: (me.name || 'You') + ' (You)', sub: 'Message yourself' });
      }
      if (!contacts) { box.innerHTML = html + '<p class="wx-note">Loading contacts…</p>'; lazyAv(box); return; }
      const list = ql ? rank(contacts, raw, (c) => [c.name, c.pushname, c.phone], 200) : contacts;
      const similarNC = ql && lastPartial;
      const isNumber = d.length >= 7 && /^[+\d\s()-]+$/.test(raw);
      if (isNumber && !list.some((c) => c.phone === d)) html += `<p class="wx-sec">Phone number</p><div id="wx-nc-num"><p class="wx-note">Checking +${esc(d)} on WhatsApp…</p></div>`;
      if (list.length) {
        html += `<p class="wx-sec">${ql ? (similarNC ? `No exact match for '${esc(raw)}' · similar contacts` : 'Contacts') : 'Contacts on WhatsApp'}</p>`;
        let letter = '';
        list.slice(0, ql ? 200 : 3000).forEach((c) => {
          const L = (c.name[0] || '#').toUpperCase(), key = /\p{L}/u.test(L) ? L : '#';
          if (!ql && key !== letter) { letter = key; html += `<p class="wx-letter">${esc(letter)}</p>`; }
          html += contactRow(c, '', '', ql ? raw : '');
        });
      }
      const groups = ql ? rank(chats.filter((c) => c.kind === 'group'), raw, (c) => [c.name], 30) : [];
      if (groups.length) html += '<p class="wx-sec">Groups</p>' + groups.map((c) => contactRow({ jid: c.jid, name: c.name, kind: 'group', sub: 'Group' })).join('');
      if (ql && !list.length && !groups.length && !isNumber) html += `<p class="wx-note">No results found for '${esc(raw)}'</p>`;
      box.innerHTML = html; lazyAv(box);
      clearTimeout(checkTimer);
      if (isNumber && $('wx-nc-num')) {
        const seq = ++checkSeq;
        checkTimer = setTimeout(async () => {
          let r; try { r = await getJSON('/check.json?to=' + d); } catch (e) { r = { exists: null }; }
          const el = $('wx-nc-num'); if (!el || seq !== checkSeq) return;
          const jid = r.jid && r.jid.endsWith('@c.us') ? r.jid : d + '@c.us';
          el.innerHTML = r.exists === false ? `<p class="wx-note">+${esc(d)} is not on WhatsApp. Check the number and the country code.</p>` : contactRow({ jid, name: '+' + d, sub: 'Message this number' });
          lazyAv(el);
        }, 450);
      }
    }
    function openNewChat() {
      $('wx-nc-q').value = ''; renderNewChat(); setTimeout(() => $('wx-nc-q').focus(), 30);
      loadContacts().then(() => { if (view === 'newchat') renderNewChat(); }).catch((e) => toast(e.message));
    }
    $('wx-new').addEventListener('click', () => go('newchat'), sig);
    $('wx-nc-q').addEventListener('input', renderNewChat, sig);
    $('wx-nc-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const f = $('wx-nc-list').querySelector('[data-pick]'); if (f) { e.preventDefault(); f.click(); } } }, sig);
    $('wx-nc-list').addEventListener('click', (e) => {
      if (e.target.closest('[data-ncgroup]')) { go('newgroup'); openNewGroup(); return; }
      const r = e.target.closest('[data-pick]'); if (!r) return;
      known.set(r.dataset.pick, r.dataset.name.replace(/ \(You\)$/, ''));
      go('chats'); openChat(r.dataset.pick);
    }, sig);

    // New group
    const members = new Map();
    function renderMembers() {
      const ql = $('wx-ng-q').value.trim().toLowerCase(), d = digitsOf(ql);
      void d; const list = rank(contacts || [], ql, (c) => [c.name, c.pushname, c.phone], 500);
      $('wx-ng-list').innerHTML = list.length ? list.map((c) => contactRow(c, `role="checkbox" aria-checked="${members.has(c.jid)}"`, '<span class="wx-check">' + (members.has(c.jid) ? '<i class="ri-check-line"></i>' : '') + '</span>', ql)).join('')
        : `<p class="wx-note">${contacts ? 'No contacts found' : 'Loading contacts…'}</p>`;
      lazyAv($('wx-ng-list'));
      $('wx-ng-picked').innerHTML = [...members].map(([jid, n]) => `<span class="wx-pill">${esc(n)}<button type="button" data-unpick="${esc(jid)}" aria-label="Remove ${esc(n)}"><i class="ri-close-line"></i></button></span>`).join('');
      $('wx-ng-next').hidden = !members.size;
    }
    function openNewGroup() {
      members.clear(); $('wx-ng-1').hidden = false; $('wx-ng-2').hidden = true; $('wx-ng-title').textContent = 'Add group members';
      $('wx-ng-q').value = ''; renderMembers(); setTimeout(() => $('wx-ng-q').focus(), 30);
      loadContacts().then(() => { if (view === 'newgroup') renderMembers(); }).catch(() => {});
    }
    $('wx-ng-q').addEventListener('input', renderMembers, sig);
    $('wx-ng-list').addEventListener('click', (e) => { const r = e.target.closest('[data-pick]'); if (!r) return;
      members.has(r.dataset.pick) ? members.delete(r.dataset.pick) : members.set(r.dataset.pick, r.dataset.name); renderMembers(); $('wx-ng-q').focus(); }, sig);
    $('wx-ng-picked').addEventListener('click', (e) => { const b = e.target.closest('[data-unpick]'); if (b) { members.delete(b.dataset.unpick); renderMembers(); } }, sig);
    $('wx-ng-next').addEventListener('click', () => { $('wx-ng-1').hidden = true; $('wx-ng-2').hidden = false; $('wx-ng-title').textContent = 'New group';
      $('wx-ng-count').textContent = `Members: ${[...members.values()].join(', ')}`; $('wx-ng-err').hidden = true; $('wx-ng-2').querySelector('input').focus(); }, sig);
    $('wx-ng-back').addEventListener('click', () => { if (!$('wx-ng-2').hidden) { $('wx-ng-2').hidden = true; $('wx-ng-1').hidden = false; $('wx-ng-title').textContent = 'Add group members'; } else go('newchat'); }, sig);
    $('wx-ng-2').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target); members.forEach((_, jid) => fd.append('participants', jid));
      const btn = e.target.querySelector('.wx-fab'); btn.disabled = true; $('wx-ng-err').hidden = true;
      try { const r = await post('/groups', fd); e.target.reset(); go('chats'); await loadChats(); if (r.jid) { known.set(r.jid, String(fd.get('name'))); openChat(r.jid); } }
      catch (err) { $('wx-ng-err').textContent = err.message; $('wx-ng-err').hidden = false; }
      finally { btn.disabled = false; }
    }, sig);

    // ── conversation ──
    let msgs = [], hasOlder = false, loadingOlder = false, replyTo = null, editing = null, info = null, presence = null, unreadAtOpen = 0, aroundMode = false;
    const box = $('wx-msgs');
    const isGroup = () => openJid && openJid.endsWith('@g.us');
    const atBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight < 90;
    // Group senders: the saved name, else WhatsApp's "~ pushname", else the number (never a hidden @lid id)
    const senderName = (m) => {
      const saved = (contacts || []).find((c) => c.jid === m.author)?.name || people.get(m.author)?.name;
      if (saved) return saved;
      if (m.authorName) return '~ ' + m.authorName;
      return people.get(m.author)?.label || num(m.author) || 'Unknown';
    };

    function bubble(m, first) {
      const me = m.fromMe;
      const media = m.media && !['ptt', 'audio', 'document'].includes(m.type);
      const src = m.media ? q('/media?id=' + enc(m.id)) : '';
      let body = '', cls = media ? 'media' : '';
      if (m.type === 'sticker') { cls = 'sticker media'; body = `<img class="wx-img" style="width:150px;height:150px;min-height:0;object-fit:contain;background:none" src="${src}" alt="Sticker" loading="lazy">`; }
      else if (m.type === 'image') body = `<img class="wx-img" data-view-media="${esc(m.id)}" src="${src}" alt="${esc(m.text || 'Photo')}" loading="lazy">`;
      else if (m.type === 'video') body = `<div class="wx-vid"><video preload="metadata" controls src="${src}"></video></div>`;
      else if (m.type === 'ptt' || m.type === 'audio') body = `<div class="wx-voice">${m.type === 'ptt' ? av(me ? (me0.jid || '') : m.author || openJid, me ? (me0.name || 'You') : senderName(m), 'user', 'sm') : '<i class="ri-headphone-fill" style="font-size:26px;color:#ff2e74"></i>'}<audio controls preload="none" src="${src}"></audio></div>`;
      else if (m.type === 'document') body = `<a class="wx-doc" href="${src}" download="${esc(m.filename || 'file')}"><i class="ri-file-text-fill"></i><span><b>${esc(m.filename || 'Document')}</b><small>${esc((m.mimetype || '').split('/').pop().toUpperCase())}</small></span><i class="ri-download-2-line" style="font-size:20px;color:var(--wx-ink3);margin-left:auto"></i></a>`;
      else if (m.type === 'location' && m.location) body = `<a class="wx-loc" target="_blank" rel="noopener" href="https://maps.google.com/?q=${m.location.lat},${m.location.lng}"><i class="ri-map-pin-fill"></i><span>${esc(m.location.title || 'Location')}</span></a>`;
      else if (m.vcard) body = `<div class="wx-card">${av(m.vcard.phone ? m.vcard.phone + '@c.us' : '', m.vcard.name, 'user')}<b style="font-weight:500">${esc(m.vcard.name || 'Contact')}</b></div>${m.vcard.phone ? `<button type="button" class="wx-card-act" data-msg-contact="${esc(m.vcard.phone)}" data-cname="${esc(m.vcard.name)}">Message</button>` : ''}`;
      else if (m.poll) body = `<div class="wx-poll"><h4>${fmt(m.poll.name)}</h4><p class="hint"><i class="ri-checkbox-multiple-line"></i>${m.poll.multi ? 'Select one or more' : 'Select one'}</p><ul>${m.poll.options.map((o) => `<li><span>${esc(o)}</span><b data-opt="${esc(o)}"></b></li>`).join('')}</ul><button type="button" class="wx-card-act" data-votes="${esc(m.id)}">View votes</button></div>`;
      else if (m.type === 'revoked') body = `<span class="wx-deleted"><i class="ri-forbid-line"></i>${me ? 'You deleted this message' : 'This message was deleted'}</span>`;
      const link = m.link ? `<a class="wx-linkcard" href="${esc(m.link.url)}" target="_blank" rel="noopener"><b>${esc(m.link.title)}</b><small>${esc(m.link.desc)}</small></a>` : '';
      const text = m.text && !m.poll && m.type !== 'revoked' ? `<div class="wx-text ${media ? 'wx-cap' : ''}">${fmt(m.text)}</div>` : '';
      const metaIn = `${m.starred ? '<i class="ri-star-fill" aria-label="Starred"></i>' : ''}${m.edited ? '<span>Edited</span>' : ''}<span>${clock(m.t)}</span>${me ? tick(m.ack) : ''}`;
      const overMedia = media && !m.text;
      const author = isGroup() && !me && first ? `<div class="wx-author" style="color:${nameColor(m.author)}">${esc(senderName(m))}</div>` : '';
      const fwd = m.forwarded ? '<div class="wx-fwd"><i class="ri-share-forward-fill"></i>Forwarded</div>' : '';
      const quote = m.quoted ? `<button type="button" class="wx-quote" data-jump="${esc(m.quoted.stanza)}"><b>${esc(m.quoted.who ? (m.quoted.who === me0.jid ? 'You' : nameOf(m.quoted.who)) : '')}</b>${esc(m.quoted.text)}</button>` : '';
      const reacts = m.reactions?.length ? `<div class="wx-reacts">${m.reactions.map((r) => `<button type="button" class="${r.mine ? 'mine' : ''}" data-react-toggle="${esc(r.emoji)}" data-mine="${r.mine ? 1 : 0}" title="${r.mine ? 'Tap to remove' : 'React'}">${esc(r.emoji)}${r.count > 1 ? `<b>${r.count}</b>` : ''}</button>`).join('')}</div>` : '';
      return `<div class="wx-m ${me ? 'me' : ''} ${first ? 'first' : ''}" data-id="${esc(m.id)}">
        <div class="wx-b ${cls}">${author}${fwd}${quote}${link}${body}${text}<span class="wx-meta ${overMedia ? 'over' : ''}">${metaIn}</span>
          <button type="button" class="wx-mchev" data-msgmenu aria-label="Message options"><i class="ri-arrow-down-s-line"></i></button>
          ${m.type !== 'revoked' && !readOnly() ? '<button type="button" class="wx-mreact" data-msgreact aria-label="React"><i class="ri-emotion-line"></i></button>' : ''}</div>${reacts}</div>`;
    }
    function render(keep = 'bottom') {
      const prevH = box.scrollHeight, prevTop = box.scrollTop, wasBottom = atBottom();
      let html = hasOlder ? '<div class="wx-more"><button type="button" id="wx-older">Load earlier messages</button></div>' : '';
      let lastDay = '', lastKey = '';
      const unreadFrom = unreadAtOpen ? msgs.length - unreadAtOpen : -1;
      msgs.forEach((m, i) => {
        const d = dayLabel(m.t);
        if (d !== lastDay) { html += `<div class="wx-day"><span>${esc(d)}</span></div>`; lastDay = d; lastKey = ''; }
        if (i === unreadFrom && unreadFrom > 0) { html += `<div class="wx-unread-line" id="wx-unread-line"><span>${unreadAtOpen} unread message${unreadAtOpen === 1 ? '' : 's'}</span></div>`; lastKey = ''; }
        const key = (m.fromMe ? 'me' : m.author || 'x');
        html += bubble(m, key !== lastKey).replace('<div class="wx-m ', `<div data-day="${esc(d)}" class="wx-m `); lastKey = key;
      });
      if (!msgs.length) html += `<div class="wx-sys"><span><i class="ri-lock-fill"></i> Messages are end-to-end encrypted. No one outside of this chat, not even WhatsApp, can read or listen to them.</span></div>`;
      box.innerHTML = html; lazyAv(box);
      if (isGroup()) {
        const unknown = msgs.filter((m) => !m.fromMe && m.author && !m.authorName && !(contacts || []).some((c) => c.jid === m.author)).map((m) => m.author);
        if (unknown.some((j) => !people.has(j))) resolvePeople(unknown, () => { if (isGroup()) render('stay'); });
      }
      if (keep === 'unread' && $('wx-unread-line')) $('wx-unread-line').scrollIntoView({ block: 'center' });
      else if (keep === 'top') box.scrollTop = box.scrollHeight - prevH + prevTop;
      else if (keep === 'stay' && !wasBottom) box.scrollTop = prevTop;
      else box.scrollTop = box.scrollHeight;
      updateDown();
    }
    let unseenBelow = 0;
    function updateDown() { const show = !atBottom(); $('wx-down').hidden = !show; if (!show) unseenBelow = 0; $('wx-down-n').hidden = !unseenBelow; $('wx-down-n').textContent = unseenBelow; }
    let dayTimer = null;
    function floatDay() {
      const chip = $('wx-float-day'); if (!chip) return;
      const top = box.getBoundingClientRect().top + 8;
      const first = [...box.querySelectorAll('.wx-m')].find((el) => el.getBoundingClientRect().bottom > top + 30);
      const sep = [...box.querySelectorAll('.wx-day')].find((el) => Math.abs(el.getBoundingClientRect().top - top) < 34);
      if (!first || sep) { chip.hidden = true; return; }
      chip.querySelector('span').textContent = first.dataset.day || ''; chip.hidden = false;
      clearTimeout(dayTimer); dayTimer = setTimeout(() => { chip.hidden = true; }, 1400);
    }
    box.addEventListener('scroll', () => { updateDown(); floatDay(); if (box.scrollTop < 60 && hasOlder && !loadingOlder) loadOlder(); }, sig);
    $('wx-down').addEventListener('click', () => { if (aroundMode) { aroundMode = false; refreshOpen(true); } else box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' }); }, sig);

    const readOnly = () => { const c = chatOf(openJid); return !!(c && c.readOnly) || kindOf(openJid || '') === 'channel' || !!(info && info.kind === 'group' && info.announce && !info.iAmAdmin); };
    function paintComposer() {
      const ro = readOnly(), blocked = info && info.blocked;
      $('wx-compose').hidden = ro || blocked; $('wx-rec').hidden = true;
      $('wx-readonly').hidden = !(ro || blocked);
      $('wx-readonly').textContent = blocked ? `You blocked this contact. Unblock it from the contact info to send a message.` : kindOf(openJid || '') === 'channel' ? 'Only channel admins can send messages here.' : 'Only admins can send messages';
    }
    function headSub() {
      const el = $('wx-head-sub'); el.classList.remove('typing');
      const kind = kindOf(openJid || '');
      if (presence && kind === 'user' && (presence.state === 'composing' || presence.state === 'typing')) { el.textContent = 'typing…'; el.classList.add('typing'); return; }
      if (presence && kind === 'user' && presence.state === 'recording_audio') { el.textContent = 'recording audio…'; el.classList.add('typing'); return; }
      if (presence && kind === 'group' && presence.typing?.length) { const t = presence.typing[0]; el.textContent = `${nameOf(t.who).split(' ')[0]} is ${t.type === 'recording_audio' ? 'recording audio' : 'typing'}…`; el.classList.add('typing'); return; }
      if (kind === 'user') {
        if (presence?.online) { el.textContent = 'online'; return; }
        if (presence?.lastSeen) { const d = new Date(presence.lastSeen * 1000); el.textContent = `last seen ${sameDay(d, new Date()) ? 'today' : listTime(presence.lastSeen).toLowerCase()} at ${clock(presence.lastSeen)}`; return; }
        el.textContent = 'click here for contact info'; return;
      }
      if (kind === 'group') { el.textContent = info?.participants ? info.participants.map((p) => p.me ? 'You' : (p.name || (p.phone ? '+' + p.phone : (p.pushname ? '~' + p.pushname : '')))).filter(Boolean).slice(0, 30).join(', ') : 'click here for group info'; return; }
      el.textContent = 'Channel';
    }

    async function openChat(jid, opt = {}) {
      if (!jid) return;
      const switching = openJid !== jid || opt.force;
      if (switching) {
        saveDraft(); openJid = jid; info = null; presence = null; msgs = []; hasOlder = false; aroundMode = false; clearReply(); cancelEdit(); closePickers();
        const c = chatOf(jid);
        unreadAtOpen = c ? (c.unread || 0) : 0;
        $('wx-intro').hidden = true; $('wx-convo').hidden = false; app.classList.add('reading');
        $('wx-head-name').textContent = nameOf(jid); paintAvatar($('wx-head-av'), jid, nameOf(jid), kindOf(jid)); headSub();
        box.innerHTML = '<div class="wx-sys"><span>Loading messages…</span></div>';
        $('wx-text').value = drafts.get(jid) || ''; autoGrow(); paintSend(); paintComposer();
        app.querySelectorAll('.wx-row.is-on').forEach((r) => r.classList.remove('is-on'));
        app.querySelectorAll(`.wx-row[data-jid="${CSS.escape(jid)}"]`).forEach((r) => r.classList.add('is-on'));
        if (app.classList.contains('with-info')) openInfo(infoView === 'search' ? 'search' : 'info');
        loadInfoState(jid);
        if (kindOf(jid) !== 'channel') chatAction(jid, 'presence').catch(() => {});
        startPresence();
        if (!opt.quiet && innerWidth > 860) setTimeout(() => $('wx-text').focus(), 50);
      }
      try {
        const j = await getJSON('/messages.json?jid=' + enc(jid));
        if (openJid !== jid) return;
        if (j.name && !chatOf(jid)) { known.set(jid, j.name); $('wx-head-name').textContent = j.name; }
        const incoming = j.messages || [];
        hasOlder = incoming.length >= 40;
        msgs = incoming;
        render(switching && unreadAtOpen ? 'unread' : switching ? 'bottom' : 'stay');
        const c = chatOf(jid);
        if (c && (c.unread || c.markedUnread)) { c.unread = 0; c.markedUnread = false; post('/seen', { jid }).catch(() => {}); renderChats(); }
      } catch (e) { if (openJid === jid) box.innerHTML = `<div class="wx-sys"><span>${esc(e.message)}</span></div>`; }
    }
    async function refreshOpen(toBottom) {
      if (!openJid || aroundMode) return;
      const jid = openJid;
      try {
        const j = await getJSON('/messages.json?jid=' + enc(jid));
        if (openJid !== jid || aroundMode) return;
        const latest = j.messages || [];
        if (!latest.length) { msgs = []; render(); return; }
        const firstId = latest[0].id, idx = msgs.findIndex((m) => m.id === firstId);
        const before = idx > 0 ? msgs.slice(0, idx) : (idx === 0 ? [] : msgs.length > latest.length ? [] : []);
        const oldIds = new Set(msgs.map((m) => m.id));
        const newIn = latest.filter((m) => !oldIds.has(m.id) && !m.fromMe).length;
        const sig1 = JSON.stringify(msgs.slice(-latest.length)), next = idx >= 0 ? before.concat(latest) : latest;
        if (JSON.stringify(next.slice(-latest.length)) === sig1 && !toBottom) return;
        if (idx < 0) hasOlder = latest.length >= 40;
        const bottom = atBottom();
        msgs = next; unreadAtOpen = 0;
        render(toBottom || bottom ? 'bottom' : 'stay');
        if (!bottom && newIn) { unseenBelow += newIn; updateDown(); }
        if (newIn && !document.hidden) post('/seen', { jid }).catch(() => {});
      } catch (_) { /* next tick */ }
    }
    async function loadOlder() {
      if (!msgs.length) return;
      loadingOlder = true;
      try {
        const j = await getJSON(`/messages.json?jid=${enc(openJid)}&before=${enc(msgs[0].id)}`);
        const older = (j.messages || []).filter((m) => !msgs.some((x) => x.id === m.id));
        hasOlder = (j.messages || []).length >= 40;
        msgs = older.concat(msgs); render('top');
      } catch (_) { /* ignore */ } finally { loadingOlder = false; }
    }
    async function jumpTo(idOrStanza) {
      const el = [...box.querySelectorAll('.wx-m')].find((x) => x.dataset.id === idOrStanza || x.dataset.id.endsWith('_' + idOrStanza) || x.dataset.id.includes('_' + idOrStanza + '_'));
      if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.remove('hl'); void el.offsetWidth; el.classList.add('hl'); return; }
      if (!idOrStanza.includes('_')) { toast('That message is further back. Scroll up to load earlier messages.'); return; }
      try {
        const j = await api(`chats/${enc(openJid)}/around/${enc(idOrStanza)}`);
        aroundMode = true; msgs = j.messages || []; hasOlder = true; unreadAtOpen = 0; render('stay');
        $('wx-down').hidden = false;
        requestAnimationFrame(() => jumpTo(idOrStanza));
      } catch (e) { toast(e.message); }
    }
    function closeChat() {
      saveDraft(); openJid = null; info = null; msgs = []; $('wx-convo').hidden = true; $('wx-intro').hidden = false;
      app.classList.remove('reading', 'with-info'); clearInterval(window.__swmWaPresence);
      app.querySelectorAll('.wx-row.is-on').forEach((r) => r.classList.remove('is-on'));
    }
    $('wx-back').addEventListener('click', closeChat, sig);
    $('wx-who').addEventListener('click', () => openInfo('info'), sig);
    $('wx-head-search').addEventListener('click', () => openInfo('search'), sig);
    $('wx-head-menu').addEventListener('click', (e) => {
      const jid = openJid, kind = kindOf(jid), c = chatOf(jid) || {};
      openMenu(e.currentTarget, [
        { icon: 'ri-information-line', label: kind === 'group' ? 'Group info' : kind === 'channel' ? 'Channel info' : 'Contact info', run: () => openInfo('info') },
        { icon: 'ri-search-line', label: 'Search', run: () => openInfo('search') },
        kind !== 'channel' && (c.muted ? { icon: 'ri-volume-up-line', label: 'Unmute notifications', run: () => act(chatAction(jid, 'mute', 0), 'Chat unmuted') }
          : { icon: 'ri-volume-mute-line', label: 'Mute notifications', run: () => muteDialog(jid) }),
        { icon: 'ri-close-circle-line', label: 'Close chat', run: closeChat },
        '-',
        kind !== 'channel' && { icon: 'ri-eraser-line', label: 'Clear chat', danger: true, run: () => clearChat(jid) },
        kind === 'user' && (info?.blocked ? { icon: 'ri-forbid-line', label: 'Unblock', run: () => blockChat(jid, false) } : { icon: 'ri-forbid-line', label: 'Block', danger: true, run: () => blockChat(jid, true) }),
        kind === 'group' && { icon: 'ri-logout-box-r-line', label: 'Exit group', danger: true, run: () => exitGroup(jid) },
        kind !== 'channel' && { icon: 'ri-delete-bin-6-line', label: 'Delete chat', danger: true, run: () => deleteChat(jid) },
      ]);
    }, sig);

    // presence: online / last seen / typing…
    function startPresence() {
      clearInterval(window.__swmWaPresence);
      const jid = openJid; if (!jid || kindOf(jid) === 'channel') return;
      const tickP = async () => {
        if (document.hidden || openJid !== jid) return;
        try { presence = await api(`chats/${enc(jid)}/presence`); if (openJid === jid) headSub(); } catch (_) {}
      };
      tickP(); window.__swmWaPresence = setInterval(tickP, 5000);
    }

    // message actions
    function msgById(id) { return msgs.find((m) => m.id === id); }
    const canEdit = (m) => m.fromMe && m.type === 'chat' && Date.now() / 1000 - m.t < 15 * 60;
    const canDeleteForAll = (m) => (m.fromMe || (info && info.iAmAdmin)) && m.type !== 'revoked' && Date.now() / 1000 - m.t < 60 * 3600;
    async function react(m, emoji) {
      if (emoji === '+') { openEmoji((e) => react(m, e), true); return; }
      try { await msgAction('react', m.id, emoji); await refreshOpen(); } catch (e) { toast(e.message); }
    }
    async function deleteMsg(m) {
      const all = canDeleteForAll(m);
      const v = await dialog({ title: 'Delete message?', buttons: [
        all ? { label: 'Delete for everyone', ghost: true, value: 'all' } : null, { label: 'Delete for me', ghost: true, value: 'me' }, { label: 'Cancel', value: null }].filter(Boolean) });
      if (!v) return;
      try { await msgAction('delete', m.id, v === 'all', openJid); msgs = msgs.filter((x) => v === 'all' || x.id !== m.id); await refreshOpen(); if (v === 'me') render('stay'); } catch (e) { toast(e.message); }
    }
    async function forwardMsg(m) {
      await loadContacts().catch(() => {});
      const pool = [...chats.map((c) => ({ jid: c.jid, name: c.name, kind: c.kind })), ...(contacts || []).filter((c) => !chats.some((x) => x.jid === c.jid)).map((c) => ({ jid: c.jid, name: c.name, kind: 'user' }))];
      const picked = new Set();
      const html = `<label class="wx-search" style="margin:0 0 8px"><i class="ri-search-line"></i><input id="wx-fw-q" type="search" placeholder="Search name or number"></label><div id="wx-fw-list" style="max-height:46vh;overflow-y:auto"></div><p class="wx-note" id="wx-fw-n" style="padding:8px 0 0;text-align:left">You can forward to up to 5 chats.</p>`;
      const p = dialog({ title: 'Forward message to', html, buttons: [{ label: 'Cancel', ghost: true, value: null }, { label: 'Forward', value: () => [...picked] }] });
      const list = document.getElementById('wx-fw-list'), qEl = document.getElementById('wx-fw-q');
      const draw = () => { const ql = qEl.value.trim().toLowerCase();
        list.innerHTML = rank(pool, ql, (c) => [c.name, num(c.jid)], 150).map((c) => contactRow({ ...c, sub: c.kind === 'group' ? 'Group' : num(c.jid) }, `role="checkbox" aria-checked="${picked.has(c.jid)}"`, '<span class="wx-check">' + (picked.has(c.jid) ? '<i class="ri-check-line"></i>' : '') + '</span>', ql)).join('') || '<p class="wx-note">No chats or contacts found</p>';
        lazyAv(list); };
      qEl.addEventListener('input', draw); list.addEventListener('click', (e) => { const r = e.target.closest('[data-pick]'); if (!r) return;
        if (picked.has(r.dataset.pick)) picked.delete(r.dataset.pick); else if (picked.size < 5) picked.add(r.dataset.pick); else toast('You can only share with up to 5 chats'); draw(); });
      draw();
      const to = await p;
      if (!to || !to.length) return;
      try { await msgAction('forward', m.id, to); toast(`Message forwarded`); loadChats(); } catch (e) { toast(e.message); }
    }
    function msgMenu(anchor, m, onlyReact) {
      const ro = readOnly();
      if (onlyReact) { openMenu(anchor, [], { react: (e) => react(m, e) }); return; }
      openMenu(anchor, [
        !ro && m.type !== 'revoked' && { icon: 'ri-reply-line', label: 'Reply', run: () => setReply(m) },
        m.text && { icon: 'ri-file-copy-line', label: 'Copy', run: () => navigator.clipboard?.writeText(m.text).then(() => toast('Message copied')) },
        m.type !== 'revoked' && { icon: 'ri-share-forward-line', label: 'Forward', run: () => forwardMsg(m) },
        m.media && { icon: 'ri-download-2-line', label: 'Download', run: () => { const a = document.createElement('a'); a.href = q('/media?id=' + enc(m.id)); a.download = m.filename || ''; a.click(); } },
        !ro && m.type !== 'revoked' && { icon: m.pinned ? 'ri-unpin-line' : 'ri-pushpin-line', label: m.pinned ? 'Unpin' : 'Pin', run: async () => { try { await msgAction('pin', m.id, !m.pinned); toast(m.pinned ? 'Message unpinned' : 'Message pinned'); refreshOpen(); } catch (e) { toast(e.message); } } },
        m.type !== 'revoked' && { icon: m.starred ? 'ri-star-off-line' : 'ri-star-line', label: m.starred ? 'Unstar' : 'Star', run: async () => { try { await msgAction('star', m.id, !m.starred); m.starred = !m.starred; render('stay'); } catch (e) { toast(e.message); } } },
        canEdit(m) && { icon: 'ri-pencil-line', label: 'Edit', run: () => startEdit(m) },
        isGroup() && !m.fromMe && m.author && { icon: 'ri-chat-private-line', label: 'Reply privately', run: () => { const who = m.author; known.set(who, senderName(m)); openChat(who).then(() => setReply(m)); } },
        '-',
        m.type !== 'revoked' && !ro && { icon: 'ri-delete-bin-6-line', label: 'Delete', danger: true, run: () => deleteMsg(m) },
      ], { react: ro || m.type === 'revoked' ? null : (e) => react(m, e) });
    }
    box.addEventListener('click', async (e) => {
      const el = e.target.closest('.wx-m'); const m = el && msgById(el.dataset.id);
      if (e.target.id === 'wx-older') { loadOlder(); return; }
      if (!m) return;
      if (e.target.closest('[data-msgmenu]')) { e.stopPropagation(); msgMenu(e.target.closest('[data-msgmenu]'), m); return; }
      if (e.target.closest('[data-msgreact]')) { e.stopPropagation(); msgMenu(e.target.closest('[data-msgreact]'), m, true); return; }
      const rt = e.target.closest('[data-react-toggle]'); if (rt) { react(m, rt.dataset.mine === '1' ? '' : rt.dataset.reactToggle); return; }
      const j = e.target.closest('[data-jump]'); if (j && j.dataset.jump) { jumpTo(j.dataset.jump); return; }
      if (e.target.closest('[data-view-media]')) { viewMedia(m.id); return; }
      const mc = e.target.closest('[data-msg-contact]'); if (mc) { known.set(mc.dataset.msgContact + '@c.us', mc.dataset.cname); openChat(mc.dataset.msgContact + '@c.us'); return; }
      const vb = e.target.closest('[data-votes]');
      if (vb) {
        try { const { votes } = await msgAction('votes', m.id);
          const tally = {}; votes.forEach((v) => v.options.forEach((o) => { tally[o] = (tally[o] || []).concat(v.who); }));
          el.querySelectorAll('[data-opt]').forEach((b) => { b.textContent = (tally[b.dataset.opt] || []).length; });
          dialog({ title: 'Poll details', html: m.poll.options.map((o) => `<p style="margin:10px 0 4px"><b>${esc(o)}</b> · ${(tally[o] || []).length} vote${(tally[o] || []).length === 1 ? '' : 's'}</p><p style="color:var(--wx-ink3)">${(tally[o] || []).map((w) => esc(w === me0.jid ? 'You' : nameOf(w))).join(', ')}</p>`).join(''), buttons: [{ label: 'OK', value: null }] });
        } catch (err) { toast(err.message); }
      }
    }, sig);
    box.addEventListener('contextmenu', (e) => { const el = e.target.closest('.wx-m'); const m = el && msgById(el.dataset.id); if (m && !e.target.closest('a')) { e.preventDefault(); msgMenu(el.querySelector('[data-msgmenu]'), m); } }, sig);
    box.addEventListener('dblclick', (e) => { const el = e.target.closest('.wx-m'); const m = el && msgById(el.dataset.id); if (m && !readOnly() && innerWidth > 860 && !e.target.closest('a,audio,video')) { window.getSelection()?.removeAllRanges(); setReply(m); } }, sig);

    // media viewer (photos and videos of this chat)
    function viewMedia(id) {
      const items = msgs.filter((m) => m.type === 'image' || m.type === 'video');
      let i = Math.max(0, items.findIndex((m) => m.id === id));
      const v = document.createElement('div'); v.className = 'wx-viewer'; v.setAttribute('role', 'dialog'); v.setAttribute('aria-label', 'Media viewer');
      const draw = () => { const m = items[i]; const src = q('/media?id=' + enc(m.id));
        v.innerHTML = `<div class="wx-viewer-top">${av(m.fromMe ? me0.jid : (m.author || openJid), m.fromMe ? 'You' : senderName(m), 'user', 'sm')}<div class="who">${esc(m.fromMe ? 'You' : (isGroup() ? senderName(m) : nameOf(openJid)))}<small>${esc(dayLabel(m.t))} at ${clock(m.t)}</small></div>
          <a class="ic" href="${src}" download aria-label="Download"><i class="ri-download-2-line"></i></a><button type="button" class="ic" data-x aria-label="Close"><i class="ri-close-line"></i></button></div>
          <div class="wx-viewer-body" style="position:relative">${i > 0 ? '<button type="button" class="ic" data-prev style="position:absolute;left:10px;background:var(--wx-chip)" aria-label="Previous"><i class="ri-arrow-left-s-line"></i></button>' : ''}
          ${m.type === 'video' ? `<video src="${src}" controls autoplay></video>` : `<img src="${src}" alt="">`}
          ${i < items.length - 1 ? '<button type="button" class="ic" data-next style="position:absolute;right:10px;background:var(--wx-chip)" aria-label="Next"><i class="ri-arrow-right-s-line"></i></button>' : ''}</div>
          ${m.text ? `<p style="text-align:center;padding:0 10% 24px;color:var(--wx-ink)">${fmt(m.text)}</p>` : ''}`;
        lazyAv(v); };
      const close = () => { v.remove(); document.removeEventListener('keydown', key, true); };
      const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } if (e.key === 'ArrowLeft' && i > 0) { i--; draw(); } if (e.key === 'ArrowRight' && i < items.length - 1) { i++; draw(); } };
      v.addEventListener('click', (e) => { if (e.target.closest('[data-x]') || e.target === v.querySelector('.wx-viewer-body')) close(); else if (e.target.closest('[data-prev]')) { i--; draw(); } else if (e.target.closest('[data-next]')) { i++; draw(); } });
      document.addEventListener('keydown', key, true);
      draw(); document.body.append(v);
    }

    // ── composer ──
    const text = $('wx-text');
    function autoGrow() { text.style.height = 'auto'; text.style.height = Math.min(text.scrollHeight, 120) + 'px'; }
    function paintSend() { $('wx-send').innerHTML = text.value.trim() || editing ? '<i class="ri-send-plane-2-fill"></i>' : '<i class="ri-mic-line"></i>'; $('wx-send').setAttribute('aria-label', text.value.trim() || editing ? 'Send' : 'Record a voice message'); $('wx-send').style.color = text.value.trim() || editing ? 'var(--wx-green)' : ''; }
    function saveDraft() {
      if (!openJid || editing) return;
      const v = text.value.trim(); if (v) drafts.set(openJid, v); else drafts.delete(openJid);
      store.set(K('drafts'), Object.fromEntries(drafts));
    }
    let typingTimer = null, typingSent = 0;
    text.addEventListener('input', () => {
      autoGrow(); paintSend();
      if (!openJid || kindOf(openJid) === 'channel') return;
      if (Date.now() - typingSent > 3500) { typingSent = Date.now(); chatAction(openJid, 'typing', 'typing').catch(() => {}); }
      clearTimeout(typingTimer); typingTimer = setTimeout(() => { typingSent = 0; if (openJid) chatAction(openJid, 'typing', 'paused').catch(() => {}); saveDraft(); }, 3000);
    }, sig);
    text.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing && ((prefs.enter && !e.shiftKey) || (!prefs.enter && (e.ctrlKey || e.metaKey)))) { e.preventDefault(); $('wx-compose').requestSubmit(); }
      if (e.key === 'Escape' && editing) { e.preventDefault(); cancelEdit(); }
      if (e.key === 'ArrowUp' && !text.value && !editing) { const mine = [...msgs].reverse().find(canEdit); if (mine) { e.preventDefault(); startEdit(mine); } }
    }, sig);
    function setReply(m) {
      cancelEdit(); replyTo = m;
      $('wx-reply-q').innerHTML = `<b style="color:${m.fromMe ? '#06cf9c' : nameColor(m.author)}">${esc(m.fromMe ? 'You' : (isGroup() ? senderName(m) : nameOf(openJid)))}</b>${kindIcon[m.type] ? `<i class="${kindIcon[m.type]}"></i> ` : ''}${esc(m.text || ({ image: 'Photo', video: 'Video', ptt: 'Voice message', audio: 'Audio', document: m.filename || 'Document', sticker: 'Sticker', location: 'Location', poll_creation: 'Poll' }[m.type] || ''))}`;
      $('wx-reply').hidden = false; text.focus();
    }
    function clearReply() { replyTo = null; $('wx-reply').hidden = true; }
    function startEdit(m) {
      clearReply(); editing = m;
      $('wx-reply-q').innerHTML = `<b style="color:var(--wx-green)">Edit message</b>${esc(m.text)}`; $('wx-reply').hidden = false;
      text.value = m.text; autoGrow(); paintSend(); text.focus();
    }
    function cancelEdit() { if (!editing) return; editing = null; $('wx-reply').hidden = true; text.value = drafts.get(openJid) || ''; autoGrow(); paintSend(); }
    $('wx-reply-x').addEventListener('click', () => { if (editing) cancelEdit(); else clearReply(); }, sig);

    $('wx-compose').addEventListener('submit', async (e) => {
      e.preventDefault();
      const to = openJid, body = text.value;
      if (!to) return;
      if (!body.trim() && !editing) { startRecording(); return; }
      if (editing) {
        const m = editing; editing = null; $('wx-reply').hidden = true; text.value = ''; autoGrow(); paintSend();
        if (body.trim() && body !== m.text) { try { await msgAction('edit', m.id, body); m.text = body; m.edited = true; render('stay'); } catch (err) { toast(err.message); } }
        return;
      }
      const fd = new FormData(); fd.append('to', to); fd.append('text', body);
      if (replyTo) fd.append('quotedId', replyTo.id);
      const pending = { id: 'pending-' + Date.now(), t: Math.floor(Date.now() / 1000), fromMe: true, type: 'chat', text: body, ack: 0, quoted: replyTo ? { text: replyTo.text || '', stanza: '', who: replyTo.fromMe ? me0.jid : replyTo.author } : null };
      msgs.push(pending); render('bottom');
      text.value = ''; autoGrow(); paintSend(); clearReply(); drafts.delete(to); store.set(K('drafts'), Object.fromEntries(drafts));
      chatAction(to, 'typing', 'paused').catch(() => {});
      try { await post('/send', fd); if (openJid === to) await refreshOpen(true); loadChats(); }
      catch (err) { msgs = msgs.filter((m) => m !== pending); render('stay'); text.value = body; autoGrow(); paintSend(); toast(err.message); }
    }, sig);

    // attachments: menu, preview with caption, then send
    function closePickers() { $('wx-attach').hidden = true; $('wx-emoji').hidden = true; $('wx-attach-btn').classList.remove('on'); }
    $('wx-attach-btn').addEventListener('click', (e) => { e.stopPropagation(); const open = $('wx-attach').hidden; closePickers(); $('wx-attach').hidden = !open; $('wx-attach-btn').classList.toggle('on', open); }, sig);
    document.addEventListener('click', (e) => { if (!e.target.closest('#wx-attach, #wx-attach-btn, #wx-emoji, #wx-emoji-btn')) closePickers(); }, sig);
    $('wx-attach').addEventListener('click', (e) => {
      const b = e.target.closest('[data-attach]'); if (!b) return; closePickers();
      const k = b.dataset.attach;
      if (k === 'doc') $('wx-f-doc').click(); else if (k === 'media') $('wx-f-media').click(); else if (k === 'audio') $('wx-f-audio').click();
      else if (k === 'poll') pollDialog(); else if (k === 'contact') contactDialog(); else if (k === 'location') sendLocation();
    }, sig);
    let queue = [];
    function previewFiles(files, opts = {}) {
      queue = [...files].slice(0, 30); if (!queue.length) return;
      const f = queue[0];
      $('wx-pv-name').textContent = queue.length > 1 ? `${queue.length} files` : f.name;
      const url = URL.createObjectURL(f);
      $('wx-pv-body').innerHTML = f.type.startsWith('image/') && !opts.doc ? `<img src="${url}" alt="">` : f.type.startsWith('video/') && !opts.doc ? `<video src="${url}" controls></video>`
        : `<div class="wx-preview-doc"><i class="${f.type.startsWith('audio/') ? 'ri-headphone-fill' : 'ri-file-text-fill'}"></i><p>${esc(f.name)}</p><p>${(f.size / 1048576).toFixed(1)} MB</p></div>`;
      $('wx-pv-voice-wrap').hidden = !f.type.startsWith('audio/'); $('wx-pv-voice').checked = false;
      $('wx-pv-cap').value = text.value; $('wx-pv-cap').hidden = f.type.startsWith('audio/');
      $('wx-preview').hidden = false; $('wx-pv-cap').focus();
    }
    ['wx-f-doc', 'wx-f-media', 'wx-f-audio'].forEach((id) => $(id).addEventListener('change', (e) => { previewFiles(e.target.files, { doc: id === 'wx-f-doc' }); e.target.value = ''; }, sig));
    $('wx-pv-x').addEventListener('click', () => { $('wx-preview').hidden = true; queue = []; }, sig);
    $('wx-pv-cap').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('wx-pv-send').click(); } }, sig);
    $('wx-pv-send').addEventListener('click', async () => {
      const to = openJid, files = queue, cap = $('wx-pv-cap').value, voice = $('wx-pv-voice').checked;
      $('wx-preview').hidden = true; queue = []; if (cap === text.value) { text.value = ''; autoGrow(); paintSend(); }
      for (let n = 0; n < files.length; n++) {
        const fd = new FormData(); fd.append('to', to); fd.append('text', n === 0 ? cap : ''); fd.append('file', files[n]);
        if (voice) fd.append('voice', '1');
        if (n === 0 && replyTo) fd.append('quotedId', replyTo.id);
        toast(`Sending ${files[n].name}…`);
        try { await post('/send', fd); } catch (err) { toast(err.message); break; }
      }
      clearReply(); if (openJid === to) refreshOpen(true); loadChats();
    }, sig);
    // drag and drop onto the conversation
    $('wx-main').addEventListener('dragover', (e) => { if (openJid && !readOnly()) e.preventDefault(); }, sig);
    $('wx-main').addEventListener('drop', (e) => { if (!openJid || readOnly()) return; e.preventDefault(); if (e.dataTransfer.files.length) previewFiles(e.dataTransfer.files); }, sig);
    text.addEventListener('paste', (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); previewFiles(files); } }, sig);

    async function pollDialog() {
      const html = `<div class="wx-form" style="padding:0"><label><span>Question</span><input class="wx-input" id="wx-poll-q" maxlength="255" placeholder="Ask question"></label>
        <div id="wx-poll-opts" style="display:grid;gap:6px"><span style="color:var(--wx-green-d);font-size:14px">Options</span></div>
        <label class="wx-radio"><input type="checkbox" id="wx-poll-multi" checked> Allow multiple answers</label></div>`;
      const p = dialog({ title: 'Create poll', html, buttons: [{ label: 'Cancel', ghost: true, value: null },
        { label: 'Send', value: (w) => ({ question: w.querySelector('#wx-poll-q').value, options: [...w.querySelectorAll('[data-popt]')].map((i) => i.value).filter((v) => v.trim()), multiple: w.querySelector('#wx-poll-multi').checked }) }] });
      const optsBox = document.getElementById('wx-poll-opts');
      const addOpt = () => { const i = document.createElement('input'); i.className = 'wx-input'; i.placeholder = '+ Add'; i.maxLength = 100; i.dataset.popt = '1';
        i.addEventListener('input', () => { const all = [...optsBox.querySelectorAll('[data-popt]')]; if (all[all.length - 1].value && all.length < 12) addOpt(); }); optsBox.append(i); };
      addOpt(); addOpt();
      const v = await p;
      if (!v) return;
      try { await api('send-extra', { kind: 'poll', to: openJid, ...v }); refreshOpen(true); loadChats(); } catch (e) { toast(e.message); }
    }
    async function contactDialog() {
      await loadContacts().catch(() => {});
      let chosen = null;
      const p = dialog({ title: 'Send contact', html: `<label class="wx-search" style="margin:0 0 8px"><i class="ri-search-line"></i><input id="wx-ct-q" type="search" placeholder="Search name or number"></label><div id="wx-ct-list" style="max-height:50vh;overflow-y:auto"></div>`,
        buttons: [{ label: 'Cancel', ghost: true, value: null }, { label: 'Send', value: () => chosen }] });
      const list = document.getElementById('wx-ct-list'), qEl = document.getElementById('wx-ct-q');
      const draw = () => { const ql = qEl.value.trim().toLowerCase(), d = digitsOf(ql);
        void d; list.innerHTML = rank(contacts || [], ql, (c) => [c.name, c.pushname, c.phone], 150).map((c) => contactRow(c, `role="radio" aria-checked="${chosen?.jid === c.jid}"`, '<span class="wx-check" style="border-radius:50%">' + (chosen?.jid === c.jid ? '<i class="ri-check-line"></i>' : '') + '</span>', ql)).join('') || '<p class="wx-note">No contacts found</p>'; lazyAv(list); };
      qEl.addEventListener('input', draw); list.addEventListener('click', (e) => { const r = e.target.closest('[data-pick]'); if (r) { chosen = { jid: r.dataset.pick, name: r.dataset.name }; draw(); } });
      draw();
      const c = await p; if (!c) return;
      try { await api('send-extra', { kind: 'contact', to: openJid, contact: c.jid, name: c.name }); refreshOpen(true); } catch (e) { toast(e.message); }
    }
    function sendLocation() {
      if (!navigator.geolocation) { toast('This browser cannot share a location.'); return; }
      toast('Finding your location…');
      navigator.geolocation.getCurrentPosition(async (pos) => {
        const ok = await confirmBox('Send your current location?', `Accurate to ${Math.round(pos.coords.accuracy)} metres.`, 'Send');
        if (!ok) return;
        try { await api('send-extra', { kind: 'location', to: openJid, lat: pos.coords.latitude, lng: pos.coords.longitude, title: '' }); refreshOpen(true); } catch (e) { toast(e.message); }
      }, () => toast('Location permission was refused.'), { enableHighAccuracy: true, timeout: 15000 });
    }

    // emoji picker (composer and reactions)
    let emojiTarget = null;
    function openEmoji(onPick, asReaction) {
      emojiTarget = onPick || null;
      const recent = store.get('swm-wa-emoji-recent', []);
      const tabs = Object.keys(EMOJI);
      const panel = $('wx-emoji');
      panel.innerHTML = `<div class="wx-emoji-tabs">${recent.length ? '<button type="button" data-etab="recent" aria-label="Recent"><i class="ri-time-line"></i></button>' : ''}${tabs.map((t) => `<button type="button" data-etab="${t}" aria-label="${EMOJI[t][0]}"><i class="${t}"></i></button>`).join('')}</div><div class="wx-emoji-grid" id="wx-emoji-grid"></div>`;
      const grid = panel.querySelector('#wx-emoji-grid');
      grid.innerHTML = (recent.length ? `<h5 data-esec="recent">Recent</h5>${recent.map((e) => `<button type="button" data-e="${e}">${e}</button>`).join('')}` : '') +
        tabs.map((t) => `<h5 data-esec="${t}">${EMOJI[t][0]}</h5>` + EMOJI[t][1].split(' ').map((e) => `<button type="button" data-e="${e}">${e}</button>`).join('')).join('');
      panel.querySelectorAll('[data-etab]').forEach((b) => b.addEventListener('click', () => { grid.querySelector(`[data-esec="${b.dataset.etab}"]`)?.scrollIntoView({ block: 'start' }); panel.querySelectorAll('[data-etab]').forEach((x) => x.classList.toggle('is-on', x === b)); }));
      $('wx-attach').hidden = true; panel.hidden = false; panel.dataset.reaction = asReaction ? '1' : '';
    }
    $('wx-emoji').addEventListener('click', (e) => {
      const b = e.target.closest('[data-e]'); if (!b) return; const em = b.dataset.e;
      const recent = [em, ...store.get('swm-wa-emoji-recent', []).filter((x) => x !== em)].slice(0, 24); store.set('swm-wa-emoji-recent', recent);
      if (emojiTarget) { const f = emojiTarget; emojiTarget = null; $('wx-emoji').hidden = true; f(em); return; }
      const s = text.selectionStart ?? text.value.length, en = text.selectionEnd ?? s;
      text.value = text.value.slice(0, s) + em + text.value.slice(en); text.selectionStart = text.selectionEnd = s + em.length; autoGrow(); paintSend(); text.focus();
    }, sig);
    $('wx-emoji-btn').addEventListener('click', (e) => { e.stopPropagation(); if (!$('wx-emoji').hidden && !emojiTarget) { $('wx-emoji').hidden = true; return; } openEmoji(null); }, sig);

    // voice messages
    let rec = null, recChunks = [], recStart = 0, recTimer = null;
    async function startRecording() {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast('This browser cannot record audio.'); return; }
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (_) { toast('Allow microphone access to record a voice message.'); return; }
      const type = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
      rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined); recChunks = [];
      rec.ondataavailable = (ev) => { if (ev.data.size) recChunks.push(ev.data); };
      rec.start(250); recStart = Date.now();
      $('wx-compose').hidden = true; $('wx-rec').hidden = false;
      const paint = () => { const s = Math.floor((Date.now() - recStart) / 1000); $('wx-rec-t').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
      paint(); recTimer = setInterval(paint, 500);
      if (openJid) chatAction(openJid, 'typing', 'recording').catch(() => {});
    }
    function stopRecording() {
      return new Promise((resolve) => {
        if (!rec) return resolve(null);
        const r = rec; rec = null; clearInterval(recTimer);
        r.onstop = () => { r.stream.getTracks().forEach((t) => t.stop()); resolve(new Blob(recChunks, { type: r.mimeType || 'audio/webm' })); };
        r.stop();
        $('wx-rec').hidden = true; paintComposer();
        if (openJid) chatAction(openJid, 'typing', 'paused').catch(() => {});
      });
    }
    $('wx-rec-x').addEventListener('click', () => stopRecording(), sig);
    $('wx-rec-send').addEventListener('click', async () => {
      const to = openJid, blob = await stopRecording(); if (!blob || blob.size < 1200) { toast('The recording was too short.'); return; }
      const ext = (blob.type || '').includes('ogg') ? 'ogg' : 'webm';
      const fd = new FormData(); fd.append('to', to); fd.append('text', ''); fd.append('voice', '1'); fd.append('file', new File([blob], `voice-note.${ext}`, { type: blob.type }));
      if (replyTo) fd.append('quotedId', replyTo.id);
      try { await post('/send', fd); clearReply(); if (openJid === to) refreshOpen(true); loadChats(); } catch (e) { toast(e.message); }
    }, sig);

    // ── right drawer: contact / group info, search, shared media ──
    let infoView = 'info';
    async function loadInfoState(jid) {
      try { const i = await api(`chats/${enc(jid)}/info`); if (openJid !== jid) return; info = i;
        if (i.name && !chatOf(jid)) known.set(jid, i.name);
        headSub(); paintComposer(); if (app.classList.contains('with-info') && infoView === 'info') drawInfo(); } catch (_) { /* optional */ }
    }
    function openInfo(v) {
      infoView = v; app.classList.add('with-info');
      $('wx-info-title').textContent = v === 'search' ? 'Search messages' : v === 'shared' ? 'Media, links and docs' : kindOf(openJid || '') === 'group' ? 'Group info' : kindOf(openJid || '') === 'channel' ? 'Channel info' : 'Contact info';
      if (v === 'search') drawSearch(); else if (v === 'shared') drawShared('media'); else drawInfo();
    }
    $('wx-info-x').addEventListener('click', () => { if (infoView === 'shared') openInfo('info'); else app.classList.remove('with-info'); }, sig);
    function drawInfo() {
      const b = $('wx-info-body'), jid = openJid, i = info;
      if (!i) { b.innerHTML = '<p class="wx-note">Loading…</p>'; return; }
      const c = chatOf(jid) || {};
      let html = `<div class="wx-card-sec center">${av(jid, i.name, i.kind, 'lg')}<h3>${esc(i.name)}</h3>`;
      if (i.kind === 'user') html += `<p class="muted">+${esc(i.phone)}</p>${i.pushname && i.pushname !== i.name ? `<p class="muted">~${esc(i.pushname)}</p>` : ''}${i.business ? '<p class="muted">Business account</p>' : ''}`;
      if (i.kind === 'group') html += `<p class="muted">Group · ${i.count} member${i.count === 1 ? '' : 's'}</p>`;
      if (i.kind === 'channel') html += '<p class="muted">Channel</p>';
      html += `<div class="wx-actions"><button type="button" data-i="search"><i class="ri-search-line"></i>Search</button>${i.kind === 'group' && i.iAmAdmin ? '<button type="button" data-i="add"><i class="ri-user-add-line"></i>Add</button>' : ''}</div></div>`;
      if (i.kind === 'group') {
        html += `<div class="wx-card-sec"><div class="lbl">Group description${i.iAmAdmin ? '<button type="button" data-i="desc">Edit</button>' : ''}</div><p class="wx-text">${i.description ? fmt(i.description) : '<span style="color:var(--wx-ink3)">Add group description</span>'}</p>
          ${i.created ? `<p class="muted" style="font-size:13px;margin-top:10px">Created ${new Date(i.created * 1000).toLocaleDateString()}</p>` : ''}</div>`;
      }
      if (i.kind === 'user') html += `<div class="wx-card-sec"><div class="lbl">About</div><p>${esc(i.about || '—')}</p></div>`;
      html += `<div class="wx-card-sec" id="wx-i-media"><div class="lbl">Media, links and docs <button type="button" data-i="shared">View all</button></div><div class="wx-media-grid" id="wx-i-thumbs"><span class="wx-note" style="grid-column:1/-1;padding:6px 0">Loading…</span></div></div>`;
      if (i.kind !== 'channel') html += `<div class="wx-card-sec" style="padding:0"><button type="button" class="wx-act-row" data-i="mute"><i class="${c.muted ? 'ri-volume-up-line' : 'ri-volume-mute-line'}"></i>${c.muted ? 'Unmute notifications' : 'Mute notifications'}</button></div>`;
      if (i.kind === 'group') {
        html += `<div class="wx-card-sec" style="padding:16px 0 6px"><div class="lbl" style="padding:0 30px">${i.count} members${i.participants.length > 8 ? '<button type="button" data-i="msearch"><i class="ri-search-line"></i></button>' : ''}</div>
          ${i.iAmAdmin ? '<button type="button" class="wx-act-row" data-i="add"><span class="wx-av sm" style="background:var(--wx-green)"><i class="ri-user-add-fill"></i></span>Add member</button><button type="button" class="wx-act-row" data-i="invite"><span class="wx-av sm" style="background:var(--wx-green)"><i class="ri-links-line"></i></span>Invite to group via link</button>' : ''}
          <div id="wx-i-members">${membersHTML(i.participants)}</div></div>`;
        if (i.iAmAdmin) html += `<div class="wx-card-sec" style="padding:0"><button type="button" class="wx-act-row" data-i="announce" role="switch" aria-checked="${i.announce}"><i class="ri-chat-off-line"></i><span style="flex:1">Only admins can send messages</span><span class="wx-toggle"></span></button>
          <button type="button" class="wx-act-row" data-i="subject"><i class="ri-pencil-line"></i>Change group name</button>
          <label class="wx-act-row" style="cursor:pointer"><i class="ri-image-edit-line"></i>Change group icon<input type="file" accept="image/jpeg,image/png,image/webp" data-i-icon hidden></label></div>`;
      }
      if (i.kind === 'user' && i.common?.length) html += `<div class="wx-card-sec" style="padding:16px 0 6px"><div class="lbl" style="padding:0 30px">${i.common.length} group${i.common.length === 1 ? '' : 's'} in common</div>${i.common.map((g) => `<button type="button" class="wx-member" data-open="${esc(g.jid)}" data-name="${esc(g.name)}">${av(g.jid, g.name, 'group', 'sm')}<span class="m-mid"><b>${esc(g.name)}</b></span></button>`).join('')}</div>`;
      html += '<div class="wx-card-sec" style="padding:0">';
      if (i.kind === 'group') html += '<button type="button" class="wx-act-row danger" data-i="leave"><i class="ri-logout-box-r-line"></i>Exit group</button>';
      if (i.kind === 'user') html += `<button type="button" class="wx-act-row ${i.blocked ? '' : 'danger'}" data-i="block"><i class="ri-forbid-line"></i>${i.blocked ? 'Unblock' : 'Block'} ${esc(i.name)}</button>`;
      if (i.kind !== 'channel') html += `<button type="button" class="wx-act-row danger" data-i="clear"><i class="ri-eraser-line"></i>Clear chat</button><button type="button" class="wx-act-row danger" data-i="delete"><i class="ri-delete-bin-6-line"></i>Delete chat</button>`;
      html += '</div>';
      b.innerHTML = html; lazyAv(b);
      api(`chats/${enc(jid)}/shared?kind=media`).then((r) => { if (openJid !== jid) return; const el = $('wx-i-thumbs'); if (!el) return;
        el.innerHTML = r.items.length ? r.items.slice(0, 6).map((m) => `<button type="button" data-imedia="${esc(m.id)}">${m.type === 'video' ? '<span style="display:grid;place-items:center;height:100%;font-size:30px;color:var(--wx-ink3)"><i class="ri-video-fill"></i></span>' : `<img src="${q('/media?id=' + enc(m.id))}" alt="" loading="lazy">`}</button>`).join('') : '<span class="wx-note" style="grid-column:1/-1;padding:6px 0;text-align:left">No media yet</span>'; }).catch(() => {});
    }
    const memberLabel = (p) => p.me ? 'You' : (p.name || (p.phone ? '+' + p.phone : (p.pushname ? '~' + p.pushname : 'Unknown')));
    const membersHTML = (list) => list.map((p) => `<button type="button" class="wx-member" data-member="${esc(p.jid)}">${av(p.jid, memberLabel(p), 'user', 'sm')}
      <span class="m-mid"><b>${esc(memberLabel(p))}</b><small>${!p.me && p.name && p.phone ? '+' + esc(p.phone) : (!p.me && !p.name && p.phone && p.pushname ? '~' + esc(p.pushname) : '')}</small></span>${p.admin ? `<span class="adm">${p.owner ? 'Group owner' : 'Group admin'}</span>` : ''}</button>`).join('');
    $('wx-info-body').addEventListener('click', async (e) => {
      const jid = openJid, i = info;
      const thumb = e.target.closest('[data-imedia]'); if (thumb) { const id = thumb.dataset.imedia; if (!msgById(id)) { const r = await api(`chats/${enc(jid)}/shared?kind=media`).catch(() => ({ items: [] })); r.items.forEach((m) => { if (!msgById(m.id)) msgs.unshift(m); }); } viewMedia(id); return; }
      const og = e.target.closest('[data-open]'); if (og) { known.set(og.dataset.open, og.dataset.name); openChat(og.dataset.open); return; }
      const mem = e.target.closest('[data-member]');
      if (mem) {
        const p = i.participants.find((x) => x.jid === mem.dataset.member); if (!p || p.me) return;
        const msgJid = p.phone ? p.phone + '@c.us' : p.jid;
        const gAct = async (action, okText) => { try { await api(`groups/${enc(jid)}/action`, { action, value: [p.jid] }); toast(okText); loadInfoState(jid); } catch (err) { toast(err.message); } };
        openMenu(mem, [
          { icon: 'ri-chat-1-line', label: `Message ${memberLabel(p)}`, run: () => { known.set(msgJid, memberLabel(p)); openChat(msgJid); } },
          i.iAmAdmin && (p.admin ? { icon: 'ri-shield-user-line', label: 'Dismiss as admin', run: () => gAct('demote', 'Dismissed as admin') } : { icon: 'ri-shield-user-line', label: 'Make group admin', run: () => gAct('promote', 'Now a group admin') }),
          i.iAmAdmin && { icon: 'ri-user-unfollow-line', label: 'Remove', danger: true, run: async () => { if (await confirmBox(`Remove ${memberLabel(p)} from "${i.name}" group?`, '', 'Remove', true)) gAct('remove', 'Removed from the group'); } },
        ], { alignLeft: true });
        return;
      }
      const btn = e.target.closest('[data-i]'); if (!btn) return;
      const k = btn.dataset.i;
      if (k === 'search') openInfo('search');
      else if (k === 'shared') openInfo('shared');
      else if (k === 'mute') { const c = chatOf(jid); if (c?.muted) act(chatAction(jid, 'mute', 0), 'Chat unmuted').then(drawInfo); else muteDialog(jid); }
      else if (k === 'block') blockChat(jid, !i.blocked);
      else if (k === 'clear') clearChat(jid);
      else if (k === 'delete') deleteChat(jid);
      else if (k === 'leave') exitGroup(jid);
      else if (k === 'msearch') {
        const box2 = btn.closest('.wx-card-sec'); if (box2.querySelector('#wx-msq')) { box2.querySelector('#wx-msq').focus(); return; }
        box2.querySelector('.lbl').insertAdjacentHTML('afterend', '<label class="wx-search" style="margin:0 20px 8px"><i class="ri-search-line"></i><input id="wx-msq" type="search" placeholder="Search members" autocomplete="off"></label>');
        const inp = box2.querySelector('#wx-msq'); inp.focus(); decorateSearch(box2);
        inp.addEventListener('input', () => { $('wx-i-members').innerHTML = membersHTML(rank(i.participants, inp.value, (p) => [p.name, p.pushname, p.phone], 1024)); lazyAv($('wx-i-members')); });
      }
      else if (k === 'desc' || k === 'subject') {
        const v = await dialog({ title: k === 'desc' ? 'Group description' : 'Group name', html: k === 'desc' ? `<textarea class="wx-input" id="wx-gd" rows="5" maxlength="2048">${esc(i.description)}</textarea>` : `<input class="wx-input" id="wx-gd" maxlength="100" value="${esc(i.name)}">`,
          buttons: [{ label: 'Cancel', ghost: true, value: null }, { label: 'Save', value: (w) => w.querySelector('#wx-gd').value }] });
        if (v === null) return;
        try { await api(`groups/${enc(jid)}/action`, { action: k === 'desc' ? 'description' : 'subject', value: v }); toast('Saved'); if (k === 'subject') { const c = chatOf(jid); if (c) c.name = v; $('wx-head-name').textContent = v; loadChats(); } loadInfoState(jid); } catch (err) { toast(err.message); }
      }
      else if (k === 'announce') { try { await api(`groups/${enc(jid)}/action`, { action: 'announce', value: !i.announce }); loadInfoState(jid); } catch (err) { toast(err.message); } }
      else if (k === 'invite') {
        try { const r = await api(`groups/${enc(jid)}/action`, { action: 'invite' });
          const v = await dialog({ title: 'Invite to group via link', html: `<p style="margin-bottom:10px">Anyone with WhatsApp can follow this link to join this group. Only share it with people you trust.</p><p style="word-break:break-all;color:var(--wx-green-d)">${esc(r.link)}</p>`,
            buttons: [{ label: 'Reset link', ghost: true, value: 'reset' }, { label: 'Copy link', value: 'copy' }] });
          if (v === 'copy') navigator.clipboard?.writeText(r.link).then(() => toast('Link copied'));
          if (v === 'reset' && await confirmBox('Reset this link?', 'The current link will stop working.', 'Reset link', true)) { await api(`groups/${enc(jid)}/action`, { action: 'revoke' }); toast('Link reset'); }
        } catch (err) { toast(err.message); }
      }
      else if (k === 'add') {
        await loadContacts().catch(() => {});
        const inGroup = new Set(i.participants.map((p) => p.jid)), picked = new Set();
        const p = dialog({ title: 'Add member', html: `<label class="wx-search" style="margin:0 0 8px"><i class="ri-search-line"></i><input id="wx-am-q" type="search" placeholder="Search name or number"></label><div id="wx-am-list" style="max-height:50vh;overflow-y:auto"></div>`,
          buttons: [{ label: 'Cancel', ghost: true, value: null }, { label: 'Add', value: () => [...picked] }] });
        const list = document.getElementById('wx-am-list'), qEl = document.getElementById('wx-am-q');
        const draw = () => { const ql = qEl.value.trim().toLowerCase(), d = digitsOf(ql);
          void d; list.innerHTML = rank((contacts || []).filter((c) => !inGroup.has(c.jid)), ql, (c) => [c.name, c.pushname, c.phone], 200).map((c) => contactRow(c, `role="checkbox" aria-checked="${picked.has(c.jid)}"`, '<span class="wx-check">' + (picked.has(c.jid) ? '<i class="ri-check-line"></i>' : '') + '</span>', ql)).join('') || '<p class="wx-note">No contacts found</p>'; lazyAv(list); };
        qEl.addEventListener('input', draw); list.addEventListener('click', (ev) => { const r = ev.target.closest('[data-pick]'); if (r) { picked.has(r.dataset.pick) ? picked.delete(r.dataset.pick) : picked.add(r.dataset.pick); draw(); } });
        draw();
        const add = await p; if (!add || !add.length) return;
        try { await api(`groups/${enc(jid)}/action`, { action: 'add', value: add }); toast('Added'); loadInfoState(jid); } catch (err) { toast(err.message); }
      }
    }, sig);
    $('wx-info-body').addEventListener('change', async (e) => {
      if (!e.target.matches('[data-i-icon]') || !e.target.files[0]) return;
      const fd = new FormData(); fd.append('target', 'group'); fd.append('jid', openJid); fd.append('file', e.target.files[0]);
      try { await post('/photo', fd); avatarCache.delete(openJid); paintAvatar($('wx-head-av'), openJid, nameOf(openJid), 'group'); toast('Group icon changed'); drawInfo(); } catch (err) { toast(err.message); }
    }, sig);

    function drawSearch() {
      const b = $('wx-info-body');
      b.innerHTML = `<div style="background:var(--wx-bg);padding:10px 0"><label class="wx-search"><i class="ri-search-line"></i><input id="wx-ms-q" type="search" placeholder="Search…" autocomplete="off"></label></div><div id="wx-ms-res"><p class="wx-note">Search for messages with ${esc(nameOf(openJid))}.</p></div>`;
      const input = $('wx-ms-q'); let t = null, seq = 0; input.focus();
      input.addEventListener('input', () => { clearTimeout(t); const v = input.value.trim(); if (v.length < 2) { $('wx-ms-res').innerHTML = ''; return; }
        const my = ++seq; $('wx-ms-res').innerHTML = '<p class="wx-note">Searching messages…</p>';
        t = setTimeout(async () => { try { const r = await api(`chats/${enc(openJid)}/search?q=${enc(v)}`); if (my !== seq) return;
          const rx = new RegExp(v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig');
          $('wx-ms-res').innerHTML = r.results.length ? r.results.map((m) => `<button type="button" class="wx-hit" data-hit="${esc(m.id)}"><small>${esc(listTime(m.t))}</small><span>${m.fromMe ? tick(m.ack) + ' ' : ''}${esc(m.text).replace(rx, (x) => `<mark>${x}</mark>`)}</span></button>`).join('') : '<p class="wx-note">No messages found</p>';
        } catch (e) { $('wx-ms-res').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; } }, 350); });
      $('wx-ms-res').addEventListener('click', (e) => { const h = e.target.closest('[data-hit]'); if (h) { if (innerWidth <= 860) app.classList.remove('with-info'); jumpTo(h.dataset.hit); } });
    }
    async function drawShared(kind) {
      const b = $('wx-info-body'), jid = openJid;
      b.innerHTML = `<div class="wx-tabs3">${[['media', 'Media'], ['docs', 'Docs'], ['links', 'Links']].map(([k, l]) => `<button type="button" class="${k === kind ? 'is-on' : ''}" data-tab3="${k}">${l}</button>`).join('')}</div><div id="wx-sh" style="padding:14px;background:var(--wx-bg);min-height:100%"><p class="wx-note">Loading…</p></div>`;
      b.querySelectorAll('[data-tab3]').forEach((x) => x.addEventListener('click', () => drawShared(x.dataset.tab3)));
      try {
        const r = await api(`chats/${enc(jid)}/shared?kind=${kind}`); if (openJid !== jid) return;
        const el = $('wx-sh');
        if (!r.items.length) { el.innerHTML = `<p class="wx-note">No ${kind === 'docs' ? 'documents' : kind}</p>`; return; }
        if (kind === 'media') { el.innerHTML = `<div class="wx-media-grid">${r.items.map((m) => `<button type="button" data-imedia="${esc(m.id)}">${m.type === 'video' ? '<span style="display:grid;place-items:center;height:100%;font-size:30px;color:var(--wx-ink3)"><i class="ri-video-fill"></i></span>' : `<img src="${q('/media?id=' + enc(m.id))}" alt="" loading="lazy">`}</button>`).join('')}</div>`;
          r.items.forEach((m) => { if (!msgById(m.id)) msgs.unshift(m); }); }
        else if (kind === 'docs') el.innerHTML = r.items.map((m) => `<a class="wx-doc" style="margin-bottom:8px" href="${q('/media?id=' + enc(m.id))}" download="${esc(m.filename || 'file')}"><i class="ri-file-text-fill"></i><span><b>${esc(m.filename || 'Document')}</b><small>${esc(listTime(m.t))}</small></span></a>`).join('');
        else el.innerHTML = r.items.map((m) => { const u = (m.text.match(/https?:\/\/\S+/) || [''])[0]; return `<a class="wx-hit" href="${esc(u)}" target="_blank" rel="noopener"><small>${esc(listTime(m.t))}</small><span style="color:#027eb5">${esc(u)}</span></a>`; }).join('');
      } catch (e) { $('wx-sh').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; }
    }

    // ── profile ──
    let me = {}, me0 = { jid: '', name: '' };
    async function loadMe() {
      try { me = await api('me'); me0 = { jid: me.jid, name: me.name || 'You' }; } catch (_) { return; }
      const rail = $('wx-rail-me'); if (me.avatar) rail.innerHTML = `<img src="${esc(me.avatar)}" alt="">`; else rail.textContent = initials(me.name || 'Me');
      [$('wx-set-av'), $('wx-prof-av')].forEach((el) => { el.innerHTML = me.avatar ? `<img src="${esc(me.avatar)}" alt="">` : `<i class="ri-user-fill"></i>`; });
      $('wx-set-name').textContent = me.name || '+' + me.phone; $('wx-set-about').textContent = me.about || '';
      if (document.activeElement !== $('wx-prof-name')) $('wx-prof-name').value = me.name || '';
      if (document.activeElement !== $('wx-prof-about')) $('wx-prof-about').value = me.about || '';
      $('wx-prof-phone').textContent = me.phone ? '+' + me.phone : '';
    }
    async function saveMe(body) {
      $('wx-prof-err').hidden = true;
      try { await api('me', body); toast('Saved'); loadMe(); } catch (e) { $('wx-prof-err').textContent = e.message; $('wx-prof-err').hidden = false; }
    }
    $('wx-prof-name-save').addEventListener('click', () => saveMe({ name: $('wx-prof-name').value }), sig);
    $('wx-prof-about-save').addEventListener('click', () => saveMe({ about: $('wx-prof-about').value }), sig);
    $('wx-prof-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('wx-prof-name-save').click(); }, sig);
    $('wx-prof-about').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('wx-prof-about-save').click(); }, sig);
    $('wx-prof-file').addEventListener('change', async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append('target', 'me'); fd.append('file', f);
      try { await post('/photo', fd); toast('Profile photo changed'); setTimeout(loadMe, 1500); } catch (err) { toast(err.message); }
      e.target.value = '';
    }, sig);

    // ── starred ──
    async function loadStarred() {
      const el = $('wx-starlist'); el.innerHTML = '<p class="wx-note">Loading…</p>';
      try {
        const { messages } = await api('starred');
        el.innerHTML = messages.length ? messages.map((m) => `<button type="button" class="wx-hit" data-star-chat="${esc(m.chat)}" data-star-id="${esc(m.id)}">
            <small>${esc(m.fromMe ? 'You' : nameOf(m.author || m.chat))}${m.chat.endsWith('@g.us') ? ' · ' + esc(nameOf(m.chat)) : ''} · ${esc(listTime(m.t))}</small>
            <span>${kindIcon[m.type] ? `<i class="${kindIcon[m.type]}"></i> ` : ''}${esc(m.text || m.type)}</span></button>`).join('')
          : '<div class="wx-note"><i class="ri-star-line" style="font-size:40px;display:block;margin-bottom:10px"></i>No starred messages<br>Tap and hold on any message in any chat to star it, so you can easily find it later.</div>';
      } catch (e) { el.innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; }
    }
    $('wx-starlist').addEventListener('click', async (e) => { const b = e.target.closest('[data-star-chat]'); if (!b) return; await openChat(b.dataset.starChat); jumpTo(b.dataset.starId); }, sig);

    // ── status ──
    let statuses = [];
    async function loadStatuses() {
      const el = $('wx-statuslist');
      try { statuses = (await api('statuses')).statuses || []; }
      catch (e) { el.innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; return; }
      const mine = statuses.find((s) => s.mine), others = statuses.filter((s) => !s.mine);
      const recent = others.filter((s) => s.msgs.some((m) => !m.viewed)), seen = others.filter((s) => !s.msgs.some((m) => !m.viewed));
      const row = (s) => `<div class="wx-row" role="button" tabindex="0" data-status="${esc(s.jid)}"><span class="wx-ring ${s.msgs.some((m) => !m.viewed) || s.mine ? '' : 'seen'}">${av(s.mine ? me0.jid : s.jid, s.name, 'user')}</span>
        <div class="wx-row-mid"><div class="wx-row-top"><span class="wx-row-name">${esc(s.mine ? 'My status' : s.name)}</span></div><div class="wx-row-bot"><span class="wx-row-last">${esc(listTime(Math.max(...s.msgs.map((m) => m.t))))}</span></div></div></div>`;
      let html = mine ? row(mine) : `<div class="wx-row" role="button" tabindex="0" data-go="statusnew">${av(me0.jid, me0.name, 'user')}<div class="wx-row-mid"><div class="wx-row-top"><span class="wx-row-name">My status</span></div><div class="wx-row-bot"><span class="wx-row-last">Click to add status update</span></div></div></div>`;
      if (recent.length) html += '<p class="wx-sec">Recent</p>' + recent.map(row).join('');
      if (seen.length) html += '<p class="wx-sec">Viewed</p>' + seen.map(row).join('');
      if (!others.length) html += '<p class="wx-note">No status updates from your contacts right now.</p>';
      el.innerHTML = html; lazyAv(el);
      $('wx-rail-status').hidden = !recent.length;
    }
    $('wx-statuslist').addEventListener('click', (e) => { const r = e.target.closest('[data-status]'); if (r) viewStatus(r.dataset.status); }, sig);
    function viewStatus(jid) {
      const order = statuses.filter((s) => !s.mine || s.jid === jid);
      let si = Math.max(0, order.findIndex((s) => s.jid === jid)), mi = 0, timer = null, started = 0, dur = 5000;
      const v = document.createElement('div'); v.className = 'wx-status-view'; v.setAttribute('role', 'dialog'); v.setAttribute('aria-label', 'Status');
      const close = () => { clearTimeout(timer); cancelAnimationFrame(raf); v.remove(); document.removeEventListener('keydown', key, true); loadStatuses(); };
      let raf = 0;
      const next = () => { const s = order[si]; if (mi < s.msgs.length - 1) mi++; else if (si < order.length - 1) { si++; mi = 0; } else return close(); draw(); };
      const prev = () => { if (mi > 0) mi--; else if (si > 0) { si--; mi = 0; } draw(); };
      const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } if (e.key === 'ArrowRight') next(); if (e.key === 'ArrowLeft') prev(); };
      function anim() { const bar = v.querySelector(`[data-bar="${mi}"] i`); if (bar) bar.style.width = Math.min(100, (Date.now() - started) / dur * 100) + '%'; raf = requestAnimationFrame(anim); }
      function draw() {
        clearTimeout(timer); cancelAnimationFrame(raf);
        const s = order[si], m = s.msgs[mi], src = q('/media?id=' + enc(m.id));
        v.innerHTML = `<div class="wx-status-bars">${s.msgs.map((_, k) => `<span data-bar="${k}"><i style="width:${k < mi ? 100 : 0}%"></i></span>`).join('')}</div>
          <div class="wx-status-top">${av(s.mine ? me0.jid : s.jid, s.name, 'user', 'sm')}<div class="who">${esc(s.mine ? 'My status' : s.name)}<small>${esc(listTime(m.t))}</small></div><button type="button" class="ic" data-x aria-label="Close"><i class="ri-close-line"></i></button></div>
          <div class="wx-status-body" style="${m.type === 'chat' ? `background:${m.bg || '#1fa855'};margin:10px 18%;border-radius:8px;padding:0` : ''}">
            ${m.type === 'image' ? `<img src="${src}" alt="">` : m.type === 'video' ? `<video src="${src}" autoplay playsinline></video>` : `<div class="wx-status-text">${fmt(m.text)}</div>`}
            <button type="button" class="wx-status-nav prev" data-prev aria-label="Previous"></button><button type="button" class="wx-status-nav next" data-next aria-label="Next"></button></div>
          ${m.type !== 'chat' && m.text ? `<p class="wx-status-cap">${fmt(m.text)}</p>` : ''}
          ${!s.mine ? `<form class="wx-preview-cap" data-reply-status style="padding:8px 18% 22px"><input placeholder="Reply…" aria-label="Reply to status"><button type="submit" class="wx-send" aria-label="Send"><i class="ri-send-plane-2-fill"></i></button></form>` : ''}`;
        lazyAv(v);
        const vid = v.querySelector('video');
        dur = 5000; started = Date.now();
        if (vid) vid.addEventListener('loadedmetadata', () => { dur = Math.min(30000, (vid.duration || 5) * 1000); started = Date.now(); clearTimeout(timer); timer = setTimeout(next, dur); });
        timer = setTimeout(next, dur); anim();
        if (!s.mine && !m.viewed) { m.viewed = true; api('statuses/seen', { jid: s.jid, msgId: m.id }).catch(() => {}); }
        const f = v.querySelector('[data-reply-status]');
        f?.querySelector('input').addEventListener('focus', () => { clearTimeout(timer); cancelAnimationFrame(raf); });
        f?.addEventListener('submit', async (ev) => { ev.preventDefault(); const t = f.querySelector('input').value.trim(); if (!t) return;
          const fd = new FormData(); fd.append('to', s.jid); fd.append('text', t); try { await post('/send', fd); toast('Reply sent'); f.querySelector('input').value = ''; } catch (err) { toast(err.message); } });
      }
      v.addEventListener('click', (e) => { if (e.target.closest('[data-x]')) close(); else if (e.target.closest('[data-next]')) next(); else if (e.target.closest('[data-prev]')) prev(); });
      document.addEventListener('keydown', key, true);
      document.body.append(v); draw();
    }

    // ── Post to many + status composer + scheduled (SWM extras) ──
    let targets = null, activeIdx = 0, sugg = [];
    const picked = new Map();
    const tq = $('wa-target-q'), tbox = $('wa-targets');
    async function loadTargets() {
      renderPicked(); loadGuard();
      if (targets) return;
      try {
        const [t] = await Promise.all([getJSON('/targets.json'), loadContacts().catch(() => null)]);
        const seen = new Set(), chatName = new Map(chats.map((c) => [c.jid, c.name]));
        [t.groups, t.channels].forEach((l) => (l || []).forEach((x) => { if (chatName.get(x.jid)) x.name = chatName.get(x.jid); }));
        targets = [
          ...(t.groups || []).map((x) => ({ ...x, kind: 'group', label: 'Group' })),
          ...(t.channels || []).map((x) => ({ ...x, kind: 'channel', label: 'Channel' })),
          ...chats.filter((c) => c.kind === 'user').map((c) => ({ jid: c.jid, name: c.name, kind: 'user', label: 'Chat' })),
          ...(contacts || []).map((c) => ({ jid: c.jid, name: c.name, kind: 'user', label: 'Contact', phone: c.phone, pushname: c.pushname })),
        ].filter((x) => x.jid && !seen.has(x.jid) && seen.add(x.jid));
        if (document.activeElement === tq) suggest();
      } catch (e) { tbox.innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; }
    }
    function renderPicked() {
      $('wa-picked').innerHTML = [...picked.values()].map((t) => `<span class="wx-pill ${firstOf.get(t.jid) === 'cold' ? 'cold' : ''}" title="${firstOf.get(t.jid) === 'cold' ? 'Never chatted with this number and not a saved contact: will be refused' : ''}">${av(t.jid, t.name, t.kind)}${firstOf.get(t.jid) === 'cold' ? '<i class="ri-error-warning-fill"></i>' : ''}${esc(t.name)}<button type="button" data-unpick="${esc(t.jid)}" aria-label="Remove ${esc(t.name)}"><i class="ri-close-line"></i></button></span>`).join('');
      lazyAv($('wa-picked'));
      $('wa-to-lbl').textContent = picked.size ? `Send to (${picked.size})` : 'Send to';
      checkPeople(); renderSafety();
    }
    // ── anti-ban: who would get a FIRST message, today's usage, and a plain-language safety summary ──
    const firstOf = new Map();             // jid -> 'known' | 'new' | 'cold'
    let guard = null, checkT = null;
    const isPerson = (jid) => /@(c\.us|lid|s\.whatsapp\.net)$/.test(jid);
    function checkPeople() {
      clearTimeout(checkT);
      const need = [...picked.keys()].filter((j) => isPerson(j) && !firstOf.has(j));
      if (!need.length) return;
      checkT = setTimeout(async () => {
        try { const r = await api('check-targets', { jids: need }); Object.entries(r.targets || {}).forEach(([j, v]) => firstOf.set(j, v)); renderPicked(); } catch (_) {}
      }, 300);
    }
    async function loadGuard() { try { guard = await api('guard'); } catch (_) { guard = null; } renderSafety(); paintSafetySub(); }
    function renderSafety() {
      const box = $('wa-safety'); if (!box) return;
      const list = [...picked.values()];
      const people = list.filter((t) => isPerson(t.jid)), groups = list.filter((t) => t.jid.endsWith('@g.us')), chans = list.filter((t) => t.jid.endsWith('@newsletter'));
      const cold = people.filter((t) => firstOf.get(t.jid) === 'cold'), fresh = people.filter((t) => firstOf.get(t.jid) === 'new');
      const text = postForm.querySelector('[name=text]').value;
      const rows = [];
      const row = (cls, icon, html) => rows.push(`<div class="wx-safe-row ${cls}"><i class="${icon}"></i><span>${html}</span></div>`);
      if (list.length) {
        const parts = [groups.length && `${groups.length} group${groups.length > 1 ? 's' : ''}`, chans.length && `${chans.length} channel${chans.length > 1 ? 's' : ''}`, people.length && `${people.length} ${people.length > 1 ? 'people' : 'person'}`].filter(Boolean);
        const mins = Math.max(1, Math.round(list.length * 14 / 60));
        row('', 'ri-time-line', `<b>${parts.join(' · ')}</b>. Goes out gradually, a few at a time with pauses like a person: about ${mins} minute${mins > 1 ? 's' : ''}.`);
      }
      if (list.length > 50) row('bad', 'ri-close-circle-fill', `At most <b>50</b> chats per post. Remove ${list.length - 50}.`);
      if (people.length > 25) row('bad', 'ri-close-circle-fill', `At most <b>25 individual people</b> per post (you have ${people.length}). For announcements use a group or channel: one message reaches everyone safely.`);
      if (cold.length) row('bad', 'ri-close-circle-fill', `<b>${cold.length}</b> ${cold.length > 1 ? 'have' : 'has'} never chatted with this number and ${cold.length > 1 ? 'are' : 'is'} not saved (marked red). Messages to strangers are the main reason WhatsApp bans numbers, so they will be refused.`);
      if (fresh.length) row('warn', 'ri-error-warning-fill', `${fresh.length} saved contact${fresh.length > 1 ? 's have' : ' has'} no chat with this number yet. First messages are limited to a few per hour.`);
      if (people.length >= 2 && /https?:\/\//i.test(text)) row('warn', 'ri-link', 'A link sent to many people one by one looks like spam to WhatsApp. Prefer sharing links in a group or channel.');
      if (people.length >= 2 && text && !text.includes('{name}')) row('', 'ri-user-smile-line', 'Tip: add <b>{name}</b> so each person gets their own greeting. Identical messages to many people look like spam.');
      if (guard?.pausedUntil) row('bad', 'ri-hand', `Sending is paused for safety until ${new Date(guard.pausedUntil).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}: ${esc(guard.pauseReason || '')}. It will continue by itself.`);
      if (guard && list.length) {
        const u = guard.used, l = guard.limits;
        row('', 'ri-shield-check-line', `Today: ${u.bulk.day} of ${l.bulk.day} Post-to-many messages · ${u.new.day} of ${l.new.day} first messages · ${u.msg.hour} of ${l.msg.hour} messages this hour.`);
      }
      box.innerHTML = rows.length ? `<div class="wx-safe-box">${rows.join('')}</div>` : '';
    }
    function suggest() {
      const raw = tq.value.trim();
      if (!targets) { tbox.innerHTML = '<p class="wx-note">Loading your chats, groups, channels and contacts…</p>'; tbox.hidden = false; tq.setAttribute('aria-expanded', 'true'); return; }
      const pool = targets.filter((t) => !picked.has(t.jid));
      sugg = raw ? rank(pool, raw, (t) => [t.name, t.pushname, t.phone], 40) : pool.filter((t) => t.kind !== 'user' || t.label === 'Chat').slice(0, 12);
      const similar = raw && lastPartial;
      const d = raw.replace(/\D/g, '');
      if (d.length >= 7 && /^[+\d\s()-]+$/.test(raw) && !sugg.some((t) => t.phone === d)) sugg.unshift({ jid: d + '@c.us', name: '+' + d, kind: 'user', label: 'Number' });
      activeIdx = 0;
      tbox.innerHTML = (raw ? (similar ? `<p class="wx-sec" style="padding:8px 16px 4px">No exact match for '${esc(raw)}' · similar names</p>` : '') : '<p class="wx-sec" style="padding:8px 16px 4px">Groups, channels and recent chats</p>') + (sugg.length ? sugg.map((t, i) => `<div class="wx-row ${i === 0 ? 'is-active' : ''}" role="option" aria-selected="${i === 0}" data-sug="${i}">${av(t.jid, t.name, t.kind)}
          <div class="wx-row-mid"><div class="wx-row-top"><span class="wx-row-name">${raw ? hl(t.name, raw) : esc(t.name)}</span><span class="kind">${t.label}</span></div>
          ${t.label === 'Contact' || t.label === 'Number' ? `<div class="wx-row-bot"><span class="wx-row-last">${t.phone ? '+' + esc(t.phone) : ''}</span></div>` : ''}</div></div>`).join('')
        : `<p class="wx-note">No chat, group, channel or contact matches '${esc(raw)}'.</p>`);
      tbox.hidden = false; tq.setAttribute('aria-expanded', 'true'); lazyAv(tbox);
    }
    function pick(i) {
      const t = sugg[i]; if (!t) return;
      picked.set(t.jid, t); tq.value = ''; renderPicked(); suggest(); tq.focus();
    }
    const closeSuggest = () => { tbox.hidden = true; tq.setAttribute('aria-expanded', 'false'); };
    tq.addEventListener('focus', () => { suggest(); loadTargets(); }, sig);
    tq.addEventListener('input', suggest, sig);
    tq.addEventListener('keydown', (e) => {
      if (tbox.hidden && (e.key === 'ArrowDown' || e.key === 'Enter')) { suggest(); }
      const rows = [...tbox.querySelectorAll('[data-sug]')];
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (!rows.length) return;
        activeIdx = (activeIdx + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
        rows.forEach((r, i) => { r.classList.toggle('is-active', i === activeIdx); r.setAttribute('aria-selected', String(i === activeIdx)); });
        rows[activeIdx].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') { e.preventDefault(); if (rows.length) pick(activeIdx); }
      else if (e.key === 'Escape') { e.stopPropagation(); closeSuggest(); }
      else if (e.key === 'Backspace' && !tq.value && picked.size) { picked.delete([...picked.keys()].pop()); renderPicked(); suggest(); }
    }, sig);
    tbox.addEventListener('mousedown', (e) => { const r = e.target.closest('[data-sug]'); if (r) { e.preventDefault(); pick(Number(r.dataset.sug)); } }, sig);
    tq.addEventListener('blur', () => setTimeout(closeSuggest, 120), sig);
    $('wa-picked').addEventListener('click', (e) => { const b = e.target.closest('[data-unpick]'); if (b) { picked.delete(b.dataset.unpick); renderPicked(); if (!tbox.hidden) suggest(); } }, sig);
    function whenToggle(form) {
      form.querySelectorAll('[name=when]').forEach((r) => r.addEventListener('change', () => {
        const later = form.querySelector('[name=when]:checked').value === 'later'; const dt = form.querySelector('[name=send_at]'); dt.hidden = !later; dt.required = later;
        form.querySelector('[type=submit] span').textContent = later ? 'Schedule' : (form.id === 'wa-status' ? 'Post status' : 'Send');
      }, sig));
    }
    async function submitPost(form, kind) {
      const err = form.querySelector('.wa-error'); err.hidden = true; err.className = 'wa-error wx-err';
      const fd = new FormData(form); fd.append('kind', kind);
      if (kind === 'message') fd.append('targets', JSON.stringify([...picked.values()].map((t) => ({ jid: t.jid, name: t.name }))));
      if (form.querySelector('[name=when]:checked').value !== 'later') fd.delete('send_at');
      fd.delete('when');
      const btn = form.querySelector('[type=submit]'); btn.disabled = true;
      try {
        const r = await post('/posts', fd);
        if (r.status === 'failed') throw new Error(r.error || 'It could not be sent.');
        form.reset(); form.querySelector('[name=send_at]').hidden = true; if (kind === 'message') { picked.clear(); renderPicked(); }
        err.hidden = false; err.className = 'wa-error wx-ok';
        err.textContent = r.note || (fd.get('send_at') ? 'Scheduled. You can follow it under Scheduled.' : (kind === 'status' ? 'Posting your status…' : 'Queued. You can follow it under Scheduled.'));
        if (kind === 'message') loadGuard();
        if (kind === 'status') setTimeout(loadStatuses, 3000);
      } catch (e2) { err.hidden = false; err.textContent = e2.message; }
      finally { btn.disabled = false; }
    }
    app.querySelectorAll('.wx-file').forEach((w) => {
      const input = w.querySelector('input[type=file]'), chip = w.querySelector('.wx-file-chip'), img = chip.querySelector('img');
      const paint = () => { const f = input.files[0]; chip.hidden = !f; w.querySelector('label span').textContent = f ? 'Change file' : 'Choose a file';
        if (!f) return; chip.querySelector('.nm').textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
        if (f.type.startsWith('image/')) { img.src = URL.createObjectURL(f); img.hidden = false; } else img.hidden = true; };
      input.addEventListener('change', paint, sig);
      chip.querySelector('[data-file-x]').addEventListener('click', () => { input.value = ''; paint(); }, sig);
      input.form?.addEventListener('reset', () => setTimeout(paint, 0), sig);
    });
    const postForm = $('wa-post'), statusForm = $('wa-status');
    whenToggle(postForm); whenToggle(statusForm);
    postForm.addEventListener('input', () => renderSafety(), sig);
    postForm.addEventListener('change', () => renderSafety(), sig);
    // {name}: at the cursor while typing; otherwise the message starts with a greeting ("Hi {name}, ...")
    let nameCaret = null;
    postForm.querySelector('[name=text]').addEventListener('blur', (e) => { nameCaret = [e.target.selectionStart, e.target.selectionEnd]; }, sig);
    postForm.querySelector('[name=text]').addEventListener('focus', () => { nameCaret = null; }, sig);
    $('wa-name-token').addEventListener('mousedown', (e) => e.preventDefault(), sig);
    $('wa-name-token').addEventListener('click', () => {
      const ta = postForm.querySelector('[name=text]');
      if (ta.value.includes('{name}')) { ta.focus(); return; }
      if (document.activeElement === ta || nameCaret) {
        const [a0, b0] = document.activeElement === ta ? [ta.selectionStart, ta.selectionEnd] : nameCaret;
        if (a0 > 0 || b0 > 0 || !ta.value) {
          ta.value = ta.value.slice(0, a0) + '{name}' + ta.value.slice(b0); ta.focus(); ta.selectionStart = ta.selectionEnd = a0 + 6; renderSafety(); return;
        }
      }
      ta.value = 'Hi {name}, ' + ta.value.replace(/^\s+/, ''); ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; renderSafety();
    }, sig);
    postForm.addEventListener('submit', (e) => { e.preventDefault(); if (!picked.size) { const err = postForm.querySelector('.wa-error'); err.hidden = false; err.className = 'wa-error wx-err'; err.textContent = 'Choose at least one chat, group, channel or contact.'; tq.focus(); return; }
      const ppl = [...picked.keys()].filter(isPerson);
      if (picked.size > 50 || ppl.length > 25 || ppl.some((j) => firstOf.get(j) === 'cold')) { renderSafety(); $('wa-safety').scrollIntoView({ block: 'nearest' }); const err = postForm.querySelector('.wa-error'); err.hidden = false; err.className = 'wa-error wx-err'; err.textContent = 'Fix the items in red first: they would put the number at risk of a WhatsApp ban.'; return; }
      if (!postForm.querySelector('[name=text]').value.trim() && !postForm.querySelector('[name=file]').files.length) { const err = postForm.querySelector('.wa-error'); err.hidden = false; err.className = 'wa-error wx-err'; err.textContent = 'Write a message or attach a file.'; return; }
      submitPost(postForm, 'message'); }, sig);
    statusForm.addEventListener('submit', (e) => { e.preventDefault(); submitPost(statusForm, 'status'); }, sig);
    const badge = { pending: ['Scheduled', 'badge-gold'], sending: ['Sending', 'badge-warn'], sent: ['Sent', 'badge-ok'], partial: ['Partly sent', 'badge-warn'], failed: ['Failed', 'badge-bad'], cancelled: ['Cancelled', 'badge-neut'] };
    async function loadPosts() {
      try { const { posts } = await getJSON('/posts.json');
        $('wa-posts').innerHTML = posts.length ? posts.map((p) => { const [label, cls] = badge[p.status] || [p.status, 'badge-neut'];
          const pct = p.total ? Math.round(100 * p.done / p.total) : 0;
          const prog = p.kind === 'message' && (p.status === 'sending' || p.status === 'partial') ? `<div class="wx-progress"><div class="wx-meter"><i style="width:${pct}%"></i></div><small style="display:inline">${p.done} of ${p.total} sent${p.next && p.status === 'sending' ? ' · next ' + esc(p.next.split(', ').pop()) : ''}</small></div>` : '';
          return `<div class="wx-hit" style="display:flex;gap:12px;align-items:flex-start"><i class="${p.kind === 'status' ? 'ri-donut-chart-line' : 'ri-send-plane-line'}" style="font-size:20px;color:var(--wx-green)"></i><div style="flex:1;min-width:0">
            <b style="font-weight:600">${p.kind === 'status' ? 'Status' : 'To ' + esc(p.targets.slice(0, 4).join(', ')) + (p.targets.length > 4 ? ` and ${p.targets.length - 4} more` : '')}</b>
            <span>${esc(p.text) || (p.media ? '<i>' + esc(p.media) + '</i>' : '')}</span>
            <small>${esc(p.when)}${p.by ? ' · ' + esc(p.by) : ''}${p.error ? ' · ' + esc(p.error) : ''}</small>${prog}</div>
            <div style="display:grid;gap:6px;justify-items:end"><span class="badge ${cls}">${label}</span>${p.can_cancel ? `<button type="button" class="wx-btn ghost" style="height:30px;padding:0 12px" data-cancel="${p.id}">${p.status === 'sending' ? 'Stop' : 'Cancel'}</button>` : ''}</div></div>`; }).join('')
          : '<p class="wx-note">Nothing scheduled or sent yet.</p>';
      } catch (e) { $('wa-posts').innerHTML = `<p class="wx-note">${esc(e.message)}</p>`; }
    }
    $('wa-posts').addEventListener('click', async (e) => { const b = e.target.closest('[data-cancel]'); if (!b) return;
      if (b.textContent === 'Stop' && !await confirmBox('Stop this post?', 'Chats that already got it keep it; nobody else will receive it.', 'Stop', true)) return;
      try { await post(`/posts/${b.dataset.cancel}/cancel`, {}); loadPosts(); } catch (err) { toast(err.message); } }, sig);

    // ── live updates ──
    function onActivity() {
      if (mode !== 'app') return;
      loadChats(); if (openJid) refreshOpen();
      if (view === 'archived') loadArchived();
      if (view === 'channels') loadChannels();
    }
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || mode !== 'app') return;
      if (menuEl) { closeMenu(); return; }
      if (!$('wx-emoji').hidden || !$('wx-attach').hidden) { closePickers(); return; }
      if (!$('wx-preview').hidden) { $('wx-preview').hidden = true; return; }
      if (editing) { cancelEdit(); return; }
      if (replyTo) { clearReply(); return; }
      if (app.classList.contains('with-info')) { app.classList.remove('with-info'); return; }
      if (view !== 'chats') { go('chats'); return; }
      if (openJid) closeChat();
    }, sig);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && mode === 'app') { refreshState(); if (openJid) post('/seen', { jid: openJid }).catch(() => {}); } }, sig);
    window.addEventListener('beforeunload', saveDraft, sig);

    refreshState();
    window.__swmWaTimer = setInterval(() => { if (!document.hidden) refreshState(); }, 3000);
  }

  window.SWMWhatsApp = { init };
})();
