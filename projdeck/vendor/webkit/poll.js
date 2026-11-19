/* webkit poll.js: fragment polling. Config is only <main data-poll="URL" data-interval="MS">. */
(function () {
  "use strict";
  var main = document.querySelector("main[data-poll]");
  if (!main) return;
  var url = main.getAttribute("data-poll");
  var interval = parseInt(main.getAttribute("data-interval"), 10) || 5000;
  var lastOk = new Date();
  var timer = null;
  var busy = false;

  function narrow() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 700px)").matches);
  }

  function collapseNarrow(skip) {
    if (!narrow()) return;
    var list = main.querySelectorAll("details[data-collapse-narrow]");
    for (var i = 0; i < list.length; i++) {
      var key = list[i].getAttribute("data-key");
      if (key && skip && Object.prototype.hasOwnProperty.call(skip, key)) continue;
      list[i].removeAttribute("open");
      list[i].open = false;
    }
  }

  function saveOpen() {
    var state = {};
    var list = main.querySelectorAll("details[data-key]");
    for (var i = 0; i < list.length; i++) {
      state[list[i].getAttribute("data-key")] = !!list[i].open;
    }
    return state;
  }

  function restoreOpen(state) {
    var list = main.querySelectorAll("details[data-key]");
    for (var i = 0; i < list.length; i++) {
      var key = list[i].getAttribute("data-key");
      if (Object.prototype.hasOwnProperty.call(state, key)) {
        list[i].open = state[key];
        if (state[key]) list[i].setAttribute("open", "");
        else list[i].removeAttribute("open");
      }
    }
  }

  function hhmm(d) {
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function showOffline() {
    var el = document.getElementById("poll-offline");
    if (!el) {
      el = document.createElement("div");
      el.id = "poll-offline";
      el.setAttribute("role", "alert");
      document.body.insertBefore(el, document.body.firstChild);
    }
    el.textContent = "连接断开，显示的是 " + hhmm(lastOk) + " 的数据";
  }

  function hideOffline() {
    var el = document.getElementById("poll-offline");
    if (el) el.remove();
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(tick, interval);
  }

  function tick() {
    if (document.hidden) { schedule(); return; }
    if (busy) { schedule(); return; }
    busy = true;
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var killer = ctrl ? setTimeout(function () { ctrl.abort(); }, Math.max(interval * 2, 10000)) : null;
    Promise.resolve()
      .then(function () {
        var opts = { cache: "no-store", credentials: "same-origin" };
        if (ctrl) opts.signal = ctrl.signal;
        return fetch(url, opts);
      })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (html) {
        var state = saveOpen();
        main.innerHTML = html;
        restoreOpen(state);
        collapseNarrow(state);
        lastOk = new Date();
        hideOffline();
      })
      .catch(function () { showOffline(); })
      .then(function () {
        if (killer) clearTimeout(killer);
        busy = false;
        schedule();
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });
  collapseNarrow(null);
  schedule();
})();
