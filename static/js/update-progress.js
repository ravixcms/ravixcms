/* Live progress of an update. Asks the status endpoint every two seconds. While the site restarts a few answers fail:
   that is expected and shown as "restarting", never as an error. Reads only data-* attributes (no inline script: the CSP forbids it). */
(function () {
  "use strict";
  var root = document.getElementById("update-progress");
  if (!root) { return; }
  var url = root.dataset.url, doneClass = "is-done";
  var bar = root.querySelector("[data-bar]"), pct = root.querySelector("[data-percent]"), note = root.querySelector("[data-note]");
  var failures = 0, timer = null;

  function paint(d) {
    failures = 0;
    root.dataset.state = d.status;
    root.querySelectorAll("[data-phase]").forEach(function (li) {
      var p = d.phases.filter(function (x) { return x.key === li.dataset.phase; })[0];
      if (p) { li.dataset.state = p.state; }
    });
    if (bar) { bar.style.width = d.percent + "%"; bar.setAttribute("aria-valuenow", d.percent); }
    if (pct) { pct.textContent = d.percent + "%"; }
    if (note) { note.textContent = d.status === "queued" ? root.dataset.queued : (d.finished ? "" : (d.rolling_back ? root.dataset.rollingBack : "")); }
    if (d.finished) {
      root.classList.add(doneClass);
      var out = root.querySelector("[data-outcome-" + d.status.replace("_", "-") + "]");
      if (out) { out.hidden = false; }
      var msg = root.querySelector("[data-message]");
      if (msg && d.message) { msg.textContent = d.message; msg.hidden = false; }
      return;
    }
    schedule();
  }

  function lost() {
    failures += 1;
    if (note) { note.textContent = root.dataset.restarting; }
    schedule();
  }

  function poll() {
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(paint, lost);
  }

  function schedule() { timer = window.setTimeout(poll, failures > 5 ? 5000 : 2000); }
  poll();
})();
