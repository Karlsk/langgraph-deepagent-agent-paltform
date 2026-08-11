---
name: demo-skill
description: Minimal demo skill that teaches the agent to answer with a fixed greeting format.
license: MIT
---

# Demo Skill

## When to Use

- The user asks for a "demo greeting" or mentions the demo skill.

## Instructions

1. Greet the user with exactly one line: `[demo-skill] Hello from a deepagents skill!`.
2. Then summarize what this skill did in one sentence.

## Notes

- This skill is instruction-only: deepagents skills never execute code by
  themselves; execution requires the `execute` tool backed by a
  `SandboxBackendProtocol` backend.
