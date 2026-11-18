## Loose ends of W1 to W4 (2026-11-11 evening)

Built and checked in the game in this run:

| Piece | What changed | Checked live |
|---|---|---|
| `fnv_watch frames=N` (`every=k`) | The companion sampler: one program of up to 16 values run in the main-loop message every k frames, kept in a ring in the game, read once. Items are predicate text (`[[0x011DEA3C]+0x30]:f32`) or `player.x/y/z/yaw`, `frame` | 120 consecutive frames, none missed |
| `fnv_events subscribe=[...]` | xNVSE event sinks (`onactivate`, `onhit`, `ondeath`, `onadd`, `onload`, ...): frame, reference and the event's argument words; words that point to forms come back as form ids. `sink_since`, `ref`, `unsubscribe` | OnActivate recorded when the player opened a door |
| `fnv_debug hits=N` | The companion counts hits that pass `when` and stops from the Nth (`bp after`, sent before `bp set`: set first, the next frame's hit stopped at once, seen live) | TES::RunScripts stopped on hit 120 exactly |
| A stop during a wait | A breakpoint that parked the main thread while `fnv_wait_for` or a step ran frames left the request open and the one pipe instance busy: the game hung (seen live). The companion now ends that request with `stopped`; the tools say `STOPPED` and name fnv_debug | 120th-hit break during a 5 s wait: the wait returned, the game resumed |
| `fnv_flow trace` | The trace is read under the step stop that ends it (it read the breakpoint's seq: `no_trace`); hex trace values decoded; the result field is `endedBy` (a duplicate `reason` argument made every finished trace fail) | TES::RunScripts: 114 quests visited, 52 scripts run, locals per line |
| `fnv_blame field` | Plugin code in a stack is named `module+offset` | GameHour is written every frame by `jip_nvse.dll+0x10FA4` |
| Wire numbers | The companion parses numbers as hex; several calls sent decimal (`resume` seq, `bp set` id, `ret` pop, `trace read`, `dbg read`): wrong values from 10 up. All send hex now | (found while checking hits=120) |
| `fnv_session start` | Failed on the locked warning log; it now turns the live warning capture on | session start, health ok |
| `fnv_photo` | Refused with `MENU_OPEN` while a menu is open (all frames were the pause menu's backdrop) | |
| `fnv_form reload` | Takes the rebuilt plugin's path: the running game locks its plugin files against writing | HelloRedFlash renamed in the running game from a rebuilt copy |

Still not done: S6 end to end (a changed texture file and a misspelled mesh in a test plugin), `type` into menus,
a door panel swinging into a corridor, `fnv_flow call` and depth 1, `fnv_blame form`, several `log` expressions.
