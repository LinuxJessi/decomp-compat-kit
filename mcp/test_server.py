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


class LintTu(unittest.TestCase):
    def test_late_proto_unused_goto_mask(self):
        d = Path(tempfile.mkdtemp(prefix="lint-")); src = d / "x.c"
        src.write_text("#define UNUSED_ONE 1\n#define USED_ONE 2\nint f(void)\n{\n    int v = USED_ONE & 0x40;\n    goto out;\nout:\n    return v;\n}\nint f(void);\n")
        base = d / "base.c"; base.write_text("int f(void)\n{\n    return 0;\n}\n")
        with mock.patch.object(server, "UPSTREAM", d):
            out = server.lint_tu("x.c", base=str(base))
        self.assertEqual(out["late_protos"], ["f"])
        self.assertEqual(out["unused"], ["UNUSED_ONE"])
        self.assertEqual(out["gotos"], ["goto out;"])
        self.assertEqual(len(out["masks"]), 1)
        with mock.patch.object(server, "UPSTREAM", d):
            self.assertIn("error", server.lint_tu("nope.c"))



class CompilerFamily(unittest.TestCase):
    """The CodeWarrior gate (docs/CODEWARRIOR.md): the family comes from fingerprint_elf.py's JSON,
    the prologue style from match_kit disasm, and match_init refuses sq-prologue functions in a
    Metrowerks/ProDG original without opening a sandbox."""

    MW = json.dumps(dict(verdict=dict(compiler="Metrowerks CodeWarrior for PS2 (MWCC)", confidence="high"),
                         startup=dict(kind="metrowerks-psstart")))
    GCC = json.dumps(dict(verdict=dict(compiler="SCE ee-gcc 2.96 (Cygnus/SCE R5900 GCC, 2.9x family)", confidence="high")))
    SQ_DIS = "# f @ 0x100000\naddiu       $sp, $sp, -0x40\nsq          $s0, 0x0($sp)\nsq          $s1, 0x10($sp)\nsq          $ra, 0x20($sp)\n"
    SD_DIS = "# g @ 0x100000\naddiu       $sp, $sp, -0x20\nsd          $s0, 0x0($sp)\nsd          $ra, 0x8($sp)\nlq          $v0, 0x10($sp)\n"

    def setUp(self):
        server._FAMILY_CACHE.clear()
        self.bin = Path(tempfile.mkdtemp(prefix="fam-")) / "SLUS_tst.00"
        self.bin.write_bytes(b"\x7fELF" + b"\0" * 60)

    def fake_run(self, outputs):
        def run(tool, args, timeout=600):
            key = tool if tool != "match_kit.py" else "match_kit.py " + args[0]
            out = outputs.get(key, "")
            return dict(rc=0 if out else 1, stdout=out, stderr="" if out else "no output", truncated=False, command=tool)
        return run

    def test_family_is_cached_per_file(self):
        with mock.patch.object(server, "run", side_effect=self.fake_run({"fingerprint_elf.py": self.MW})) as run:
            a = server.binary_family(self.bin)
            b = server.binary_family(self.bin)
        self.assertEqual(a["family"], "metrowerks")
        self.assertEqual(a["startup"], "metrowerks-psstart")
        self.assertIs(a, b)
        self.assertEqual(run.call_count, 1)
        self.assertIn("ee-gcc only", server.family_warning(a))

    def test_gcc_original_has_no_warning(self):
        with mock.patch.object(server, "run", side_effect=self.fake_run({"fingerprint_elf.py": self.GCC})):
            fam = server.binary_family(self.bin)
        self.assertEqual(fam["family"], "ee-gcc")
        self.assertIsNone(server.family_warning(fam))

    def test_ambiguous_census_warns(self):
        amb = json.dumps(dict(verdict=dict(compiler="sq/16-byte saves: SN ProDG or Metrowerks CodeWarrior (census cannot separate them)",
                                           confidence="medium")))
        with mock.patch.object(server, "run", side_effect=self.fake_run({"fingerprint_elf.py": amb})):
            self.assertEqual(server.binary_family(self.bin)["family"], "ambiguous")

    def test_prologue_style(self):
        with mock.patch.object(server, "run", side_effect=self.fake_run({"match_kit.py disasm": self.SQ_DIS})):
            self.assertEqual(server.prologue_style("f")["style"], "sq")
        with mock.patch.object(server, "run", side_effect=self.fake_run({"match_kit.py disasm": self.SD_DIS})):
            pro = server.prologue_style("g")
        self.assertEqual(pro["style"], "sd")
        self.assertEqual(pro["counts"], {"sd": 2})          # the lq is a load, not a save
        with mock.patch.object(server, "run", side_effect=self.fake_run({})):
            self.assertIsNone(server.prologue_style("h")["style"])

    def test_match_init_refuses_sq_in_codewarrior_binary(self):
        outputs = {"fingerprint_elf.py": self.MW, "match_kit.py disasm": self.SQ_DIS,
                   "match_kit.py init": json.dumps(dict(sandbox="/nowhere"))}
        with mock.patch.object(server, "GAME", self.bin.parent), mock.patch.object(server, "BINARY", self.bin.name), \
             mock.patch.object(server, "run", side_effect=self.fake_run(outputs)) as run, \
             mock.patch.object(server, "match_kit_module", return_value=mock.Mock(default_compiler=lambda: "stock")):
            out = server.match_init("f")
        self.assertTrue(out.get("refused"))
        self.assertEqual(out["rc"], 3)
        self.assertIn("sq", out["stopped"])
        self.assertNotIn("match_kit.py init", [c.args[0] + " " + c.args[1][0] for c in run.call_args_list])

    def test_match_init_warns_but_opens_sd_function(self):
        outputs = {"fingerprint_elf.py": self.MW, "match_kit.py disasm": self.SD_DIS,
                   "match_kit.py init": json.dumps(dict(sandbox="/nowhere"))}
        with mock.patch.object(server, "GAME", self.bin.parent), mock.patch.object(server, "BINARY", self.bin.name), \
             mock.patch.object(server, "run", side_effect=self.fake_run(outputs)), \
             mock.patch.object(server, "match_kit_module", return_value=mock.Mock(default_compiler=lambda: "stock")):
            out = server.match_init("g")
        self.assertFalse(out.get("refused"))
        self.assertEqual(out["sandbox"], "/nowhere")
        self.assertIn("ee-gcc only", out["warning"])
        self.assertEqual(out["compiler_family"]["family"], "metrowerks")

    def test_gcc_original_opens_without_disasm(self):
        outputs = {"fingerprint_elf.py": self.GCC, "match_kit.py init": json.dumps(dict(sandbox="/nowhere"))}
        with mock.patch.object(server, "GAME", self.bin.parent), mock.patch.object(server, "BINARY", self.bin.name), \
             mock.patch.object(server, "run", side_effect=self.fake_run(outputs)) as run, \
             mock.patch.object(server, "match_kit_module", return_value=mock.Mock(default_compiler=lambda: "stock")):
            out = server.match_init("g")
        self.assertNotIn("warning", out)
        self.assertEqual([c.args[0] for c in run.call_args_list], ["fingerprint_elf.py", "match_kit.py"])

    def test_codewarrior_doc_is_served(self):
        text = server.doc_resource("codewarrior")
        self.assertIn("MWo", text)
        self.assertIn("overlayID", text)
        self.assertIn("991111-01", server.doc_resource("sdk-archive"))

class SandboxImage(unittest.TestCase):
    """DECOMP_IMAGE reaches the verifier from the sandbox tools too (issue #2): match_init and
    match_check build through match_kit.judge, not through the server's own verify argv."""

    def argv_with(self, image):
        spec = importlib.util.spec_from_file_location("match_kit_image", HERE.parent / "tools" / "match_kit.py")
        mk = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ):
            os.environ.pop("DECOMP_IMAGE", None)
            if image:
                os.environ["DECOMP_IMAGE"] = image
            spec.loader.exec_module(mk)
        tooling_failure = subprocess.CompletedProcess([], 2, "", "stopped by the test")
        with mock.patch.object(mk, "binary", return_value=Path("/nowhere/original")), \
             mock.patch.object(mk.subprocess, "run", return_value=tooling_failure) as run:
            result = mk.judge(Path(tempfile.mkdtemp(prefix="sb-")), dict(tu="main/tu000"), compiler="stock")
        self.assertEqual(result["verdict"], "UNVERIFIABLE")
        return run.call_args.args[0]

    def test_image_is_passed(self):
        argv = self.argv_with("debian:trixie-slim")
        self.assertEqual(argv[argv.index("--image") + 1], "debian:trixie-slim")

    def test_unset_keeps_the_verifier_default(self):
        self.assertNotIn("--image", self.argv_with(None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
