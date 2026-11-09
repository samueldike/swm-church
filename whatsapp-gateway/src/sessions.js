// WhatsApp sessions: one WhatsApp Web (WPPConnect + headless Chromium) per tenant id ("b<branch id>").
//
// Persistence is the whole point of this file. A connected session must survive restarts, redeploys,
// browser crashes and network blips, and only end when someone presses Disconnect. The safeguards are
// ported from unana-ai's service (apps/api/src/whatsapp/service.ts), where each one fixed a real outage:
//   * credentials live on the /data volume and are NEVER wiped except by an explicit logout;
//   * stale Chromium singleton locks (left by the previous container) are cleared before every launch;
//   * a reaper SIGKILLs any Chromium that no live session owns, because two browsers on one session token
//     make WhatsApp unpair both;
//   * WPPConnect's autoClose stays 0 (it killed connected sessions); our own login window closes only
//     abandoned QR screens;
//   * at boot every session whose registry entry says "connected" is restored, and a watchdog relaunches
//     any connected session that went silent;
//   * UNPAIRED while the QR is on screen is normal, not a logout.
import { create } from '@wppconnect-team/wppconnect';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';
import { log } from './log.js';
import * as registry from './registry.js';

const sessions = new Map();           // id -> { client, starting, wasConnected }
const restoring = new Set();
const offlineStrikes = new Map();
const activity = new Map();           // id -> counter bumped on every message/ack, polled by the dashboard
const LOGIN_WINDOW_MS = 10 * 60_000;
const WATCHDOG_INTERVAL_MS = 60_000;
const OFFLINE_STRIKES_BEFORE_RESTART = 3;

export const sessionDir = id => path.join(config.sessionDir, id);
const tokenFile = id => path.join(config.sessionDir, `${id}.data.json`);
export const bump = id => activity.set(id, (activity.get(id) || 0) + 1);
export const activityOf = id => activity.get(id) || 0;
export const clientOf = id => sessions.get(id)?.client || null;

export function widToString(v) {
  if (!v) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    if (v._serialized) return String(v._serialized);
    if (v.user && v.server) return `${v.user}@${v.server}`;          // WA-JS wid objects
    if (v.wid || v.id) return widToString(v.wid || v.id);
    return '';
  }
  return String(v);
}
export const phoneFromJid = jid => (jid || '').split('@')[0]?.split(':')[0] || '';

function discardClient(id) {
  const entry = sessions.get(id);
  sessions.delete(id);
  offlineStrikes.delete(id);
  const client = entry?.client;
  if (client) void Promise.resolve().then(() => client.close()).catch(() => {});
}

async function listSessionChromiumPids() {
  const root = path.resolve(config.sessionDir);
  const found = [];
  let names = [];
  try { names = await fs.readdir('/proc'); } catch { return found; }
  for (const name of names) {
    if (!/^\d+$/.test(name)) continue;
    let cmd = '';
    try { cmd = await fs.readFile(`/proc/${name}/cmdline`, 'utf8'); } catch { continue; }
    const args = cmd.split('\0');
    if (!/chrom/i.test(args[0] || '')) continue;
    const dirArg = args.find(a => a.startsWith('--user-data-dir='));
    if (!dirArg) continue;
    const dir = path.resolve(dirArg.slice('--user-data-dir='.length));
    if (!dir.startsWith(root + path.sep)) continue;
    const id = dir.slice(root.length + 1).split(path.sep)[0];
    if (id) found.push({ pid: Number(name), id });
  }
  return found;
}

async function reapOrphanBrowsers(purgeId) {
  let killed = 0;
  for (const { pid, id } of await listSessionChromiumPids().catch(() => [])) {
    if (purgeId) { if (id !== purgeId) continue; }
    else {
      const e = sessions.get(id);
      if ((e && (e.starting || e.client)) || restoring.has(id)) continue;
    }
    try { process.kill(pid, 'SIGKILL'); killed++; } catch { /* gone */ }
  }
  if (killed) log.warn({ killed, purgeId }, 'reaped orphaned Chromium processes');
}

async function clearSingletonLocks(id) {
  for (const n of ['SingletonLock', 'SingletonCookie', 'SingletonSocket']) {
    await fs.rm(path.join(sessionDir(id), n), { force: true }).catch(() => {});
  }
}

const isProfileLocked = e => /profile.*in use|singleton|already running/i.test(String(e?.message || e));

function createOptions(id, phoneNumber) {
  const args = ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run'];
  const opts = {
    session: id,
    waitForLogin: false,
    folderNameToken: config.sessionDir,
    headless: true,
    logQR: false,
    disableWelcome: true,
    updatesLog: false,
    autoClose: 0,                         // MUST stay 0: WPPConnect's autoClose killed connected sessions
    deviceSyncTimeout: 180000,
    browserArgs: args,
    puppeteerOptions: { executablePath: config.chromium, args, protocolTimeout: 240000 },
    catchQR: qr => {
      const code = typeof qr === 'string' && qr ? (qr.startsWith('data:') ? qr : `data:image/png;base64,${qr}`) : '';
      if (code) void registry.patch(id, { status: 'qr', qr: code });
    },
    statusFind: status => {
      const s = String(status).toLowerCase();
      if (s === 'qr') return void registry.patch(id, { status: 'qr' });
      if (s.includes('autoclose') || s === 'browserclose') return void handleBrowserClosed(id, s);
      if (s === 'islogged' || s === 'qrreadsuccess' || s === 'inchat') {
        // may fire before setupClient registers the client; probeLogin() catches that case
        setTimeout(() => { const e = sessions.get(id); if (e?.client) void markConnected(id, e.client); }, 500);
      }
      // 'notLogged' fires during normal phone pairing: never treat it as a disconnect.
    },
  };
  if (phoneNumber) {
    opts.phoneNumber = phoneNumber;
    opts.catchLinkCode = code => { if (code) void registry.patch(id, { status: 'connecting', linkCode: code }); };
  }
  return opts;
}

function scheduleLoginWindow(id, client) {
  const t = setTimeout(async () => {
    const e = sessions.get(id);
    if (!e || e.client !== client || e.wasConnected) return;
    const wasConnected = registry.get(id).status === 'connected';
    discardClient(id);
    if (wasConnected) {
      log.warn({ id }, 'login window expired during restore: relaunching');
      return void tryRestore(id);
    }
    log.warn({ id }, 'login window expired without pairing: closing browser');
    await registry.patch(id, { status: 'disconnected', qr: null, linkCode: null });
  }, LOGIN_WINDOW_MS);
  t.unref?.();
}

async function handleBrowserClosed(id, status) {
  const e = sessions.get(id);
  if (e?.starting || restoring.has(id)) return;
  const wasConnected = e?.wasConnected === true || registry.get(id).status === 'connected';
  discardClient(id);
  if (wasConnected) {
    log.warn({ id, status }, 'browser of a connected session closed: restoring');
    setTimeout(() => void tryRestore(id), 5000);
    return;
  }
  await registry.patch(id, { status: 'disconnected', qr: null, linkCode: null });
}

async function setupClient(id, client) {
  sessions.set(id, { client, starting: false });
  scheduleLoginWindow(id, client);
  client.onStateChange(state => {
    const s = String(state).toLowerCase();
    if (s.includes('connected') || s.includes('inchat') || s.includes('success')) {
      setTimeout(() => void markConnected(id, client), 1500);
      return;
    }
    if (s.includes('unpaired') || s.includes('conflict')) {
      // UNPAIRED is the normal state while the QR waits; only a session that HAD connected was logged out.
      if (sessions.get(id)?.wasConnected !== true && !s.includes('conflict')) return;
      if (s.includes('conflict')) {
        // Another WhatsApp Web tab took over: take the session back instead of giving up.
        void client.useHere?.().catch(() => {});
        return;
      }
      log.warn({ id, state }, 'logged out from the phone: marking disconnected');
      discardClient(id);
      void registry.patch(id, { status: 'disconnected', qr: null, linkCode: null, jid: null, phone: null,
        lastError: 'WhatsApp was logged out from the phone (Linked devices). Connect again to continue.' });
      return;
    }
    if (s.includes('close') || s.includes('timeout')) {
      log.warn({ id, state }, 'connection dropped: restoring');
      discardClient(id);
      setTimeout(() => void tryRestore(id), 5000);
    }
  });
  client.onAnyMessage(() => bump(id));
  client.onAck?.(() => bump(id));
  // A saved login reports "isLogged" (and CONNECTED / MAIN) while create() is still running, before this
  // client is registered, so those events are missed. Ask the page directly until it is linked.
  client.onInterfaceChange?.(st => {
    if (String(st?.mode || st || '').toUpperCase() === 'MAIN') void markConnected(id, client);
  });
  probeLogin(id, client);
  log.info({ id }, 'client ready, waiting for state confirmation');
}

function probeLogin(id, client) {
  let tries = 0;
  const tick = async () => {
    const e = sessions.get(id);
    if (!e || e.client !== client || e.wasConnected || ++tries > LOGIN_WINDOW_MS / 5000) return;
    let linked = false;
    try { linked = (await client.isLoggedIn?.()) === true; } catch { /* page busy */ }
    if (!linked) {
      try { const host = await client.getHostDevice(); linked = Boolean(host?.wid || host?.id); } catch { /* not yet */ }
    }
    if (linked) await markConnected(id, client);
    const now = sessions.get(id);
    if (now && now.client === client && !now.wasConnected) setTimeout(tick, 5000).unref?.();
  };
  setTimeout(tick, 2000).unref?.();
}

// Only a real WhatsApp id counts ("2348012345678@c.us"); the device suffix (":12") is dropped.
const asJid = j => { const m = String(j || '').match(/^(\d+)(?::\d+)?@(c\.us|s\.whatsapp\.net)$/); return m ? `${m[1]}@c.us` : null; };

async function markConnected(id, client) {
  let jid = null;
  for (let i = 0; i < 5 && !jid; i++) {
    try {
      const host = await client.getHostDevice();
      jid = asJid(widToString(host?.wid || host?.me || host?.id));
    } catch { /* retry */ }
    if (!jid) { try { jid = asJid(widToString(await client.getWid())); } catch { jid = null; } }
    if (!jid) {
      try {
        jid = widToString(await client.page.evaluate(() => {
          const str = (w) => w ? (typeof w === 'string' ? w : (w._serialized || (w.user && w.server ? `${w.user}@${w.server}` : null))) : null;
          let w = null;
          try { w = str(window.WPP?.conn?.getMyUserId?.()); } catch { /* not ready */ }
          if (!w) try { w = str(window.WPP?.whatsapp?.UserPrefs?.getMaybeMeUser?.()); } catch { /* not ready */ }
          if (!w) {
            // WhatsApp Web keeps the account id in localStorage, e.g. "\"2348012345678:5@c.us\""
            const raw = localStorage.getItem('last-wid-md') || localStorage.getItem('last-wid') || '';
            const m = raw.replace(/"/g, '').match(/^(\d+)(?::\d+)?@(c\.us|s\.whatsapp\.net)$/);
            if (m) w = `${m[1]}@c.us`;
          }
          return w;
        }));
        jid = asJid(jid);
      } catch { jid = null; }
    }
    if (jid && !jid.includes('@')) jid = null;
    if (!jid && i < 4) await new Promise(r => setTimeout(r, 2000));
  }
  if (!jid) {
    let seen = null;
    try {
      seen = await client.page.evaluate(() => ({
        wpp: typeof window.WPP, conn: typeof window.WPP?.conn?.getMyUserId,
        ls: Object.keys(localStorage).filter(k => /wid/i.test(k)).map(k => [k, String(localStorage.getItem(k)).slice(0, 40)]),
      }));
    } catch (err) { seen = String(err?.message || err).slice(0, 200); }
    log.warn({ id, seen }, 'linked but the account id could not be read yet');
    return;
  }
  const e = sessions.get(id);
  if (e) e.wasConnected = true;
  let name = '';
  try { const me = await client.getContact(jid); name = me?.pushname || me?.name || ''; } catch { /* optional */ }
  await registry.patch(id, { status: 'connected', jid, phone: phoneFromJid(jid), name, qr: null, linkCode: null,
    lastError: null, connectedAt: registry.get(id).connectedAt || new Date().toISOString() });
  bump(id);
}

export async function start(id, phoneNumber) {
  phoneNumber = (phoneNumber || '').replace(/\D/g, '') || undefined;
  const existing = sessions.get(id);
  if (existing?.starting) return;
  if (existing?.client) {
    try {
      const host = await existing.client.getHostDevice();
      if (host?.wid && !phoneNumber) return void markConnected(id, existing.client);
    } catch { /* stale */ }
    discardClient(id);
  }
  sessions.set(id, { client: null, starting: true });
  await registry.patch(id, { status: 'connecting', qr: null, linkCode: null, lastError: null });
  const launch = async () => {
    await fs.mkdir(sessionDir(id), { recursive: true });
    await reapOrphanBrowsers(id);
    await clearSingletonLocks(id);
    const client = await create(createOptions(id, phoneNumber));
    await setupClient(id, client);
  };
  try {
    await launch();
  } catch (e) {
    if (isProfileLocked(e)) {
      try { await launch(); return; } catch (e2) { e = e2; }
    }
    sessions.delete(id);
    await registry.patch(id, { status: 'disconnected', qr: null, linkCode: null, lastError: 'Could not start WhatsApp Web. Try again.' });
    log.error({ id, err: String(e?.message || e) }, 'start failed');
    throw e;
  }
}

export async function tryRestore(id) {
  if (restoring.has(id)) return;
  restoring.add(id);
  try {
    if (registry.get(id).status !== 'connected') return;
    const has = await fs.access(sessionDir(id)).then(() => true).catch(() => false);
    if (!has) {
      await registry.patch(id, { status: 'disconnected', lastError: 'The saved WhatsApp login is missing. Connect again.' });
      return;
    }
    sessions.set(id, { client: null, starting: true });
    await reapOrphanBrowsers(id);
    await clearSingletonLocks(id);
    const client = await create(createOptions(id));
    await setupClient(id, client);
  } catch (e) {
    log.warn({ id, err: String(e?.message || e) }, 'restore failed (watchdog will retry)');
    discardClient(id);
  } finally {
    restoring.delete(id);
  }
}

/** The live client, restoring it first if the browser went away. Null while not connected. */
export async function live(id) {
  const c = sessions.get(id)?.client;
  if (!c) { await tryRestore(id); return sessions.get(id)?.client || null; }
  try { await c.getHostDevice(); return c; } catch {
    discardClient(id); await tryRestore(id); return sessions.get(id)?.client || null;
  }
}

/** Manual disconnect: the only path that logs out and wipes the saved credentials. */
export async function logout(id) {
  const e = sessions.get(id);
  if (e?.client) {
    try { await e.client.logout(); } catch { /* ignore */ }
    try { await e.client.close(); } catch { /* ignore */ }
  }
  sessions.delete(id);
  await reapOrphanBrowsers(id);
  await fs.rm(sessionDir(id), { recursive: true, force: true }).catch(() => {});
  await fs.rm(tokenFile(id), { force: true }).catch(() => {});
  await registry.patch(id, { status: 'disconnected', qr: null, linkCode: null, jid: null, phone: null, name: '', lastError: null, connectedAt: null });
}

async function watchdog() {
  for (const id of registry.connectedIds()) {
    if (restoring.has(id)) continue;
    const e = sessions.get(id);
    if (e?.starting) continue;
    if (!e?.client) { log.warn({ id }, 'watchdog: connected session has no browser: restoring'); await tryRestore(id); continue; }
    if (!e.wasConnected) continue;
    let dead = false;
    try {
      const online = await Promise.race([e.client.isConnected(), new Promise((_, r) => setTimeout(() => r(new Error('timeout')), 15000))]);
      if (online) { offlineStrikes.delete(id); continue; }
      const n = (offlineStrikes.get(id) || 0) + 1;
      offlineStrikes.set(id, n);
      dead = n >= OFFLINE_STRIKES_BEFORE_RESTART;
    } catch { dead = true; }
    if (dead) { log.warn({ id }, 'watchdog: session unresponsive: relaunching'); offlineStrikes.delete(id); discardClient(id); await tryRestore(id); }
  }
}

export async function restoreAll() {
  await fs.mkdir(config.sessionDir, { recursive: true });
  await reapOrphanBrowsers().catch(() => {});
  setInterval(() => void reapOrphanBrowsers().catch(() => {}), 60_000).unref?.();
  setInterval(() => void watchdog().catch(e => log.warn({ err: String(e) }, 'watchdog sweep failed')), WATCHDOG_INTERVAL_MS).unref?.();
  for (const id of registry.connectedIds()) {
    log.info({ id }, 'restoring session');
    void tryRestore(id);
  }
}

export async function shutdown() {
  await Promise.all([...sessions.values()].map(e => e.client?.close().catch(() => {})));
}
