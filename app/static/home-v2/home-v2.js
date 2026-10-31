/* Page behaviour for /home-v2. All content works without this file; it adds the journey on top,
   and only asks for the 3D world (world.min.js) once the page itself is ready. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var world = null;

  /* ─── The world: skipped for Save-Data and machines without WebGL ─── */
  function webgl() {
    try { return !!doc.createElement("canvas").getContext("webgl2"); } catch (e) { return false; }
  }
  function loadWorld() {
    var conn = navigator.connection || {};
    if (conn.saveData || !webgl()) { root.classList.add("no-world"); return; }
    var lite = (navigator.deviceMemory && navigator.deviceMemory <= 3) || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) || innerWidth < 768;
    var url = doc.querySelector('script[src*="home-v2.js"]').src.replace("home-v2.js", "world.min.js");
    import(url).then(function (mod) {
      world = mod.start(doc.getElementById("world"), { lite: !!lite, still: reduce });
      root.classList.add("has-world");
    }).catch(function () { root.classList.add("no-world"); });
  }
  if (doc.readyState === "complete") setTimeout(loadWorld, 60);
  else addEventListener("load", function () { setTimeout(loadWorld, 60); });

  // Tap the ground and a drop falls there. Controls and reading text keep their own clicks.
  // "click", not a pointer event: a finger that scrolled the page never fires one.
  addEventListener("click", function (ev) {
    if (!world || reduce) return;
    if (ev.target.closest && ev.target.closest("a,button,input,label,li,table,dialog,figure,img,form,.now,p,h1,h2,h3,blockquote")) return;
    if (world.rain(ev.clientX, ev.clientY)) root.classList.add("has-rained");
  });

  /* ─── Header, menu ─── */
  var bar = doc.getElementById("bar"), menu = doc.getElementById("menu");
  doc.getElementById("menu-open").addEventListener("click", function () { menu.showModal(); });
  doc.getElementById("menu-close").addEventListener("click", function () { menu.close(); });
  menu.addEventListener("click", function (e) { if (e.target === menu) menu.close(); });

  /* ─── Reveal on arrival ─── */
  var reveals = doc.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (el) { io.observe(el); });
  } else reveals.forEach(function (el) { el.classList.add("in"); });

  /* ─── The verse: word by word, as the drop falls ─── */
  var verse = doc.getElementById("verse"), words = [];
  if (verse && !reduce) {
    (function split(node) {
      Array.prototype.slice.call(node.childNodes).forEach(function (n) {
        if (n.nodeType === 1) { split(n); return; }
        var frag = doc.createDocumentFragment();
        n.textContent.split(/(\s+)/).forEach(function (t) {
          if (!t.trim()) { frag.appendChild(doc.createTextNode(t)); return; }
          var s = doc.createElement("span"); s.className = "w"; s.textContent = t; words.push(s); frag.appendChild(s);
        });
        node.replaceChild(frag, n);
      });
    })(verse);
  }

  /* ─── Scroll-linked pieces, all read in one pass per frame ─── */
  var promise = doc.getElementById("promise"), values = doc.getElementById("values");
  var ripples = doc.querySelectorAll("#ripples li");
  var acts = doc.querySelectorAll("[data-act]");
  var drifts = doc.querySelectorAll("[data-speed]");
  var belts = doc.querySelectorAll(".belt");
  var beltBox = doc.getElementById("belts");
  var chapter = doc.getElementById("chapter"), chapterText = chapter.querySelector("span"), chapterBar = chapter.querySelector("i");
  var ring = -2, lit = 0, ticking = false;

  function within(el) {
    var b = el.getBoundingClientRect(), span = b.height - innerHeight;
    var p = el.classList.contains("flow") ? (innerHeight * 0.85 - b.top) / b.height : span > 0 ? -b.top / span : 0;
    return Math.min(1, Math.max(0, p));
  }
  // A pinned chapter must fit its screen. When it cannot (a phone on its side, a small phone,
  // large text settings) it stops pinning and reads as an ordinary section.
  function fit() {
    doc.querySelectorAll(".tall").forEach(function (sec) {
      sec.classList.remove("flow");
      var pad = sec.querySelector(".sticky .wrap");
      if (pad && pad.offsetHeight + 96 > innerHeight) sec.classList.add("flow");
    });
    update();
  }
  function update() {
    ticking = false;
    var y = scrollY, h = innerHeight, max = doc.documentElement.scrollHeight - h;
    bar.classList.toggle("solid", y > h * 0.6);

    if (words.length) {
      var n = Math.round(Math.min(1, within(promise) * 1.25) * words.length);
      while (lit < n) words[lit++].classList.add("on");
      while (lit > n) words[--lit].classList.remove("on");
    }

    var v = within(values), i = v > 0 && v < 1 ? Math.min(6, Math.floor(v * 7)) : -1;
    if (i !== ring) {
      ring = i;
      ripples.forEach(function (li, k) { li.classList.toggle("on", k === i); });
      if (world) world.setRing(i);
    }

    if (!reduce) {
      drifts.forEach(function (el) {
        var b = el.getBoundingClientRect();
        if (b.bottom > -80 && b.top < h + 80) el.style.translate = "0 " + ((b.top + b.height / 2 - h / 2) * parseFloat(el.dataset.speed)).toFixed(1) + "px";
      });
      if (beltBox) {
        var bb = beltBox.getBoundingClientRect();
        if (bb.bottom > 0 && bb.top < h) belts.forEach(function (el) {
          var range = Math.max(0, el.scrollWidth - innerWidth), p = (h - bb.top) / (h + bb.height);
          el.style.transform = "translate3d(" + (-(el.dataset.dir === "1" ? p : 1 - p) * range).toFixed(1) + "px,0,0)";
        });
      }
    }

    var mid = h / 2, name = "";
    for (var a = 0; a < acts.length; a++) {
      var r = acts[a].getBoundingClientRect();
      if (r.top <= mid && r.bottom > mid) { name = acts[a].dataset.name; break; }
    }
    if (name !== chapterText.textContent) chapterText.textContent = name;
    chapterBar.style.transform = "scaleX(" + (max > 0 ? y / max : 0).toFixed(3) + ")";
  }
  function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", fit, { passive: true });
  addEventListener("orientationchange", fit, { passive: true });
  if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(fit);
  fit();

  /* ─── Twelve units light the tree ─── */
  var unitList = doc.getElementById("units");
  if (unitList) {
    var pick = function (e) { var li = e.target.closest("[data-unit]"); if (world) world.setLamp(li ? +li.dataset.unit : -1); };
    var clear = function () { if (world) world.setLamp(-1); };
    unitList.addEventListener("pointerover", pick);
    unitList.addEventListener("focusin", pick);
    unitList.addEventListener("pointerleave", clear);
    unitList.addEventListener("focusout", clear);
  }

  /* ─── Gallery photos load when the belts come near ─── */
  if (beltBox) {
    var load = function () { beltBox.querySelectorAll("img[data-src]").forEach(function (im) { im.src = im.dataset.src; im.removeAttribute("data-src"); }); };
    if ("IntersectionObserver" in window) {
      var near = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { load(); near.disconnect(); } }, { rootMargin: "120% 0px" });
      near.observe(beltBox);
    } else load();
  }

  /* ─── Listening on the page ─── */
  var audio = null, active = null, pulse = 0;
  var now = doc.getElementById("now"), nowTitle = doc.getElementById("now-title"), nowToggle = doc.getElementById("now-toggle");

  function setPlaying(on) {
    nowToggle.classList.toggle("is-on", on);
    nowToggle.setAttribute("aria-label", on ? "Pause" : "Play");
    if (active) active.classList.toggle("is-on", on);
    clearInterval(pulse);
    if (on && !reduce) pulse = setInterval(function () { if (world) world.pulse(); }, 2600);
  }
  function stop() {
    if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
    setPlaying(false);
    active = null; now.hidden = true;
  }
  doc.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-play]");
    if (!btn) return;
    if (btn === active) { audio.paused ? audio.play() : audio.pause(); return; }
    if (!audio) {
      audio = new Audio();
      audio.preload = "none";
      audio.addEventListener("play", function () { setPlaying(true); });
      audio.addEventListener("pause", function () { setPlaying(false); });
      audio.addEventListener("ended", stop);
      // A message with no playable file here still has its own page.
      audio.addEventListener("error", function () { if (active && audio.getAttribute("src")) location.href = active.dataset.page; });
    }
    if (active) active.classList.remove("is-on");
    active = btn;
    nowTitle.textContent = btn.dataset.title;
    now.hidden = false;
    audio.src = btn.dataset.play;
    audio.play().catch(function () {});
  });
  nowToggle.addEventListener("click", function () { if (audio) { audio.paused ? audio.play() : audio.pause(); } });
  doc.getElementById("now-close").addEventListener("click", stop);
})();
