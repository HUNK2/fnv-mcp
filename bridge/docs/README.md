# fnv-mcp documentation

fnv-mcp is an MCP server that lets an agent build and test Fallout: New Vegas mods in the running game. Pick the
part that fits what you are doing.

## Setting it up

1. [FNV-MCP-SETUP.md](FNV-MCP-SETUP.md): from an empty Windows machine to `fnv_status` answering from the game,
   with or without Mod Organizer 2, and how to remove it again.
2. [KNOWN-ISSUES.md](KNOWN-ISSUES.md): open problems, including the ones met during setup.
3. [The plugin builder](../README.md): `fnv_spec`, which turns a JSON spec into a plugin without the GECK.

## Using the tools (for an agent, or a person watching one)

- [AGENT-GUIDE.md](AGENT-GUIDE.md): the five rules, the working loop and what to ask the user first. Copy it into
  your mod project for your agent.
- Call `fnv_status` first, then `fnv_guide` (`action=list`, `topic`, `find`). The guides it serves are in
  [fnv_mcp/guides](../fnv_mcp/guides): start with `start`, then `build-a-place`, `test-a-mod`, `quest-dialogue`,
  `npc-ai-combat`, `safety-undo`, and for problems `crash`, `script`, `slow`, `check-plugin`, `change-engine`.
  Clients that show MCP prompts offer the same guides as prompts.
- [TOOLS.md](TOOLS.md): every tool with its actions, parameters and an example call, by task. Generated from the
  code.
- [FNV-LIVE-TOOLS.md](FNV-LIVE-TOOLS.md): how the tools reach the game, control levels, the journal and undo.
- [SCENARIOS.md](SCENARIOS.md): scenario tests, the file format, steps and checks.
- [FNV-ENGINE-TOOLS.md](FNV-ENGINE-TOOLS.md): the offline engine knowledge (the engine map from the decompilation)
  and the crash doctor.

## Changing the MCP itself

Work on the MCP in a clone of the repository: `CLAUDE.md`, `AGENTS.md` and `history/` are not in the release
download.

- The repository's [CLAUDE.md](../../CLAUDE.md) (also read by other agents through [AGENTS.md](../../AGENTS.md)): tests,
  live testing, and the traps. `tools/fnv_live_regress.py --suite loop` checks the loop real use is made of
  (native modules, decomp code, console, memory, input) in the running game before a push.
- [FNV-TOOL-CONVENTIONS.md](FNV-TOOL-CONVENTIONS.md): how an action-style tool is shaped and what a new tool needs.
- [FNV-COMPANION-V2.md](FNV-COMPANION-V2.md): the wire contract between the bridge and the engine companion.
- [FNV-SYMDB.md](FNV-SYMDB.md): the compiler's symbol database for the decompiled functions.
- [history/](history/README.md): how the tools were built and tested, with dates; nothing there is needed to use them.
