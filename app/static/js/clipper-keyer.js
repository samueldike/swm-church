// Green-screen frames: a live preview of the frame with its screen colour made see-through, over a stand-in
// "video", before it is uploaded. Click the preview to pick the screen colour. The rule is the same as the server's
// (app/core/clipper/frames.py:chroma_key): compare tint (Cb/Cr), soft band, no colour spill. Keep them in step.
(() => {
  const chroma = (r, g, b) => [128 - 0.168736 * r - 0.331264 * g + 0.5 * b, 128 + 0.5 * r - 0.418688 * g - 0.081312 * b];
  const hexRgb = (h) => { h = (h || '#00ff00').replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
  const rgbHex = (r, g, b) => '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');

  function enhance(box) {
    if (box.dataset.ready) return;
    box.dataset.ready = '1';
    const form = box.closest('form');
    const file = form.querySelector('input[type=file][name=file]');
    const on = box.querySelector('[name=green]');
    const colour = box.querySelector('[name=key_color]');
    const tol = box.querySelector('[name=key_tolerance]');
    const tolOut = box.querySelector('[data-tol]');
    const canvas = box.querySelector('canvas');
    const note = box.querySelector('[data-keyer-note]');
    const wrap = box.querySelector('[data-keyer-preview]');
    let src = null;        // ImageData of the picture, preview size

    const draw = () => {
      if (!src) { wrap.hidden = true; return; }
      wrap.hidden = false;
      const { width: w, height: h } = src;
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext('2d');
      // The stand-in video behind the frame.
      const g = ctx.createLinearGradient(0, 0, w, h);
      g.addColorStop(0, '#2b5876'); g.addColorStop(1, '#4e4376');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.font = `bold ${Math.round(w / 9)}px sans-serif`;
      ctx.textAlign = 'center'; ctx.fillText('VIDEO', w / 2, h / 2 + w / 30);
      const out = new ImageData(new Uint8ClampedArray(src.data), w, h);
      if (on.checked) {
        const key = hexRgb(colour.value);
        const [kcb, kcr] = chroma(...key);
        const t1 = (Number(tol.value) / 100) * 0.4, t2 = t1 * 1.6;
        const k = key.indexOf(Math.max(...key));
        const others = [0, 1, 2].filter((c) => c !== k);
        const d8 = out.data;
        for (let i = 0; i < d8.length; i += 4) {
          const [cb, cr] = chroma(d8[i], d8[i + 1], d8[i + 2]);
          const d = Math.hypot(cb - kcb, cr - kcr) / 255;
          const keep = Math.min(1, Math.max(0, (d - t1) / Math.max(1e-6, t2 - t1)));
          if (d < t2 * 1.8) d8[i + k] = Math.min(d8[i + k], Math.max(d8[i + others[0]], d8[i + others[1]]));
          d8[i + 3] = d8[i + 3] * keep;
        }
      }
      const tmp = document.createElement('canvas'); tmp.width = w; tmp.height = h;
      tmp.getContext('2d').putImageData(out, 0, 0);
      ctx.drawImage(tmp, 0, 0);
      if (tolOut) tolOut.textContent = tol.value;
    };

    file.addEventListener('change', () => {
      src = null;
      const f = file.files && file.files[0];
      if (!f || !/^image\//.test(f.type)) { draw(); return; }
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, 320 / img.width);
        const w = Math.max(1, Math.round(img.width * scale)), h = Math.max(1, Math.round(img.height * scale));
        const c = document.createElement('canvas'); c.width = w; c.height = h;
        const cx = c.getContext('2d'); cx.drawImage(img, 0, 0, w, h);
        src = cx.getImageData(0, 0, w, h);
        // No see-through area at all: it is most likely a green-screen frame. Take the middle's colour.
        let clear = 0;
        for (let i = 3; i < src.data.length; i += 4 * 7) if (src.data[i] < 250) clear += 1;
        if (!clear) {
          const m = ((Math.floor(h / 2) * w) + Math.floor(w / 2)) * 4;
          colour.value = rgbHex(src.data[m], src.data[m + 1], src.data[m + 2]);
          on.checked = true;
          if (note) note.textContent = 'This picture has no see-through part, so its screen colour (taken from the middle) will be removed. Click the preview to pick another colour.';
        } else if (note) {
          note.textContent = 'This picture already has a see-through part. Tick "Remove a green screen" only if it also has one.';
        }
        URL.revokeObjectURL(img.src);
        draw();
      };
      img.src = URL.createObjectURL(f);
    });
    canvas.addEventListener('click', (e) => {
      if (!src) return;
      const r = canvas.getBoundingClientRect();
      const x = Math.floor((e.clientX - r.left) * src.width / r.width), y = Math.floor((e.clientY - r.top) * src.height / r.height);
      const m = (y * src.width + x) * 4;
      colour.value = rgbHex(src.data[m], src.data[m + 1], src.data[m + 2]);
      on.checked = true;
      draw();
    });
    [on, colour, tol].forEach((el) => el.addEventListener('input', draw));
    // A fresh form (the upload window reopens): forget the old picture.
    form.addEventListener('reset', () => { src = null; on.checked = false; draw(); });
  }

  const run = () => document.querySelectorAll('[data-keyer]').forEach(enhance);
  run();
  document.addEventListener('turbo:load', run);
  window.SwmKeyer = { run };
})();
