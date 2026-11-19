import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEBKIT = Path(__file__).resolve().parent.parent / "projdeck" / "vendor" / "webkit"
FILES = ["base.css", "poll.js"]


@pytest.mark.parametrize("name", FILES)
def test_no_external_or_dynamic_code(name):
    text = (WEBKIT / name).read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    assert "@import url(" not in text
    assert "eval(" not in text and "new Function" not in text


def test_base_css_contents():
    css = (WEBKIT / "base.css").read_text(encoding="utf-8")
    assert "@media (max-width: 700px)" in css
    for cls in (".card", ".pill", ".counter"):
        assert re.search(re.escape(cls) + r"\s*[{,]", css), cls
    for var in ("--busy", "--sub", "--wait", "--wait-now", "--done"):
        assert re.search(re.escape(var) + r"\s*:", css), var
    for var in ("--radius", "--gap", "--fs"):
        assert re.search(re.escape(var) + r"\s*:", css), var
    assert "input[type=checkbox]:disabled" in css
    assert "details > summary" in css
    for cls in (".busy", ".sub", ".wait", ".wait-now", ".done", ".empty"):
        assert re.search(re.escape(cls) + r"\s*\{\s*fill:", css), cls


DRIVER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const out = {};

class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.parent = null; this.open = false; this.textContent = ''; }
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = String(v); if (n === 'id') this.id = v; if (n === 'open') this.open = true; }
  removeAttribute(n) { delete this.attrs[n]; if (n === 'open') this.open = false; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
  insertBefore(el) { el.parent = this; this.children.unshift(el); }
  get firstChild() { return this.children[0] || null; }
  set innerHTML(html) {
    this.children = [];
    const re = /<details([^>]*)>/g; let m;
    while ((m = re.exec(html))) {
      const d = new El('details'); d.parent = this;
      const a = /([\w-]+)(?:="([^"]*)")?/g; let x;
      while ((x = a.exec(m[1]))) d.setAttribute(x[1], x[2] === undefined ? '' : x[2]);
      this.children.push(d);
    }
  }
  querySelectorAll(sel) {
    const m = /^details\[([\w-]+)\]$/.exec(sel);
    return this.children.filter(c => c.tag === 'details' && m[1] in c.attrs);
  }
}

const main = new El('main');
main.setAttribute('data-poll', '/fragment/home');
main.setAttribute('data-interval', '1000');
const d0 = new El('details'); d0.setAttribute('data-key', 'b1'); d0.setAttribute('open', ''); d0.parent = main;
main.children.push(d0);
const body = new El('body'); body.children.push(main);

let timers = [], hiddenFlag = false, mode = 'ok';
let nextId = 1;
global.setTimeout = (f, ms) => { const t = { id: nextId++, f, ms }; timers.push(t); return t.id; };
global.clearTimeout = (id) => { timers = timers.filter(t => t.id !== id); };
global.window = { matchMedia: () => ({ matches: false }) };
global.document = {
  get hidden() { return hiddenFlag; },
  body,
  querySelector: (s) => (s === 'main[data-poll]' ? main : null),
  getElementById: (id) => body.children.find(c => c.id === id) || null,
  createElement: (t) => new El(t),
  addEventListener: () => {},
};
global.fetch = async (u, o) => {
  const signal = o && o.signal;
  if (mode === 'hang') return new Promise((res, rej) => { if (signal) signal.addEventListener('abort', () => rej(new Error('abort'))); });
  if (mode === 'fail') throw new Error('down');
  return { ok: true, text: async () => '<details data-key="b1">x</details><details data-key="b2">y</details>' };
};

let hang = false;
const flush = () => new Promise(r => setImmediate(r));
async function fire(ms) { const i = timers.findIndex(t => t.ms === (ms || 1000)); if (i < 0) return; const t = timers.splice(i, 1)[0]; t.f(); await flush(); await flush(); }

(async () => {
  eval(src);
  mode = 'ok'; await fire();
  out.open_after_replace = main.querySelectorAll('details[data-key]').map(d => [d.getAttribute('data-key'), d.open]);
  mode = 'fail'; await fire();
  const b = document.getElementById('poll-offline');
  out.banner_after_fail = b ? b.textContent : null;
  mode = 'ok'; await fire();
  out.banner_after_recover = document.getElementById('poll-offline') ? 'present' : null;
  mode = 'hang'; global.fetch0 = global.fetch;
  await fire(); /* fetch hangs, busy stays true */
  await fire(10000); /* timeout timer aborts the request */
  out.banner_after_hang = document.getElementById('poll-offline') ? 'present' : null;
  mode = 'ok'; await fire();
  hiddenFlag = true; let called = 0; const old = global.fetch;
  global.fetch = async (u, o) => {
  const signal = o && o.signal; called++; return old(); };
  await fire();
  out.fetch_while_hidden = called;
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_poll_js_behaviour(tmp_path):
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    r = subprocess.run(["node", str(driver), str(WEBKIT / "poll.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["open_after_replace"] == [["b1", True], ["b2", False]]
    assert re.fullmatch(r"连接断开，显示的是 \d\d:\d\d 的数据", out["banner_after_fail"])
    assert out["banner_after_recover"] is None
    assert out["banner_after_hang"] == "present"
    assert out["fetch_while_hidden"] == 0
