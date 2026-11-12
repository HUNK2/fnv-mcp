# PLAN-3 exit gate, round 3: shared state, journal and undo

Auditor scope: geck_mcp/fnv_state.py, geck_mcp/fnv_journal.py, the undo executor and op handlers in geck_mcp/fnv_live.py, GameIdentity, and the callers that mark or confirm entries (fnv_time, fnv_eyes, fnv_create, fnv_control, fnv_agent, fnv_probe, fnv_data). Read-only. Two findings were checked with offline snippets in the scratchpad (a temp journal and a fake link; no game).

Counts: high 1, medium 2, low 6.

---

## HIGH

### H1. Out-of-order undo is only guarded for place_adjust, time fixed/speed and hot swaps. Memory writes, patches, settings, AVs and positions on the same target end with the wrong value.
- Where: geck_mcp/fnv_live.py:158-173 (`_order_key` / `_later_open`), used at fnv_live.py:1525-1528. The `undo(journal_id=...)` path is at fnv_live.py:1541-1544.
- What happens: `fnv_mem_write` sets X from 00 to 01 (E1, undo hex 00). Then it sets X from 01 to 02 (E2, undo hex 01). Then `fnv_undo journal_id=E1` writes 00 and marks E1 undone, while E2 stays open. A later `fnv_revert_all` (or `fnv_session end`) runs E2's undo and writes 01. The final value is 01; the original was 00.
  - The same happens with `fnv_patch` (same `write` op). With overlapping but different ranges, the result is worse. Example: a 5-byte jmp patch at A, then a 2-byte nop at A+2, then undo of the first by id. Revert then writes the jmp's middle bytes back into the restored code, which leaves a broken instruction in .text and is a likely crash.
  - The same happens with `setting_set` (`console`), `script_set`, `actor_control set_av/force_av/set_position` (`console_ref` absolute lines) and `rng_seed` (`writes`).
- Why I'm confident: `_order_key` returns a key only for `kind == 'place_adjust'` and for `time_restore` fixed/speed, and `_undo_swap_script` has its own check. Every other op returns None, so `_later_open` returns [] and `_apply_undo` runs. PLAN-3 S3 exit criterion 5 says out-of-order undo "is refused with a reason, not applied wrong", but it is implemented for three kinds only.
- Reproduced offline (scratchpad t1.py): a Journal with two `write` entries to the same address, a fake link, then `undo(journal_id=E1)` followed by `revert_all()`. The memory ends at `01`, not the original `00`.
- Fix idea: extend `_order_key` with `('mem', address range)` for write/writes, `('console_ref', ref, verb/axis)` for console_ref, and `('setting', name)` / `('script_set', target, what, name, index)`. Overlapping write ranges should conflict, not only equal addresses. Alternatively, refuse any `journal_id` undo while a later open entry touches the same target.

---

## MEDIUM

### M1. Undoing a clock pause from another server process does nothing but is marked undone. The pausing server then double-Enables later.
- Where: geck_mcp/fnv_live.py:1790-1807 (`_undo_time_restore`, what=pause), geck_mcp/fnv_time.py:144-154 (`require_game` reloads `paused_by_us` only on a pid/start change) and 221-226 (`resume` returns changed=False without raising).
- What happens: server B uses `fnv_time_control` once (status, fixed, anything), so its TimeControl caches `paused_by_us=False` for this game. Server A then pauses (journal entry, state file `time_pause=True`). B runs `fnv_revert_all` or `fnv_undo`. B's `control.resume()` sees its stale `paused_by_us=False` and returns the "not paused by this tool" verdict without an exception. The op returns None, so `_apply_undos` marks the entry undone and reports `undone: true`, but the clock stays paused.
  - Follow-on: A still has `paused_by_us=True, restored=False`. A's `fnv_time_control pause` then answers "already paused" while the entry is closed. A's `resume` calls Enable blindly (no counter check when `restored` is False). If anything else released the counter meanwhile, that drives it below its baseline.
- Why I'm confident: `require_game` only reloads from ProcState inside `if proc[0] != self.pid or start != self.start`. `_undo_time_restore` ignores the verdict of `resume()`. Supervisor reload overlap and two clients are the documented two-server cases (fnv_state.py docstring).
- Fix idea: in the pause undo, reload `paused_by_us` from ProcState (or read the clock counter) before resuming, and return `{'undone': False, ...}` when `resume()` reports changed=False while the counter is still non-zero.

### M2. A hot-swap undo whose request times out after landing stays open, and a retry swaps the code back.
- Where: geck_mcp/fnv_live.py:1810-1820 (`_undo_swap_script`, not repeatable) with geck_mcp/fnv_data.py:861-875 (`swap_script_code`, one `link.batch`), and the exception path at fnv_live.py:1565-1567.
- What happens: `fnv_undo` on a `script_hot_swap` entry sends the 8 swap writes in one batch. The game applies them in that frame. If the reply is lost (COMPANION_TIMEOUT or COMPANION_DISCONNECTED, for example a frame stall or loading), the exception skips `mark_undone`, and the result shows an error. The agent retries `fnv_undo` (or `fnv_revert_all`). The swap is an exchange, so the second run puts the hot-swapped code back, and that is reported as `undone: true`. The same happens on SWAP_FAILED with some writes ok: the retry swaps a half-swapped pair.
- Why I'm confident: a swap is a toggle, so it is the one op where "ran but not recorded" makes the retry wrong. `_apply_undos` marks undone only when the op returns normally.
- Fix idea: before swapping, read both objects' data pointers and compare them with the values stored at journal time. Treat "already back" as done (mark undone). Or write the swap's intent ('undoing') to the journal before the batch and verify after a timeout.

---

## LOW

### L1. An undo that lands after the change but before its confirm is reopened by the confirm.
- Where: geck_mcp/fnv_journal.py:103-110.
- What happens: server A's `mem_write` or `patch` writes its pending intent and the write lands. Before A's `confirm`, server B's `fnv_revert_all` runs the repeatable `write` undo, which restores the old bytes, and marks the entry undone. A's confirm then folds the entry back to `undone: False`. The journal shows an open change that is no longer in the game. A later undo rewrites the same old bytes, so the result is harmless for absolute-restore ops, but `fnv_journal` and revert counts are misleading.
- Fix idea: record in the `_undone` event whether the entry was pending at the time, and on confirm reopen only when the undo ran before the change. Or keep it undone and add a note.

### L2. `fnv_undo` (count) is stuck on a newest pending entry with a non-repeatable op.
- Where: geck_mcp/fnv_live.py:1546 and 1530-1534.
- What happens: an `actor_control` console action (op `console`, pending) whose call timed out is the newest undoable entry. Every `fnv_undo` with count=1 picks it, gets "outcome unknown", and never reaches older entries. `entries(undoable=True)` does not skip entries the executor will refuse.
- Fix idea: have `undo(count)` skip pending non-repeatable entries (list them as skipped), or document that journal_id is needed.

### L3. The compaction fallback in `atomic_write` truncates and rewrites journal.jsonl in place. A reader in another process can then miss kept entries.
- Where: geck_mcp/fnv_state.py:125-136, used by fnv_journal.py:267. The reader is fnv_journal.py:66-90.
- What happens: if `os.replace` keeps failing for 0.5 s (another process or AV holds the journal open without delete share), compaction writes in place. That write is not atomic (a crash there empties the journal), and it keeps the same file id. Another server's `_refresh` detects a rewrite only by `size < offset` or an ino change. If its offset is smaller than the compacted size, it goes on reading from a byte offset in the new layout. Entries that moved to before that offset are never read by that process, so its `fnv_undo` and `fnv_revert_all` skip them. If the in-place write raises, the archived entries stay in the journal and are archived again next start, so they are duplicated in the archive.
- Fix idea: for the journal, never fall back to an in-place write. Fail the compaction (it is best effort) and keep the old file.

### L4. A JSON line with a wrong field type breaks every journal read and every act tool.
- Where: geck_mcp/fnv_journal.py:96-121, 138, 162.
- What happens: a hand-edited or foreign line such as `{"id":"a","seq":"7"}` or `{"kind":"_confirm","id":["x"]}` is valid JSON, so `_refresh` keeps it. `last_seq` then raises TypeError (str > int), as do `entries(since_seq)` and the fold (unhashable id). `Journal.add` fails, so every intent-first act tool fails until the line is removed. Checked offline (scratchpad t2.py): both lines make `add` and `entries` raise TypeError.
- Fix idea: in `_refresh`, keep a record only when id/seq/ids have the expected types (str id, int seq, list of str ids). Skip others like torn lines.

### L5. The probe-remove undo reads and writes probe_session.json outside one lock.
- Where: geck_mcp/fnv_live.py:1671-1675, geck_mcp/fnv_probe.py:310-315 (`_load` unlocked, `_save` locked).
- What happens: if another server saves a new probe session between the undo's `_load` and `_save` (the install path saves at fnv_probe.py:429), the undo writes back the older session with `armed: False`, and the new session's record is lost. The `probe disarm` it sends is also global, so it disarms a newer install's probes too (this overlaps with H1: there is no order key for probe entries).
- Fix idea: use `update_json` for the flip, and refuse the undo of an older probe entry while a later probe entry is open.

### L6. A time speed change from an out-of-range multiplier is made with no journal entry.
- Where: geck_mcp/fnv_time.py:358-370.
- What happens: if the current multiplier is outside 0.05..10 (for example after `sgtm 20` in the console, or 0), `speed(x)` skips `_journal` but still writes both multiplier floats. `fnv_revert_all` then cannot put the clock speed back. `fixed()` has a related edge: a previous step faster than 1000 fps is journaled as a value that `fixed()` rejects on undo (ValueError), so that undo always fails.
- Fix idea: always journal the raw previous float, and have the undo write it directly instead of through the range-checked setter.

---

## Checked and found sound (no finding)
- `locked`: same-thread reentrancy, a per-key thread gate before the OS byte lock, and a timeout on both. No lock-order inversion found: no caller holds another state lock while journaling, and the hook callbacks run on the calling thread, so `_unhooked` → `mark_undone` inside an undo is reentrant.
- Undo-twice across processes: `_apply_undos` rereads the entry under the journal lock and marks it undone in the same critical section. Within one process, the thread gate serializes callers.
- Seq numbers: the sidecar and the journal max are taken under the lock, file order equals seq order, and compaction keeps `last`.
- Torn last line: it is closed by `_append`, and readers consume whole lines only. A replaced file is detected by an ino change.
- Compaction keeps event order (UNDONE/CONFIRM after the body, in original relative order), so the pending/undone/confirm fold gives the same result after compaction.
- Identity: the companion `started` and `process_started` are both the FILETIME of the creation time, `link.hello` is cleared on connect and close, entries with no game are orphans, and `_apply_undo` refuses entries of another process.
