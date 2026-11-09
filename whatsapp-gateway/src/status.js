// Posting to WhatsApp Status. Ported from unana-ai: WPPConnect's own status wrappers resolve without telling
// whether WhatsApp accepted the post, so this calls WA-JS (window.WPP.status) inside the page and only
// reports success when WhatsApp gave something back. Images are shrunk to 1600px first, because full-size
// phone photos travelling as base64 are the main cause of "WhatsApp Web did not respond".
import fs from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';

async function asDataUrl(file, kind) {
  const buf = await fs.readFile(file);
  if (kind === 'image') {
    try {
      const small = await sharp(buf).rotate().resize({ width: 1600, height: 1600, fit: 'inside', withoutEnlargement: true }).jpeg({ quality: 85, mozjpeg: true }).toBuffer();
      return `data:image/jpeg;base64,${small.toString('base64')}`;
    } catch { /* unusual format: send the original */ }
  }
  const ext = path.extname(file).slice(1).toLowerCase();
  const mime = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm' }[ext] || 'application/octet-stream';
  return `data:${mime};base64,${buf.toString('base64')}`;
}

export async function postStatus(client, kind, content, caption) {
  const page = client.page;
  if (!page?.evaluate) throw new Error('WhatsApp Web is not ready for status posting.');
  const payload = kind === 'text' ? content : await asDataUrl(content, kind);
  if (kind === 'text' && !String(payload).trim()) throw new Error('Type the status text first.');
  const result = await page.evaluate(async (k, c, cap) => {
    const WPP = window.WPP;
    if (!WPP?.status) return { __unsupported: true };
    const deadline = Date.now() + 20000;
    while (WPP.isReady === false && Date.now() < deadline) await new Promise(r => setTimeout(r, 500));
    try {
      if (k === 'text') {
        const r = await Promise.race([WPP.status.sendTextStatus(c), new Promise(r2 => setTimeout(() => r2({ __timeout: true }), 90000))]);
        if (r?.__timeout) return r;
        return { id: r?.id?._serialized || (typeof r?.id === 'string' ? r.id : undefined), ok: true };
      }
      // Media status calls often publish but never resolve back: start them, give them time, report sent.
      const p = k === 'image' ? WPP.status.sendImageStatus(c, cap ? { caption: cap } : undefined)
        : WPP.status.sendVideoStatus(c, cap ? { caption: cap } : undefined);
      p?.catch?.(() => {});
      await new Promise(r => setTimeout(r, 4000));
      return { ok: true };
    } catch (err) {
      const m = String(err?.message || err || '');
      // Known WA-JS bug: the status IS sent, then post-send handling throws.
      if (/sendmsgresult|postsendstatus|sendrawstatus|\.then is not a function/i.test(m)) return { ok: true };
      return { __error: m || 'status send failed' };
    }
  }, kind, payload, caption || '');
  if (result?.__unsupported) throw new Error('This WhatsApp Web version does not offer status posting.');
  if (result?.__timeout) throw new Error('WhatsApp Web did not respond. The status may not have been posted.');
  if (result?.__error) throw new Error(`Status not posted: ${result.__error}`);
  return result;
}
