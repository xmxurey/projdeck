"""T23 black-box mutation check: the four strengthened test files must catch wrong code.

Each case copies the repo (no .git) into tmp, applies one text mutation to projdeck/ (it must
match exactly once, else the case fails), and runs the cases that must notice it in a child pytest.
Slow: runs only with PROJ_MUTATION_TESTS=1 (see tests/_mutation.py).
"""
import subprocess
import sys

import pytest

from tests import _mutation as M

pytestmark = M.requires_mutation

F_SOURCES = "tests/test_task_sources_spec.py"
F_SYNC = "tests/test_sync_live_spec.py"
F_BOARD = "tests/test_board_build_spec.py"
F_AGENT = "tests/test_agent_live_spec.py"
ALL_FILES = [F_SOURCES, F_SYNC, F_BOARD, F_AGENT]

TRANSCRIPT_INDEX_OLD = '''    try:
        dirs = sorted(os.listdir(base))
    except OSError:
        return index
    for d in dirs:
        pd = os.path.join(base, d)
        try:
            names = sorted(os.listdir(pd))
        except OSError:
            continue
        for fn in names:
            if fn.endswith(".jsonl"):
                index.setdefault('''
TRANSCRIPT_INDEX_NEW = TRANSCRIPT_INDEX_OLD.replace(
    "sorted(os.listdir(base))", "sorted(e.name for e in os.scandir(base))").replace(
    "sorted(os.listdir(pd))", "sorted(e.name for e in os.scandir(pd))")

# (id, file under projdeck/, old text, new text, the cases that must fail)
MUTATIONS = [
    ("find_tasks_always_none", "agent.py",
     '    if not found:\n        return None\n    found["errors"] = errors',
     '    return None\n    found["errors"] = errors', [F_SOURCES + "::test_ledger_beats_sdd"]),
    ("plan_without_task_titles_gives_empty_plan", "agent.py",
     '        if tasks:\n            return {"source": "plan"',
     '        if True:\n            return {"source": "plan"',
     [F_SOURCES + "::test_plan_without_task_headings_gives_no_source"]),
    ("ledger_without_tasks_gives_none", "agent.py",
     '    cur = state.get("current_task")\n',
     '    if not items:\n        return None\n    cur = state.get("current_task")\n',
     [F_SOURCES + "::test_state_json_without_tasks_list_does_not_crash"]),
    ("load_caches_sorted_by_name", "sync.py",
     'caches = [read_cache(h["name"]) for h in cfg["hosts"]]',
     'caches = [read_cache(h["name"]) for h in sorted(cfg["hosts"], key=lambda h: h["name"])]',
     [F_SYNC + "::test_collect_and_live_caches_never_collide"]),
    ("board_four_keys_empty", "board.py",
     '    out["tiles"] = build_tiles(caches, out, names, cfg, now)\n    return out\n',
     '    out["tiles"] = build_tiles(caches, out, names, cfg, now)\n'
     '    out["top_todos"], out["backlogs"], out["tiles"], out["lanes"] = [], [], [], []\n'
     '    return out\n',
     [F_BOARD + "::test_lanes_todos_backlogs_tiles_carry_the_content_of_the_input"]),
    ("transcript_index_via_scandir", "agent.py",
     TRANSCRIPT_INDEX_OLD, TRANSCRIPT_INDEX_NEW,
     [F_AGENT + "::test_transcript_deleted_after_listdir_is_silent"]),
]


@pytest.mark.parametrize("mid,fname,old,new,nodes", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_is_caught(tmp_path, mid, fname, old, new, nodes):
    repo = M.copy_tree(tmp_path / "repo")
    M.apply_mutation(repo, "projdeck/" + fname, old, new)
    # sanity: the mutated source still imports (a syntax error would "fail" for the wrong reason)
    chk = subprocess.run([sys.executable, "-c", "import projdeck.%s" % fname[:-3]], cwd=str(repo),
                         env=M.child_env(tmp_path / "home"), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, universal_newlines=True)
    assert chk.returncode == 0, chk.stdout
    r = M.run_pytest(repo, nodes, tmp_path / "home")
    M.assert_caught(r, nodes, mid)
    assert "SyntaxError" not in r.stdout and "ImportError" not in r.stdout


def test_unmutated_copy_passes_all_four_files(tmp_path):
    repo = M.copy_tree(tmp_path / "repo")
    r = M.run_pytest(repo, ALL_FILES, tmp_path / "home")
    assert r.returncode == 0, r.stdout[-3000:]
