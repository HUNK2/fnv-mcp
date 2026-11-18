# Scenario tests

A scenario is a short YAML (or JSON) file that drives the running game and checks what happens: where to start, the steps, what to expect, and how to clean up. `fnv_test` runs one file (`action=run`), every file of a folder (`run_all`, by default the project's `fnv.tests_dir`) or steps given inline (`steps`, no file). `fnv_test_save` makes the named saves a scenario can start from. The test-a-mod guide (`fnv_guide topic test-a-mod`) shows where tests fit in the working loop. Examples: `examples/fnv-tests/`.

## A first scenario

```yaml
name: hello wasteland cell and a reversible change
start: {coc: HelloWasteland}          # menu | continue | {save: name} | {coc: CellEditorID}
plugins: [HelloWasteland.esp]          # must be loaded
steps:
  - expect: {eval: "GetName (player.GetParentCell)", equals: "Hello Wasteland"}
  - set: {global: GameHour, value: 10}
  - wait: {frames: 10}
  - expect: {global: GameHour, approx: 10, tolerance: 0.2}
  - expect: {console: "player.GetAV Health", contains: "GetActorValue: Health >>"}
  - expect: {no_script_errors: true}
teardown:
  - revert_all: true
```

A run keeps the world going: the game opens its pause menu when its window loses focus, so while a test runs
the companion closes the pause menu and message boxes (`allow_menus: true` turns that off, for a test about a
menu). On a coc start the DLC pack quests show "items added" boxes for about a minute; the companion closes
them in the background, so the launch does not wait for them. Each run also turns on crash context, so a
crash during a test names the running script (`crash_context: false` turns it off).

Steps: `run` (console line), `script`, `wait` (`seconds`, `frames`, or `until` an expression with a
`timeout`), `expect`, `set` (stage, global, variable, objective), `actor`, `input`, `move`, `mem_write`,
`call`, `snapshot`, `revert_all`, `quit`. Expectations: `eval`, `item_count`, `stage`, `quest_running`,
`actor_value`, `variable`, `global`, `cell`, `console`, `disabled`, `dead`, `exists`, `mem`,
`no_script_errors`, compared with `equals`, `not_equals`, `gt`, `ge`, `lt`, `le`, `approx`, `contains`.

A run launches the game when needed, applies `start` (message boxes that appear on arrival, like the DLC
packs' "items added" boxes on a coc start, are closed), stops at the first failure (or not, with
`continue_on_failure: true`), and returns each step's actual value. A failing run adds a screenshot of
the game window. If the game dies, the report carries the crash doctor's diagnosis of the new crash log
and the game is relaunched. Reports are saved in `state/fnv/test-reports`.

Files are a YAML subset or JSON; no YAML library is needed. The rules are below. Examples:
`examples/fnv-tests/`.

## Scenario file format

A scenario file is either JSON (the whole file starts with `{` or `[`) or the YAML subset described here. A
file a full YAML parser reads differently is read by these rules, not YAML's.

- **Structure.** Block mappings (`key: value`) and block lists (`- item`), nested by indentation. Indent with
  spaces; a tab in the indentation is refused. A list may sit at the same indentation as its key. `- key: value`
  starts a mapping inside a list item. `---` lines are ignored.
- **Flow values.** `{a: 1, b: x}` and `[1, 2]`, nested, on one line. Text after the closing bracket is refused.
- **Plain scalars.** `true` `yes` `on` and `false` `no` `off` (any case) are booleans; `null` and `~` are null.
  Whole numbers become integers and decimals (`1.5`, `.5`, `1.5e3`) become floats. A number with a leading zero
  (`00138562`), hex (`0x1F`) and `1E5` (no decimal point) stay text, so form ids can be written bare. Quote a word
  you want as text: `'yes'`.
- **Quoted scalars.** `'single'` takes the text as written (`''` is one quote). `"double"` takes JSON escapes
  (`\t`, `\n`, `\"`, `\u00e9`); any other backslash is refused, so write a Windows path in single quotes.
- **Comments.** `#` at the start of a line, or after a space or tab, starts a comment, except inside quotes.
  `x#y` is text.
- **Block scalars.** `|` joins the more-indented lines that follow with line breaks, `>` with spaces. Unlike
  YAML, blank lines are dropped, each line's own indentation is dropped, and a ` #` inside starts a comment.
  Chomping and indentation indicators (`|-`, `|2`) are not supported: the lines after them are refused.
- **Not supported.** Anchors and aliases (`&x`, `*x`) and tags (`!!str`) are read as plain text. Keys are text.
  No multi-document files and no multi-line flow values.

## Agent steps and checks

```yaml
name: agent wave end to end
start: {test_save: agentwave}         # a save made with fnv_test_save
guard: {message_boxes: keep}          # keep message boxes for the test (default: close them)
trace_script: HelloQuest              # report which statements of this script ran and which never did
steps:
  - spawn: {form: 0006B53D, as: ammo} # $ammo in later steps
  - expect: {exists: $ammo}
  - trace: {function: "TESQuest::RunScript"}
  - wait: {seconds: 1}
  - expect: {called: "TESQuest::RunScript", at_least: 10}
  - run: ShowMessage 0011E661
  - expect: {message_box: Hardcore}
  - menu: {press: "No"}
  - expect: {menu_open: MessageMenu, is_false: true}
  - expect: {condition: 00138562, list: requirements, passes: false}
  - time: {advance_hours: 1}
  - watch: {items: [GetCurrentTime], seconds: 1}
  - expect: {marker: 03002FCA}
  - screenshot: agent-wave-end
  - expect: {no_script_errors: true}
teardown:
  - trace: {function: "TESQuest::RunScript", action: stop}
  - revert_all: true                  # this test's changes only
```

Steps: `spawn` (`as:` names the new ref), `move`, `save` / `load` (test saves), `menu` (`press`, `close`,
`close_all`), `screenshot`, `watch` (fails when `until` never becomes true), `trace`, `time`, `rng`.
Checks: `called` (calls recorded by a running trace, `at_least` / `at_most`), `condition` (a record's
condition lists, optionally one `list`, `passes`), `message_box` (text in the open box), `marker` (a quest
target is shown), `menu_open`. `revert_all: true` in a test undoes only the changes made since its steps
began (`revert_all: process` for every change of the game process).

A failing run adds a debug bundle: the open menus and message box, the console's last 40 lines and a
screenshot. With `trace_script` the report has `scriptTrace`: statements run, and the lines that never ran.
