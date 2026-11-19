"""Spec-derived tests for the webkit front-end assets (base.css, poll.js)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "projdeck", "vendor", "webkit")
CSS = os.path.join(KIT, "base.css")
JS = os.path.join(KIT, "poll.js")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- static

@pytest.mark.parametrize("path", [CSS, JS])
def test_files_exist_and_nonempty(path):
    assert os.path.isfile(path)
    assert len(_read(path).strip()) > 50


@pytest.mark.parametrize("path", [CSS, JS])
def test_no_external_urls(path):
    src = _read(path)
    assert "http://" not in src
    assert "https://" not in src


@pytest.mark.parametrize("path", [CSS, JS])
def test_no_import_url_eval_or_new_function(path):
    src = _read(path)
    assert not re.search(r"@import\s+url\(", src)
    assert "eval(" not in src
    assert not re.search(r"new\s+Function", src)


def test_css_narrow_media_query():
    assert re.search(r"@media\s*\(\s*max-width\s*:\s*700px\s*\)", _read(CSS))


@pytest.mark.parametrize("cls", ["card", "pill", "counter"])
def test_css_base_component_classes(cls):
    assert re.search(r"\.%s\b(?![-\w])" % cls, _read(CSS))


@pytest.mark.parametrize("var", ["busy", "sub", "wait", "wait-now", "done"])
def test_css_color_variables_defined(var):
    assert re.search(r"--%s\s*:\s*[^;]+;" % re.escape(var), _read(CSS))


def test_css_no_unbalanced_braces():
    src = re.sub(r"/\*.*?\*/", "", _read(CSS), flags=re.S)
    assert src.count("{") == src.count("}")


def test_poll_js_reads_documented_attributes():
    src = _read(JS)
    for needle in ("data-poll", "data-interval", "data-key", "data-collapse-narrow",
                   "poll-offline", "700"):
        assert needle in src


# ---------------------------------------------------------------- node harness

HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const [jsPath, scenarioJson] = process.argv.slice(2);
const sc = JSON.parse(scenarioJson);

function parseAttrs(s) {
  const a = new Map();
  const re = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(s))) a.set(m[1], m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : '');
  return a;
}
function matchSimple(el, sel) {
  sel = sel.trim();
  const m = /^([\w-]+|\*)?(#[\w-]+)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/.exec(sel);
  if (!m) return false;
  if (m[1] && m[1] !== '*' && el.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
  if (m[2] && el.getAttribute('id') !== m[2].slice(1)) return false;
  if (m[3]) for (const c of m[3].split('.').filter(Boolean))
    if (!(el.getAttribute('class') || '').split(/\s+/).includes(c)) return false;
  if (m[4]) {
    const re = /\[([\w:-]+)(?:=["']?([^\]"']*)["']?)?\]/g; let x;
    while ((x = re.exec(m[4]))) {
      if (!el.hasAttribute(x[1])) return false;
      if (x[2] !== undefined && el.getAttribute(x[1]) !== x[2]) return false;
    }
  }
  return true;
}
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.children = [];
    this.parentNode = null; this._html = ''; this.style = {}; this.textContent = '';
    this.hidden = false;
  }
  get id() { return this.getAttribute('id') || ''; }
  set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get open() { return this.attrs.has('open'); }
  set open(v) { if (v) this.attrs.set('open', ''); else this.attrs.delete('open'); }
  get dataset() {
    const self = this;
    return new Proxy({}, {
      get(_, k) { const n = 'data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase()); return self.attrs.has(n) ? self.attrs.get(n) : undefined; },
      set(_, k, v) { self.attrs.set('data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase()), String(v)); return true; },
    });
  }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); if (n === 'hidden') this.hidden = true; }
  removeAttribute(n) { this.attrs.delete(n); if (n === 'hidden') this.hidden = false; }
  hasAttribute(n) { return this.attrs.has(n); }
  matches(s) { return s.split(',').some(p => matchSimple(this, p)); }
  get innerHTML() { return this._html; }
  set innerHTML(h) {
    this._html = String(h);
    this.children.forEach(c => c.parentNode = null);
    this.children = [];
    const re = /<details\b([^>]*)>/gi; let m;
    while ((m = re.exec(this._html))) {
      const d = new El('details'); d.attrs = parseAttrs(m[1]); d.parentNode = this; this.children.push(d);
    }
  }
  get firstChild() { return this.children[0] || null; }
  _adopt(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; }
  appendChild(n) { this._adopt(n); this.children.push(n); return n; }
  append(...ns) { ns.forEach(n => this.appendChild(n)); }
  prepend(...ns) { ns.reverse().forEach(n => { this._adopt(n); this.children.unshift(n); }); }
  insertBefore(n, ref) { this._adopt(n); const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); return n; }
  insertAdjacentElement(pos, n) {
    if (pos === 'afterbegin') this.prepend(n); else if (pos === 'beforeend') this.appendChild(n);
    else if (pos === 'beforebegin') this.parentNode.insertBefore(n, this);
    else { const p = this.parentNode, i = p.children.indexOf(this); p.insertBefore(n, p.children[i + 1] || null); }
    return n;
  }
  before(n) { this.parentNode.insertBefore(n, this); }
  after(n) { const p = this.parentNode, i = p.children.indexOf(this); p.insertBefore(n, p.children[i + 1] || null); }
  removeChild(n) { const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); n.parentNode = null; return n; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceChildren(...ns) { this.children.forEach(c => c.parentNode = null); this.children = []; ns.forEach(n => this.appendChild(n)); }
  querySelectorAll(sel) {
    const out = [];
    const walk = e => e.children.forEach(c => { if (c.matches && c.matches(sel)) out.push(c); walk(c); });
    walk(this); return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  addEventListener() {}
  removeEventListener() {}
  get classList() {
    const self = this;
    const get = () => (self.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    return { add: c => self.setAttribute('class', [...new Set([...get(), c])].join(' ')),
             remove: c => self.setAttribute('class', get().filter(x => x !== c).join(' ')),
             contains: c => get().includes(c),
             toggle(c) { if (get().includes(c)) this.remove(c); else this.add(c); } };
  }
}

const html = new El('html'), head = new El('head'), body = new El('body'), main = new El('main');
html.appendChild(head); html.appendChild(body); body.appendChild(main);
main.setAttribute('data-poll', sc.poll || '/fragment/home');
if (sc.interval !== null) main.setAttribute('data-interval', String(sc.interval === undefined ? 5000 : sc.interval));
main.innerHTML = sc.initial;

const timers = []; let tid = 0;
const fetches = [];
let mode = 'ok', respHtml = '';
const docListeners = {};
const document = {
  hidden: false, visibilityState: 'visible', readyState: 'complete',
  documentElement: html, head, body,
  createElement: t => new El(t),
  createTextNode: t => { const e = new El('#text'); e.textContent = t; return e; },
  getElementById: id => html.querySelector('#' + id),
  querySelector: s => html.matches(s) ? html : html.querySelector(s),
  querySelectorAll: s => html.querySelectorAll(s),
  addEventListener: (ev, fn) => { (docListeners[ev] = docListeners[ev] || []).push(fn); },
  removeEventListener() {},
};
class DOMParser { parseFromString(s) { const b = new El('body'); b.innerHTML = s;
  return { body: b, documentElement: b, querySelector: x => b.querySelector(x), querySelectorAll: x => b.querySelectorAll(x) }; } }
const sandbox = {
  document, DOMParser, console, Date, Promise, JSON, Math, Map, Set, Array, Object, String, Number, Proxy,
  AbortController: class { constructor() { const ls = []; this.signal = { aborted: false, addEventListener: (e, f) => ls.push(f), removeEventListener() {} }; this.abort = () => { this.signal.aborted = true; ls.forEach(f => f()); }; } },
  fetch: async (url, opts) => {
    fetches.push(String(url));
    if (mode === 'hang') return new Promise((res, rej) => {
      const sg = opts && opts.signal;
      if (!sg) return;
      const err = () => { const e = new Error('aborted'); e.name = 'AbortError'; rej(e); };
      if (sg.aborted) return err();
      sg.addEventListener('abort', err);
    });
    if (mode === 'throw') throw new TypeError('network down');
    return { ok: true, status: 200, text: async () => respHtml };
  },
  setInterval: (fn, ms) => { timers.push({ fn, ms, id: ++tid }); return tid; },
  setTimeout: (fn, ms) => { timers.push({ fn, ms, id: ++tid, once: true }); return tid; },
  clearInterval: id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); },
  clearTimeout: id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); },
  addEventListener() {}, removeEventListener() {},
  innerWidth: sc.width,
  matchMedia: q => { const m = /max-width:\s*(\d+)px/.exec(q); return { matches: !!m && sc.width <= +m[1], media: q, addEventListener() {}, addListener() {}, removeEventListener() {} }; },
  requestAnimationFrame: fn => fn(),
};
sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);

const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
function snap() {
  const ds = {};
  main.querySelectorAll('details[data-key]').forEach(d => { ds[d.getAttribute('data-key')] = { open: d.hasAttribute('open'), gen: d.getAttribute('data-gen') }; });
  const b = document.getElementById('poll-offline');
  return { fetches: fetches.length, urls: fetches.slice(), details: ds,
    banners: html.querySelectorAll('#poll-offline').length,
    banner: b ? { text: (b.textContent || '') + ' ' + (b.innerHTML || ''), visible: !b.hidden && b.style.display !== 'none' && b.getAttribute('hidden') === null } : null,
    timers: timers.map(t => t.ms) };
}
(async () => {
  const out = [];
  vm.runInContext(fs.readFileSync(jsPath, 'utf8'), ctx, { filename: 'poll.js' });
  (docListeners['DOMContentLoaded'] || []).forEach(f => f());
  await flush();
  out.push(snap());
  for (const st of sc.steps) {
    if (st.fetch) { mode = st.fetch.mode; respHtml = st.fetch.html || ''; }
    if (st.hidden !== undefined) { document.hidden = st.hidden; document.visibilityState = st.hidden ? 'hidden' : 'visible';
      (docListeners['visibilitychange'] || []).forEach(f => f()); }
    if (st.set_open) { const d = main.querySelectorAll('details[data-key]').find(x => x.getAttribute('data-key') === st.set_open[0]); d.open = st.set_open[1]; }
    if (st.tick) { const ts = timers.slice(); for (const t of ts) { if (t.once) { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); } t.fn(); } }
    await flush();
    out.push(snap());
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack); process.exit(1); });
"""

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

INITIAL = ('<details data-key="b1" open><summary>one</summary></details>'
           '<details data-key="b2"><summary>two</summary></details>')


def new_html(b1_open=False, b2_open=False):
    return ('<details data-key="b1" data-gen="2"%s><summary>one</summary></details>'
            '<details data-key="b2" data-gen="2"%s><summary>two</summary></details>'
            '<details data-key="b3" data-gen="2"><summary>three</summary></details>'
            % (" open" if b1_open else "", " open" if b2_open else ""))


@pytest.fixture
def run_js(tmp_path):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS)

    def _run(steps, initial=INITIAL, width=1200, interval=5000, poll="/fragment/home"):
        sc = {"steps": steps, "initial": initial, "width": width, "interval": interval, "poll": poll}
        p = subprocess.run(["node", str(h), JS, json.dumps(sc)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout.strip().splitlines()[-1])
    return _run


def ok(html):
    return {"fetch": {"mode": "ok", "html": html}}


@pytestmark_node
def test_polls_configured_url_and_replaces_content(run_js):
    snaps = run_js([ok(new_html()), {"tick": 1}], poll="/fragment/projects")
    last = snaps[-1]
    assert last["fetches"] >= 1
    assert set(last["urls"]) == {"/fragment/projects"}
    assert last["details"]["b1"]["gen"] == "2"
    assert "b3" in last["details"]


@pytestmark_node
def test_interval_attribute_drives_timer(run_js):
    snaps = run_js([ok(new_html())], interval=7321)
    assert 7321 in snaps[-1]["timers"]


@pytestmark_node
def test_open_details_stay_open_after_replacement(run_js):
    snaps = run_js([ok(new_html(b1_open=False)), {"tick": 1}])
    d = snaps[-1]["details"]
    assert d["b1"]["gen"] == "2"          # content really replaced
    assert d["b1"]["open"] is True        # b1 was open, server html says closed
    assert d["b2"]["open"] is False       # b2 was closed, stays closed


@pytestmark_node
def test_user_toggle_between_polls_is_preserved(run_js):
    snaps = run_js([ok(new_html()), {"set_open": ["b2", True]}, {"set_open": ["b1", False]},
                    {"tick": 1}])
    d = snaps[-1]["details"]
    assert d["b2"]["gen"] == "2"
    assert d["b2"]["open"] is True
    assert d["b1"]["open"] is False


@pytestmark_node
def test_unkeyed_new_details_follow_server_state(run_js):
    snaps = run_js([ok(new_html()), {"tick": 1}])
    assert snaps[-1]["details"]["b3"]["open"] is False


@pytestmark_node
def test_fetch_failure_shows_offline_banner_then_clears(run_js):
    snaps = run_js([
        {"fetch": {"mode": "throw"}}, {"tick": 1},
        ok(new_html()), {"tick": 1},
    ])
    assert snaps[0]["banner"] is None or not snaps[0]["banner"]["visible"]
    failed = snaps[2]
    assert failed["banner"] is not None and failed["banner"]["visible"]
    assert re.search(r"连接断开，显示的是 \d{2}:\d{2} 的数据", failed["banner"]["text"])
    recovered = snaps[-1]
    assert recovered["details"]["b1"]["gen"] == "2"
    assert recovered["banner"] is None or not recovered["banner"]["visible"]


@pytestmark_node
def test_banner_not_duplicated_on_repeated_failures(run_js):
    snaps = run_js([{"fetch": {"mode": "throw"}}, {"tick": 1}, {"tick": 1}, {"tick": 1}])
    assert snaps[-1]["fetches"] > snaps[0]["fetches"]
    assert snaps[-1]["banner"]["visible"]
    # getElementById only finds the first one: count every banner in the document (F9)
    assert [s["banners"] for s in snaps[2:]] == [1, 1, 1]


@pytestmark_node
def test_hidden_page_pauses_polling(run_js):
    snaps = run_js([ok(new_html()), {"hidden": True}, {"tick": 1}, {"tick": 1}])
    assert snaps[-1]["fetches"] == snaps[2]["fetches"]


@pytestmark_node
def test_visible_page_polls_each_tick(run_js):
    snaps = run_js([ok(new_html()), {"tick": 1}, {"tick": 1}])
    assert snaps[-1]["fetches"] > snaps[1]["fetches"]


@pytestmark_node
def test_polling_resumes_after_page_becomes_visible(run_js):
    snaps = run_js([ok(new_html()), {"hidden": True}, {"tick": 1},
                    {"hidden": False}, {"tick": 1}, {"tick": 1}])
    assert snaps[-1]["fetches"] > snaps[3]["fetches"]


NARROW_INITIAL = ('<details data-key="n1" data-collapse-narrow open><summary>a</summary></details>'
                  '<details data-key="n2" open><summary>b</summary></details>')


@pytestmark_node
def test_narrow_screen_collapses_marked_details_only(run_js):
    snaps = run_js([], initial=NARROW_INITIAL, width=500)
    d = snaps[0]["details"]
    assert d["n1"]["open"] is False
    assert d["n2"]["open"] is True


@pytestmark_node
def test_wide_screen_leaves_marked_details_open(run_js):
    snaps = run_js([], initial=NARROW_INITIAL, width=1200)
    d = snaps[0]["details"]
    assert d["n1"]["open"] is True
    assert d["n2"]["open"] is True


@pytestmark_node
def test_narrow_boundary_700_counts_as_narrow(run_js):
    assert run_js([], initial=NARROW_INITIAL, width=700)[0]["details"]["n1"]["open"] is False
    assert run_js([], initial=NARROW_INITIAL, width=701)[0]["details"]["n1"]["open"] is True


@pytestmark_node
def test_hung_fetch_times_out_and_shows_banner(run_js):
    # fetch never settles unless aborted; the timeout must fire and show the banner
    snaps = run_js([{"fetch": {"mode": "hang"}}, {"tick": 1}, {"tick": 1}, {"tick": 1}],
                   interval=5000)
    assert snaps[-1]["banner"] is not None and snaps[-1]["banner"]["visible"]
    assert re.search(r"连接断开，显示的是 \d{2}:\d{2} 的数据", snaps[-1]["banner"]["text"])


@pytestmark_node
def test_banner_clears_after_hang_then_success(run_js):
    snaps = run_js([{"fetch": {"mode": "hang"}}, {"tick": 1}, {"tick": 1}, {"tick": 1},
                    ok(new_html()), {"tick": 1}, {"tick": 1}])
    last = snaps[-1]
    assert last["details"]["b1"]["gen"] == "2"
    assert last["banner"] is None or not last["banner"]["visible"]


@pytestmark_node
def test_timeout_scales_with_slow_interval(run_js):
    snaps = run_js([{"fetch": {"mode": "hang"}}, {"tick": 1}], interval=20000)
    assert any(t >= 40000 for t in snaps[-1]["timers"])


def test_css_has_size_space_radius_variables():
    src = _read(CSS)
    assert re.search(r"--[\w-]*(font|fs|text|size)[\w-]*\s*:", src)
    assert re.search(r"--[\w-]*(space|gap|pad|sp)[\w-]*\s*:", src)
    assert re.search(r"--[\w-]*(radius|round)[\w-]*\s*:", src)


def test_css_details_and_disabled_checkbox_styles():
    src = _read(CSS)
    assert re.search(r"\bdetails\b", src)
    assert re.search(r"\bsummary\b", src)
    assert re.search(r"checkbox", src) and re.search(r"disabled", src)


@pytest.mark.parametrize("cls", ["busy", "sub", "wait", "wait-now", "done", "empty"])
def test_css_svg_fill_classes(cls):
    src = _read(CSS)
    assert re.search(r"\.%s\b[^{]*\{[^}]*\bfill\s*:" % re.escape(cls), src)
