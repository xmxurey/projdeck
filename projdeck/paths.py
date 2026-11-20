"""Path helpers shared by the terminal UI and the zellij session naming."""


def rel_to_home(path, home):
    """`path` relative to `home` (dev/projdeck), or None when the path is not strictly
    under home (an empty or missing home never matches)."""
    root = (home or "").rstrip("/")
    p = path.rstrip("/")
    if root and p.startswith(root + "/"):
        return p[len(root) + 1:]
    return None
