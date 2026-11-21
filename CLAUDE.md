# fnv-mcp

MCP server for building and testing Fallout: New Vegas mods in the running game. Python package `bridge/geck_mcp`,
branch `main`, checkout `C:\DecompModGithub\fnv-mcp-code` (the only one; `geck-mcp-nv` is the old one).

## Git
- "Push" / "GitHub" means the private remote `fnv-mcp` (`git push fnv-mcp main`). Never push
  to `origin` (public fork) or open anything upstream (`2Shay96/geck-mcp-nv`).
- Commit only when asked. Never commit the unpacked game image, the fnvdecomp API key or personal data.

## Tests
- Use the project's Python: `bridge/.venv/Scripts/python.exe` (3.12). The system Python has an old `mcp`.
- Full suite (about 11 minutes, run in the background), from `bridge`:
  `PYTHONPATH=. .venv/Scripts/python.exe tests/ci_run.py`
- One file: `PYTHONPATH=. .venv/Scripts/python.exe -m unittest discover -s tests -p test_x.py`
  (`python -m unittest tests.x` fails: another `tests` package shadows the folder).
- New tool parameters must be added to `action_params.py` (exact match is tested) and described in `param_docs.py`.
- Bash heredocs mangle backslashes: write edit scripts with the Write tool.

## Live testing
- Drive the working tree in-process with a script (`Client(make_server(Project.load(...)))`), run in the app's
  Terminal panel. MO2 launches from the Bash tool fail (its job object: ERROR_ACCESS_DENIED).
- The companion pipe has one instance: never use the MCP server's tools and an in-process script at the same time.
  The registered server runs from this checkout with `--reload`: it reloads itself when `bridge/geck_mcp` or the
  project file changes. `fnv_status` data `server` names the folder and commit that answered.
- A sleeping monitor hangs the game at startup (COMPANION_NOT_READY; Kernel-Power event 566 `ScMonitorPower` in
  the launch second). Keep a separate display-on helper running across all live runs
  (`SetThreadExecutionState(0x80000003)`); after a hang, `fnv_game quit force` and launch again.
- Never touch real saves (`Documents\My Games\FalloutNV\Saves`); `engine_control` stays `read` by default.

## Engine facts used by fnv_place
- `TESBoundObject` bounds (OBND, 6 x int16 min then max) at base form +0x24; `TESObjectREFR` base form at +0x20;
  form type byte at +4. Up axis from angles: (-sin Y, sin X cos Y, cos X cos Y).
- Pick rays (`fnv_hands.Rays.cast`) batch many rays per companion call; a hit naming no reference is the landscape.
- Footprint snap rules: the `build-a-place` guide (`bridge/geck_mcp/guides/build-a-place.md`); their live
  evidence: `bridge/docs/history/live-tools-reference-2026-11-18.md` ("Creation loop").
- The tool reference `bridge/docs/TOOLS.md` is generated: after changing a tool run
  `PYTHONPATH=. .venv/Scripts/python.exe tools/gen_tool_docs.py` from `bridge`, and give a new tool its group,
  example and action lines in `geck_mcp/tool_examples.py`. Docs index: `bridge/docs/README.md`.
