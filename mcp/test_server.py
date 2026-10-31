#!/usr/bin/env python3
"""No-docker tests for mcp/server.py: the rewrite round trip of match_write_function (the
duplicated-declarations bug of the first run and the two ways the roundtable showed a text
memo breaking), the contract pre-check with docker mocked, and triage_unit from the JSON
report. Run: .venv/bin/python -I mcp/test_server.py"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
os.environ.setdefault("DECOMP_UPSTREAM", str(HERE.parent))          # any directory: the tests never build
os.environ["DECOMP_SANDBOXES"] = tempfile.mkdtemp(prefix="ps2mcp-test-")
spec = importlib.util.spec_from_file_location("ps2decomp_server", HERE / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

SOURCE = """#include "common.h"

INCLUDE_ASM("asm/nonmatchings/ov01/x", before1);

INCLUDE_ASM("asm/nonmatchings/ov01/x", target);

INCLUDE_ASM("asm/nonmatchings/ov01/x", after1);

int other(void)
{
    return 1;
}
"""


def make_sandbox(name="target"):
    sb = Path(tempfile.mkdtemp(prefix="sb-", dir=os.environ["DECOMP_SANDBOXES"])) / name
    (sb / "upstream" / "src" / "ov01").mkdir(parents=True)
    (sb / "upstream" / "src" / "ov01" / "x.c").write_text(SOURCE)
    (sb / "budget.json").write_text(json.dumps(dict(function=name, path="src/ov01/x.c", tu="ov01/tu000", forms=[], forms_max=3)))
    return sb


class WriteRoundTrip(unittest.TestCase):
    def setUp(self):
        self.sb = make_sandbox()
        self.src = self.sb / "upstream" / "src" / "ov01" / "x.c"

    def write(self, n, declarations="extern int probeDecl;"):
        return server.match_write_function(str(self.sb), f"/* probe comment {n} */\nint target(void)\n{{\n    return {n};\n}}",
                                           declarations)

    def assert_single(self, n):
        text = self.src.read_text()
        self.assertEqual(text.count("extern int probeDecl;"), 1, text)
        self.assertEqual(text.count("probe comment"), 1, text)
        self.assertEqual(text.count("int target(void)"), 1, text)
        self.assertIn(f"return {n};", text)
        self.assertEqual(text.count("before1"), 1)
        self.assertEqual(text.count("after1"), 1)
        self.assertEqual(text.count("INCLUDE_ASM"), 2)

    def test_three_writes_keep_one_copy(self):
        self.assertEqual(self.write(1)["how"], "replaced INCLUDE_ASM stub")
        self.assert_single(1)
        self.assertEqual(self.write(2)["how"], "replaced the previous write (declarations, comment and body)")
        self.assert_single(2)
        self.write(3, declarations="")
        text = self.src.read_text()
        self.assertEqual(text.count("extern int probeDecl;"), 0)
        self.assertIn("return 3;", text)

    def test_hand_edit_inside_block_survives_nothing_but_no_duplicates(self):
        self.write(1)
        self.src.write_text(self.src.read_text().replace("return 1;", "return  1 ;  /* edited by hand */"))
        self.write(2)
        self.assert_single(2)

    def test_match_best_restore_then_write(self):
        self.write(1)
        form1 = self.src.read_text()
        self.write(2)
        self.src.write_text(form1)                 # what match_best does: an older form back on disk
        self.write(3)
        self.assert_single(3)

    def test_edit_elsewhere_keeps_anchors(self):
        self.write(1)
        server.match_edit(str(self.sb), "return 1;\n}\n\nINCLUDE_ASM", "return 1;\n}\n\nINCLUDE_ASM")  # no-op edit path
        self.src.write_text(self.src.read_text().replace('#include "common.h"', '#include "common.h"\n#include "extra.h"'))
        self.write(2)
        self.assert_single(2)

    def test_rewrite_keeps_layout(self):
        self.write(1)
        form1 = self.src.read_text()
        self.write(2)
        self.write(1)
        self.assertEqual(self.src.read_text(), form1)

    def test_anchor_lost_falls_back_with_warning(self):
        self.write(1)
        self.src.write_text(self.src.read_text().replace("after1", "after1_renamed"))
        out = self.write(2)
        self.assertEqual(out["how"], "replaced existing definition")
        self.assertIn("warning", out)


class ContractProbe(unittest.TestCase):
    def setUp(self):
        server._PROBED.clear()
        self.tc = Path(tempfile.mkdtemp(prefix="tc-"))
        self.env = mock.patch.dict(os.environ, {"DECOMP_TOOLCHAINS": str(self.tc)})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_unshared_volume_is_named(self):
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "", "")) as run:
            why = server.contract_blocker()
        self.assertIn("not shared with docker", why)
        self.assertIn("DECOMP_TOOLCHAINS", why)
        self.assertEqual(run.call_count, 1)
        self.assertIn("--platform", run.call_args[0][0])

    def test_timeout_is_not_blamed_on_sharing(self):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 1)
        with mock.patch.object(server.subprocess, "run", side_effect=boom):
            why = server.contract_blocker()
        self.assertIn("docker cannot run", why)
        self.assertNotIn("not shared", why)

    def test_success_is_cached_per_process(self):
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertIsNone(server.contract_blocker())
            self.assertIsNone(server.contract_blocker())
        self.assertEqual(run.call_count, 1)

    def test_failure_is_not_cached(self):
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "", "")) as run:
            server.contract_blocker()
            server.contract_blocker()
        self.assertEqual(run.call_count, 2)

    def test_missing_directory(self):
        with mock.patch.dict(os.environ, {"DECOMP_TOOLCHAINS": str(self.tc / "nope")}):
            self.assertIn("no toolchains directory", server.contract_blocker())


class TriageFromJson(unittest.TestCase):
    RECORDS = [dict(name="a", va="0x1", size=8, cls="trivial", score=0, instruction_count=2, callee_count=0,
                    switch_jumps=[], cop2_count=0, quad_count=0, g_hint="?", flags=["leaf"]),
               dict(name="b", va="0x2", size=400, cls="hard", score=90, instruction_count=100, callee_count=3,
                    switch_jumps=[1], cop2_count=0, quad_count=2, g_hint="-G0", flags=[]),
               dict(name="c", va="0x3", size=40, cls="easy", score=20, instruction_count=10, callee_count=1,
                    switch_jumps=[], cop2_count=0, quad_count=0, g_hint="?", flags=[])]

    def fake_run(self, tool, args, timeout=600):
        out = Path(args[args.index("--json") + 1])
        out.write_text(json.dumps(dict(elf="X", functions=self.RECORDS, skipped=["z"])))
        return dict(rc=0, stdout="x" * 20000, stderr="", truncated=True, command="triage")

    def test_rows_histogram_and_order(self):
        with mock.patch.object(server, "run", side_effect=self.fake_run):
            out = server.triage_unit(binary="X")
        self.assertEqual(out["histogram"], {"trivial": 1, "hard": 1, "easy": 1})
        self.assertEqual([r["name"] for r in out["rows"]], ["a", "c", "b"])
        self.assertEqual(out["rows"][1]["class"], "easy")
        self.assertEqual(out["rows"][2]["switch"], 1)
        self.assertEqual(out["outside_image"], 1)
        self.assertIn("trivial=1 easy=1 hard=1", out["summary"])
        with mock.patch.object(server, "run", side_effect=self.fake_run):
            out = server.triage_unit(binary="X", classes="easy")
        self.assertEqual(out["total_rows"], 1)

    def test_function_record(self):
        with mock.patch.object(server, "run", side_effect=lambda tool, args, timeout=300: self.fake_run(tool, args)):
            with mock.patch.object(self, "RECORDS", self.RECORDS[:1]):
                out = server.triage_function("a", binary="X")
        self.assertEqual(out["result"]["name"], "a")
        self.assertFalse(list(HERE.parent.glob("-")), "triage must not write a file named '-' into the kit root")


if __name__ == "__main__":
    unittest.main(verbosity=1)
