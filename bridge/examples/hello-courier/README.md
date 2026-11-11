# Hello Courier: scenario S1 (build a quest mod, play it as a player)

A contact in a long room asks the courier to find three tokens on the floor and pays 100 caps for them. Built from
`hello-courier.spec.json` with the spec builder; its quest script and the two dialogue result scripts were compiled
by the running game (`fnv_scripts_compile`).

| Part | What it is |
| --- | --- |
| `HelloCourierContact` | An NPC cloned from the template dump `VStreetSS6Traveler01` (factions and packages removed) |
| `HelloCourier` | The quest: priority 80, stages 10 to 40, three objectives, the quest script |
| `GREETING` (override) | Four lines for the contact: the offer (choice: accept), waiting, found (choice: hand in), done |
| `HelloCourierAccept`, `HelloCourierHandIn` | The player's two topics; their lines' result scripts set stage 20 and 40, take the tokens and pay |
| `HelloCourierRoom` | Six floor tiles, two lights, the contact at the east end, three tokens, a coc marker at the west end |

`tests/s1-play.json` is the scenario: one `fnv_do` call that walks to the contact, talks, accepts, picks up the three
tokens, talks again, hands them in and photographs the contact, with checks of the stage, items and caps after each
part. It needs the game in `HelloCourierRoom` (`fnv_launch coc:HelloCourierRoom`) with no tokens or caps.

Templates: the NPC and `GREETING` come from `state/templates/FalloutNV.esm/` (made with
`tools/esm_research.py dump FalloutNV.esm OUT.json VStreetSS6Traveler01 GREETING`, then one file per record); they
are not in the repository.
