import re

import pytest

try:
    from projdeck.vendor.webkit import webkit
except ImportError:  # pragma: no cover
    import webkit

NAMES = {"100.101.102.103", "demo-vm", "demo-vm.tail0000.ts.net"}

EXPECTED_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                               "img-src 'self'; connect-src 'self'; base-uri 'none'; "
                               "form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def test_h_escapes_dangerous_chars():
    out = webkit.h('<script>alert(1)</script>"\'&')
    for ch in '<>"\'':
        assert ch not in out
    assert "&" in out
    # every & must begin an entity: the raw ampersand itself was escaped
    assert not re.search(r"&(?!#?\w+;)", out)
    assert "&amp;" in out or "&#38;" in out


def test_h_none_is_empty():
    assert webkit.h(None) == ""


def test_h_plain_and_non_str():
    assert webkit.h("hello 世界") == "hello 世界"
    assert webkit.h("") == ""
    assert webkit.h(42) == "42"


def test_h_no_double_escape_difference():
    # escaping an already escaped string escapes its ampersand again
    once = webkit.h("<")
    assert webkit.h(once) != once


@pytest.mark.parametrize("addr", ["0.0.0.0", "192.168.1.5", "::", "100.128.0.1",
                                  "100.63.255.255", "10.0.0.1", "8.8.8.8", "", "garbage",
                                  "fe80::1"])
def test_check_bind_rejects(addr):
    with pytest.raises(ValueError):
        webkit.check_bind(addr)


@pytest.mark.parametrize("addr", ["100.101.102.103", "100.64.0.1", "127.0.0.1", "::1",
                                  "100.127.255.255", "127.0.0.2"])
def test_check_bind_accepts(addr):
    assert webkit.check_bind(addr) is None


@pytest.mark.parametrize("hdr", ["demo-vm.tail0000.ts.net:8787", "demo-vm",
                                 "100.101.102.103:8787", "100.101.102.103",
                                 "demo-vm:8787", "demo-vm.tail0000.ts.net"])
def test_host_allowed_true(hdr):
    assert webkit.host_allowed(hdr, NAMES, 8787) is True


@pytest.mark.parametrize("hdr", ["evil.example", "demo-vm.evil.example", "", "evil.example:8787",
                                 "demo-vm:9999", "100.101.102.104:8787", "demo-vm.evil.example:8787",
                                 "evil.example/demo-vm", "xdemo-vm"])
def test_host_allowed_false(hdr):
    assert webkit.host_allowed(hdr, NAMES, 8787) is False


def test_host_allowed_empty_names():
    assert webkit.host_allowed("demo-vm", set(), 8787) is False


def test_security_headers_exact():
    assert dict(webkit.SECURITY_HEADERS) == EXPECTED_HEADERS


def _page(title="T", nav="<a href=\"/\">home</a>", body="<p>hi</p>",
          css=("/static/base.css", "/static/app.css")):
    return webkit.layout(title, nav, body, list(css))


def test_layout_has_poll_script():
    assert '<script src="/static/poll.js">' in _page()


def test_layout_no_inline_script_or_style_or_external():
    out = _page()
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", out, re.S | re.I)
    assert scripts, "expected script tag"
    assert all(s.strip() == "" for s in scripts)
    assert "<style" not in out.lower()
    assert 'style="' not in out
    assert "http://" not in out and "https://" not in out


def test_layout_title_escaped():
    out = _page(title="<script>alert(1)</script>\"'&")
    assert "<script>alert(1)" not in out
    m = re.search(r"<title>(.*?)</title>", out, re.S)
    assert m
    for ch in "<>\"'":
        assert ch not in m.group(1)


def test_layout_includes_css_nav_body():
    out = _page(nav="<nav-marker>", body="<b>BODY-MARK</b>")
    assert 'href="/static/base.css"' in out
    assert 'href="/static/app.css"' in out
    assert "<nav-marker>" in out
    assert "<b>BODY-MARK</b>" in out


def test_layout_empty_css_list():
    out = _page(css=())
    assert '<script src="/static/poll.js">' in out
    assert 'style="' not in out


@pytest.mark.parametrize("hdr", ["demo-vm:8787²", "demo-vm:²", "demo-vm:" + "9" * 5000,
                                 "demo-vm:８７８７", "demo-vm:08787",
                                 "demo-vm:8787:8787", "demo-vm:", "demo-vm:-8787", "demo-vm: 8787",
                                 "demo-vm²"])
def test_host_allowed_weird_port_returns_false_without_raising(hdr):
    assert webkit.host_allowed(hdr, NAMES, 8787) is False
