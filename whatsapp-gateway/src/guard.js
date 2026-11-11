// Anti-ban guardrails. Every outgoing action of a WhatsApp account passes through here (chat messages,
// Post to many, forwards, polls, contact cards, statuses, group creation and adds).
//
// WhatsApp restricts or bans numbers that behave like bulk senders: many messages in a short time, many first
// messages to people who never chatted with the number (worst: people who have not saved it), many groups
// created or people added, and messages that keep failing. So, per linked account:
//   * rolling limits per minute / hour / day for each kind of action (LIMITS below), counted across restarts;
//   * first messages are counted separately: "new" (a saved contact with no chat yet) and "cold" (a number
//     that is not a saved contact and never chatted: the riskiest kind);
//   * a brake: when WhatsApp answers with signs of rate limiting or restriction, or sends keep failing,
//     bulk sending and first messages stop for a while (replies to existing chats still work).
// Limits are deliberately conservative: a church account that stays well inside them looks like a person.
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';

export const LIMITS = {
  msg: { minute: 15, hour: 200, day: 1000 },     // any message (chats, groups, channels)
  bulk: { minute: 6, hour: 120, day: 400 },      // messages sent by Post to many
  new: { hour: 10, day: 30 },                    // first message to a saved contact you have no chat with
  cold: { hour: 3, day: 8 },                     // first message to a number that is not a saved contact
  forward: { hour: 30, day: 150 },
  group_create: { day: 3 },
  group_add: { hour: 10, day: 30 },              // people added to groups
  status: { hour: 6, day: 20 },
};
export const LABELS = {
  msg: 'messages', bulk: 'Post-to-many messages', new: 'first messages to contacts you have not chatted with',
  cold: 'messages to numbers that are not saved contacts', forward: 'forwards', group_create: 'new groups',
  group_add: 'people added to groups', status: 'status updates',
};
const WINDOW = { minute: 60_000, hour: 3_600_000, day: 86_400_000 };
const BRAKE_PATTERN = /rate.?over.?limit|too many|429|spam|banned|restricted|not.?allowed.?to.?send|temporarily|blocked/i;

const file = () => path.join(config.dataDir, 'guard.json');
let state = {};                    // id -> { ev: { kind: [timestamps] }, pausedUntil, pauseReason, fails: [timestamps] }
let saveTimer = null;

export async function load() {
  try { state = JSON.parse(await fs.readFile(file(), 'utf8')) || {}; } catch { state = {}; }
}
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { const tmp = file() + '.tmp'; await fs.writeFile(tmp, JSON.stringify(state)); await fs.rename(tmp, file()); } catch { /* next time */ }
  }, 1000);
  saveTimer.unref?.();
}
const s = id => (state[id] ||= { ev: {}, fails: [] });
function prune(id) {
  const cut = Date.now() - WINDOW.day, st = s(id);
  for (const k of Object.keys(st.ev)) st.ev[k] = st.ev[k].filter(t => t > cut);
  st.fails = (st.fails || []).filter(t => t > Date.now() - 600_000);
}

export class SafetyLimit extends Error {
  constructor(message, retryAt) { super(message); this.status = 429; this.retryAt = retryAt; }
}
const when = t => new Date(t).toISOString();
const human = ms => (ms < 120_000 ? `${Math.ceil(ms / 1000)} seconds` : ms < 7_200_000 ? `${Math.ceil(ms / 60_000)} minutes` : `${Math.ceil(ms / 3_600_000)} hours`);

/** Throws SafetyLimit if any of `kinds` would exceed a limit. `opts.risky`: bulk / first-message actions. */
export function check(id, kinds, n = 1, opts = {}) {
  prune(id);
  const st = s(id), now = Date.now();
  if (opts.risky && st.pausedUntil && st.pausedUntil > now) {
    throw new SafetyLimit(`Sending is paused for safety until ${new Date(st.pausedUntil).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })} UTC: ${st.pauseReason || 'WhatsApp pushed back'}. Replies to existing chats still work.`, when(st.pausedUntil));
  }
  for (const k of kinds) {
    const lim = LIMITS[k]; if (!lim) continue;
    const list = st.ev[k] || [];
    for (const [w, max] of Object.entries(lim)) {
      const inWin = list.filter(t => t > now - WINDOW[w]);
      if (inWin.length + n > max) {
        const freeAt = (inWin[inWin.length - max + n - 1] ?? now) + WINDOW[w];
        throw new SafetyLimit(`Safety limit reached: at most ${max} ${LABELS[k]} per ${w}. This protects the number from being banned by WhatsApp. Try again in about ${human(freeAt - now)}.`, when(freeAt));
      }
    }
  }
}
export function record(id, kinds, n = 1) {
  const st = s(id), now = Date.now();
  for (const k of kinds) { (st.ev[k] ||= []); for (let i = 0; i < n; i++) st.ev[k].push(now); }
  st.fails = [];
  save();
}
export function pause(id, ms, reason) {
  const st = s(id);
  st.pausedUntil = Math.max(st.pausedUntil || 0, Date.now() + ms); st.pauseReason = reason;
  save();
}
export function resume(id) { const st = s(id); st.pausedUntil = 0; st.pauseReason = ''; st.fails = []; save(); }

/** Called when a send fails: WhatsApp's own push-back pauses risky sending; repeated failures too. */
export function failure(id, message) {
  const st = s(id), msg = String(message || '');
  if (BRAKE_PATTERN.test(msg)) { pause(id, 6 * 3_600_000, `WhatsApp answered "${msg.slice(0, 80)}"`); return; }
  st.fails = [...(st.fails || []).filter(t => t > Date.now() - 600_000), Date.now()];
  if (st.fails.length >= 5) pause(id, 3_600_000, 'several messages in a row failed');
  save();
}

export function usage(id) {
  prune(id);
  const st = s(id), now = Date.now(), used = {};
  for (const [k, lim] of Object.entries(LIMITS)) {
    used[k] = {};
    for (const w of Object.keys(lim)) used[k][w] = (st.ev[k] || []).filter(t => t > now - WINDOW[w]).length;
  }
  return { limits: LIMITS, labels: LABELS, used, pausedUntil: st.pausedUntil && st.pausedUntil > now ? when(st.pausedUntil) : null, pauseReason: st.pausedUntil > now ? st.pauseReason : '' };
}

/** Human pacing: show "typing…" for a moment proportional to the text before a bulk message. */
export async function typeLikeAPerson(c, jid, text) {
  if (jid.endsWith('@newsletter')) return;
  const ms = Math.min(6000, 1200 + String(text || '').length * 25) + Math.floor(Math.random() * 900);
  try { await c.startTyping(jid); } catch { /* optional */ }
  await new Promise(r => setTimeout(r, ms));
  try { await c.stopTyping(jid); } catch { /* optional */ }
}
