/* Nova: apply the saved colour scheme before first paint (no flash). Loaded synchronously in <head>. */
(function () {
  try {
    var m = localStorage.getItem("nova-scheme");
    if (m === "dark" || m === "light") document.documentElement.setAttribute("data-scheme", m);
  } catch (e) { /* storage blocked: follow the system */ }
  document.documentElement.classList.add("js");
})();
