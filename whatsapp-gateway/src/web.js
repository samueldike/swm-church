// WhatsApp Web features for SWM's console: chat and message actions, contact/group info, shared media,
// search, starred messages, statuses, presence, profile, group admin, polls/location/contact cards and
// voice notes. Registered by server.js (same auth and helpers). Everything acts on the church's own
// linked account, exactly as the official WhatsApp Web would.
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import * as registry from './registry.js';
import * as S from './sessions.js';

const run = (cmd, args) => new Promise((resolve, reject) =>
  execFile(cmd, args, { timeout: 120000 }, (err, _out, stderr) => (err ? reject(new Error(String(stderr || err.message).slice(-300))) : resolve())));

/** Browser recordings (webm/opus, m4a…) become WhatsApp voice notes: OGG/Opus, mono, 48 kHz. */
export async function toVoice(file) {
  if (/\.(ogg|opus)$/i.test(file)) return file;
  const out = path.join(os.tmpdir(), `voice-${Date.now()}-${Math.random().toString(36).slice(2)}.ogg`);
  await run('ffmpeg', ['-y', '-loglevel', 'error', '-i', file, '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k', out]);
  setTimeout(() => void fs.rm(out, { force: true }), 10 * 60_000).unref?.();
  return out;
}

/** Reactions are not part of the message list: fetch them for the messages that have any. */
export async function withReactions(c, shaped, raw, withTimeout) {
  await Promise.all(raw.map(async (m, i) => {
    if (!m?.hasReaction) return;
    try {
      const r = await withTimeout(c.getReactions(S.widToString(m.id)), 6000, 'reactions');
      const list = (r?.reactions || []).map(x => ({ emoji: x.aggregateEmoji || x.id, count: (x.senders || []).length || 1, mine: !!x.hasReactionByMe }));
      if (list.length) shaped[i].reactions = list;
    } catch { /* optional */ }
  }));
  return shaped;
}

export function registerWeb(app, h) {
  const { wrap, needClient, withTimeout, toJid, mediaPath, name, shapeMessage, G, firstContact } = h;
  // Run a send through the anti-ban guard: refuse past the limits, count it, and let failures pull the brake.
  async function guarded(sid, kinds, n, risky, fn) {
    G.check(sid, kinds, n, { risky });
    let r;
    try { r = await fn(); } catch (e) { G.failure(sid, e?.message); throw e; }
    G.record(sid, kinds, n);
    return r;
  }
  const ev = (c, fn, ...args) => withTimeout(c.page.evaluate(fn, ...args), 30000, 'WhatsApp Web');
  const ok = (req, res, extra = {}) => { S.bump(req.params.id); res.json({ ok: true, ...extra }); };
  const bad = (msg, status = 400) => Object.assign(new Error(msg), { status });
  const myJid = id => registry.get(id).jid || '';
  const clean = list => list.filter(m => m && m.type !== 'e2e_notification' && m.type !== 'notification_template' && m.type !== 'gp2' && m.type !== 'protocol');

  // WhatsApp now hides many people behind a "lid" id (xxx@lid) that is NOT a phone number. Resolve it to the
  // real number and saved name when the phone knows them, so nobody is shown as "+1327279165550".
  const people = new Map();                       // session|jid -> { name, pushname, phone }
  async function person(c, sid, jid) {
    const key = sid + '|' + jid;
    if (people.has(key)) return people.get(key);
    let k = await withTimeout(c.getContact(jid), 5000, 'contact').catch(() => null);
    let phone = jid.endsWith('@c.us') ? S.phoneFromJid(jid) : '';
    if (!phone && jid.endsWith('@lid')) {
      const e = await withTimeout(c.getPnLidEntry(jid), 5000, 'lid').catch(() => null);
      const pn = S.widToString(e?.phoneNumber || e?.phone || e?.pn);
      if (pn && pn.includes('@')) {
        phone = S.phoneFromJid(pn);
        const k2 = await withTimeout(c.getContact(pn.replace('@s.whatsapp.net', '@c.us')), 5000, 'contact').catch(() => null);
        if (k2) k = { ...k, ...Object.fromEntries(Object.entries(k2).filter(([, v]) => v)) };
      }
    }
    const out = { name: (k?.name || k?.verifiedName || '').trim(), pushname: (k?.pushname || '').trim(), phone };
    people.set(key, out);
    if (people.size > 20000) people.clear();
    return out;
  }
  const label = p => p.name || (p.pushname ? '~' + p.pushname : (p.phone ? '+' + p.phone : 'Unknown'));

  const shapeAll = async (c, raw) => withReactions(c, raw.map(shapeMessage), raw, withTimeout);

  // ─── chat actions: archive, pin, mute, read/unread, clear, delete, block, typing, leave ───
  app.post('/sessions/:id/chats/:jid/action', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = req.params.jid, { action, value } = req.body || {};
    switch (action) {
      case 'archive': await c.archiveChat(jid, !!value); break;
      case 'pin':
        try { await c.pinChat(jid, !!value); } catch (e) {
          if (/max|limit|3/i.test(String(e?.message))) throw bad('You can only pin up to 3 chats.');
          throw e;
        }
        break;
      case 'mute':
        if (!value) await ev(c, j => window.WPP.chat.unmute(j), jid);
        else await ev(c, (j, v) => window.WPP.chat.mute(j, v === 'always' ? { expiration: -1 } : { duration: Number(v) }), jid, value);
        break;
      case 'unread': await c.markUnseenMessage(jid); break;
      case 'read': await c.sendSeen(jid).catch(() => {}); break;
      case 'clear': await c.clearChat(jid, true); break;
      case 'delete': await c.deleteChat(jid); break;
      case 'block': await c.blockContact(jid); break;
      case 'unblock': await c.unblockContact(jid); break;
      case 'leave': await c.leaveGroup(jid); break;
      case 'presence': await c.subscribePresence(jid).catch(() => {}); return res.json({ ok: true });
      case 'typing':
        if (value === 'typing') await c.startTyping(jid).catch(() => {});
        else if (value === 'recording') await c.startRecording(jid).catch(() => {});
        else await c.stopTyping(jid).catch(() => {});
        return res.json({ ok: true });
      default: throw bad('Unknown action.');
    }
    ok(req, res);
  }));

  // ─── message actions: react, star, delete, edit, pin, forward, votes ───
  app.post('/sessions/:id/messages/action', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const { action, id, chat, value } = req.body || {};
    if (!id) throw bad('Which message?');
    switch (action) {
      case 'react': await c.sendReactionToMessage(id, value || false); break;
      case 'star': await c.starMessage(id, !!value); break;
      case 'delete': await c.deleteMessage(chat, id, !value); break;            // value=true: delete for everyone
      case 'edit':
        if (!String(value || '').trim()) throw bad('Type the new text.');
        await c.editMessage(id, String(value));
        break;
      case 'pin': await ev(c, (m, on) => window.WPP.chat.pinMsg(m, !!on), id, !!value); break;
      case 'forward': {
        const to = [...new Set((Array.isArray(value) ? value : [value]).filter(Boolean).map(toJid))].slice(0, 5);
        if (!to.length) throw bad('Choose who to forward to.');
        const ids = Array.isArray(id) ? id : [id];
        for (const t of to) {
          const first = await firstContact(c, t);
          for (const m of ids) await guarded(req.params.id, ['msg', 'forward', ...first], 1, first.length > 0, () => c.forwardMessage(t, m));
        }
        break;
      }
      case 'votes': {
        const [v, msg] = await Promise.all([withTimeout(c.getVotes(id), 15000, 'votes'), c.getMessageById(id).catch(() => null)]);
        const opts = msg?.pollOptions || [];
        // WA-JS gives the chosen options as their local ids (numbers): turn them back into the option names.
        const label = o => (typeof o === 'number' ? (opts.find(p => p.localId === o)?.name ?? opts[o]?.name ?? String(o)) : (o?.name ?? String(o)));
        return res.json({ votes: (v?.votes || []).map(x => ({ who: S.widToString(x.sender), options: (x.selectedOptions || []).filter(o => o !== null && o !== undefined).map(label) })) });
      }
      default: throw bad('Unknown action.');
    }
    ok(req, res);
  }));

  // ─── around a message (jump from search, quotes, starred) ───
  app.get('/sessions/:id/chats/:jid/around/:msgId', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = req.params.jid, id = req.params.msgId;
    const [before, after, self] = await Promise.all([
      withTimeout(c.getMessages(jid, { count: 25, id, direction: 'before' }), 25000, 'messages').catch(() => []),
      withTimeout(c.getMessages(jid, { count: 25, id, direction: 'after' }), 25000, 'messages').catch(() => []),
      c.getMessageById(id).catch(() => null),
    ]);
    const all = clean([...before, ...(self ? [self] : []), ...after]);
    const seen = new Set();
    const uniq = all.filter(m => { const k = S.widToString(m.id); return !seen.has(k) && seen.add(k); });
    res.json({ messages: await shapeAll(c, uniq), more: after.length >= 25 });
  }));

  // ─── search inside one chat (WhatsApp Web searches the loaded history) ───
  app.get('/sessions/:id/chats/:jid/search', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const q = String(req.query.q || '').trim().toLowerCase();
    if (q.length < 2) return res.json({ results: [] });
    const msgs = await withTimeout(c.getMessages(req.params.jid, { count: 1500 }), 40000, 'search');
    const hits = clean(msgs).filter(m => String(m.type === 'chat' ? m.body : (m.caption || m.filename || '')).toLowerCase().includes(q));
    res.json({ results: hits.slice(-100).reverse().map(shapeMessage) });
  }));

  // ─── shared media, links and documents ───
  app.get('/sessions/:id/chats/:jid/shared', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const msgs = clean(await withTimeout(c.getMessages(req.params.jid, { count: 800 }), 40000, 'media'));
    const kind = req.query.kind;
    const pick = kind === 'docs' ? m => m.type === 'document'
      : kind === 'links' ? m => /https?:\/\/\S+/i.test(m.type === 'chat' ? m.body || '' : m.caption || '')
        : m => m.type === 'image' || m.type === 'video';
    res.json({ items: msgs.filter(pick).reverse().slice(0, 150).map(shapeMessage) });
  }));

  // ─── contact / group info ───
  app.get('/sessions/:id/chats/:jid/info', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = req.params.jid;
    const ch = await c.getChatById(jid).catch(() => null);
    const out = {
      jid, name: name(ch) || '+' + S.phoneFromJid(jid),
      kind: jid.endsWith('@g.us') ? 'group' : jid.endsWith('@newsletter') ? 'channel' : 'user',
      archived: !!ch?.archive, pinned: !!ch?.pin, muteUntil: ch?.muteExpiration || 0,
    };
    if (out.kind === 'user') {
      const [contact, about, blocked, common] = await Promise.all([
        c.getContact(jid).catch(() => null),
        withTimeout(c.getStatus(jid), 8000, 'about').catch(() => null),
        withTimeout(c.getBlockList(), 8000, 'blocklist').catch(() => []),
        withTimeout(c.getCommonGroups(jid), 10000, 'common groups').catch(() => []),
      ]);
      out.phone = S.phoneFromJid(jid);
      out.pushname = contact?.pushname || '';
      out.business = !!contact?.isBusiness;
      out.saved = !!contact?.isMyContact;
      out.about = about?.status || '';
      out.blocked = (blocked || []).map(S.widToString).includes(jid);
      out.common = await Promise.all((common || []).slice(0, 20).map(async g => {
        const gid = S.widToString(g?.id || g);
        const gc = await c.getChatById(gid).catch(() => null);
        return { jid: gid, name: name(gc) || 'Group' };
      }));
    } else if (out.kind === 'group') {
      const meta = ch?.groupMetadata || {};
      const me = myJid(req.params.id);
      out.description = meta.desc || '';
      out.created = meta.creation || 0;
      out.announce = !!meta.announce;
      const parts = (meta.participants || []).slice(0, 1024);
      out.participants = await Promise.all(parts.map(async p => {
        const pj = S.widToString(p.id);
        const who = await person(c, req.params.id, pj);
        const isMe = pj === me || (who.phone && who.phone === S.phoneFromJid(me));
        return { jid: pj, name: who.name, pushname: who.pushname, phone: who.phone, admin: !!(p.isAdmin || p.isSuperAdmin), owner: !!p.isSuperAdmin, me: isMe };
      }));
      // WhatsApp Web order: you, then admins, then saved contacts A-Z, then everyone else
      out.participants.sort((a, b) => (b.me - a.me) || (b.admin - a.admin) || (!!b.name - !!a.name) || label(a).localeCompare(label(b)));
      out.iAmAdmin = out.participants.some(p => p.me && p.admin);
      out.count = (meta.participants || []).length;
    }
    res.json(out);
  }));

  // ─── presence: online, last seen, typing… ───
  app.get('/sessions/:id/chats/:jid/presence', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = req.params.jid;
    const p = await ev(c, j => {
      const s = window.WPP?.whatsapp?.PresenceStore?.get(j);
      if (!s) return null;
      const typing = s.isGroup
        ? (s.chatstates?.getModelsArray?.() || []).filter(x => x.type === 'typing' || x.type === 'recording_audio').map(x => ({ who: x.id?._serialized || String(x.id), type: x.type }))
        : [];
      return { online: !!s.isOnline, state: s.chatstate?.type || null, typing };
    }, jid).catch(() => null);
    let lastSeen = null;
    if (jid.endsWith('@c.us') && !p?.online) lastSeen = await withTimeout(c.getLastSeen(jid), 6000, 'last seen').catch(() => null);
    res.json({ online: !!p?.online, state: p?.state || null, typing: p?.typing || [], lastSeen: typeof lastSeen === 'number' ? lastSeen : null });
  }));

  // ─── names for ids the page could not resolve (group message authors on @lid) ───
  app.post('/sessions/:id/people', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const ids = [...new Set((req.body?.ids || []).map(String))].filter(j => /@(c\.us|lid)$/.test(j)).slice(0, 300);
    const out = {};
    await Promise.all(ids.map(async j => { const p = await person(c, req.params.id, j); out[j] = { ...p, label: label(p) }; }));
    res.json({ people: out });
  }));

  // ─── starred messages (all chats) ───
  app.get('/sessions/:id/starred', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const list = await ev(c, async () => {
      const st = window.WPP?.whatsapp?.StarredMsgStore;
      if (!st) return [];
      try { await st.loadMore?.(); } catch { /* fine */ }
      return st.getModelsArray().slice(-300).map(m => ({
        id: m.id?._serialized, chat: m.id?.remote?._serialized || String(m.id?.remote || ''), t: m.t, fromMe: !!m.id?.fromMe, type: m.type,
        text: m.type === 'chat' ? (m.body || '') : (m.caption || m.filename || ''), author: m.author?._serialized || m.from?._serialized || '',
      }));
    });
    res.json({ messages: (list || []).reverse() });
  }));

  // ─── statuses (Updates) ───
  app.get('/sessions/:id/statuses', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const me = myJid(req.params.id);
    const raw = await ev(c, () => {
      const st = window.WPP?.whatsapp?.StatusV3Store;
      if (!st) return [];
      return st.getModelsArray().map(s => {
        const list = s.msgs?.getModelsArray?.() || [];
        const unread = Math.max(0, s.unreadCount || 0);
        // WhatsApp keeps an unread count per contact: the last <unread> statuses are the ones not seen yet.
        return {
          jid: s.id?._serialized || String(s.id), t: s.t || 0, unread,
          msgs: list.map((m, i) => ({
            id: m.id?._serialized, type: m.type, t: m.t, text: m.type === 'chat' ? (m.body || '') : (m.caption || ''),
            bg: m.backgroundColor ? '#' + (m.backgroundColor >>> 0).toString(16).padStart(8, '0').slice(2) : null, viewed: i < list.length - unread,
          })),
        };
      });
    });
    const out = await Promise.all((raw || []).filter(s => s.msgs.length).map(async s => {
      const who = await person(c, req.params.id, s.jid);
      const mine = s.jid === me || (who.phone && who.phone === S.phoneFromJid(me));
      return { ...s, mine, name: label(who), phone: who.phone };
    }));
    out.sort((a, b) => (b.mine - a.mine) || (b.t - a.t));
    res.json({ statuses: out });
  }));
  app.post('/sessions/:id/statuses/seen', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const { jid, msgId } = req.body || {};
    await withTimeout(c.sendReadStatus(jid, msgId), 10000, 'status seen').catch(() => {});
    res.json({ ok: true });
  }));

  // ─── my profile ───
  app.get('/sessions/:id/me', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = myJid(req.params.id);
    const [about, pic] = await Promise.all([
      withTimeout(c.getStatus(jid), 8000, 'about').catch(() => null),
      withTimeout(c.getProfilePicFromServer(jid), 8000, 'photo').catch(() => null),
    ]);
    const r = registry.get(req.params.id);
    let profileName = await withTimeout(c.getProfileName(), 6000, 'name').catch(() => '');
    if (typeof profileName !== 'string') profileName = '';
    if (!profileName || /^\+?[\d\s()-]+$/.test(profileName)) profileName = (r.name && !/^\+?[\d\s()-]+$/.test(r.name)) ? r.name : '';
    res.json({ jid, phone: r.phone, name: profileName, about: about?.status || '', avatar: pic?.eurl || pic?.imgFull || pic?.img || null });
  }));
  app.post('/sessions/:id/me', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const { name: newName, about, photo } = req.body || {};
    if (typeof newName === 'string' && newName.trim()) {
      await c.setProfileName(newName.trim().slice(0, 25));
      await registry.patch(req.params.id, { name: newName.trim().slice(0, 25) });
    }
    if (typeof about === 'string') await c.setProfileStatus(about.slice(0, 139));
    if (photo) await c.setProfilePic(mediaPath(photo));
    ok(req, res);
  }));

  // ─── group admin ───
  app.post('/sessions/:id/groups/:jid/action', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const jid = req.params.jid, { action, value } = req.body || {};
    const people = () => [...new Set((Array.isArray(value) ? value : [value]).filter(Boolean).map(toJid))];
    switch (action) {
      case 'subject': if (!String(value || '').trim()) throw bad('Type the group name.'); await c.setGroupSubject(jid, String(value).trim().slice(0, 100)); break;
      case 'description': await c.setGroupDescription(jid, String(value || '').slice(0, 2048)); break;
      case 'add': { const list = people(); await guarded(req.params.id, ['group_add'], list.length, true, () => c.addParticipant(jid, list)); break; }
      case 'remove': await c.removeParticipant(jid, people()); break;
      case 'promote': await c.promoteParticipant(jid, people()); break;
      case 'demote': await c.demoteParticipant(jid, people()); break;
      case 'announce': await c.setMessagesAdminsOnly(jid, !!value); break;
      case 'icon': await c.setGroupIcon(jid, mediaPath(value)); break;
      case 'invite': return res.json({ link: await c.getGroupInviteLink(jid) });
      case 'revoke': return res.json({ link: await c.revokeGroupInviteLink(jid) });
      default: throw bad('Unknown action.');
    }
    ok(req, res);
  }));

  // ─── poll, location, contact card ───
  app.post('/sessions/:id/send-extra', wrap(async (req, res) => {
    const c = await needClient(req.params.id);
    const { kind } = req.body || {};
    const to = toJid(req.body?.to);
    const first = await firstContact(c, to);
    G.check(req.params.id, ['msg', ...first], 1, { risky: first.length > 0 });
    let r;
    try {
    if (kind === 'poll') {
      const q = String(req.body.question || '').trim().slice(0, 255);
      const choices = [...new Set((req.body.options || []).map(o => String(o).trim().slice(0, 100)).filter(Boolean))].slice(0, 12);
      if (!q || choices.length < 2) throw bad('A poll needs a question and at least two options.');
      r = await c.sendPollMessage(to, q, choices, { selectableCount: req.body.multiple ? 0 : 1 });
    } else if (kind === 'location') {
      const lat = Number(req.body.lat), lng = Number(req.body.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) throw bad('Location not available.');
      r = await c.sendLocation(to, String(lat), String(lng), String(req.body.title || '').slice(0, 100));
    } else if (kind === 'contact') {
      r = await c.sendContactVcard(to, toJid(req.body.contact), String(req.body.name || '').slice(0, 100));
    } else throw bad('Unknown kind.');
    } catch (e) { if (e.status !== 400) G.failure(req.params.id, e?.message); throw e; }
    G.record(req.params.id, ['msg', ...first]);
    ok(req, res, { id: S.widToString(r?.id) || null });
  }));
}
