"""webkit core: HTML escaping, page skeleton, security headers, bind/Host checks.

Standard library only. All dynamic text must go through h().
"""
import html
import ipaddress

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def h(s):
    """Escape text for HTML (text and attribute contexts). None -> ''."""
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def layout(title, nav, body, css):
    """Full HTML page. title and css paths are escaped; nav and body are
    trusted HTML the caller has already built with h()."""
    links = "".join('<link rel="stylesheet" href="%s">\n' % h(c) for c in css)
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n%s</head>\n<body>\n%s\n%s\n"
        '<script src="/static/poll.js"></script>\n</body>\n</html>\n'
    ) % (h(title), links, nav, body)


def check_bind(addr):
    """Allow only 100.64.0.0/10 or loopback; raise ValueError otherwise."""
    try:
        ip = ipaddress.ip_address(addr)
    except (ValueError, TypeError):
        raise ValueError("invalid bind address: %r" % (addr,))
    if ip.is_loopback:
        return
    if ip.version == 4 and ip in _TAILNET:
        return
    raise ValueError("bind address %s is not loopback or in 100.64.0.0/10" % addr)


def host_allowed(header, names, port):
    """True if the Host header names an allowed host, with no port or with `port`."""
    if not header or not isinstance(header, str):
        return False
    host = header.strip()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False
        name, rest = host[1:end], host[end + 1:]
        if rest and not rest.startswith(":"):
            return False
        p = rest[1:] if rest else None
    else:
        name, sep, p = host.partition(":")
        if not sep:
            p = None
    if p is not None and p != str(port):
        return False
    name = name.rstrip(".").lower()
    if not name:
        return False
    return name in {n.rstrip(".").lower() for n in names}
