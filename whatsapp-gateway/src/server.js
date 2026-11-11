// HTTP API used only by the SWM app (internal Docker network, Bearer token). Every session is a tenant id
// ("b<branch id>"); SWM decides who may use which one.
import express from 'express';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';
import { log } from './log.js';
import * as registry from './registry.js';
import * as S from './sessions.js';
import { postStatus } from './status.js';

const app = express();
app.use(express.json({ limit: '2mb' }));

app.get('/health', (_req, res) => res.json({ ok: true }));

app.use((req, res, next) => {
  if (!config.token || req.get('authorization') !== `Bearer ${config.token}`) return res.status(401).json({ error: 'unauthorised' });
  next();
});

const ID = /^[a-z0-9_-]{1,40}$/i;
app.param('id', (req, res, next, id) => (ID.test(id) ? next() : res.status(400).json({ error: 'bad session id' })));

const wrap = fn => async (req, res) => {
  try { await fn(req, res); } catch (e) {
    const msg = String(e?.message || e);
    log.warn({ path: req.path, err: msg.slice(0, 300) }, 'request failed');
    if (!res.headersSent) res.status(e.status || 500).json({ error: msg.slice(0, 400) });
  }
};
const withTimeout = (p, ms, what) => Promise.race([p, new Promise((_, r) => setTimeout(() => r(new Error(`${what} timed out`)), ms))]);
const needClient = async id => {
  // A browser showing the QR code is not a usable account: only a confirmed connection may read or send.
  const c = registry.get(id).status === 'connected' ? await S.live(id) : null;
  if (!c) { const e = new Error('WhatsApp is not connected'); e.status = 409; throw e; }
  return c;
};
const toJid = to => {
  const v = String(to || '').trim();
  if (v.includes('@')) return v.replace('@s.whatsapp.net', '@c.us');
  const digits = v.replace(/\D/g, '');
  if (digits.length < 7) { const e = new Error('Enter a full phone number with the country code.'); e.status = 400; throw e; }
  return `${digits}@c.us`;
};
/** Only files inside SWM's media folder may be sent (no reading arbitrary paths). */
const mediaPath = p => {
  const full = path.resolve(config.mediaRoot, String(p || '').replace(/^\/+/, ''));
  if (!full.startsWith(path.resolve(config.mediaRoot) + path.sep)) { const e = new Error('bad media path'); e.status = 400; throw e; }
  return full;
};
const name = c => (c?.name || c?.formattedTitle || c?.contact?.name || c?.contact?.pushname || c?.contact?.formattedName || S.phoneFromJid(S.widToString(c?.id)) || '').trim();

// ─── session control ───
app.get('/sessions/:id', wrap(async (req, res) => {
  const r = registry.get(req.params.id);
  res.json({ ...r, live: !!S.clientOf(req.params.id), activity: S.activityOf(req.params.id) });
}));
app.post('/sessions/:id/start', wrap(async (req, res) => {
  void S.start(req.params.id, req.body?.phone).catch(() => {});
  res.json({ ok: true });
}));
app.post('/sessions/:id/logout', wrap(async (req, res) => { await S.logout(req.params.id); res.json({ ok: true }); }));

// ─── chats ───
const previewCache = new Map();
function preview(m) {
  if (!m) return '';
  const kinds = { image: 'Photo', video: 'Video', ptt: 'Voice message', audio: 'Audio', document: 'Document', sticker: 'Sticker', location: 'Location', vcard: 'Contact', revoked: 'This message was deleted' };
  const text = (m.caption || (m.type === 'chat' ? m.body : '') || '').replace(/\s+/g, ' ').trim();
  const label = kinds[m.type] || '';
  return label && text ? `${label}: ${text}` : (text || label || '');
}
app.get('/sessions/:id/chats', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const filter = req.query.filter;
  const opts = { count: Math.min(Number(req.query.count) || 60, 200) };
  if (filter === 'groups') opts.onlyGroups = true;
  if (filter === 'channels') opts.onlyNewsletter = true;
  if (filter === 'unread') opts.onlyWithUnreadMessage = true;
  if (filter === 'users') opts.onlyUsers = true;
  const chats = await withTimeout(c.listChats(opts), 25000, 'listChats');
  const out = [];
  await Promise.all(chats.map(async ch => {
    const jid = S.widToString(ch.id);
    if (!jid || jid === 'status@broadcast') return;
    const key = `${req.params.id}|${jid}|${ch.t || 0}`;
    let last = previewCache.get(key);
    if (last === undefined) {
      try {
        const [m] = await withTimeout(c.getMessages(jid, { count: 1 }), 8000, 'last message');
        last = m ? { text: preview(m), fromMe: !!m.fromMe, ack: m.ack ?? null } : null;
      } catch { last = null; }
      previewCache.set(key, last);
      if (previewCache.size > 3000) previewCache.clear();
    }
    out.push({
      jid, name: name(ch) || jid, t: ch.t || 0, unread: ch.unreadCount || 0,
      kind: jid.endsWith('@g.us') ? 'group' : jid.endsWith('@newsletter') ? 'channel' : 'user',
      pinned: !!ch.pin, muted: !!(ch.muteExpiration && ch.muteExpiration !== 0), archived: !!ch.archive, last,
    });
  }));
  out.sort((a, b) => (b.pinned - a.pinned) || (b.t - a.t));
  res.json({ chats: out });
}));

function shapeMessage(m) {
  const sender = m.sender || {};
  return {
    id: S.widToString(m.id), t: m.t || m.timestamp || 0, fromMe: !!m.fromMe, type: m.type || 'chat',
    text: m.type === 'chat' ? (m.body || '') : (m.caption || ''),
    author: S.widToString(m.author || m.from),
    authorName: (sender.pushname || sender.name || sender.formattedName || '').trim(),
    media: ['image', 'video', 'ptt', 'audio', 'document', 'sticker'].includes(m.type),
    mimetype: m.mimetype || null, filename: m.filename || null, duration: m.duration || null,
    ack: m.ack ?? null,
    quoted: m.quotedMsg ? { text: preview(m.quotedMsg), id: S.widToString(m.quotedStanzaID || '') } : null,
    location: m.type === 'location' ? { lat: m.lat, lng: m.lng } : null,
  };
}
app.get('/sessions/:id/chats/:jid/messages', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const params = { count: Math.min(Number(req.query.count) || 40, 100) };
  if (req.query.before) { params.id = String(req.query.before); params.direction = 'before'; }
  let msgs;
  try {
    msgs = await withTimeout(c.getMessages(req.params.jid, params), 25000, 'getMessages');
  } catch (e) {
    // A new chat (from New chat) has no history yet: show an empty conversation, like WhatsApp Web.
    if (!/chat not found|not found/i.test(String(e?.message || e))) throw e;
    msgs = [];
  }
  let chatName = '';
  try { chatName = name(await c.getChatById(req.params.jid)); } catch { /* optional */ }
  res.json({ name: chatName, messages: msgs.filter(m => m && m.type !== 'e2e_notification' && m.type !== 'notification_template').map(shapeMessage) });
}));
app.post('/sessions/:id/chats/:jid/seen', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  await c.sendSeen(req.params.jid).catch(() => {});
  res.json({ ok: true });
}));

// Decrypted media for one message, cached on disk.
app.get('/sessions/:id/media/:msgId', wrap(async (req, res) => {
  const safe = req.params.msgId.replace(/[^a-zA-Z0-9_@.-]/g, '_').slice(0, 180);
  const dir = path.join(config.cacheDir, req.params.id);
  const file = path.join(dir, safe);
  try {
    const meta = JSON.parse(await fs.readFile(file + '.json', 'utf8'));
    res.type(meta.mimetype || 'application/octet-stream');
    return res.sendFile(file);
  } catch { /* not cached */ }
  const c = await needClient(req.params.id);
  const m = await c.getMessageById(req.params.msgId);
  if (!m) return res.status(404).json({ error: 'message not found' });
  const buf = await withTimeout(c.decryptFile(m), 60000, 'download');
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(file, buf);
  await fs.writeFile(file + '.json', JSON.stringify({ mimetype: m.mimetype, filename: m.filename || null }));
  res.type(m.mimetype || 'application/octet-stream').send(buf);
}));

app.get('/sessions/:id/avatar/:jid', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const p = await withTimeout(c.getProfilePicFromServer(req.params.jid), 10000, 'avatar').catch(() => null);
  res.json({ url: p?.eurl || p?.imgFull || p?.img || null });
}));

// ─── contacts and groups (New chat panel) ───
const contactCache = new Map();          // id -> { at, list }
app.get('/sessions/:id/contacts', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const hit = contactCache.get(req.params.id);
  if (hit && Date.now() - hit.at < 5 * 60_000 && !req.query.fresh) return res.json({ contacts: hit.list });
  const all = await withTimeout(c.getAllContacts(), 30000, 'contacts');
  const seen = new Set(), list = [];
  for (const k of all || []) {
    const jid = S.widToString(k.id);
    if (!jid || !jid.endsWith('@c.us') || seen.has(jid)) continue;
    if (!k.isMyContact || k.isMe || k.isGroup) continue;            // "Contacts on WhatsApp": saved contacts
    if (k.isWAContact === false) continue;
    seen.add(jid);
    list.push({ jid, name: (k.name || k.formattedName || k.pushname || '').trim() || '+' + S.phoneFromJid(jid),
      pushname: (k.pushname || '').trim(), phone: S.phoneFromJid(jid) });
  }
  list.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  contactCache.set(req.params.id, { at: Date.now(), list });
  res.json({ contacts: list });
}));

// body: { name, participants: [jid|phone] }
app.post('/sessions/:id/groups', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const title = String(req.body?.name || '').trim().slice(0, 100);
  const members = [...new Set((req.body?.participants || []).map(toJid))];
  if (!title) return res.status(400).json({ error: 'Give the group a name.' });
  if (!members.length) return res.status(400).json({ error: 'Add at least one member.' });
  const r = await withTimeout(c.createGroup(title, members), 60000, 'createGroup');
  const gid = S.widToString(r?.gid || r?.groupInfo?.id || r?.id || r?.wid) || null;
  S.bump(req.params.id);
  res.json({ ok: true, jid: gid });
}));

// ─── sending ───
// body: { to, text?, file?: path under MEDIA_ROOT, kind?: image|video|audio|voice|document, filename?, quotedId? }
app.post('/sessions/:id/send', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const { text = '', file, kind, filename, quotedId } = req.body || {};
  const jid = toJid(req.body?.to);
  let result;
  if (file) {
    const p = mediaPath(file);
    const fname = filename || path.basename(p);
    if (kind === 'image') result = await c.sendImage(jid, p, fname, text, quotedId);
    else if (kind === 'voice') result = await c.sendPtt(jid, p, fname, '', quotedId);
    else if (kind === 'video') result = await c.sendFile(jid, p, { filename: fname, caption: text, type: 'video', quotedMsg: quotedId });
    else if (kind === 'audio') result = await c.sendFile(jid, p, { filename: fname, type: 'audio', quotedMsg: quotedId });
    else result = await c.sendFile(jid, p, { filename: fname, caption: text, type: 'document', quotedMsg: quotedId });
  } else {
    if (!String(text).trim()) return res.status(400).json({ error: 'Type a message first.' });
    result = await c.sendText(jid, String(text), quotedId ? { quotedMsg: quotedId } : {});
  }
  S.bump(req.params.id);
  res.json({ ok: true, id: S.widToString(result?.id) || null, to: jid });
}));

app.post('/sessions/:id/check-number', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const jid = toJid(req.body?.to);
  const r = await withTimeout(c.checkNumberStatus(jid), 15000, 'number check').catch(() => null);
  res.json({ jid: S.widToString(r?.id) || jid, exists: r ? !!r.numberExists : null });
}));

// body: { kind: text|image|video, text?, file?, caption? }
app.post('/sessions/:id/status', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const { kind = 'text', text = '', file, caption = '' } = req.body || {};
  const r = await postStatus(c, kind, kind === 'text' ? String(text) : mediaPath(file), caption || text);
  S.bump(req.params.id);
  res.json({ ok: true, id: r?.id || null });
}));

app.get('/sessions/:id/targets', wrap(async (req, res) => {
  const c = await needClient(req.params.id);
  const [groups, channels] = await Promise.all([
    withTimeout(c.listChats({ onlyGroups: true }), 25000, 'groups').catch(() => []),
    withTimeout(c.listChats({ onlyNewsletter: true }), 25000, 'channels').catch(() => []),
  ]);
  const map = list => list.map(x => ({ jid: S.widToString(x.id), name: name(x) })).filter(x => x.jid).sort((a, b) => a.name.localeCompare(b.name));
  res.json({ groups: map(groups), channels: map(channels) });
}));

await registry.load();
const server = app.listen(config.port, () => log.info({ port: config.port }, 'whatsapp gateway listening'));
void S.restoreAll();
const stop = async () => { server.close(); await S.shutdown(); process.exit(0); };
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
