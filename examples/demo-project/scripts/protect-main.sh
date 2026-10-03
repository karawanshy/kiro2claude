#!/usr/bin/env bash
# Kiro and Claude Code both pass the hook payload on stdin; exit 2 blocks the command.
payload=$(cat)
branch=$(git branch --show-current 2>/dev/null)
if [ "$branch" = "main" ] && grep -Eq 'git (commit|push)' <<<"$payload"; then
  echo "blocked: commit/push on main - use a feature branch" >&2
  exit 2
fi
exit 0
