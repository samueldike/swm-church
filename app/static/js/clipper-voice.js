// Learn a pastor's voice (Clipper settings → Whose messages): the chosen video plays here and two handles mark the
// part where only he speaks. YouTube links play in YouTube's own player (IFrame API); the church's own videos and a
// recording on this computer play in a normal video player. The form posts source + start/end (seconds).
(() => {
  const clock = (t) => {
    t = Math.max(0, Math.round(t || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
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

  function enhance(box) {
    if (box.dataset.ready) return;
    box.dataset.ready = '1';
    const form = box.closest('form');
    const $ = (sel) => box.querySelector(sel);
    const wrap = $('[data-player]'), stage = $('[data-stage]'), bar = $('[data-bar]'), sel = $('[data-sel]');
    const hLo = $('[data-h=lo]'), hHi = $('[data-h=hi]'), now = $('[data-now]'), read = $('[data-read]');
    const inStart = box.querySelector('input[name=start]'), inEnd = box.querySelector('input[name=end]');
    let player = null, total = 0, lo = null, hi = null, stopAt = null, timer = null;
    const steps = box.querySelectorAll('[data-step]');

    const cur = () => (player ? player.time() : 0);
    // Nothing is marked until the person marks it: Start and End appear on the bar as they are set.
    const paint = () => {
      if (!total) return;
      const pct = (t) => `${(Math.max(0, Math.min(total, t)) / total) * 100}%`;
      hLo.hidden = lo === null; hHi.hidden = hi === null;
      if (lo !== null) hLo.style.left = pct(lo);
      if (hi !== null) hHi.style.left = pct(hi);
      const both = lo !== null && hi !== null;
      sel.hidden = !both;
      if (both) { sel.style.left = pct(lo); sel.style.width = `${((hi - lo) / total) * 100}%`; }
      now.style.left = pct(cur());
      read.textContent = both ? `${clock(lo)} to ${clock(hi)} (${clock(hi - lo)})`
        : `Start: ${lo === null ? 'not set' : clock(lo)} · End: ${hi === null ? 'not set' : clock(hi)}`;
      read.classList.toggle('bad', both && (hi - lo < 20 || hi - lo > 600));
      inStart.value = lo === null ? '' : String(Math.round(lo));
      inEnd.value = hi === null ? '' : String(Math.round(hi));
      const at_ = lo === null ? 'lo' : hi === null ? 'hi' : 'ok';
      steps.forEach((li) => {
        const k = li.dataset.step;
        li.classList.toggle('now', k === at_);
        li.classList.toggle('done', (k === 'lo' && lo !== null) || (k === 'hi' && hi !== null));
      });
    };
    const ready = (duration) => {
      total = duration || 0;
      if (!total) return;
      lo = null; hi = null;
      const wait = stage.querySelector('.vc-train-loading');
      if (wait) wait.remove();
      wrap.hidden = false;
      paint();
      clearInterval(timer);
      timer = setInterval(() => {
        if (!document.body.contains(box)) { clearInterval(timer); return; }
        if (stopAt !== null && cur() >= stopAt) { player.pause(); stopAt = null; }
        paint();
      }, 250);
    };
    const reset = () => {
      if (player) player.destroy();
      player = null; total = 0; lo = null; hi = null; stage.innerHTML = ''; wrap.hidden = true; inStart.value = ''; inEnd.value = '';
    };
    const html5 = (src) => {
      reset();
      loading('Loading the video… a long service can take a little while before it can be played.');
      const v = document.createElement('video');
      v.src = src; v.controls = true; v.playsInline = true; v.preload = 'metadata';
      stage.appendChild(v);
      v.addEventListener('error', () => loading('This video could not be loaded here. Choose another, or paste its YouTube link.'), { once: true });
      v.addEventListener('loadedmetadata', () => ready(v.duration), { once: true });
      player = { time: () => v.currentTime, seek: (t) => { v.currentTime = t; }, play: () => v.play(), pause: () => v.pause(),
        destroy: () => { v.pause(); v.removeAttribute('src'); v.load(); } };
    };
    const youtube = async (id) => {
      reset();
      loading('Opening the video…');
      await loadYT();
      const holder = document.createElement('div');
      stage.appendChild(holder);
      const yp = new window.YT.Player(holder, {
        videoId: id, playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: { onReady: () => ready(yp.getDuration()) },
      });
      player = { time: () => (yp.getCurrentTime ? yp.getCurrentTime() : 0), seek: (t) => yp.seekTo(t, true),
        play: () => yp.playVideo(), pause: () => yp.pauseVideo(), destroy: () => yp.destroy() };
    };

    // The player area shows at once with a note while the video loads (no silent wait).
    function loading(text) {
      wrap.hidden = false;
      let w = stage.querySelector('.vc-train-loading');
      if (!w) { w = document.createElement('div'); w.className = 'vc-train-loading'; stage.prepend(w); }
      w.innerHTML = '<i class="ri-loader-4-line vc-spin" aria-hidden="true"></i><span></span>';
      w.querySelector('span').textContent = text;
    }
    // One of the church's videos: one that came from YouTube plays in YouTube's player (instant, quick to seek).
    const playProject = (o) => {
      if (!o || !o.value) { reset(); return; }
      if (o.dataset.yt) youtube(o.dataset.yt);
      else if (o.dataset.src) html5(o.dataset.src);
      else reset();
    };

    // where the video comes from
    const showSource = () => {
      const src = (form.querySelector('[name=source]:checked') || {}).value || 'link';
      box.querySelectorAll('[data-when]').forEach((el) => { el.hidden = el.dataset.when !== src; });
      reset();
      if (src === 'project') { const o = $('select[name=project]'); if (o && o.selectedOptions[0]) playProject(o.selectedOptions[0]); }
      if (src === 'file') { const f = $('input[name=file]'); if (f && f.files && f.files[0]) html5(URL.createObjectURL(f.files[0])); }
    };
    box.addEventListener('change', (e) => {
      if (e.target.name === 'source') showSource();
      else if (e.target.name === 'project') playProject(e.target.selectedOptions[0]);
      else if (e.target.name === 'file') { const f = e.target.files && e.target.files[0]; if (f) html5(URL.createObjectURL(f)); else reset(); }
    });
    const openLink = () => {
      const url = $('input[name=url]').value.trim();
      const id = ytId(url);
      if (id) youtube(id);
      else if (/^https?:\/\/\S+\.(mp4|webm|mov|m4v|mp3|m4a|wav)(\?|$)/i.test(url)) html5(url);
      else if (url) { reset(); wrap.hidden = false; stage.innerHTML = '<p class="vc-muted vc-train-noprev">This link cannot play here. Type the start and end below as minutes:seconds.</p>'; manual(); }
    };
    $('[data-load]').addEventListener('click', openLink);
    $('input[name=url]').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); openLink(); } });
    // A link that cannot play here (another site): type the times instead.
    const manual = () => {
      bar.hidden = true; box.querySelector('.vc-train-tools').hidden = true;
      if (box.querySelector('[data-manual]')) return;
      const m = document.createElement('div');
      m.className = 'vc-f-row'; m.dataset.manual = '1';
      m.innerHTML = '<div class="vc-f"><label>From</label><input type="text" placeholder="50:00" data-mfrom></div><div class="vc-f"><label>To</label><input type="text" placeholder="52:00" data-mto></div>';
      wrap.appendChild(m);
      const parse = (v) => (/^\d+(:\d{1,2}){0,2}$/.test(v.trim()) ? v.trim().split(':').reduce((n, x) => n * 60 + Number(x), 0) : null);
      m.addEventListener('input', () => { const a = parse(m.querySelector('[data-mfrom]').value), b = parse(m.querySelector('[data-mto]').value); inStart.value = a ?? ''; inEnd.value = b ?? ''; });
    };

    // handles: drag, or set them at the playing moment
    const at = (clientX) => { const r = bar.getBoundingClientRect(); return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * total; };
    [hLo, hHi].forEach((h) => h.addEventListener('pointerdown', (e) => {
      e.preventDefault(); h.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const t = at(ev.clientX);
        if (h === hLo) lo = hi === null ? t : Math.min(t, hi - 5); else hi = lo === null ? t : Math.max(t, lo + 5);
        if (player) player.seek(h === hLo ? lo : hi);
        paint();
      };
      const up = () => { h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); };
      h.addEventListener('pointermove', move); h.addEventListener('pointerup', up);
    }));
    bar.addEventListener('click', (e) => { if (e.target === bar || e.target === sel) { player && player.seek(at(e.clientX)); paint(); } });
    box.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => {
      const t = cur();
      if (b.dataset.set === 'lo') { lo = Math.min(t, total - 5); if (hi !== null && hi <= lo + 5) hi = null; }
      else {
        if (lo === null) { alert('Set the start first: go to where only this preacher speaks and press Start here.'); return; }
        if (t <= lo + 5) { alert('The end must come after the start. Let it play on, then press End here.'); return; }
        hi = t;
      }
      paint();
    }));
    $('[data-play-part]').addEventListener('click', () => {
      if (!player || lo === null || hi === null) { alert('Set the start and the end first.'); return; }
      player.seek(lo); player.play(); stopAt = hi;
    });

    form.addEventListener('submit', (e) => {
      if (form.dataset.ajax) return;              // the Automatic Clipper's window sends it itself
      const a = Number(inStart.value), b = Number(inEnd.value);
      if (inStart.value === '' || inEnd.value === '' || !(b - a >= 20)) {
        e.preventDefault();
        alert(inStart.value === '' ? 'Set the start: open the video, go to where only this preacher speaks and press Start here.'
          : inEnd.value === '' ? 'Set the end: let the video play 1 to 3 minutes on, then press End here.'
            : 'The start and the end must be at least 20 seconds apart.');
        return;
      }
      const btn = form.querySelector('button[type=submit]');
      if (btn) { btn.disabled = true; btn.innerHTML = '<i class="ri-loader-4-line vc-spin"></i>Learning the voice… (a link takes about a minute)'; }
    });
    showSource();
  }

  const run = () => document.querySelectorAll('[data-voice-train]').forEach(enhance);
  run();
  document.addEventListener('turbo:load', run);
})();
