import pytest

try:
    from projdeck.vendor.webkit import webkit
except ImportError:  # standalone webkit repo
    import webkit

NAMES = {"100.101.102.103", "demo-vm", "demo-vm.tail0000.ts.net"}


def test_h_escapes_everything():
    out = webkit.h('<script>alert(1)</script>"\'&')
    for ch in '<>"\'':
        assert ch not in out
    assert "&amp;" in out
    assert webkit.h(None) == ""
    assert webkit.h(12) == "12"


@pytest.mark.parametrize("addr", ["0.0.0.0", "192.168.1.5", "::", "100.128.0.1", "", "foo", "100.63.255.255"])
def test_check_bind_rejects(addr):
    with pytest.raises(ValueError):
        webkit.check_bind(addr)


@pytest.mark.parametrize("addr", ["100.101.102.103", "100.64.0.1", "100.127.255.255", "127.0.0.1", "::1"])
def test_check_bind_accepts(addr):
    webkit.check_bind(addr)


@pytest.mark.parametrize("hdr", [
    "demo-vm.tail0000.ts.net:8787", "demo-vm", "100.101.102.103:8787", "DEMO-VM:8787",
    "demo-vm.tail0000.ts.net.",
])
def test_host_allowed(hdr):
    assert webkit.host_allowed(hdr, NAMES, 8787)


@pytest.mark.parametrize("hdr", [
    "evil.example", "demo-vm.evil.example", "", None, "demo-vm:9999", "demo-vm:abc",
    "evil.example:8787", "demo-vm@evil.example", "demo-vm:8787:1",
    "demo-vm:\xb2", "demo-vm:" + "1" * 5000, "demo-vm:８７８７", "demo-vm:08787",
])
def test_host_denied(hdr):
    assert not webkit.host_allowed(hdr, NAMES, 8787)


def test_host_allowed_ipv6_bracket():
    assert webkit.host_allowed("[::1]:8787", {"::1"}, 8787)
    assert not webkit.host_allowed("[::2]:8787", {"::1"}, 8787)


def test_security_headers_exact():
    assert webkit.SECURITY_HEADERS == {
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


def test_layout_safe():
    out = webkit.layout("<b>t</b>", "<nav>n</nav>", "<main>x</main>", ["/static/base.css", "/static/app.css"])
    assert '<script src="/static/poll.js">' in out
    assert out.count("<script") == 1
    assert "style=" not in out
    assert "http://" not in out and "https://" not in out
    assert "&lt;b&gt;t&lt;/b&gt;" in out and "<b>t</b>" not in out
    assert '<link rel="stylesheet" href="/static/base.css">' in out
    assert "<nav>n</nav>" in out and "<main>x</main>" in out
