# kiro2claude

Use your [Kiro](https://kiro.dev) AI workflow in [Claude Code](https://code.claude.com),
without maintaining a second copy of it.

Teams that use both tools end up with two sets of agents, skills, steering and
hooks that slowly drift apart. kiro2claude keeps **`.kiro/` as the single source
of truth**: at the start of every Claude Code session it translates `.kiro/` into
the files Claude Code reads, and only when something changed. Kiro users keep
editing `.kiro/` as before; Claude Code users get the same workflow.

## What it translates

| Kiro (`.kiro/`) | Claude Code (generated, gitignored) |
|---|---|
| `steering/*.md` with `inclusion: always` (the default) | `@` imports in `CLAUDE.md`, loaded every session |
| `steering/*.md` with `inclusion: fileMatch` | `.claude/rules/<name>.md` with `paths:`, loaded for matching files |
| `steering/*.md` with `inclusion: auto` / `manual` | skill `steering-<name>`, loaded when relevant / only via `/steering-<name>` |
| `specs/` | listed in `CLAUDE.md` |
| `agents/*.json` | `.claude/agents/<name>.md` subagents, with Kiro tools mapped to Claude Code tools |
| `skills/<name>/SKILL.md` | `.claude/skills/<name>` (symlink; same format) |
| `hooks/*.json`, `hooks/*.kiro.hook` | run live by a dispatcher on the matching Claude Code hook event |
| `settings/mcp.json` | `.mcp.json` |

Agents load the same context as in Kiro: `file://` resources before starting,
`skill://` resources when needed, and always-on steering once (through `CLAUDE.md`).

## How it works

`kiro2claude init` writes one small `.claude/settings.json` that you commit. It
never changes, and it wires two things:

- **SessionStart → `sync`**: hashes `.kiro/` and exits in about 30 ms when nothing
  changed. Otherwise it regenerates the files above and tells the session what
  changed.
- **Every other hook event → `dispatch`**: reads `.kiro/hooks/` when the event
  fires and runs the matching Kiro hooks, so hook edits need no regeneration.
  Exit code 2 blocks the action in both tools, so existing guard scripts work
  unchanged.

Every generated file is recorded in `.claude/.kiro2claude/state.json`. The tool
only overwrites or deletes files it created: a hand-written `CLAUDE.md` is left
alone, and the instructions go to `.claude/kiro2claude.md` instead (add
`@.claude/kiro2claude.md` to your `CLAUDE.md`).

## Install

Python 3.9+ and no dependencies. Pick one:

**Installed command** (each developer installs it once):

```bash
pipx install git+https://github.com/karawanshy/kiro2claude
# or: uv tool install git+https://github.com/karawanshy/kiro2claude

cd your-project          # must contain .kiro/
kiro2claude init         # writes .claude/settings.json + .gitignore entries, then syncs
git add .claude/settings.json .gitignore && git commit -m "Use kiro2claude"
```

**Vendored copy** (nothing to install for teammates): copy `src/kiro2claude.py`
into your repo, for example as `tools/kiro2claude.py`, and run:

```bash
python3 tools/kiro2claude.py init
```

The generated hooks then run that file by path.

If the tool is missing (not installed, or on a branch without the vendored copy),
the hooks skip quietly and the session shows a one-line notice instead of failing.

## Commands

```bash
kiro2claude sync            # regenerate if .kiro/ changed
kiro2claude sync --force    # regenerate anyway
kiro2claude sync --check    # exit 1 if generated files are stale (CI / pre-commit)
kiro2claude init [--force]  # write settings.json + .gitignore entries, then sync
kiro2claude dispatch <Event> # run Kiro hooks for a Claude Code hook event (used by settings.json)
kiro2claude --version
```

## Mappings and limitations

- **Coordinator agents.** Claude Code subagents cannot start other subagents. Kiro
  agents that delegate (tool `subagent`) get a note to run in the main session,
  which then delegates with the Agent tool.
- **Tools.** `read` → Read, Grep, Glob · `write` → Edit, Write, NotebookEdit ·
  `shell` → Bash · `subagent` → Agent · `todo_list` → TodoWrite · `web_search`,
  `web_fetch` → WebSearch, WebFetch. `@mcp` and `*` grant all tools, since Claude
  Code lists MCP tools by name. Unknown tools are reported, not granted.
- **Hook triggers.** `PreToolUse`, `PostToolUse`, `PromptSubmit`, `AgentStop`,
  `AgentSpawn`, `fileEdited`/`fileCreated` (on Edit/Write with path patterns) map
  to Claude Code events. Triggers with no equivalent, such as `PostTaskExec`,
  become instructions in `CLAUDE.md`. `askAgent` hooks add their prompt to
  Claude's context.
- **Skill name clashes.** A Kiro skill named like a Claude Code built-in command
  (for example `security-review`) is exposed as `kiro-<name>`.
- **First session after a change.** Files written at session start may load only
  in the next session; the session is told so.
- **Branch switches.** Generated files are gitignored, so they stay on disk when
  you switch branches. Until every branch has the hook, a branch without it keeps
  the previous branch's generated setup.
- **Platforms.** macOS and Linux. On Windows, skills are copied instead of
  symlinked and hooks need a bash-compatible shell.

## Development

```bash
python3 -m unittest discover tests
ruff check . && ruff format --check .   # CI uses ruff 0.16.8
```

## License

[MIT](LICENSE)
