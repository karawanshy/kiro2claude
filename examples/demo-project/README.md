# Demo project

A tiny to-do API project whose AI workflow lives in `.kiro/`. Only `.kiro/` is
written by hand; kiro2claude generates everything Claude Code reads.

```text
.kiro/
├── agents/
│   ├── implementer.json         # write + shell, loads the spec and a skill
│   └── reviewer.json            # read-only
├── skills/
│   └── write-tests/SKILL.md
├── steering/
│   ├── product.md               # always on
│   ├── api-style.md             # only for src/api/**
│   └── release.md               # manual: /steering-release
├── hooks/
│   ├── protect-main.json        # PreToolUse: blocks commit/push on main
│   └── test-reminder.kiro.hook  # after editing src/**/*.py
├── specs/todo-api/              # requirements.md, tasks.md
└── settings/mcp.json            # one MCP server
```

The MCP server is a real one, [`mcp-server-fetch`](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)
(started with `uvx`). Claude Code asks before it starts a project's MCP servers,
so you can decline it; the rest of the demo doesn't need it.

## Try it

Copy the folder out of this repo first, so the generated files (and the hook's
`git` checks) belong to the demo rather than to the kiro2claude checkout.

```bash
cp -R examples/demo-project /tmp/demo-project && cd /tmp/demo-project
git init -q
python3 /path/to/kiro2claude/src/kiro2claude.py init   # or `kiro2claude init` if installed
```

```text
$ kiro2claude init
kiro2claude: wrote .claude/settings.json and .gitignore entries
kiro2claude: synced
added: .claude/agents/implementer.md, .claude/agents/reviewer.md, .claude/rules/api-style.md, .claude/skills/steering-release/SKILL.md, .claude/skills/write-tests, .mcp.json, CLAUDE.md

$ kiro2claude sync
kiro2claude: up to date
```

What each `.kiro/` file became:

| `.kiro/` | Generated |
|---|---|
| `steering/product.md` (always) | `@.kiro/steering/product.md` import in `CLAUDE.md` |
| `steering/api-style.md` (fileMatch) | `.claude/rules/api-style.md` with `paths: ["src/api/**"]` |
| `steering/release.md` (manual) | `.claude/skills/steering-release/` (run `/steering-release`) |
| `agents/*.json` | `.claude/agents/implementer.md`, `.claude/agents/reviewer.md` |
| `skills/write-tests/` | `.claude/skills/write-tests` (symlink) |
| `specs/todo-api/` | listed in `CLAUDE.md` |
| `hooks/*` | run live by `kiro2claude dispatch` from `.claude/settings.json` |
| `settings/mcp.json` | `.mcp.json` |

Then start Claude Code in the folder. The hook works the same as in Kiro: on
`main`, a `git commit` is blocked with
`blocked: commit/push on main - use a feature branch`.
