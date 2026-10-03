"""Tests for kiro2claude. Run: python3 -m unittest discover tests"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import kiro2claude as k2c  # noqa: E402


def write(root: Path, relpath: str, content) -> None:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if isinstance(content, str) else json.dumps(content))


def make_fixture(root: Path) -> None:
    write(
        root,
        ".kiro/agents/backend.json",
        {
            "name": "backend",
            "description": "Builds backend features. Owns the API.",
            "prompt": "You are the backend agent.",
            "tools": ["read", "write", "shell", "@mcp"],
            "resources": [
                "file://.kiro/steering/**/*.md",
                "skill://.kiro/skills/security-review/SKILL.md",
            ],
        },
    )
    write(
        root,
        ".kiro/agents/reviewer.json",
        {
            "name": "reviewer",
            "description": "Reviews code.",
            "prompt": "Review.",
            "tools": ["read"],
        },
    )
    write(
        root,
        ".kiro/agents/orchestrator.json",
        {
            "name": "orchestrator",
            "description": "Coordinates agents.",
            "prompt": "Coordinate.",
            "tools": ["read", "subagent", "todo_list"],
            "model": "claude-sonnet-4",
        },
    )
    write(
        root,
        ".kiro/skills/db-migration/SKILL.md",
        "---\nname: db-migration\ndescription: Migrations.\n---\nBody\n",
    )
    write(
        root,
        ".kiro/skills/security-review/SKILL.md",
        "---\nname: security-review\ndescription: Sec.\n---\nBody\n",
    )
    write(root, ".kiro/skills/security-review/LICENSE.txt", "license")
    write(root, ".kiro/steering/product.md", "# Product\nRules.\n")
    write(
        root,
        ".kiro/steering/frontend/ui.md",
        '---\ninclusion: fileMatch\nfileMatchPattern: "frontend/**"\n---\n# UI\nUse tokens.\n',
    )
    write(root, ".kiro/steering/release.md", "---\ninclusion: manual\n---\n# Release checklist\n")
    write(root, ".kiro/steering/ai.md", "---\ninclusion: auto\ndescription: AI rules\n---\n# AI\n")
    write(root, ".kiro/specs/app/tasks.md", "- [ ] task\n")
    write(
        root,
        ".kiro/hooks/guard.json",
        {
            "version": "v1",
            "hooks": [
                {
                    "name": "guard",
                    "trigger": "PreToolUse",
                    "matcher": "execute_bash|shell",
                    "action": {"type": "command", "command": "bash guard.sh", "timeout": 15},
                },
                {
                    "name": "gate",
                    "trigger": "PostTaskExec",
                    "description": "Readiness gate.",
                    "action": {"type": "command", "command": "bash gate.sh"},
                },
            ],
        },
    )
    write(
        root,
        ".kiro/hooks/lint.kiro.hook",
        {
            "enabled": True,
            "name": "lint",
            "when": {"type": "fileEdited", "patterns": ["src/**/*.py"]},
            "then": {"type": "askAgent", "prompt": "Run the linter."},
        },
    )
    # Blocks any command containing "danger"; echoes context otherwise.
    write(root, "guard.sh", 'grep -q danger && { echo "blocked: danger" >&2; exit 2; }; exit 0\n')


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        make_fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        return k2c.sync(self.root, **kw)

    def read(self, relpath):
        return (self.root / relpath).read_text()

    def test_generates_everything(self):
        status, changes, warnings = self.sync()
        self.assertEqual(status, "synced")

        backend = self.read(".claude/agents/backend.md")
        self.assertIn("name: backend", backend)
        self.assertNotIn("tools:", backend)  # @mcp => inherit all tools
        # Always-on steering comes via CLAUDE.md; other file resources are preloaded like Kiro.
        self.assertNotIn("`.kiro/steering/product.md`", backend)
        self.assertIn("already in your context", backend)
        refs = backend.split("Read these before starting:")[1]
        self.assertIn("`.kiro/steering/frontend/ui.md`", refs)  # ** glob expanded
        self.assertIn("Claude Code skill `kiro-security-review`", backend)

        self.assertIn("tools: Read, Grep, Glob\n", self.read(".claude/agents/reviewer.md"))
        orch = self.read(".claude/agents/orchestrator.md")
        self.assertIn("tools: Read, Grep, Glob, Agent, TodoWrite", orch)
        self.assertIn("model: sonnet", orch)
        self.assertIn("subagents cannot start other subagents", orch)

        self.assertTrue((self.root / ".claude/skills/db-migration").is_symlink())
        renamed = self.read(".claude/skills/kiro-security-review/SKILL.md")
        self.assertIn("name: kiro-security-review", renamed)
        self.assertTrue(
            (self.root / ".claude/skills/kiro-security-review/LICENSE.txt").is_symlink()
        )

        rule = self.read(".claude/rules/frontend-ui.md")
        self.assertIn('paths:\n  - "frontend/**"', rule)
        self.assertIn("Use tokens.", rule)
        self.assertIn(
            "disable-model-invocation: true", self.read(".claude/skills/steering-release/SKILL.md")
        )
        auto = self.read(".claude/skills/steering-ai/SKILL.md")
        self.assertIn('description: "AI rules"', auto)
        self.assertNotIn("disable-model-invocation", auto)

        md = self.read("CLAUDE.md")
        self.assertIn("@.kiro/steering/product.md", md)
        self.assertNotIn("@.kiro/steering/frontend/ui.md", md)
        self.assertIn("`.kiro/specs/app/`: `tasks.md`", md)
        self.assertIn("Coordinator agents (`orchestrator`)", md)
        self.assertIn("`guard`: PreToolUse", md)
        self.assertIn("`lint`: PostToolUse", md)
        self.assertIn(
            "`gate` (Kiro trigger `PostTaskExec`): run `bash gate.sh`. Readiness gate.", md
        )
        self.assertTrue(any("kiro-security-review" in w for w in warnings))

    def test_second_run_is_a_noop_until_kiro_changes(self):
        self.sync()
        mtime = (self.root / "CLAUDE.md").stat().st_mtime_ns
        self.assertEqual(self.sync()[0], "current")
        self.assertEqual(self.sync(check=True)[0], "current")

        write(self.root, ".kiro/steering/new.md", "# New\n")
        self.assertEqual(self.sync(check=True)[0], "stale")
        status, changes, _ = self.sync()
        self.assertEqual(status, "synced")
        # Only CLAUDE.md changes: agents don't list always-on steering.
        self.assertEqual(changes["updated"], ["CLAUDE.md"])
        self.assertIn("@.kiro/steering/new.md", self.read("CLAUDE.md"))
        self.assertGreaterEqual((self.root / "CLAUDE.md").stat().st_mtime_ns, mtime)

    def test_removes_stale_outputs_and_prunes_dirs(self):
        self.sync()
        (self.root / ".kiro/agents/reviewer.json").unlink()
        (self.root / ".kiro/steering/frontend/ui.md").unlink()
        _, changes, _ = self.sync()
        self.assertIn(".claude/agents/reviewer.md", changes["removed"])
        self.assertFalse((self.root / ".claude/agents/reviewer.md").exists())
        self.assertFalse((self.root / ".claude/rules").exists())

    def test_regenerates_deleted_output(self):
        self.sync()
        (self.root / ".claude/agents/backend.md").unlink()
        self.assertEqual(self.sync()[0], "synced")
        self.assertTrue((self.root / ".claude/agents/backend.md").exists())

    def test_never_overwrites_unowned_files(self):
        write(self.root, "CLAUDE.md", "# Hand-written\n")
        write(self.root, ".claude/agents/reviewer.md", "mine\n")
        _, _, warnings = self.sync()
        self.assertEqual(self.read("CLAUDE.md"), "# Hand-written\n")
        self.assertEqual(self.read(".claude/agents/reviewer.md"), "mine\n")
        self.assertIn("@../.kiro/steering/product.md", self.read(".claude/kiro2claude.md"))
        self.assertTrue(any("reviewer.md: exists and was not created" in w for w in warnings))
        self.assertEqual(self.sync()[0], "current")

    def test_mcp_translation(self):
        write(
            self.root,
            ".kiro/settings/mcp.json",
            {
                "mcpServers": {
                    "local": {
                        "command": "npx",
                        "args": ["x"],
                        "env": {"T": "${env:TOKEN}"},
                        "autoApprove": ["a"],
                    },
                    "remote": {"url": "https://example.com/mcp"},
                    "off": {"command": "y", "disabled": True},
                }
            },
        )
        self.sync()
        mcp = json.loads(self.read(".mcp.json"))["mcpServers"]
        self.assertEqual(mcp["local"], {"command": "npx", "args": ["x"], "env": {"T": "${TOKEN}"}})
        self.assertEqual(mcp["remote"], {"url": "https://example.com/mcp", "type": "http"})
        self.assertNotIn("off", mcp)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        make_fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def dispatch(self, event, payload):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}),
            mock.patch("sys.stdin", io.StringIO(json.dumps(payload))),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = k2c.cmd_dispatch([event])
        return code, out.getvalue(), err.getvalue()

    def test_pre_tool_use_blocks_via_kiro_hook(self):
        code, _, err = self.dispatch(
            "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm danger"}}
        )
        self.assertEqual(code, 2)
        self.assertIn("blocked: danger", err)

    def test_pre_tool_use_allows(self):
        code, out, _ = self.dispatch(
            "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        )
        self.assertEqual((code, out), (0, ""))

    def test_matcher_skips_other_tools(self):
        code, _, _ = self.dispatch(
            "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "danger"}}
        )
        self.assertEqual(code, 0)

    def test_file_edited_ask_agent_adds_context(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.root / "src/app/x.py")},
        }
        code, out, _ = self.dispatch("PostToolUse", payload)
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["hookSpecificOutput"]["additionalContext"], "Run the linter."
        )
        payload["tool_input"]["file_path"] = str(self.root / "docs/x.md")
        self.assertEqual(self.dispatch("PostToolUse", payload)[1], "")

    def test_unmapped_trigger_never_runs(self):
        code, out, err = self.dispatch("Stop", {})
        self.assertEqual((code, out, err), (0, "", ""))


class SettingsTests(unittest.TestCase):
    def run_hook(self, root, script, event, index=0, path=None):
        cmd = k2c.settings_json(root, script)["hooks"][event][0]["hooks"][index]["command"]
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
        if path is not None:
            env["PATH"] = path
        return subprocess.run(
            ["bash", "-c", cmd], env=env, input="{}", text=True, capture_output=True
        )

    def test_vendored_hooks_skip_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            script = root / "tools/kiro2claude.py"  # vendored, but not present
            cmd = k2c.settings_json(root, script)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn('"$CLAUDE_PROJECT_DIR/tools/kiro2claude.py"', cmd)
            self.assertEqual(self.run_hook(root, script, "PreToolUse").returncode, 0)
            start = self.run_hook(root, script, "SessionStart")
            self.assertEqual(start.returncode, 0)
            self.assertIn("is not installed", json.loads(start.stdout)["systemMessage"])

    def test_installed_hooks_skip_when_command_is_missing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(d).resolve()
            script = Path(elsewhere) / "kiro2claude.py"  # outside the project: installed mode
            cmd = k2c.settings_json(root, script)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertTrue(cmd.endswith("kiro2claude dispatch PreToolUse"))
            no_tool = "/usr/bin:/bin"
            self.assertEqual(self.run_hook(root, script, "PreToolUse", path=no_tool).returncode, 0)
            start = self.run_hook(root, script, "SessionStart", path=no_tool)
            self.assertIn("is not installed", json.loads(start.stdout)["systemMessage"])

    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(k2c.main(["--version"]), 0)
        self.assertEqual(out.getvalue().strip(), f"kiro2claude {k2c.__version__}")


class ExampleProjectTests(unittest.TestCase):
    """examples/demo-project runs as its READMEs show, so the docs can't drift."""

    REPO = Path(__file__).resolve().parents[1]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "demo-project"
        shutil.copytree(self.REPO / "examples/demo-project", self.root)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.env = mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def run_cmd(self, fn, args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = fn(args)
        return code, out.getvalue(), err.getvalue()

    def test_init_and_sync_output_match_the_readmes(self):
        code, init_out, _ = self.run_cmd(k2c.cmd_init, [])
        self.assertEqual(code, 0)
        code, sync_out, _ = self.run_cmd(k2c.cmd_sync, [])
        self.assertEqual((code, sync_out), (0, "kiro2claude: up to date\n"))
        for readme in ("README.md", "examples/demo-project/README.md"):
            text = (self.REPO / readme).read_text()
            for line in (init_out + sync_out).splitlines():
                self.assertTrue(line in text, f"{readme} is missing demo output line: {line}")
        for path in (
            "CLAUDE.md",
            ".mcp.json",
            ".claude/settings.json",
            ".claude/agents/implementer.md",
            ".claude/agents/reviewer.md",
            ".claude/rules/api-style.md",
            ".claude/skills/steering-release/SKILL.md",
        ):
            self.assertTrue((self.root / path).is_file(), path)
        self.assertTrue((self.root / ".claude/skills/write-tests").is_symlink())

    def test_protect_main_hook_blocks_commits_on_main_only(self):
        commit = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}})
        code, _, err = self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], commit)
        self.assertEqual(code, 2)
        self.assertIn("blocked: commit/push on main", err)
        ls = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], ls)[0], 0)
        self.git("checkout", "-q", "-b", "feature")
        self.assertEqual(self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], commit)[0], 0)


class FrontmatterTests(unittest.TestCase):
    def test_parses_lists_and_quotes(self):
        meta, body = k2c.split_frontmatter(
            "---\n\nname: x\ninclusion: fileMatch\n"
            "fileMatchPattern: ['a/**', \"b\"]\nlist:\n  - one\n---\nBody"
        )
        self.assertEqual(
            meta,
            {
                "name": "x",
                "inclusion": "fileMatch",
                "fileMatchPattern": ["a/**", "b"],
                "list": ["one"],
            },
        )
        self.assertEqual(body, "Body")

    def test_no_frontmatter(self):
        self.assertEqual(k2c.split_frontmatter("# Title\n"), ({}, "# Title\n"))


if __name__ == "__main__":
    unittest.main()
