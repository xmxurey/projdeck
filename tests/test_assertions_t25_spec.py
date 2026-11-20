"""T25 black-box mutation checks: the tightened assertions in four test files must fail when
the code under them is deliberately wrong. Each mutant is a text replacement applied to a copy
of the tree (no .git); the cases that must notice it are then run in a child pytest.
Slow: runs only with PROJ_MUTATION_TESTS=1 (see tests/_mutation.py)."""
import pytest

from tests import _mutation as M

pytestmark = M.requires_mutation

ZC = "tests/test_zellij_create_spec.py::"
KEYS = "tests/test_keys_spec.py::"
UICLI = "tests/test_ui_cli.py::"
TEST_FILES = ["tests/test_zellij_create_spec.py", "tests/test_zellij_create.py",
              "tests/test_keys_spec.py", "tests/test_ui_cli.py"]
CLI = "projdeck/cli.py"
OPENER = "projdeck/opener.py"
UI = "projdeck/ui.py"

ATTACH_OLD = ('    return "if %s; then exec zellij attach %s; else cd %s && %s; fi" % (\n'
              '        live, q(name), q(path), _then_shell(PICKER))')
LAYOUT_ERR = (
    '        print("cannot write the zellij layout, running here instead: %s" % termtext.one_line(e),\n'
    '              file=sys.stderr)\n')

# name -> (file, old, new, the cases that must fail)
MUTANTS = {
    "4_attach_echo": (OPENER, ATTACH_OLD, '    return "echo attach proj-quant"',
                      [ZC + "test_one_live_session_is_attached",
                       ZC + "test_several_live_sessions_show_the_picker_first"]),
    "5_no_exec_shell": (OPENER, """    return '"$SHELL" -lic %s; %s' % (q(inner), LOGIN_SHELL)""",
                        """    return '"$SHELL" -lic %s' % q(inner)""",
                        [ZC + "test_live_proj_session_not_owned_goes_through_zellij_attach_only"]),
    "6_error_twice": (CLI, LAYOUT_ERR, LAYOUT_ERR + LAYOUT_ERR,
                      [ZC + "test_unwritable_layout_dir_falls_back_with_one_stderr_line",
                       ZC + "test_layout_failing_midway_falls_back_and_leaves_no_final_file"]),
    "6_error_no_reason": (CLI, LAYOUT_ERR,
                          '        print("cannot write the zellij layout, running here instead",\n'
                          '              file=sys.stderr)\n',
                          [ZC + "test_layout_failing_midway_falls_back_and_leaves_no_final_file"]),
    "8_no_second_fzf": (CLI,
                        '                _, _, sid = ui.pick_session([(r, s) for s in r["sessions"]], now)\n',
                        '                sid = r["sessions"][0]["id"]\n',
                        [KEYS + "test_ctrl_y_enters_session_picker_and_resumes_selected"]),
    "9_always_first_row": (CLI, "        r = model.find_row(rows, host, path)\n        if r is None:",
                           "        r = rows[0]\n        if r is None:",
                           [KEYS + "test_enter_opens_the_selected_project[/p/b]"]),
    "20_eleven_batches": (CLI, 'print("%d batch(es) will be permanently deleted." % len(old))',
                          'print("1%d batch(es) will be permanently deleted." % len(old))',
                          [UICLI + "test_cli_clean_restore_purge"]),
    "20_dir_p10": (CLI, 'termtext.one_line(b["project"]))\n\n\ndef _batch_line',
                   'termtext.one_line(b["project"]) + "0")\n\n\ndef _batch_line',
                   [UICLI + "test_cli_clean_restore_purge"]),
}


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    return tmp_path_factory.mktemp("t25")


def test_unmutated_copy_passes_all_four_files(sandbox):
    tree = M.copy_tree(sandbox / "clean")
    for rel in TEST_FILES:
        r = M.run_pytest(tree, [rel], sandbox / "clean-home")
        assert r.returncode == 0, rel + "\n" + r.stdout[-1500:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_caught(sandbox, name):
    rel_src, old, new, nodes = MUTANTS[name]
    tree = M.copy_tree(sandbox / ("m_" + name))
    M.apply_mutation(tree, rel_src, old, new)
    M.assert_caught(M.run_pytest(tree, nodes, sandbox / ("m_" + name + "-home")), nodes, name)
