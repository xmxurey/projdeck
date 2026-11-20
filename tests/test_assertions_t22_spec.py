"""T22 black-box mutation checks: the tightened serve assertions in the two serve test files
must fail when serve.py is deliberately wrong. Each mutant is a text replacement applied to a
copy of the tree (no .git); the cases that must notice it are run in a child pytest.
Slow: runs only with PROJ_MUTATION_TESTS=1 (see tests/_mutation.py)."""
import pytest

from tests import _mutation as M

pytestmark = M.requires_mutation

WEB = "tests/test_web_serve_spec.py::"
REFRESH = "tests/test_serve_refresh_spec.py::"
FILES = ["tests/test_web_serve_spec.py", "tests/test_serve_refresh_spec.py"]
SERVE = "projdeck/serve.py"

_SUMMARY_ZERO = (
    "_public(dict(snap, summary={k: (0 if isinstance(v, int) and not isinstance(v, bool) "
    "else v) for k, v in (snap.get(\"summary\") or {}).items()}))")

# name -> (old, new, the cases that must fail)
MUTANTS = {
    "1_keep_query": (
        'path = self.path.split("?", 1)[0].split("#", 1)[0]', "path = self.path",
        [WEB + "test_query_string_does_not_change_routing"]),
    "11_summary_zero": ("json.dumps(_public(snap), ensure_ascii=False)",
                        "json.dumps(" + _SUMMARY_ZERO + ", ensure_ascii=False)",
                        [WEB + "test_state_json_parses_and_has_no_prompts"]),
    # the delayed thread leaks into later cases (ruling), hence only these two, run on their own
    "15_local_delayed_thread": (
        'if h.get("local") or not h.get("ssh"):',
        'if not h.get("local") and not h.get("ssh"):',
        [REFRESH + "test_pusher_sends_doc_to_each_ssh_host_not_local",
         REFRESH + "test_pusher_with_no_remote_hosts_does_nothing"]),
    "15_drain_sleeps_first": (
        '                _push_one(host, body, self.cfg["ssh_timeout"])',
        '                time.sleep(0.5)\n'
        '                _push_one(host, body, self.cfg["ssh_timeout"])',
        [REFRESH + "test_single_doc_is_pushed_once"]),
    "15_push_every_doc": (
        'lane["pending"] = body\n',
        'lane["pending"] = body\n                lane.setdefault("q", []).append(body)\n',
        [REFRESH + "test_three_docs_during_inflight_push_collapse_to_first_and_last"]),
    "16_sigterm_nonzero": (
        "    except (KeyboardInterrupt, _Terminated):\n        pass\n",
        "    except KeyboardInterrupt:\n        pass\n    except _Terminated:\n"
        "        raise SystemExit(3)\n",
        [REFRESH + "test_sigterm_ends_serve_through_normal_exit",
         REFRESH + "test_sigterm_right_after_bind_still_exits"]),
}
# the push-every-doc mutant needs a second edit (drain pops from the queue)
EXTRA = {"15_local_delayed_thread": [(
    'host["ssh"], PUSH_CMD]', 'host.get("ssh") or "s-host", PUSH_CMD]'), (
    '        try:\n            while True:\n                with self._lock:\n                    body,',
    '        try:\n            if host.get("local"):\n                time.sleep(0.5)\n'
    '            while True:\n                with self._lock:\n                    body,')],
    "15_push_every_doc": (
    'body, lane["pending"] = lane["pending"], None',
    'q = lane.setdefault("q", []); body = q.pop(0) if q else None; lane["pending"] = None')}


def _extra(name):
    e = EXTRA.get(name, [])
    return e if isinstance(e, list) else [e]


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    return tmp_path_factory.mktemp("t22")


def test_unmutated_copy_passes_both_files(sandbox):
    tree = M.copy_tree(sandbox / "clean")
    for rel in FILES:
        r = M.run_pytest(tree, [rel], sandbox / "clean-home", timeout=500)
        assert r.returncode == 0, rel + "\n" + r.stdout[-1500:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_caught(sandbox, name):
    old, new, nodes = MUTANTS[name]
    tree = M.copy_tree(sandbox / ("m_" + name))
    path = tree / SERVE
    text = path.read_text(encoding="utf-8")
    for o, n in [(old, new)] + _extra(name):
        assert text.count(o) == 1, "mutation target not found exactly once: %s" % o
        text = text.replace(o, n)
    path.write_text(text, encoding="utf-8")
    M.assert_caught(M.run_pytest(tree, nodes, sandbox / ("m_" + name + "-home"), timeout=500),
                    nodes, name)
