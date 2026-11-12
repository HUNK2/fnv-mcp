# Round-3 audit: companion (C) and the code that talks to it

Scope read: native/engine/fnvmcp_engine.c, w1_pred.c, w1_gate.c, w1_samp.c, w1_guard.c, w1_input.c, w2_stop.c,
w2_bp.c, w5_mod.c, w5_code.c, w6_cov.c, fake_host.c (head only); geck_mcp/fnv_link.py, fnv_debugger.py, fnv_flow.py,
fnv_control.py, fnv_deep.py (hook plumbing and prologue decoder); fnv_game.py (exit note / companion version gate).
Read-only. The only thing run was two `decode_prologue` calls offline (shown under H1).

Counts: high 1, medium 3, low 9.

---

## HIGH

### H1. An int3 can land inside a hook's patched bytes, or be copied into a hook's trampoline, and the game crashes

- Where:
  - `native/engine/w2_bp.c:105-130` `isite_add`: it checks only other int3 sites and coverage. It never checks hooks (`hooks[]`: target..target+stolen), probe ranges (`bp_probe_at`, `w2_bp.c:140`, checks only `target == addr`), or the input-poll patch.
  - `native/engine/fnvmcp_engine.c:779-780` `op_hook`: it releases coverage bytes (`cov_release_range`) before it saves `h->original`. It does nothing like that for live int3 sites, so a 0xCC from a breakpoint, a `to` site or a trace site is saved as an "original" byte and copied into the trampoline.
  - `native/engine/w6_cov.c:135` `cov_write_all`: coverage arm also skips only exact probe and isite addresses, not hook-owned bytes.
  - Python: `geck_mcp/fnv_debugger.py:622-640` `_hook_hold` and `fnv_flow.py:41-60` `hook_at` do no clean-image or `bp list` check before hooking. `_break_set` (`fnv_debugger.py:563-600`) sets trap breaks with no check against the hooks.
- What happens (two plain fnv_debug calls are enough):
  - **A. A breakpoint first, then a hook at the same function.**
    1. `fnv_debug break location=F hits=2`, or `thread=any`, or any break when the 4 hw slots are busy. This takes the trap path, so the companion writes an int3 at F.
    2. `fnv_debug break location=F` (no hits). This takes the hook path. `_hook_hold` reads `CC 8B EC ...`, and `decode_prologue` treats 0xCC as a 1-byte instruction. Checked offline: `decode_prologue(bytes.fromhex('cc8bec83ec10')+pad) -> (6, [])`.
    3. `op_hook` copies the 0xCC into the trampoline and patches F with `E9`.
    4. The next call of F runs the trampoline and hits the 0xCC at an address that is not an isite.
    5. In `on_trap`: `cov_on_breakpoint` and `bp_on_breakpoint` say no, and the int3-probe path says no. `on_exception` ignores EXCEPTION_BREAKPOINT. The exception goes unhandled and the game crashes.
    6. The same happens when a flow trace's int3 (hw full), a `to` site (`until F`) or a trace return site sits at F's first bytes while a hook is installed.
  - **B. A hook first, then a breakpoint inside its prologue.**
    1. F is hooked by any tool (fnv_hook_set, a tracepoint, a watch, or a function break). For a typical /Od function with no locals (`55 8B EC 8B 45 08 ...`), `decode_prologue` gives stolen = 6 (checked offline: `(6, [])`), and F now holds `E9 d0 d1 d2 d3 90`.
    2. `fnv_debug break location=<file>:<first statement line> thread=any` resolves to F+3, which is inside the jump's displacement. `isite_add` saves d2 as "orig" and writes CC there.
    3. The next call of F jumps to a garbage address and the game crashes.
    4. Variant: a site at offset 5 or more (the hook's NOP padding) does no harm at first. But after the hook is removed (`op_unhook` writes `h->original` back), the site is still live with orig = 0x90. `bp clear` then writes 0x90 into the middle of the real prologue instruction, and the game crashes on the next call.
    5. The same holds for coverage lines (`cov arm`) and for mode-0 probes (their stolen bytes are not checked either).
- Why confident: I followed every check on both write paths, and none of them looks at the other mechanism's byte range. The w2_bp.c header names only probes as excluded. No test covers the overlap: grepping tests/ for hook-plus-bp overlap finds nothing.
- Fix idea:
  - In the companion, `isite_add`, `cov_write_all`/`cov add` and `bp_probe_at` refuse ("in_use") any address in [target, target+stolen) of a live hook or armed probe.
  - `op_hook` refuses (or releases first) when a live isite lies in [target, target+stolen).
  - Python cannot do this on its own, because sites from other tools and threads are not visible to it.

---

## MEDIUM

### M1. A worker thread parked at a stop that the main thread waits on cannot be released while the bridge keeps talking: the dead-man clock is reset by requests that are never served

- Where: `native/engine/fnvmcp_engine.c:2277` (`last_contact = GetTickCount64()` for every request line and ping, before it is served), `w2_stop.c:110-115` (`stop_wait_worker` releases only after `gate_timeout` with no contact), `serve_client` (`fnvmcp_engine.c:2266-2300`; only `ping` is answered on the pipe thread, and `resume` needs the main thread).
- What happens:
  1. A trap break with `thread=any`, or an int3 break with thread=main (see L1), parks a game worker thread. That thread holds a lock or a task the main thread then waits for. The main thread blocks inside engine code, outside `stop_serve_wait`, so no request is served.
  2. Each bridge call:
     - `fnv_debug status` pings first, and the ping refreshes `last_contact`.
     - Its request also refreshes `last_contact` when queued.
     - The request then times out after 15 s. The pipe thread drops it (`pending` 1→0) and opens a new instance.
  3. `resume` cannot be delivered (it is served on the main thread), and `fnv_debug thaw` only resumes suspended threads, not stops.
  4. The worker's dead-man (120 s with no contact) never fires while the agent keeps polling. The game hangs until the agent goes silent for 2 minutes or the game is killed.
- Why confident: `last_contact` is written in `serve_client` before the request is handed to the main thread, and the worker's dead-man reads only that value. No pipe-thread op can release a stop.
- Fix idea: count only served requests as contact (set `last_contact` in `serve_request`, or when the main thread takes the request, and make ping not count while `pending` is stuck). Or let the pipe thread serve `resume` / `resume ff run` directly; `stop_release` is already interlocked.

### M2. Hook stubs and record rings are never reused: about 1000 hook installs per game end every hook tool, and each recording hook leaks 768 KB in the 32-bit game

- Where: `native/engine/fnvmcp_engine.c:782-787` and `:807-808`. The 64 KB `hook_code` area is bump-allocated, 64 bytes per hook (plus 16 per skip/native stub, 32 for inpcfg), and never given back. `h->records` is 4096*48*4 = 786,432 bytes, VirtualAlloc'd per HF_RECORD hook and never freed (by design, per the comment). Failure returns after allocation (`bad_relocation`, `out_of_memory` at `:786`, `:799`) also leak `h`, the ring and the 64 stub bytes.
- What happens:
  1. Every `hook` op takes new stub space, including the transient hooks of fnv_inspect `record_hooks` (HF_RECORD, `fnv_inspect.py:113`), subtitles, the perf heap watch, warnings capture, blame, the script debugger, frame profile, debugger holds, fnv_hook_set and tracepoints.
  2. After about 1000 installs in one game process, `hook_code_used + 64 > 65536` and every hook op fails with `out_of_memory` until the game restarts. That includes debugger function breaks.
  3. Long before that, each HF_RECORD install has committed 768 KB that is never released. 300 recording hooks is about 230 MB of extra commit in a 32-bit process that already runs close to its address-space limit, so the game itself can then fail an allocation and crash.
- Why confident: there is no free path. `op_unhook` only sets `stolen = 0`, and `op_hook`'s pruning (`:772-776`) recycles only the `hooks[]` pointer slots.
- Fix idea:
  - Keep removed hooks' stub/ring/table on a free list, keyed by flags.
  - Reuse an entry after a grace period, or when its shadow-stack users are gone (a per-hook in-flight counter, incremented in `on_enter` and decremented in `on_exit`).
  - Free the allocations on the error returns.

### M3. fnv_flow trace takes an old parked stop of a cleared break with the same bp id as its own hit, leaving its one-shot breakpoint armed for the next caller

- Where: `geck_mcp/fnv_flow.py:372-380` `_await_bp` matches any listed stop with `kind == 'bp' and id == bp_id`, with no seq baseline and no eip check. `fnv_debugger.py:610-617` `_free_bp_id` excludes only ids of live bps (`bp list` lists `used` ones) and of debugger records, not ids still held by parked stops.
- What happens:
  1. `fnv_debug break` at X gets bp id 0. It is hit, and the thread is parked (stop seq 5, id 0).
  2. The user clears the break. The stop stays parked: `bp clear` does not release stops, which is the G13 situation.
  3. `fnv_flow trace function=Y`: `_free_bp_id` returns 0, and `bp set 0 Y ... ONCE` is armed. `_await_bp` immediately returns the old stop seq 5.
  4. `resume 5 trace ...` releases the thread at X. `tr_begin` finds eip outside Y's range, so `tr_end(RETURNED)` runs and the thread parks as a step stop. `_read_trace` reads it and releases it.
  5. The tool reports "Traced Y: 0 steps ... The function returned." The real one-shot bp on Y is still armed.
  6. The next call of Y parks a thread (normally the main thread) with no tool waiting on it. Polling by the agent keeps the dead-man from firing.
- Why confident: the code paths are as listed. Parked-after-clear stops are an accepted state (G13). Nothing ties the awaited stop to this arm.
- Fix idea: read the newest stop seq before `bp set`, and accept only `kind == 'bp'`, `id == bp_id`, `seq > baseline` and `eip == fn['addr']`. Or make `_free_bp_id` also skip ids of parked stops.

---

## LOW

### L1. A thread=main trap break that falls to int3 parks every thread (E24 fixed only the hook path)
- Where: `fnv_debugger.py:566-579`. `flags` carries BP_FLAG_ALL only for thread=any. In the companion, `BP_FLAG_ALL` only sets the hw scope (`w2_bp.c` `op_bp_set`), and `bp_hit` (`w2_bp.c:355-377`) has no thread check.
- What happens:
  1. A line break with thread=main, when the debugger's 4 hw breaks are used or `_set_bp` hw is refused (no_debug_register because of watchpoints or a flow trace), becomes int3.
  2. Worker threads that run that line park. The record still says `thread: main`.
- This feeds M1.
- Fix idea: for thread=main int3 breaks, store `main and (<when>)` as the bp predicate, as E24 did for holds.

### L2. A failed unhook silently ends the hook's action or hold, and fnv_debug break clear drops the record anyway
- Where: `fnvmcp_engine.c:852-859`. `op_unhook` sets `active = 0` and `act_mode = ACT_NONE` before it checks the bytes. On `busy` it restores `active = 1` but not `act_mode`. On `unreadable` / `overwritten_by_another_patch` it leaves both cleared. `w5_mod.c:146-147` `hook_remove` does the same. `fnv_debugger.py:700-701` ignores the unhook result.
- What happens:
  - fnv_hook_set remove fails with UNHOOK_FAILED, but the skip/result already stopped, and the journal still says the action is live.
  - `fnv_debug break clear N` on a hold that fails to unhook deletes break N while the hook stays patched. One case: a later int3 on F after a "hook refused → trap" fallback makes the bytes not match.
- Fix idea: in the companion, save `act_mode` and put it back on every failure path. In Python, check the `unhook` reply in `_break_clear` and keep the record on failure.

### L3. op_hookact rewrites a live skip stub in place
- Where: `fnvmcp_engine.c:906-920`.
- What happens: a thread that just got `h->skip_stub` from `fnvmcp_on_enter` under the old mode can run the stub while `code_write` copies the new bytes (up to 9). It can run a half-written stub, or `fld` instead of `mov eax` (x87 stack imbalance) after a skip → skip_float change.
- This is the same class as B13 (stop stubs), which was fixed with a ring. Hook skip stubs were not.
- Fix idea: take a fresh 16-byte stub for each hookact (ring or bump), like `stop_skip_stub`.

### L4. Fault stops do not reload the thread's debug registers after the park
- Where: `w2_stop.c:233-257` `stop_fault`, and `w2_bp.c:466-474` `bp_fault_resume`.
- What happens:
  1. A fault-stopped thread waits inside the vectored handler, so a `dbg set` / `bp set hw` / clear made while it is parked is applied by `dbg_worker` to its kernel context. That change is lost when the handler returns the old exception CONTEXT. `bp_park` handles this with `dbg_fill(c, tid)`; `stop_fault` does not.
  2. A cleared hw breakpoint or watchpoint stays live on that thread.
  3. If its slot is later reused by a watchpoint, `dbg_note` records spurious hits at the old address for that thread.
- Fix idea: call `dbg_fill(c, GetCurrentThreadId())` after `stop_park` in `stop_fault`.

### L5. fnv_flow NOT_CALLED path: a hit between the last poll and `bp clear` leaves a parked stop that nobody resumes
- Where: `fnv_flow.py:211-216`.
- What happens: `_await_bp` returns None at the deadline, then `bp clear` is sent. A one-shot hit in that window has already parked the thread (bp clear does not release stops). The tool says "the breakpoint is cleared" while the main thread may be parked, and M1-style polling keeps it there.
- Fix idea: after `bp clear`, list stops once more and resume any bp stop with this id and a seq above the baseline.

### L6. fnv_flow `_read_trace` takes the lowest new step stop of any thread
- Where: `fnv_flow.py:389`.
- What happens:
  1. Another thread's step stop (fnv_debug stepping, a `to` site reached) with seq > the bp seq is taken as the trace's end.
  2. `trace read` answers `no_trace`, so TRACE_FAILED is raised.
  3. `_release_quietly` then resumes that other tool's step stop (E4 part 3, again by another route).
- Fix idea: filter by `s['thread'] == stop['thread']`.

### L7. ACT_RESULT is counted as applied even when the result is not replaced
- Where: `fnvmcp_engine.c:592-600` and `:636`.
- What happens: `act_applied` and the record's action byte are set at entry. When the shadow stack is full (`s->count >= SHADOW`) or `shadow_get()` fails, no exit frame is pushed, so the caller gets the real result. fnv_hook_set read still reports the call as `applied: true` (the G11 counter is wrong in that case).
- Fix idea: count ACT_RESULT only once the shadow frame is pushed.

### L8. `_store_pred` keeps a claimed predicate slot when the `pred set` request fails with a LinkError
- Where: `fnv_debugger.py:485-495`. `_claimed_slots.discard` runs only on a refused reply, not when `link.one` raises.
- What happens: each timeout leaks one of the 23 break-condition slots for the life of the server, until TOO_MANY_CONDITIONS.
- Fix idea: use try/except around the `pred set` and discard on any exception.

### L9. The gate stays paused or opens against the user's hold after a main-thread stop
- Where: `w2_stop.c` `stop_park` (`stop_closed_gate`), and `w1_gate.c` `op_hold`.
- What happens:
  1. A main-thread stop that closed the gate itself sets `stop_closed_gate`.
  2. While parked, the user sends `hold off` and later `hold on`.
  3. When the stop is released, `gate_open()` runs because `stop_closed_gate` is still true, and the user's later hold is dropped. The clock-pause count stays balanced.
- Rare (needs hold changes while parked at a main-thread stop).
- Fix idea: clear `stop_closed_gate` whenever `op_hold` changes the gate.

---

## Checked and found sound (not findings)
- Pipe protocol:
  - The request buffer is bounded (REQUEST_MAX, disconnect without a newline).
  - The response overflows into `response_too_large`. `emit_text` escapes to ASCII, and non-finite floats are null.
  - The `pending` 1→2 / 1→0 CAS between the pipe and main threads is atomic.
  - A suspended step/until keeps the pipe busy, but a main-thread stop ends it (`gate_abort_stopped`) and the worker dead-man still counts down, because no new requests are read meanwhile.
  - Python `_io` closes on every LinkError, never resends after a send, and checks the reply id and result count.
- Exit note: max length is 174 bytes, under the 256-byte buffer. `EXIT_NOTE_COMPANION = 3` is checked against the companion version saved at launch, so an older companion never yields the "terminated from outside" note.
- Predicates: the runner checks op sizes and stack bounds again, so a `pred set` racing a hook-thread eval cannot overrun. Loads go through ReadProcessMemory.
- The guarded call's SEH unlink (B9) and on_guard ordering are correct for the main-thread-only guard stack.
- mod unload vs a running handler or frame callback: busy is counted before `fn` is re-read, and `native` is volatile.
- The gate re-entry path (a stop inside `gate_frame_tick` answering a request twice) needs the expression op 0x60, which the bridge's predicate compiler never emits. It is unreachable from the bridge today.
