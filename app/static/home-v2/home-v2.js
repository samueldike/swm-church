/* Page behaviour for /home-v2. All content works without this file; it adds the life on top. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (id) { return doc.getElementById(id); };
  var all = function (sel, el) { return Array.prototype.slice.call((el || doc).querySelectorAll(sel)); };

  /* ─── Menu ─── */
  var menu = $("menu");
  $("menu-open").addEventListener("click", function () { menu.showModal(); });
  $("menu-close").addEventListener("click", function () { menu.close(); });

  /* ─── Arrivals ─── */
  var reveals = all(".reveal");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -6% 0px" });
    reveals.forEach(function (el) { io.observe(el); });
  } else reveals.forEach(function (el) { el.classList.add("in"); });

  /* ─── Hero: photographs of the house, with water moving across them ─── */
  var slides = all("[data-slide]"), photos = all("[data-slide-photo]"), dots = all("[data-dot]");
  var pauseBtn = $("hero-pause"), slide = 0, paused = reduce, timer = 0, ripple = null;

  photos.forEach(function (im) { if (im.dataset.src && !reduce) setTimeout(function () { im.src = im.dataset.src; }, 1800); });
  function show(n) {
    slide = n % slides.length;
    var ph = photos[slide];
    if (!ph.getAttribute("src")) ph.src = ph.dataset.src;
    slides.forEach(function (s, i) {
      var on = i === slide;
      s.classList.toggle("on", on);
      s.setAttribute("aria-hidden", on ? "false" : "true");
      var a = s.querySelector("a"); if (on) a.removeAttribute("tabindex"); else a.setAttribute("tabindex", "-1");
    });
    photos.forEach(function (p, i) { p.classList.toggle("on", i === slide); });
    dots.forEach(function (d, i) { d.classList.toggle("on", i === slide); });
    if (ripple) ripple.to(ph);
  }
  function tick() { clearTimeout(timer); if (!paused) timer = setTimeout(function () { show(slide + 1); tick(); }, 7000); }
  pauseBtn.addEventListener("click", function () {
    paused = !paused;
    pauseBtn.textContent = paused ? "Play" : "Pause";
    pauseBtn.setAttribute("aria-pressed", String(paused));
    tick();
  });
  if (reduce) { pauseBtn.textContent = "Play"; pauseBtn.setAttribute("aria-pressed", "true"); }
  tick();

  function startRipple() {
    var cv = $("ripple"), gl = cv.getContext("webgl", { antialias: false, alpha: false });
    if (!gl) return null;
    var vs = "attribute vec2 p;varying vec2 u;void main(){u=p*.5+.5;gl_Position=vec4(p,0.,1.);}";
    var fs = "precision mediump float;varying vec2 u;uniform sampler2D a,b;uniform float t,m;uniform vec2 sa,sb;uniform vec3 d[4];" +
      "vec2 cov(vec2 v,vec2 s){return (v-.5)*s+.5;}" +
      "void main(){vec2 v=vec2(u.x,1.-u.y);vec2 o=vec2(sin(v.y*9.+t*.7),cos(v.x*7.-t*.5))*.0016;" +
      "for(int i=0;i<4;i++){float r=distance(v,d[i].xy);float k=d[i].z;if(k<2.5){float w=sin(r*62.-k*11.)*exp(-r*5.)*exp(-k*1.5);o+=normalize(v-d[i].xy+.0001)*w*.02;}}" +
      "vec4 c=mix(texture2D(a,cov(v+o,sa)),texture2D(b,cov(v+o,sb)),m);gl_FragColor=vec4(c.rgb,1.);}";
    function sh(type, src) { var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
    var pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return null;
    gl.useProgram(pr);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var lp = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 2, gl.FLOAT, false, 0, 0);
    var U = {}; ["a", "b", "t", "m", "sa", "sb"].forEach(function (n) { U[n] = gl.getUniformLocation(pr, n); });
    var dl = [0, 1, 2, 3].map(function (i) { return gl.getUniformLocation(pr, "d[" + i + "]"); });
    gl.uniform1i(U.a, 0); gl.uniform1i(U.b, 1);
    var tex = [gl.createTexture(), gl.createTexture()], size = [[1, 1], [1, 1]];
    function put(i, im) {
      gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, tex[i]);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, im);
      [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T].forEach(function (k) { gl.texParameteri(gl.TEXTURE_2D, k, gl.CLAMP_TO_EDGE); });
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      size[i] = [im.naturalWidth, im.naturalHeight];
    }
    var W = 1, H = 1;
    function fit() {
      var d = Math.min(devicePixelRatio || 1, 1.5);
      W = cv.width = cv.clientWidth * d; H = cv.height = cv.clientHeight * d; gl.viewport(0, 0, W, H);
    }
    function cover(s) { var ca = W / H, ia = s[0] / s[1]; return ca > ia ? [1, ia / ca] : [ca / ia, 1]; }
    var drops = [[0, 0, 9], [0, 0, 9], [0, 0, 9], [0, 0, 9]], di = 0, blend = 0, fading = false, visible = true, last = performance.now(), t = 0, raf = 0, pending = null;
    function drop(x, y) { drops[di++ % 4] = [x, y, 0]; }
    function frame(now) {
      raf = 0;
      var dt = Math.min(.05, (now - last) / 1000); last = now; t += dt;
      if (fading) { blend += dt / 1.3; if (blend >= 1) { blend = 0; fading = false; put(0, pending); } }
      gl.uniform1f(U.t, t); gl.uniform1f(U.m, blend);
      var a = cover(size[0]), b = cover(size[1]);
      gl.uniform2f(U.sa, a[0], a[1]); gl.uniform2f(U.sb, b[0], b[1]);
      drops.forEach(function (d, i) { d[2] += dt; gl.uniform3f(dl[i], d[0], d[1], d[2]); });
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (visible && !doc.hidden) raf = requestAnimationFrame(frame);
    }
    function kick() { if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); } }
    addEventListener("resize", fit, { passive: true });
    doc.addEventListener("visibilitychange", kick);
    var hero = $("hero");
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible) kick(); }).observe(hero);
    hero.addEventListener("pointerdown", function (e) { if (!e.target.closest("a,button")) { var r = hero.getBoundingClientRect(); drop((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height); } });
    var lastMove = 0;
    hero.addEventListener("pointermove", function (e) {
      if (e.pointerType !== "mouse" || e.timeStamp - lastMove < 420) return;
      lastMove = e.timeStamp;
      var r = hero.getBoundingClientRect(); drop((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
    });
    fit();
    return {
      first: function (im) { put(0, im); put(1, im); kick(); cv.classList.add("is-on"); },
      to: function (im) {
        var go = function () { pending = im; put(1, im); blend = 0; fading = true; };
        if (im.complete && im.naturalWidth) go(); else im.addEventListener("load", go, { once: true });
      },
    };
  }

  /* ─── Next gathering ─── */
  var up = doc.querySelector(".next-up"), clock = $("clock");
  if (up && clock) {
    var at = Date.parse(up.dataset.at), parts = {};
    all("[data-c]", clock).forEach(function (b) { parts[b.dataset.c] = b; });
    var tickClock = function () {
      var left = Math.floor((at - Date.now()) / 1000);
      if (isNaN(at) || left <= 0) { clock.hidden = true; return false; }
      clock.hidden = false;
      parts.d.textContent = Math.floor(left / 86400);
      parts.h.textContent = Math.floor(left % 86400 / 3600);
      parts.m.textContent = Math.floor(left % 3600 / 60);
      parts.s.textContent = left % 60;
      return true;
    };
    if (tickClock() && !reduce) setInterval(tickClock, 1000);
  }

  /* ─── Calendar: real services and events; pick a day to see what is on ─── */
  var months = all("[data-month]"), panel = $("day-panel"), monthIx = 0;
  function esc(s) { var d = doc.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function pick(btn) {
    all(".day.is-picked").forEach(function (d) { d.classList.remove("is-picked"); });
    btn.classList.add("is-picked");
    var items = JSON.parse(btn.dataset.items || "[]");
    panel.innerHTML = "<h4>" + esc(btn.dataset.label) + "</h4>" + (items.length ? "<ul>" + items.map(function (it) {
      var name = it.event_id ? '<a href="/events/' + it.event_id + '">' + esc(it.title) + "</a>" : esc(it.title);
      return "<li>" + name + "<span>" + esc(it.time) + "</span></li>";
    }).join("") + "</ul>" : "<p>Nothing scheduled. See you at the next gathering.</p>");
  }
  function pickFirst(month) {
    var today = month.querySelector(".day.is-today"), chosen = today && today.classList.contains("has-items") ? today : null;
    if (!chosen) {
      var days = all(".day.has-items", month), from = today ? days.filter(function (d) { return +d.textContent >= +today.textContent; }) : days;
      chosen = from[0] || days[0];
    }
    if (chosen) pick(chosen);
  }
  if (months.length) {
    $("cal").addEventListener("click", function (e) {
      var day = e.target.closest(".day"), step = e.target.closest("[data-cal-step]");
      if (day) pick(day);
      if (step && !step.disabled) {
        months[monthIx].hidden = true;
        monthIx += +step.dataset.calStep;
        months[monthIx].hidden = false;
        pickFirst(months[monthIx]);
      }
    });
    pickFirst(months[0]);
  }

  /* ─── Scroll-linked: worship opening, parallax, the ring, the 3D story ─── */
  var worship = $("worship"), frameEl = $("worship-frame"), inWorship = worship && worship.querySelector(".worship-in");
  var drift = all("[data-speed]"), stage = $("belts");
  var story = $("story"), lines = all("[data-line]"), world = null, ticking = false, lineOn = 0;

  function pinned(el) {
    var b = el.getBoundingClientRect(), span = b.height - innerHeight;
    return span > 0 ? Math.min(1, Math.max(0, -b.top / span)) : 0;
  }
  function update() {
    ticking = false;
    var h = innerHeight;
    if (!reduce && worship) {
      var w = pinned(worship), open = Math.min(1, w * 1.5), ease = 1 - Math.pow(1 - open, 3);
      var base = innerWidth < 640 ? 7 : 12;
      frameEl.style.setProperty("--c", (base * (1 - ease)).toFixed(2) + "%");
      frameEl.style.setProperty("--z", (1.14 - .14 * ease).toFixed(3));
      inWorship.style.setProperty("--t", Math.min(1, Math.max(0, (w - .35) * 3)).toFixed(3));
      drift.forEach(function (el) {
        var b = el.getBoundingClientRect();
        if (b.bottom > -80 && b.top < h + 80) el.style.transform = "translateY(" + ((b.top + b.height / 2 - h / 2) * parseFloat(el.dataset.speed)).toFixed(1) + "px)";
      });
    }
    if (story && lines.length) {
      var p = pinned(story);
      var n = Math.min(lines.length - 1, Math.floor(p * lines.length * .999));
      if (n !== lineOn) { lines[lineOn].classList.remove("on"); lines[n].classList.add("on"); lineOn = n; }
      if (world) world.setProgress(p);
    }
  }
  function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onScroll, { passive: true });
  update();

  /* ─── The heavy pieces wait until the page is up, and until they are nearly in view ─── */
  function webgl2() { try { return !!doc.createElement("canvas").getContext("webgl2"); } catch (e) { return false; } }
  function loadWorld() {
    var conn = navigator.connection || {};
    if (conn.saveData || !webgl2()) { root.classList.add("no-world"); return; }
    var lite = (navigator.deviceMemory && navigator.deviceMemory <= 3) || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) || innerWidth < 768;
    var url = doc.querySelector('script[src*="home-v2.js"]').src.replace("home-v2.js", "world.min.js");
    import(url).then(function (mod) {
      world = mod.start($("world"), { lite: !!lite, still: reduce });
      world.setProgress(pinned(story));
      new IntersectionObserver(function (es) { world.setActive(es[0].isIntersecting); }).observe(story);
    }).catch(function () { root.classList.add("no-world"); });
  }
  function ready() {
    if (!reduce) {
      var first = photos[0], begin = function () { ripple = startRipple(); if (ripple) ripple.first(first); };
      if (first.complete && first.naturalWidth) begin(); else first.addEventListener("load", begin, { once: true });
    }
    if ("IntersectionObserver" in window) {
      var near = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { near.disconnect(); loadWorld(); } }, { rootMargin: "150% 0px" });
      near.observe(story);
      if (stage) {
        var soon = new IntersectionObserver(function (es) {
          if (es[0].isIntersecting) { soon.disconnect(); all("img[data-src]", stage).forEach(function (im) { im.src = im.dataset.src; }); }
        }, { rootMargin: "120% 0px" });
        soon.observe(stage);
      }
    } else { loadWorld(); if (stage) all("img[data-src]", stage).forEach(function (im) { im.src = im.dataset.src; }); }
  }
  if (doc.readyState === "complete") setTimeout(ready, 60); else addEventListener("load", function () { setTimeout(ready, 60); });

  /* ─── Family photos: pause the belts, or open any photo ─── */
  var beltBtn = $("belts-toggle"), viewer = $("viewer");
  if (beltBtn) {
    var viewerImg = $("viewer-img"), shots = all('.shot:not([aria-hidden="true"])'), shotIx = 0;
    var setPaused = function (on) {
      stage.classList.toggle("is-paused", on);
      beltBtn.setAttribute("aria-pressed", String(on));
      beltBtn.setAttribute("aria-label", on ? "Play moving photos" : "Pause moving photos");
      beltBtn.title = beltBtn.getAttribute("aria-label");
    };
    if (reduce) beltBtn.hidden = true;
    beltBtn.addEventListener("click", function () { setPaused(!stage.classList.contains("is-paused")); });
    var open = function (i) {
      shotIx = (i + shots.length) % shots.length;
      viewerImg.src = shots[shotIx].dataset.shot;
      viewerImg.alt = shots[shotIx].dataset.cap || "";
      if (!viewer.open) viewer.showModal();
    };
    stage.addEventListener("click", function (e) {
      var b = e.target.closest(".shot");
      if (b) open(Math.max(0, shots.findIndex(function (s) { return s.dataset.shot === b.dataset.shot; })));
    });
    $("viewer-close").addEventListener("click", function () { viewer.close(); });
    $("viewer-prev").addEventListener("click", function () { open(shotIx - 1); });
    $("viewer-next").addEventListener("click", function () { open(shotIx + 1); });
    viewer.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") open(shotIx - 1);
      if (e.key === "ArrowRight") open(shotIx + 1);
    });
    viewer.addEventListener("click", function (e) { if (e.target === viewer) viewer.close(); });
  }

  /* ─── Love in action: swipe, step, or let it run ─── */
  var track = $("car-track"), carPause = $("car-pause");
  if (track && carPause) {
    var car = $("car"), carN = track.children.length, carI = 0, carHeld = false, carSeen = false, carStopped = reduce;
    var count = $("car-count");
    var setStopped = function (on) {
      carStopped = on;
      carPause.setAttribute("aria-pressed", String(on));
      carPause.setAttribute("aria-label", on ? "Play slideshow" : "Pause slideshow");
      carPause.title = carPause.getAttribute("aria-label");
    };
    var carGo = function (i) {
      carI = (i + carN) % carN;
      track.scrollTo({ left: carI * track.clientWidth, behavior: reduce ? "auto" : "smooth" });
    };
    track.addEventListener("scroll", function () {
      carI = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      count.textContent = (carI + 1) + " / " + carN;
    }, { passive: true });
    $("car-prev").addEventListener("click", function () { carGo(carI - 1); });
    $("car-next").addEventListener("click", function () { carGo(carI + 1); });
    carPause.addEventListener("click", function () { setStopped(!carStopped); });
    ["pointerenter", "focusin"].forEach(function (ev) { car.addEventListener(ev, function () { carHeld = true; }); });
    ["pointerleave", "focusout"].forEach(function (ev) { car.addEventListener(ev, function () { carHeld = false; }); });
    if (reduce) setStopped(true);
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { carSeen = es[0].isIntersecting; }, { threshold: .5 }).observe(car);
    setInterval(function () { if (carSeen && !carHeld && !carStopped && !doc.hidden) carGo(carI + 1); }, 5000);
  }

  /* ─── Listening on the page ─── */
  var audio = null, active = null;
  var now = $("now"), nowTitle = $("now-title"), nowToggle = $("now-toggle");
  function setPlaying(on) {
    nowToggle.classList.toggle("is-on", on);
    nowToggle.setAttribute("aria-label", on ? "Pause" : "Play");
    if (active) active.classList.toggle("is-on", on);
  }
  function stop() {
    if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
    setPlaying(false); active = null; now.hidden = true;
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
  $("now-close").addEventListener("click", stop);
})();
