# PLAN-3 exit gate, round-3 audit: tool surface and contract

Area: fnv_live_tools.py registrations, fnv_live.py envelope / console guard / resolve, fnv_args.py, the engine_control
gate (gated_kind / MUTATING_ACTIONS), fnv_game.py launch/quit/status, fnv_session.py, fnv_crawl.py, fnv_scenario.py,
supervisor.py, server.py. Read-only; offline checks used FakeLive (tests/fakes.py) with engine_control="read".

Counts: HIGH 2, MEDIUM 2, LOW 5.

---

## HIGH

### H1. Read-classed tools run caller-supplied console lines and xNVSE expressions (fnv_snapshot, fnv_diff, fnv_watch): game changes with engine_control="read", no journal, no save backup (T1 fix incomplete)

- Where:
  - geck_mcp/fnv_live_tools.py:452-457 (`fnv_snapshot`, kind read) -> fnv_scenario.py:1110-1125 (`snapshot` feeds each
    item to `ScenarioRunner._expect`)
  - geck_mcp/fnv_live_tools.py:459-477 (`fnv_diff`, kind read; without `after` it re-runs the stored items)
  - geck_mcp/fnv_scenario.py:848-851 (`eval`: `live.link.eval(<any text>)`), :865-875 (`actor_value`/`global` text
    pasted into the expression), :882-887 (`console`: `live.console(line)`, only the quit/load/save guard)
  - geck_mcp/fnv_live_tools.py:957-960 (`fnv_watch` without frames calls invoke with action='sample' and the default
    kind 'read'; MUTATING_ACTIONS lists only 'frames') -> fnv_agent.py:467-477 (items and `until` are eval'd;
    `keep_unpaused=True` by default installs the companion `every` task that closes MessageMenu 1001 and the pause menu)
- What happens: `fnv_snapshot name=x items=[{"console": "player.kill"}, {"eval": "player.AddItem Caps001 1000"}]` or
  `fnv_watch items=["SetStage SomeQuest 50"]` changes the running game though the project has engine_control="read".
  `_run` checks engine_control only for kind 'act' (fnv_live.py:204), and gated_kind returns 'read' for these calls.
  Nothing is journaled and `changing()` (the save backup) is never called, so fnv_undo / fnv_revert_all cannot take it
  back. Even a harmless fnv_watch closes any open message box (UNPAUSER) for seconds+10 s.
- Confidence: confirmed offline. FakeLive(engine_control='read'), `live.run('fnv_snapshot', lambda:
  snapshot(live, [{'console': 'player.kill'}, {'eval': 'KillAll'}], 'x'), 'read')` -> ok True, ops sent
  `rd rd wr wr con wr wr eval eval` (the console line and the expression reached the link). gated_kind('fnv_watch',
  'sample', 'read') == 'read', gated_kind('fnv_snapshot', None, 'read') == 'read'.
- Fix idea: list fnv_snapshot and fnv_diff ('*') and fnv_watch ('sample') in MUTATING_ACTIONS (or restrict snapshot
  items to the typed getters and refuse `console`/`eval` in read kind, and default keep_unpaused off in read kind).

### H2. Every read tool that takes a ref evaluates a bare identifier as an xNVSE expression: `fnv_ref ref=QuitGame` quits the game, `fnv_quest quest=KillAll` kills every actor, with engine_control="read"

- Where: geck_mcp/fnv_live.py:427-431 (`resolve`: an EDITOR_ID-shaped ref becomes `expr = ref` and goes to
  `self.link.eval(expr)` before anything checks it is a form), reached from fnv_ref, fnv_actor_ai, fnv_quest,
  fnv_inventory, fnv_vars, fnv_effects, fnv_extra_data, fnv_condition_eval, fnv_actor_values ... (all kind read).
- What happens: the `Ref` field accepts any `[A-Za-z_]\w*` up to 200 chars. `fnv_ref ref="QuitGame"` sends
  `eval "QuitGame"`; the companion compiles it as an expression and CallFunction runs the command (op_eval,
  native/engine/fnvmcp_engine.c:1973-1994). The tool then reports REF_NOT_FOUND, after the command already ran. Any
  zero-argument command works the same way: QuitGame / qqq (game quits; quit-log says the bridge did not ask),
  KillAll, ToggleGodMode, EnablePlayerControls / DisablePlayerControls, ResetAI..., none journaled, all under read.
- Confidence: the op is confirmed offline: FakeLive(engine_control='read'), `live.run('fnv_ref', lambda:
  live.ref('QuitGame'), 'read')` sent `eval 5175697447616d65` ("QuitGame"); `live.quest('KillAll')` sent `eval
  4b696c6c416c6c` ("KillAll"). That `eval "QuitGame"` quits the game is the premise of the project's own M10 fix
  (fnv_live.py:923-928 refuses a single-line run_script `QuitGame` because that path is the same `link.eval`;
  tests/test_s5_review.py:49-54). Not run live (read-only audit).
- Fix idea: in resolve(), refuse (or never eval) an identifier that is a script command name (engine().commands, as
  substitute_editor_ids already builds), e.g. resolve bare EditorIDs through `editor_id()` / `GetFormFromMod`-style
  lookups only, or wrap as a form-typed lookup that cannot call a command.

---

## MEDIUM

### M1. Hook installs and game memory writes in tools classed 'control' bypass engine_control (D2 / T1 incomplete)

- Where:
  - geck_mcp/fnv_live_tools.py:1527-1529: `fnv_warnings action=capture mode=start` runs as 'control' ->
    fnv_warnings.py:111-125 installs a code hook on the engine's Warning function (`one('hook', ...)`, label
    `warnings_live`). fnv_warnings is not in MUTATING_ACTIONS, so no CONTROL_DISABLED; the label is owner-managed
    (fnv_live.py:107), so no journal entry and fnv_revert_all does not remove it.
  - geck_mcp/fnv_live_tools.py:1322-1329: `fnv_window release_mouse|capture_mouse|unpause` runs as 'control' ->
    fnv_world.py:999 `link.write(ig + 4, ...)` (DirectInput flags in game memory) and closes the pause menu.
- What happens: with engine_control="read", `fnv_warnings action=capture mode=start` patches game code; `fnv_window
  action=release_mouse` writes game memory. D2 (PLAN-3.md:56) says writes and installed hooks are 'act'.
- Confidence: read in code; gated_kind('fnv_warnings', None, 'control') == 'control' and gated_kind('fnv_window', None,
  'control') == 'control' (offline). `_run` gates only kind 'act'.
- Fix idea: add `'fnv_warnings': {'capture'}` (start/stop) and `'fnv_window': {'release_mouse', 'capture_mouse',
  'unpause'}` to MUTATING_ACTIONS (persist-only calls can stay control).

### M2. fnv_formid_live (read) pastes free text into a quoted xNVSE expression (quote injection)

- Where: geck_mcp/fnv_live.py:851 (`'IsModLoaded "%s"' % plugin`, `'GetModIndex "%s"' % plugin`), registered at
  fnv_live_tools.py:578-580 with `plugin: str` 5-100 chars, no pattern.
- What happens: `plugin = 'a.esp" + QuitGame + "'` gives the expression `IsModLoaded "a.esp" + QuitGame + ""`, eval'd in
  a read tool. The same unescaped pattern is used by the scenario runner's plugin checks (fnv_scenario.py:540).
- Confidence: likely. The text reaching `eval` follows directly from the format string; whether the in-game parser runs
  the embedded command was not verified live. Lower impact than H1/H2, which already give arbitrary eval under read.
- Fix idea: validate `plugin` with the PLUGIN_LOCAL file-name pattern (`^[A-Za-z0-9_. '-]{1,80}\.es[mp]$`), no `"`.

---

## LOW

### L1. After a failed launch (no game process ever started), fnv_status says the game "was terminated from outside"

- Where: geck_mcp/fnv_game.py:454 (`save_session(pid=None, launchedAt=..., start=..., ready=False)` keeps the previous
  launch's `companion` key) and :373-383 (the note's condition).
- What happens: launch -> MO2_LAUNCH_FAILED or GAME_NOT_STARTED. session has the new launchedAt, the old companion=3,
  no crash log since launch, the newest exit note is from the previous game (forLastLaunch False), and no quit-log
  line after launchedAt. fnv_status then adds exitNote "ended without an exit note ... terminated from outside" for a
  game that never ran.
- Confidence: read in code (R18 fix covers older companions and bridge force-quits, not a launch that never started).
- Fix idea: reset `companion=None` in the launch's first save_session (it is written again once the companion answers),
  or record `pid` presence: show the note only when a pid was recorded for this launch.

### L2. The audio-cache flag can stay off for the whole game when the restore write fails (N21 fix incomplete)

- Where: geck_mcp/fnv_game.py:624-630.
- What happens: `start_from_menu` turns bEnableAudioCache off, then restores it in `finally`. When wait_in_game raises
  NOT_IN_GAME (the load is still stalled at the deadline) the main loop is busy, `link.write` times out, the
  LinkError is swallowed, and nothing retries later; the GameError envelope does not say the flag is off (the
  `audioCache` field is only added on success). The game then runs with the audio cache disabled until restart.
- Confidence: read in code; no other code path writes AUDIO_CACHE_FLAG (grep).
- Fix idea: on a failed restore, remember the pending restore in session.json and retry it on the next fnv_status /
  ready() ping, and put `audioCache` in the error evidence.

### L3. fnv_run_script's quit/load guard is bypassed by a parenthesised or multi-line source (G14 / M10 incomplete)

- Where: geck_mcp/fnv_live.py:901-905 (first word only) and :925 (multi-line source is not checked at all).
- What happens: `fnv_run_script source="(QuitGame)"` (first word `(quitgame)` is not in the set) or
  `"Begin Function { }\nQuitGame\nEnd"` runs QuitGame; the quit is then traced as not asked by the bridge, which is
  what G14 set out to stop. Act tool, so engine_control=full is needed.
- Confidence: confirmed offline: `LiveService.console_refused('(QuitGame)')` is None and run_script sent
  `eval "(QuitGame)"`.
- Fix idea: tokenize the whole source (all identifiers, all lines) and refuse if any token is in CONSOLE_REFUSED.

### L4. fnv_crawl cells moves the player to exterior cells with an unjournaled `cow`, so "fnv_undo moves it back" is false

- Where: geck_mcp/fnv_crawl.py:121 (`self.live.console('cow ...')`), tool text at fnv_live_tools.py:2214-2215.
- What happens: interior cells go through agent.move (journaled, fnv_agent.py:766); exterior cells use raw
  `live.console`, which writes no journal entry and makes no save backup. A crawl of a plugin whose cells are all
  exterior leaves the player in the last cell and fnv_undo / fnv_revert_all has nothing to undo; with mixed cells the
  undo restores only to the position before the first journaled move.
- Confidence: read in code.
- Fix idea: use agent.move with a position in the target grid (or journal a `move` entry with the pre-crawl position
  once at the start of the crawl).

### L5. fnv_snapshot `items` has no length cap; console items return uncapped output

- Where: geck_mcp/fnv_live_tools.py:454 (`items: list[Any] | None`, no max_length); fnv_scenario.py:886 (`actual =
  live.console(line)['output']`, not passed through `capped` as run_console does at fnv_live.py:920).
- What happens: N console items each return up to the companion's OUTPUT_MAX, so the reply (and the snapshot file)
  grows with N without a bound. Moot once H1 gates the tool, but the read path stays unbounded.
- Fix idea: `Field(max_length=100)` on items and `capped(..., 400)` on console output in `_expect`.

---

Checked and found sound (no finding): the `_run`/`_failure` envelope (every exception becomes a coded envelope;
`_envelope_code` guards `self.game`), BridgeServer's wrapping of SDK validation errors, fnv_args ranges/as_bool/form_id,
quit/restart session pid clearing (`_quit_result`), the session end `finally`, the supervisor's routing, drain and
child-exit paths, fnv_wait_for predicates (fnv_pred has no expression op), console output size (companion caps at
OUTPUT_MAX). Known T8 / S5 not re-reported.
