/* ScentAI chat. Conversations are kept in this browser only (localStorage): visitors have no accounts and
   nothing they ask is stored on the server, except the thumbs up/down they choose to give.
   Answers stream from POST /scentai/stream as Server-Sent Events: data {"t": text}, then "done" or "error". */
(() => {
  'use strict';
  const app = document.getElementById('app');
  if (!app) return;
  const $ = (id) => document.getElementById(id);
  const ENDPOINT = app.dataset.endpoint, FEEDBACK = app.dataset.feedback, CSRF = app.dataset.csrf;
  const STORE = 'scentai-chats', MAX_CHATS = 60, LIMIT = 1600;
  const thread = $('thread'), scroller = $('scroll'), input = $('composer-input'), send = $('send'), stop = $('stop');

  // ── storage ──
  const load = () => { try { const v = JSON.parse(localStorage.getItem(STORE) || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; } };
  let chats = load();
  const save = () => { try { chats.sort((a, b) => b.updated - a.updated); localStorage.setItem(STORE, JSON.stringify(chats.slice(0, MAX_CHATS))); } catch (e) { /* private window: keep in memory */ } };
  const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  let chat = null, controller = null, busy = false;

  // ── markdown (safe: everything is escaped first, then a small, known set of syntax becomes HTML) ──
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s)]+)\)/g, (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  function markdown(src) {
    const lines = String(src || '').replace(/\r/g, '').split('\n');
    let html = '', list = null, para = [];
    const flushP = () => { if (para.length) { html += `<p>${inline(para.join(' '))}</p>`; para = []; } };
    const flushL = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const raw of lines) {
      const line = raw.trimEnd();
      let m;
      if (!line.trim()) { flushP(); flushL(); continue; }
      if ((m = line.match(/^#{1,6}\s+(.*)/))) { flushP(); flushL(); html += `<h3>${inline(m[1])}</h3>`; continue; }
      if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flushP(); flushL(); html += '<hr>'; continue; }
      if ((m = line.match(/^>\s?(.*)/))) { flushP(); flushL(); html += `<blockquote>${inline(m[1])}</blockquote>`; continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushP(); if (list !== 'ul') { flushL(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushP(); if (list !== 'ol') { flushL(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; continue; }
      if (list) flushL();
      para.push(line.trim());
    }
    flushP(); flushL();
    return html;
  }

  // ── small helpers ──
  function toast(text) { const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = text; document.body.append(t); setTimeout(() => t.remove(), 1800); }
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (e) { toast('Could not copy'); } }
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 140;
  let stick = true;
  const toEnd = (force) => { if (force || stick) scroller.scrollTop = scroller.scrollHeight; };
  scroller.addEventListener('scroll', () => { stick = nearBottom(); $('to-end').hidden = stick || !app.classList.contains('has-chat'); }, { passive: true });
  $('to-end').addEventListener('click', () => { stick = true; scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }); });
  const icon = { msg: '<svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
    ev: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4"/></svg>',
    tg: '<svg viewBox="0 0 24 24"><path d="m21 4-18 7 6 2 2 6 3-4 5 4Z"/></svg>',
    arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>' };

  // ── rendering ──
  function userTurn(m, index) {
    const node = $('tpl-user').content.firstElementChild.cloneNode(true);
    node.dataset.index = index;
    node.querySelector('.bubble').textContent = m.text;
    return node;
  }
  function botTurn(m, index) {
    const node = $('tpl-bot').content.firstElementChild.cloneNode(true);
    node.dataset.index = index;
    fillBot(node, m);
    return node;
  }
  function fillBot(node, m) {
    node.querySelector('.md').innerHTML = markdown(m.text);
    const cites = node.querySelector('.cites'); cites.innerHTML = '';
    (m.messages || []).forEach((s) => cites.insertAdjacentHTML('beforeend', `<a class="cite" href="${esc(s.url)}"><span class="ci">${icon.msg}</span><span><b>${esc(s.title)}</b><span>${esc(s.preacher ? 'Listen · ' + s.preacher : 'Listen or download')}</span></span></a>`));
    (m.events || []).forEach((s) => cites.insertAdjacentHTML('beforeend', `<a class="cite" href="${esc(s.url)}"><span class="ci">${icon.ev}</span><span><b>${esc(s.title)}</b><span>Event details</span></span></a>`));
    if (m.telegram && (m.messages || []).length) cites.insertAdjacentHTML('beforeend', `<a class="cite" href="${esc(m.telegram)}" target="_blank" rel="noopener"><span class="ci">${icon.tg}</span><span><b>Join us on Telegram</b><span>New messages as they are preached</span></span></a>`);
    const follow = node.querySelector('.follow'); follow.innerHTML = '';
    (m.follow || []).forEach((q) => { const b = document.createElement('button'); b.type = 'button'; b.dataset.prompt = q; b.innerHTML = `<span>${esc(q)}</span>${icon.arrow}`; follow.append(b); });
    node.querySelector('[data-act="up"]').setAttribute('aria-pressed', String(m.rating === 'up'));
    node.querySelector('[data-act="down"]').setAttribute('aria-pressed', String(m.rating === 'down'));
    node.querySelector('.note-stopped')?.remove();
    if (m.stopped) node.querySelector('.md').insertAdjacentHTML('afterend', '<p class="note-stopped">You stopped this answer.</p>');
    node.classList.add('done');
  }
  function renderThread() {
    thread.innerHTML = '';
    (chat?.messages || []).forEach((m, i) => thread.append(m.role === 'user' ? userTurn(m, i) : botTurn(m, i)));
    markLast();
    app.classList.toggle('has-chat', !!chat?.messages.length);
    $('title').textContent = chat?.title || 'ScentAI';
    $('copy-chat').hidden = $('delete-chat').hidden = !chat?.messages.length;
    document.title = (chat?.title ? chat.title + ' · ' : '') + 'ScentAI';
  }
  const markLast = () => { thread.querySelectorAll('.turn.bot').forEach((n, i, all) => n.classList.toggle('last', i === all.length - 1)); };

  function groupOf(t) {
    const day = 864e5, start = new Date(); start.setHours(0, 0, 0, 0);
    if (t >= start) return 'Today'; if (t >= start - day) return 'Yesterday'; if (t >= start - 7 * day) return 'Previous 7 days'; if (t >= start - 30 * day) return 'Previous 30 days'; return 'Older';
  }
  function renderHistory() {
    const needle = $('search').value.trim().toLowerCase();
    const list = chats.filter((c) => c.messages.length && (!needle || c.title.toLowerCase().includes(needle) || c.messages.some((m) => m.text.toLowerCase().includes(needle))));
    const box = $('history'); box.innerHTML = '';
    if (!list.length) { box.innerHTML = `<p class="empty-hist">${needle ? 'No chats match.' : 'Your conversations will appear here. They stay on this device.'}</p>`; return; }
    let group = '';
    list.forEach((c) => {
      const g = groupOf(c.updated); if (g !== group) { group = g; box.insertAdjacentHTML('beforeend', `<h3>${g}</h3>`); }
      const item = document.createElement('div'); item.className = 'chat-item' + (chat && c.id === chat.id ? ' on' : ''); item.dataset.id = c.id;
      item.innerHTML = `<a href="?c=${encodeURIComponent(c.id)}" title="${esc(c.title)}">${esc(c.title)}</a><button type="button" class="icon sm more" aria-label="Options for ${esc(c.title)}" aria-haspopup="menu" aria-expanded="false"><svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg></button>`;
      box.append(item);
    });
  }

  // ── chats ──
  function openChat(id, push) {
    if (busy) controller?.abort();
    chat = chats.find((c) => c.id === id) || null;
    if (push) history.pushState({}, '', chat ? `?c=${encodeURIComponent(chat.id)}` : location.pathname);
    renderThread(); renderHistory(); stick = true; toEnd(true); closeDrawer(); input.focus();
  }
  function newChat() { if (busy) controller?.abort(); chat = null; history.pushState({}, '', location.pathname); renderThread(); renderHistory(); closeDrawer(); input.value = ''; resize(); input.focus(); }
  function ensureChat(firstQuestion) {
    if (chat) return chat;
    chat = { id: uid(), title: firstQuestion.replace(/\s+/g, ' ').slice(0, 60), created: Date.now(), updated: Date.now(), messages: [] };
    chats.unshift(chat); history.replaceState({}, '', `?c=${encodeURIComponent(chat.id)}`);
    return chat;
  }

  // ── asking ──
  function setBusy(on) { busy = on; send.hidden = on; stop.hidden = !on; updateSend(); }
  async function ask(question, opts = {}) {
    question = question.trim().slice(0, LIMIT);
    if (!question || busy) return;
    const c = ensureChat(question);
    if (!opts.reuseUser) {
      c.messages.push({ role: 'user', text: question });
      thread.append(userTurn(c.messages.at(-1), c.messages.length - 1));
    }
    const bot = { role: 'assistant', text: '' };
    c.messages.push(bot); c.updated = Date.now(); save();
    app.classList.add('has-chat'); $('title').textContent = c.title; $('copy-chat').hidden = $('delete-chat').hidden = false;
    const node = botTurn(bot, c.messages.length - 1); node.classList.remove('done'); thread.append(node); markLast();
    const md = node.querySelector('.md');
    md.innerHTML = '<span class="thinking"><i></i><i></i><i></i><span class="shimmer">Searching the teachings…</span></span>';
    stick = true; toEnd(true); renderHistory();

    const historyTurns = c.messages.slice(0, -2).map((m) => ({ role: m.role, text: m.text }));
    const body = new FormData(); body.set('question', question); body.set('history', JSON.stringify(historyTurns.slice(-20))); body.set('csrf_token', CSRF);
    controller = new AbortController(); setBusy(true);
    let raf = 0;
    const paint = () => { raf = 0; md.innerHTML = markdown(bot.text); md.classList.add('caret'); toEnd(); };
    try {
      const res = await fetch(ENDPOINT, { method: 'POST', body, signal: controller.signal, headers: { 'X-CSRF-Token': CSRF, Accept: 'text/event-stream' } });
      if (!res.ok || !res.body) { let msg = 'ScentAI is unavailable right now. Please try again.'; try { msg = (await res.json()).error || msg; } catch (e) { /* not JSON */ } throw new Error(msg); }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '', ended = false;
      while (!ended) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, cut); buf = buf.slice(cut + 2);
          const ev = (block.match(/^event: (.*)$/m) || [])[1] || 'message';
          const data = block.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n');
          if (!data) continue;
          const payload = JSON.parse(data);
          if (ev === 'error') throw new Error(payload.error);
          if (ev === 'done') {
            bot.text = payload.answer || bot.text; bot.messages = payload.messages; bot.events = payload.events;
            bot.follow = payload.follow_ups; bot.telegram = payload.telegram_url; ended = true; break;
          }
          if (payload.t) { bot.text += payload.t; if (!raf) raf = requestAnimationFrame(paint); }
        }
      }
      if (!bot.text.trim()) throw new Error('ScentAI did not answer. Please try again.');
    } catch (e) {
      if (e.name === 'AbortError') { bot.stopped = true; }
      else if (!bot.text.trim()) {
        c.messages.pop(); save();
        md.classList.remove('caret');
        md.innerHTML = `<div class="err" role="alert"><span>${esc(e.message || 'Connection failed. Please try again.')}</span><button type="button" class="btn" data-act="retry-error">Try again</button></div>`;
        node.dataset.question = question; setBusy(false); return;
      } else { bot.stopped = true; }
    } finally { if (raf) cancelAnimationFrame(raf); controller = null; }
    md.classList.remove('caret');
    c.updated = Date.now(); save();
    fillBot(node, bot); markLast(); setBusy(false); renderHistory(); toEnd(); input.focus();
  }

  // ── composer ──
  const resize = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 220) + 'px'; };
  function updateSend() {
    const n = input.value.length; const count = $('count');
    count.textContent = n > LIMIT - 200 ? `${n} / ${LIMIT}` : ''; count.classList.toggle('over', n > LIMIT);
    send.disabled = busy || !input.value.trim() || n > LIMIT;
  }
  input.addEventListener('input', () => { resize(); updateSend(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('composer').requestSubmit(); } });
  $('composer').addEventListener('submit', (e) => { e.preventDefault(); if (send.disabled) return; const q = input.value; input.value = ''; resize(); updateSend(); ask(q); });
  stop.addEventListener('click', () => controller?.abort());

  // Voice input where the browser offers it.
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Speech) {
    const mic = $('mic'); mic.hidden = false; let rec = null;
    mic.addEventListener('click', () => {
      if (rec) { rec.stop(); return; }
      rec = new Speech(); rec.lang = document.documentElement.lang || 'en'; rec.interimResults = true; rec.continuous = false;
      const before = input.value ? input.value.trimEnd() + ' ' : '';
      rec.onresult = (ev) => { input.value = before + [...ev.results].map((r) => r[0].transcript).join(''); resize(); updateSend(); };
      rec.onend = () => { rec = null; mic.classList.remove('on'); mic.setAttribute('aria-label', 'Speak your question'); input.focus(); };
      rec.onerror = () => toast('Could not hear you. Check the microphone permission.');
      mic.classList.add('on'); mic.setAttribute('aria-label', 'Stop listening'); rec.start();
    });
  }

  // ── actions inside the conversation ──
  thread.addEventListener('click', async (e) => {
    const f = e.target.closest('.follow button'); if (f) { ask(f.dataset.prompt); return; }
    const btn = e.target.closest('[data-act]'); if (!btn) return;
    const turn = btn.closest('.turn'); const i = Number(turn.dataset.index); const m = chat?.messages[i];
    const act = btn.dataset.act;
    if (act === 'retry-error') { const q = turn.dataset.question; turn.remove(); ask(q, { reuseUser: true }); return; }
    if (!m) return;
    if (act === 'copy') copy(m.text);
    if (act === 'speak') {
      if (!('speechSynthesis' in window)) return toast('Reading aloud is not available in this browser');
      if (speechSynthesis.speaking) { speechSynthesis.cancel(); return; }
      const u = new SpeechSynthesisUtterance(m.text.replace(/[*#>`_]/g, '')); speechSynthesis.speak(u);
    }
    if (act === 'up' || act === 'down') {
      if (m.rating === act) return;
      m.rating = act; save(); fillBot(turn, m);
      const q = chat.messages[i - 1]?.text || '';
      const fd = new FormData(); fd.set('rating', act); fd.set('question', q); fd.set('answer', m.text.slice(0, 8000)); fd.set('csrf_token', CSRF);
      fetch(FEEDBACK, { method: 'POST', body: fd, headers: { 'X-CSRF-Token': CSRF } }).catch(() => {});
      toast(act === 'up' ? 'Thank you for the feedback' : 'Thank you. This helps us improve ScentAI');
    }
    if (act === 'retry' && !busy) {
      const q = chat.messages[i - 1]; chat.messages.splice(i); save(); renderThread();
      thread.lastElementChild?.remove(); chat.messages.pop();   // ask() adds the question back
      ask(q.text);
    }
    if (act === 'edit' && !busy) {
      const bubble = turn.querySelector('.bubble'); const acts = turn.querySelector('.acts');
      bubble.hidden = acts.hidden = true;
      const box = document.createElement('div'); box.className = 'edit';
      box.innerHTML = '<textarea aria-label="Edit your message" maxlength="1600"></textarea><div><button type="button" class="btn" data-x="cancel">Cancel</button><button type="button" class="btn primary" data-x="send">Send</button></div>';
      const ta = box.querySelector('textarea'); ta.value = m.text; turn.append(box); ta.focus(); ta.style.height = ta.scrollHeight + 'px';
      box.addEventListener('click', (ev) => {
        const x = ev.target.closest('[data-x]'); if (!x) return;
        if (x.dataset.x === 'cancel') { box.remove(); bubble.hidden = acts.hidden = false; return; }
        const text = ta.value.trim(); if (!text) return;
        chat.messages.splice(i); save(); renderThread(); ask(text);
      });
    }
  });

  // ── sidebar ──
  $('new-chat').addEventListener('click', newChat); $('new-chat-2').addEventListener('click', newChat);
  $('search').addEventListener('input', renderHistory);
  $('history').addEventListener('click', (e) => {
    const a = e.target.closest('.chat-item a'); if (a) { e.preventDefault(); openChat(a.closest('.chat-item').dataset.id, true); return; }
    const more = e.target.closest('.more'); if (!more) return;
    const item = more.closest('.chat-item'); const c = chats.find((x) => x.id === item.dataset.id);
    document.querySelectorAll('.menu').forEach((n) => n.remove());
    more.setAttribute('aria-expanded', 'true');
    const menu = document.createElement('div'); menu.className = 'menu'; menu.setAttribute('role', 'menu');
    menu.innerHTML = '<button type="button" role="menuitem" data-m="rename">Rename</button><button type="button" role="menuitem" data-m="copy">Copy conversation</button><button type="button" role="menuitem" class="danger" data-m="delete">Delete</button>';
    item.append(menu); menu.querySelector('button').focus();
    menu.addEventListener('click', (ev) => {
      const k = ev.target.closest('[data-m]')?.dataset.m; menu.remove(); more.setAttribute('aria-expanded', 'false');
      if (k === 'delete') removeChat(c);
      if (k === 'copy') copy(transcript(c));
      if (k === 'rename') {
        const a2 = item.querySelector('a'); a2.hidden = true; const inp = document.createElement('input'); inp.value = c.title; inp.setAttribute('aria-label', 'Chat name'); item.prepend(inp); inp.select();
        const done = (keep) => { if (keep && inp.value.trim()) { c.title = inp.value.trim().slice(0, 80); save(); } renderHistory(); if (chat?.id === c.id) $('title').textContent = c.title; };
        inp.addEventListener('keydown', (k2) => { if (k2.key === 'Enter') done(true); if (k2.key === 'Escape') done(false); });
        inp.addEventListener('blur', () => done(true));
      }
    });
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.menu, .more')) document.querySelectorAll('.menu').forEach((n) => { n.previousElementSibling?.setAttribute('aria-expanded', 'false'); n.remove(); }); });
  const transcript = (c) => c.messages.map((m) => `${m.role === 'user' ? 'You' : 'ScentAI'}: ${m.text}`).join('\n\n');
  function removeChat(c) {
    if (!c || !confirm('Delete this conversation? This cannot be undone.')) return;
    chats = chats.filter((x) => x.id !== c.id); save();
    if (chat?.id === c.id) newChat(); else renderHistory();
  }
  $('copy-chat').addEventListener('click', () => chat && copy(transcript(chat)));
  $('delete-chat').addEventListener('click', () => removeChat(chat));

  const mobile = () => matchMedia('(max-width: 860px)').matches;
  const scrim = $('scrim');
  function closeDrawer() { app.classList.remove('drawer'); scrim.hidden = true; }
  $('side-open').addEventListener('click', () => {
    if (mobile()) { app.classList.add('drawer'); scrim.hidden = false; $('new-chat').focus(); }
    else { app.classList.remove('collapsed'); try { localStorage.removeItem('scentai-collapsed'); } catch (e) {} }
  });
  $('side-close').addEventListener('click', () => {
    if (mobile()) closeDrawer();
    else { app.classList.add('collapsed'); try { localStorage.setItem('scentai-collapsed', '1'); } catch (e) {} }
  });
  scrim.addEventListener('click', closeDrawer);
  try { if (localStorage.getItem('scentai-collapsed') && !mobile()) app.classList.add('collapsed'); } catch (e) {}

  // Theme: follows the device unless the visitor picks one.
  const themeBtn = $('theme');
  const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const themeLabel = () => { themeBtn.querySelector('span').textContent = isDark() ? 'Light mode' : 'Dark mode'; };
  themeBtn.addEventListener('click', () => { const t = isDark() ? 'light' : 'dark'; document.documentElement.dataset.theme = t; try { localStorage.setItem('scentai-theme', t); } catch (e) {} themeLabel(); });
  themeLabel();

  // Starters and keyboard shortcuts.
  document.querySelectorAll('#starters [data-prompt]').forEach((b) => b.addEventListener('click', () => ask(b.dataset.prompt)));
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); newChat(); }
    if (e.key === 'Escape') { if (busy) controller?.abort(); closeDrawer(); }
  });
  window.addEventListener('popstate', () => { const id = new URLSearchParams(location.search).get('c'); openChat(id, false); });

  // Start: open ?c=, or ask ?q= (links from the website), or a fresh chat.
  const params = new URLSearchParams(location.search);
  if (params.get('c')) openChat(params.get('c'), false); else { renderThread(); renderHistory(); }
  if (params.get('q')) { const q = params.get('q'); history.replaceState({}, '', location.pathname); chat = null; ask(q); }
  resize(); updateSend(); input.focus();
})();
