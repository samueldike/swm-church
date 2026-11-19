// "Only this part": a preview of the source with start and end handles (new video form).
// YouTube links play in YouTube's own player (IFrame API); an upload plays from the chosen file before it is sent.
// The handles and the "from / to" boxes stay in step; the boxes are what the form submits.
(() => {
  const clock = (t) => {
    t = Math.max(0, Math.round(t));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  };
  const parse = (v) => {
    v = String(v || '').trim();
    if (!v) return null;
    if (!/^\d+(:\d{1,2}){0,2}$/.test(v)) return null;
    return v.split(':').reduce((n, x) => n * 60 + Number(x), 0);
  };
  const ytId = (url) => {
    const m = String(url).match(/(?:youtube\.com\/(?:watch\?(?:.*&)?v=|live\/|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  };
  let apiReady = null;
  const loadYT = () => apiReady || (apiReady = new Promise((res) => {
    if (window.YT && window.YT.Player) { res(); return; }
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { if (prev) prev(); res(); };
    const s = document.createElement('script'); s.src = 'https://www.youtube.com/iframe_api'; s.async = true;
    document.head.appendChild(s);
  }));

  function init() {
    const f = document.getElementById('vc-form');
    const box = document.getElementById('vc-range');
    if (!f || !box || box.dataset.ready) return;
    box.dataset.ready = '1';
    const ctl = new AbortController(); const o = { signal: ctl.signal };
    document.addEventListener('turbo:before-visit', () => { ctl.abort(); }, { once: true });
    const stage = box.querySelector('.vc-range-stage');
    const lo = box.querySelector('[data-lo]'), hi = box.querySelector('[data-hi]'), sel = box.querySelector('.vc-range-sel');
    const read = box.querySelector('[data-read]');
    const startIn = f.range_start, endIn = f.range_end;
    let player = null, total = 0, src = '', objectUrl = null;

    const paint = () => {
      if (!total) return;
      const a = Number(lo.value), b = Number(hi.value);
      sel.style.left = `${(a / total) * 100}%`; sel.style.width = `${Math.max(0, (b - a) / total) * 100}%`;
      const whole = a <= 0 && b >= total - 1;
      read.textContent = whole ? `The whole video (${clock(total)})` : `${clock(a)} to ${clock(b)} · ${clock(b - a)} to clip`;
      lo.setAttribute('aria-valuetext', clock(a)); hi.setAttribute('aria-valuetext', clock(b));
    };
    const toBoxes = () => {
      const a = Number(lo.value), b = Number(hi.value);
      startIn.value = a > 0 ? clock(a) : '';
      endIn.value = b < total - 1 ? clock(b) : '';
    };
    const fromBoxes = () => {
      if (!total) return;
      const a = parse(startIn.value), b = parse(endIn.value);
      lo.value = a === null ? 0 : Math.min(a, total); hi.value = b === null ? total : Math.min(b, total);
      if (Number(hi.value) <= Number(lo.value)) hi.value = Math.min(total, Number(lo.value) + 60);
      paint();
    };
    const ready = (dur) => {
      total = Math.floor(dur || 0);
      if (!total) return;
      [lo, hi].forEach((r) => { r.max = total; r.disabled = false; });
      box.querySelectorAll('[data-set]').forEach((b) => { b.disabled = false; });
      fromBoxes();
    };
    const reset = () => {
      if (player && player.destroy) player.destroy();
      player = null; total = 0; stage.innerHTML = ''; box.hidden = true;
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
      [lo, hi].forEach((r) => { r.disabled = true; });
      box.querySelectorAll('[data-set]').forEach((b) => { b.disabled = true; });
    };

    async function youtube(id) {
      reset(); box.hidden = false;
      const el = document.createElement('div'); stage.appendChild(el);
      await loadYT();
      if (ctl.signal.aborted) return;
      const yt = new window.YT.Player(el, {
        videoId: id, playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: { onReady: () => ready(yt.getDuration()), onStateChange: () => { if (!total) ready(yt.getDuration()); } },
      });
      player = { time: () => yt.getCurrentTime(), seek: (t) => yt.seekTo(t, true), destroy: () => yt.destroy() };
    }
    function local(file) {
      reset(); box.hidden = false;
      objectUrl = URL.createObjectURL(file);
      const v = document.createElement('video');
      v.src = objectUrl; v.controls = true; v.playsInline = true; v.preload = 'metadata';
      v.addEventListener('loadedmetadata', () => ready(v.duration), o);
      stage.appendChild(v);
      player = { time: () => v.currentTime, seek: (t) => { v.currentTime = t; }, destroy: () => {} };
    }

    f.url.addEventListener('input', () => {
      const id = ytId(f.url.value);
      if (id && id !== src) { src = id; youtube(id); }
      else if (!id && src && f.source_kind.value === 'url') { src = ''; reset(); }
    }, o);
    f.addEventListener('vc-file', (e) => { src = ''; local(e.detail); }, o);
    f.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
      if (b.dataset.tab === 'url') { src = ''; reset(); f.url.dispatchEvent(new Event('input')); }
      else { src = ''; reset(); }
    }, o));

    lo.addEventListener('input', () => {
      if (Number(lo.value) > Number(hi.value) - 30) lo.value = Math.max(0, Number(hi.value) - 30);
      paint(); toBoxes(); if (player) player.seek(Number(lo.value));
    }, o);
    hi.addEventListener('input', () => {
      if (Number(hi.value) < Number(lo.value) + 30) hi.value = Math.min(total, Number(lo.value) + 30);
      paint(); toBoxes(); if (player) player.seek(Math.max(0, Number(hi.value) - 3));
    }, o);
    box.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => {
      if (!player) return;
      const t = Math.floor(player.time());
      if (b.dataset.set === 'start') { lo.value = Math.min(t, Number(hi.value) - 30); }
      else { hi.value = Math.max(t, Number(lo.value) + 30); }
      paint(); toBoxes();
    }, o));
    box.querySelector('[data-whole]').addEventListener('click', () => { lo.value = 0; hi.value = total; paint(); toBoxes(); }, o);
    [startIn, endIn].forEach((i) => i.addEventListener('change', fromBoxes, o));
    if (f.url.value) f.url.dispatchEvent(new Event('input'));
  }

  document.addEventListener('turbo:load', init);
  if (document.readyState !== 'loading') init(); else document.addEventListener('DOMContentLoaded', init);
})();
