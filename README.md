# fnv-mcp

**MCP tools that let an AI agent build, run, debug and change Fallout: New Vegas: the running game, the engine
itself and the plugins that mod it.**

fnv-mcp is a standalone continuation of [geck-mcp-nv](https://github.com/2Shay96/geck-mcp-nv) by **2Shay**, which
it is built on and keeps the full history of; see "Built on geck-mcp-nv" below.

## What it is

One MCP server with <!-- counts -->**138 `fnv_*` tools**<!-- /counts --> for the game, its engine and plugins built
from a JSON spec. The count is checked against the server's own tool list by the tests.

- **Engine knowledge**: function and class lookup from the decompilation, a symbol database with source lines and
  named locals for the retail exe, crash explanation, engine warnings.
- **Live control**: time control (pause, step, frame advance), breakpoints and a debugger, screenshots and photo
  sheets ("eyes"), walking, talking, using doors and fast travel ("hands"), forms and scripts changed in place.
- **Engine changes**: C modules hooked into the running game, decomp functions recompiled with the game's own
  compiler and swapped in, both exportable as normal xNVSE plugins.
- **Performance and checks**: a sampling profiler, code coverage, thread, memory and object views, animation, audio,
  physics and formula inspectors, whole-plugin crawls, load-order bisection and a self-test.

## What it needs

- **Windows** 10 or 11 (Windows only), Python 3.12 and an MCP client (Claude Code, Claude Desktop, Codex...).
- Fallout: New Vegas from Steam (FalloutNV.exe 1.4.0.525), xNVSE, JIP LN NVSE, Yvile's Crash Logger and a portable
  Mod Organizer 2 instance for a dev profile of its own; or, without MO2, the game as Steam installed it, started
  with Steam's Play button (the tools then refuse to change its files unless you allow it).
- Zig 0.14.1, to build the engine companion that runs inside the game.
- A checkout of the [fnv_decomp](https://github.com/momusdigital/fnv_decomp) decompilation, from which you generate
  the engine map on your own machine.
Nothing from the game or the decompilation is in this repository: each user builds the engine map and
symbol database from their own copies.

## Start

Follow [bridge/docs/FNV-MCP-SETUP.md](bridge/docs/FNV-MCP-SETUP.md), the one setup guide. It starts from
`bridge/projects/example-fnv.json` and ends with the agent reading the running game. fnv-mcp is not a pip package:
run it from a clone with `python run_mcp.py --project <file>` (the guide registers exactly that with your MCP
client). Then a first prompt:

> Call fnv_setup_check and fix what it reports with me, then launch the game with fnv_game launch and show me
> fnv_status.

Every tool, with its actions, parameters and an example call, is in [TOOLS.md](bridge/docs/TOOLS.md); the
documentation index is [bridge/docs/README.md](bridge/docs/README.md), and the `fnv_guide` tool has step-by-step
guides. To give your own agent the rules for working with the tools, copy
[AGENT-GUIDE.md](bridge/docs/AGENT-GUIDE.md) into your mod project. Open problems are
listed in [KNOWN-ISSUES.md](bridge/docs/KNOWN-ISSUES.md). To remove it again, see "Uninstall" in the setup guide.

## Credits

- **2Shay**: author of [geck-mcp-nv](https://github.com/2Shay96/geck-mcp-nv), the project this repository is built
  on: the server's start and the offline plugin builder (`fnv_spec`).
- **HUNK2**: the `fnv_*` live, engine and debugging tools and the engine companion.
- The [fnv_decomp](https://github.com/momusdigital/fnv_decomp) project, xNVSE, JIP LN NVSE and Yvile's Crash Logger,
  whose work these tools read at runtime; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Built on geck-mcp-nv

fnv-mcp started as 2Shay's [geck-mcp-nv](https://github.com/2Shay96/geck-mcp-nv) (version 0.4.0 Beta): a GECK
bridge, an offline plugin builder from a JSON spec, script compilation with GECK's own compiler, runtime
diagnostics and an opt-in console companion. The plugin builder lives on as `fnv_spec`
([bridge/README.md](bridge/README.md)); the GECK editor tools, their 32-bit helper and the runtime and console
tools were removed on 2026-11-18, since the `fnv_*` tools do those jobs in the running game. geck-mcp-nv's own
release history is in [CHANGELOG.md](CHANGELOG.md), and the removed code is in the git history.

## Licence

- MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- The plugin builder and the server's base, from geck-mcp-nv, © 2026 2Shay. The `fnv_*` tools and the engine
  companion © 2026 HUNK2.
- Not affiliated with or endorsed by Bethesda Softworks, ZeniMax, Microsoft, Anthropic or OpenAI.
  Fallout and G.E.C.K. are trademarks of their owners.
