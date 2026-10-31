/* Every public page: the menu and the back-to-top control. */
(function () {
  "use strict";
  var doc = document, menu = doc.getElementById("menu");
  if (menu) {
    doc.getElementById("menu-open").addEventListener("click", function () { menu.showModal(); });
    doc.getElementById("menu-close").addEventListener("click", function () { menu.close(); });
  }
  var top = doc.createElement("button");
  top.type = "button";
  top.className = "totop";
  top.setAttribute("aria-label", "Back to top");
  top.title = "Back to top";
  top.innerHTML = '<i aria-hidden="true"></i>';
  doc.body.appendChild(top);
  top.addEventListener("click", function () { scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); });
  var tick = false;
  addEventListener("scroll", function () {
    if (tick) return;
    tick = true;
    requestAnimationFrame(function () { tick = false; top.classList.toggle("show", scrollY > innerHeight); });
  }, { passive: true });
})();
