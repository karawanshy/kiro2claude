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

# The bash that hook commands run in: Git Bash on Windows, not WSL's launcher.
BASH = k2c._windows_bash() if k2c.WINDOWS else shutil.which("bash")
POSIX_ONLY = unittest.skipIf(k2c.WINDOWS, "fakes PATH with POSIX shell scripts")


def write(root: Path, relpath: str, content) -> None:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    p.write_text(text, encoding="utf-8")


def linked(p: Path) -> bool:
    """A skill symlink, or on Windows without symlink rights, its copy."""
    return p.is_symlink() or (k2c.WINDOWS and p.exists())


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
        return (self.root / relpath).read_text(encoding="utf-8")

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

        self.assertTrue(linked(self.root / ".claude/skills/db-migration"))
        renamed = self.read(".claude/skills/kiro-security-review/SKILL.md")
        self.assertIn("name: kiro-security-review", renamed)
        self.assertTrue(linked(self.root / ".claude/skills/kiro-security-review/LICENSE.txt"))

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

    def prompt(self, env=None):
        with mock.patch.dict(os.environ, env or {}):
            return self.dispatch("UserPromptSubmit", {"prompt": "hi"})

    def test_prompts_are_blocked_while_sync_fails_then_ask_for_a_restart(self):
        k2c.sync(self.root)
        self.assertEqual(self.prompt(), (0, "", ""))  # healthy: nothing to say

        boom = mock.patch.object(k2c, "build_plan", side_effect=ValueError("bad agent"))
        with boom, mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(k2c.cmd_sync(["--session-start", "--force"]), 0)
            message = json.loads(out.getvalue())["systemMessage"]
            self.assertIn("could not load this project's .kiro/ workflow: bad agent", message)
            self.assertIn("blocked until this is fixed", message)
            code, out, _ = self.prompt()
        self.assertEqual(code, 0)
        blocked = json.loads(out)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("bad agent", blocked["reason"])

        with boom:
            self.assertEqual(self.prompt({"KIRO2CLAUDE_ALLOW_BROKEN": "1"})[1], "")

        fixed = json.loads(self.prompt()[1])  # the next prompt resyncs successfully
        self.assertEqual(fixed["decision"], "block")
        self.assertIn("Restart Claude Code", fixed["reason"])
        self.assertFalse((self.root / k2c.ERROR_FILE).exists())
        self.assertEqual(self.prompt(), (0, "", ""))

    def test_never_synced_project_syncs_on_first_prompt(self):
        code, out, _ = self.prompt()
        self.assertIn("Restart Claude Code", json.loads(out)["reason"])
        self.assertTrue((self.root / "CLAUDE.md").is_file())

    def test_unmapped_trigger_never_runs(self):
        code, out, err = self.dispatch("Stop", {})
        self.assertEqual((code, out, err), (0, "", ""))

    def test_guard_that_cannot_start_blocks_the_tool_call(self):
        ls = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        with (
            mock.patch.object(k2c, "WINDOWS", True),
            mock.patch.object(k2c, "_windows_bash", return_value=None),
        ):
            code, _, err = self.dispatch("PreToolUse", ls)
            self.assertEqual(code, 2)
            self.assertIn("guard: this Kiro hook could not run: Git Bash not found", err)
            with mock.patch.dict(os.environ, {"KIRO2CLAUDE_ALLOW_BROKEN": "1"}):
                code, _, err = self.dispatch("PreToolUse", ls)
            self.assertEqual(code, 1)  # reported, not blocking
            self.assertIn("guard: Git Bash not found", err)

    def test_non_ascii_payload_reaches_hook_as_utf8(self):
        write(self.root, "guard.sh", "cat > seen.txt; exit 0\n")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo \u2192 \u0645\u0631\u062d\u0628\u0627"},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252")  # a Windows default
        with (
            mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}),
            mock.patch("sys.stdin", stdin),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(k2c.cmd_dispatch(["PreToolUse"]), 0)
        seen = json.loads((self.root / "seen.txt").read_text(encoding="utf-8"))
        self.assertEqual(seen, payload)


class WindowsBashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, relpath):
        p = self.base / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        return str(p)

    def find(self, which):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_GIT_BASH_PATH": ""}):
            with mock.patch.object(k2c.shutil, "which", side_effect=which.get):
                return k2c._windows_bash()

    def test_skips_wsl_launcher_and_finds_git_bash_next_to_git(self):
        wsl = self.touch("Windows/System32/bash.exe")
        git = self.touch("Git/cmd/git.exe")
        bash = self.touch("Git/bin/bash.exe")
        self.assertEqual(self.find({"bash": wsl, "git": git}), bash)

    def test_git_under_mingw64(self):
        git = self.touch("Git/mingw64/bin/git.exe")
        bash = self.touch("Git/bin/bash.exe")
        self.assertEqual(self.find({"git": git}), bash)

    def test_claude_code_setting_wins(self):
        bash = self.touch("custom/bash.exe")
        with (
            mock.patch.dict(os.environ, {"CLAUDE_CODE_GIT_BASH_PATH": bash}),
            mock.patch.object(k2c.shutil, "which", return_value=None),
        ):
            self.assertEqual(k2c._windows_bash(), bash)

    def test_none_found(self):
        self.assertIsNone(self.find({}))

    def test_posix_keeps_the_default_shell(self):
        with mock.patch.object(k2c, "WINDOWS", False):
            self.assertEqual(k2c.hook_argv("bash x.sh"), ("bash x.sh", True))

    def test_windows_runs_hooks_in_git_bash(self):
        with (
            mock.patch.object(k2c, "WINDOWS", True),
            mock.patch.object(k2c, "_windows_bash", return_value="C:/Git/bin/bash.exe"),
        ):
            argv = k2c.hook_argv("bash x.sh")
        self.assertEqual(argv, (["C:/Git/bin/bash.exe", "-c", "bash x.sh"], False))


class SettingsTests(unittest.TestCase):
    def run_hook(self, root, script, event, index=0, path=None, env=None):
        cmd = k2c.settings_json(root, script)["hooks"][event][0]["hooks"][index]["command"]
        full = {k: v for k, v in os.environ.items() if k != "KIRO2CLAUDE_ALLOW_BROKEN"}
        full.update(CLAUDE_PROJECT_DIR=str(root), **(env or {}))
        if path is not None:
            full["PATH"] = path
        return subprocess.run(
            [BASH, "-c", cmd], env=full, input="{}", text=True, capture_output=True
        )

    def assert_reports(self, root, script, problem, path=None):
        """SessionStart explains `problem`; prompts are blocked unless allowed."""
        start = self.run_hook(root, script, "SessionStart", path=path)
        self.assertEqual(start.returncode, 0)
        message = json.loads(start.stdout)["systemMessage"]
        self.assertIn(problem, message)
        self.assertIn("blocked until this is fixed", message)
        self.assertEqual(self.run_hook(root, script, "SessionStart", 1, path=path).stdout, "")
        prompt = self.run_hook(root, script, "UserPromptSubmit", path=path)
        self.assertEqual(prompt.returncode, 0)
        blocked = json.loads(prompt.stdout)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn(problem, blocked["reason"])
        allow = {"KIRO2CLAUDE_ALLOW_BROKEN": "1"}
        allowed = self.run_hook(root, script, "UserPromptSubmit", path=path, env=allow)
        self.assertEqual((allowed.returncode, allowed.stdout), (0, ""))
        start = self.run_hook(root, script, "SessionStart", path=path, env=allow)
        self.assertIn("Continuing without it", json.loads(start.stdout)["systemMessage"])
        tool = self.run_hook(root, script, "PreToolUse", path=path)
        self.assertEqual((tool.returncode, tool.stdout), (0, ""))

    def test_vendored_hooks_report_a_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            script = root / "tools/kiro2claude.py"  # vendored, but not present
            cmd = k2c.settings_json(root, script)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn('"$CLAUDE_PROJECT_DIR/tools/kiro2claude.py"', cmd)
            self.assert_reports(root, script, "tools/kiro2claude.py is missing")

    @POSIX_ONLY
    def test_installed_hooks_report_a_missing_command(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(d).resolve()
            script = Path(elsewhere) / "kiro2claude.py"  # outside the project: installed mode
            cmd = k2c.settings_json(root, script)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertTrue(cmd.endswith("kiro2claude dispatch PreToolUse"))
            self.assert_reports(root, script, "kiro2claude is not installed", path="/usr/bin:/bin")

    def fake_bin(self, d, good=()):
        """A PATH dir where python3/python/py fail like the Windows Store stub,
        except the names in `good`, which run the real interpreter."""
        bindir = Path(d) / "bin"
        bindir.mkdir()
        for name in ("python3", "python", "py"):
            p = bindir / name
            if name in good:
                p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
            else:
                p.write_text("#!/bin/sh\necho 'Python was not found' >&2; exit 9009\n")
            p.chmod(0o755)
        return str(bindir)

    def vendored_project(self, d):
        root = Path(d).resolve() / "proj"
        make_fixture(root)
        script = root / "tools/kiro2claude.py"
        script.parent.mkdir(parents=True)
        shutil.copy(k2c.__file__, script)
        return root, script

    @POSIX_ONLY
    def test_vendored_hooks_fall_back_past_a_broken_python3(self):
        with tempfile.TemporaryDirectory() as d:
            root, script = self.vendored_project(d)
            path = self.fake_bin(d, good=("python",)) + ":/usr/bin:/bin"
            cmd = k2c.settings_json(root, script)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PATH=path)
            danger = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm danger"}})
            proc = subprocess.run(
                [BASH, "-c", cmd],
                env=env,
                input=danger,
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)  # the Kiro hook ran
            self.assertIn("blocked: danger", proc.stderr)

    @POSIX_ONLY
    def test_vendored_hooks_report_when_no_python_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root, script = self.vendored_project(d)
            self.assert_reports(root, script, "needs Python 3.9+", path=self.fake_bin(d))

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
            text = (self.REPO / readme).read_text(encoding="utf-8")
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
        self.assertTrue(linked(self.root / ".claude/skills/write-tests"))

    def test_protect_main_hook_blocks_commits_on_main_only(self):
        commit = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}})
        code, _, err = self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], commit)
        self.assertEqual(code, 2)
        self.assertIn("blocked: commit/push on main", err)
        ls = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], ls)[0], 0)
        self.git("checkout", "-q", "-b", "feature")
        self.assertEqual(self.run_cmd(k2c.cmd_dispatch, ["PreToolUse"], commit)[0], 0)


class EncodingTests(unittest.TestCase):
    """Windows Python (before 3.15) defaults to the locale code page, not UTF-8."""

    def test_sync_keeps_non_ascii_text_without_a_utf8_locale(self):
        env = dict(os.environ, PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", LANG="C")
        env.pop("PYTHONIOENCODING", None)
        probe = "import locale; print(locale.getpreferredencoding(False))"
        enc = subprocess.run(
            [sys.executable, "-c", probe], env=env, text=True, capture_output=True
        ).stdout.strip()
        if enc.lower().replace("-", "") in ("utf8", ""):
            self.skipTest(f"cannot get a non-UTF-8 default encoding here ({enc!r})")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            make_fixture(root)
            text = "A \u2192 B \u2014 \u0645\u0631\u062d\u0628\u0627"
            ui = root / ".kiro/steering/frontend/ui.md"
            ui.write_text(
                f'---\ninclusion: fileMatch\nfileMatchPattern: "frontend/**"\n---\n{text}\n',
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, k2c.__file__, "sync"],
                cwd=root,
                env=dict(env, CLAUDE_PROJECT_DIR=str(root)),
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            rule = (root / ".claude/rules/frontend-ui.md").read_bytes()
            self.assertIn(text, rule.decode("utf-8"))
            self.assertNotIn(b"\r\n", rule)


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
