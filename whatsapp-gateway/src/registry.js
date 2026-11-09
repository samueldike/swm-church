// The gateway's own record of every session (status, linked number, QR while pairing), kept on the /data
// volume so a restarted container knows which sessions to bring back. Written atomically (tmp + rename).
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';

const file = () => path.join(config.dataDir, 'registry.json');
let data = {};
let writing = Promise.resolve();

export async function load() {
  try { data = JSON.parse(await fs.readFile(file(), 'utf8')) || {}; } catch { data = {}; }
  // A QR or pairing code from before a restart is dead; connected sessions keep their status.
  for (const r of Object.values(data)) {
    if (r.status === 'qr' || r.status === 'connecting') { r.status = 'disconnected'; r.qr = null; r.linkCode = null; }
  }
}

export const get = id => ({ status: 'disconnected', ...(data[id] || {}) });
export const connectedIds = () => Object.keys(data).filter(id => data[id]?.status === 'connected');

export function patch(id, values) {
  data[id] = { ...(data[id] || {}), ...values, updatedAt: new Date().toISOString() };
  const snapshot = JSON.stringify(data, null, 1);
  writing = writing.then(async () => {
    await fs.mkdir(config.dataDir, { recursive: true });
    const tmp = file() + '.tmp';
    await fs.writeFile(tmp, snapshot);
    await fs.rename(tmp, file());
  }).catch(() => {});
  return writing;
}
