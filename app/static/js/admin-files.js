// Every plain file box in the console becomes a clear drop zone: an upload icon, "Choose a file or drag it here",
// what kinds of file it takes, and the chosen file's name and size. The real <input type=file> stays inside (over
// the whole zone, invisible), so clicking anywhere opens the picker, dropping a file works, and every form and
// script that reads the input keeps working unchanged. Inputs that already have their own button (hidden, sr-only,
// inside a <label>, or data-plain) are left alone.
(() => {
  const KIND = { png: 'PNG', jpg: 'JPEG', jpeg: 'JPEG', webp: 'WebP', gif: 'GIF', mp3: 'MP3', m4a: 'M4A', wav: 'WAV',
    ogg: 'OGG', flac: 'FLAC', mp4: 'MP4', mov: 'MOV', webm: 'WebM', mkv: 'MKV', pdf: 'PDF', docx: 'Word', doc: 'Word',
    xlsx: 'Excel', xls: 'Excel', csv: 'CSV', txt: 'text', zip: 'ZIP' };
  const kinds = (accept) => {
    const out = [];
    (accept || '').split(',').map((a) => a.trim().toLowerCase()).forEach((a) => {
      let k = '';
      if (a.startsWith('.')) k = KIND[a.slice(1)] || a.slice(1).toUpperCase();
      else if (a === 'image/*') k = 'pictures';
      else if (a === 'audio/*') k = 'audio';
      else if (a === 'video/*') k = 'video';
      else if (a.includes('/')) k = KIND[a.split('/')[1]] || (a.split('/')[1] || '').toUpperCase();
      if (k && !out.includes(k)) out.push(k);
    });
    return out;
  };
  const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function skip(input) {
    if (input.dataset.drop || input.dataset.plain !== undefined || input.hidden || input.closest('label, .file-drop, [data-no-drop]')) return true;
    if (input.classList.contains('sr-only') || input.classList.contains('hidden')) return true;
    const cs = getComputedStyle(input);
    return cs.display === 'none' || cs.visibility === 'hidden';
  }

  function enhance(input) {
    if (skip(input)) return;
    input.dataset.drop = '1';
    const zone = document.createElement('div');
    zone.className = 'file-drop';
    const many = input.multiple;
    const ks = kinds(input.getAttribute('accept'));
    zone.innerHTML = `<span class="file-drop-icon" aria-hidden="true"><i class="ri-upload-cloud-2-line"></i></span>
      <span class="file-drop-text"><b>${many ? 'Choose files' : 'Choose a file'}</b> or drag ${many ? 'them' : 'it'} here
      ${ks.length ? `<small>${esc(ks.slice(0, 5).join(', '))}${ks.length > 5 ? '…' : ''}</small>` : ''}</span>
      <span class="file-drop-chosen" hidden></span>`;
    input.parentNode.insertBefore(zone, input);
    zone.appendChild(input);
    const chosen = zone.querySelector('.file-drop-chosen');
    const show = () => {
      const files = [...(input.files || [])];
      zone.classList.toggle('has-file', files.length > 0);
      chosen.hidden = !files.length;
      chosen.innerHTML = files.length
        ? `<i class="ri-file-check-line"></i><span>${esc(files.length === 1 ? files[0].name : `${files.length} files`)}</span><small>${esc(size(files.reduce((a, f) => a + f.size, 0)))}</small>`
        : '';
    };
    input.addEventListener('change', show);
    ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add('is-over')));
    ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('is-over')));
    const form = input.form;
    if (form) form.addEventListener('reset', () => setTimeout(show, 0));
    show();
  }

  const run = (root) => (root || document).querySelectorAll('input[type=file]').forEach(enhance);
  run();
  document.addEventListener('turbo:load', () => run());
  document.addEventListener('DOMContentLoaded', () => run());
  // Upload boxes that appear later (a dialog opened, a section folded open) are enhanced as they show.
  document.addEventListener('toggle', (e) => { if (e.target && e.target.querySelectorAll) run(e.target); }, true);
  window.SwmFiles = { run };
})();
