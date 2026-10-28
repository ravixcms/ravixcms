/* Ravix Studio — public pages. Loaded only on pages that contain a video widget (~1 KB).
   A video shows a cover and a play button; nothing from the video provider is requested until the visitor clicks. */
(function () {
  "use strict";
  var SRC = {
    youtube: function (id) { return "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0"; },
    vimeo: function (id) { return "https://player.vimeo.com/video/" + id + "?autoplay=1&dnt=1"; },
    aparat: function (id) { return "https://www.aparat.com/video/video/embed/videohash/" + id + "/vt/frame?autoplay=true"; }
  };
  var ID = { youtube: /^[A-Za-z0-9_-]{11}$/, vimeo: /^\d{5,12}$/, aparat: /^[A-Za-z0-9]{3,20}$/ };

  function play(box) {
    var parts = (box.getAttribute("data-st-video") || "").split(":");
    var make = SRC[parts[0]];
    if (!make || !ID[parts[0]].test(parts[1] || "")) return false;
    var link = box.querySelector(".st-video-btn");
    var frame = document.createElement("iframe");
    frame.src = make(parts[1]);
    frame.title = link ? link.getAttribute("aria-label") || "" : "";
    frame.allow = "autoplay; fullscreen; picture-in-picture";
    frame.setAttribute("allowfullscreen", "");
    frame.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    frame.setAttribute("loading", "lazy");
    box.textContent = "";
    box.appendChild(frame);
    frame.focus();
    return true;
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest(".st-video-btn");
    if (!link) return;
    var box = link.closest("[data-st-video]");
    if (box && play(box)) event.preventDefault();  // without JS (or on a bad id) the link opens the provider's page instead
  });
})();
