"""Black-box mutation checks for tests/test_contrib_docs.py (T26).

Each counter-example is applied to a git-free copy of the repo tree and the doc test
file is run there in a subprocess: it must pass on the pristine copy and fail on every
mutation that changes behaviour or breaks the README install commands.
"""
import os

import pytest

from tests import _mutation as M

pytestmark = M.requires_mutation

UNIT = os.path.join("contrib", "systemd", "proj-serve.service")
README = "README.md"
ENABLE = "systemctl --user enable --now proj-serve"
TARGET = os.path.join("tests", "test_contrib_docs.py")
KEY_LINES = TARGET + "::test_unit_key_lines"
NO_NETWORK_ONLINE = TARGET + "::test_unit_no_network_online"
README_CODE_LINE = TARGET + "::test_readme_key_commands_are_code_block_lines[%s]" % ENABLE


def _copy(tmp_path, name):
    return M.copy_tree(str(tmp_path / name))


def _run(tree, tmp_path, nodes=(TARGET,)):
    return M.run_pytest(tree, nodes, tmp_path / ("home-" + os.path.basename(tree)))


def _read(tree, rel):
    with open(os.path.join(tree, rel), encoding="utf-8") as f:
        return f.read()


def _write(tree, rel, text):
    with open(os.path.join(tree, rel), "w", encoding="utf-8") as f:
        f.write(text)


def _mutate_unit(tree, fn):
    old = _read(tree, UNIT)
    new = fn(old)
    assert new != old
    _write(tree, UNIT, new)


def _mutate_readme(tree, fn):
    old = _read(tree, README)
    new = fn(old)
    assert new != old
    _write(tree, README, new)


def _unit_lines(text):
    return text.splitlines()


# ---- unit file mutations
def u_no_restart(t):
    return "".join(l + "\n" for l in _unit_lines(t) if l.strip() != "Restart=always")


def u_after_network_online(t):
    return t.replace("[Unit]\n", "[Unit]\nAfter=network-online.target\n", 1)


def u_after_in_service(t):
    return t.replace("[Service]\n", "[Service]\nAfter=network-online.target\n", 1)


def u_restart_on_failure(t):
    return t.replace("Restart=always", "Restart=on-failure")


def u_no_path(t):
    return "".join(l + "\n" for l in _unit_lines(t) if not l.startswith("Environment=PATH="))


def u_path_without_local_bin(t):
    return t.replace("%h/.local/bin:", "")


def u_path_without_usr_bin(t):
    return t.replace(":/usr/bin", "")


# mutation -> the case in test_contrib_docs.py that must catch it
UNIT_BAD = {u_no_path: KEY_LINES, u_path_without_local_bin: KEY_LINES,
            u_path_without_usr_bin: KEY_LINES, u_no_restart: KEY_LINES,
            u_after_network_online: NO_NETWORK_ONLINE, u_after_in_service: NO_NETWORK_ONLINE,
            u_restart_on_failure: KEY_LINES}


# ---- README mutations
def r_commented(t):
    return t.replace(ENABLE, "# " + ENABLE, 1)


def r_moved_to_prose(t):
    lines = t.split("\n")
    i = lines.index(ENABLE)
    del lines[i]
    out = "\n".join(lines)
    return out + "\n\n" + ENABLE + "\n"


def r_inline_prose(t):
    lines = t.split("\n")
    i = lines.index(ENABLE)
    del lines[i]
    return "\n".join(lines) + "\n\n运行 `" + ENABLE + "` 即可。\n"


def r_suffix(t):
    return t.replace(ENABLE + "\n", ENABLE + "-other\n", 1)


def r_prefix_sudo(t):
    return t.replace(ENABLE + "\n", "echo " + ENABLE + "\n", 1)


def r_removed(t):
    return t.replace(ENABLE + "\n", "", 1)


README_BAD = [r_commented, r_moved_to_prose, r_inline_prose, r_suffix, r_prefix_sudo,
              r_removed]  # each must be caught by README_CODE_LINE


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pristine")
    tree = _copy(tmp, "tree")
    return tree, tmp


def test_pristine_copy_passes(pristine):
    tree, tmp = pristine
    r = _run(tree, tmp)
    assert r.returncode == 0, r.stdout[-2000:]


def test_passes_without_git_dir(tmp_path):
    tree = _copy(tmp_path, "nogit")
    assert not os.path.exists(os.path.join(tree, ".git"))
    r = _run(tree, tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


@pytest.mark.parametrize("fn", sorted(UNIT_BAD, key=lambda f: f.__name__), ids=lambda f: f.__name__)
def test_unit_counterexample_fails(tmp_path, fn):
    tree = _copy(tmp_path, "unit")
    _mutate_unit(tree, fn)
    node = UNIT_BAD[fn]
    M.assert_caught(_run(tree, tmp_path, [node]), [node], fn.__name__)


@pytest.mark.parametrize("fn", README_BAD, ids=lambda f: f.__name__)
def test_readme_counterexample_fails(tmp_path, fn):
    tree = _copy(tmp_path, "readme")
    _mutate_readme(tree, fn)
    M.assert_caught(_run(tree, tmp_path, [README_CODE_LINE]), [README_CODE_LINE], fn.__name__)


def test_unit_non_behaviour_edit_still_passes(tmp_path):
    """Two-sided: the unit check only looks at lines with behavioural consequence."""
    tree = _copy(tmp_path, "benign")
    _mutate_unit(tree, lambda t: "# a comment\n" + t.replace(
        "Description=proj dashboard (read-only, Tailscale only)",
        "Description=something else entirely"))
    r = _run(tree, tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


def test_readme_extra_prose_still_passes(tmp_path):
    """Two-sided: unrelated README prose changes do not trip the check."""
    tree = _copy(tmp_path, "benign-readme")
    _mutate_readme(tree, lambda t: t + "\n附注：这是一段无关的说明。\n")
    r = _run(tree, tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]
