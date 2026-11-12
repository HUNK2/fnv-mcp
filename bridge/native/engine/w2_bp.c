/* Trap breakpoints (contract FNV-COMPANION-V2.md section 8.2) and the trace (8.4). Included by fnvmcp_engine.c after
 * w2_stop.c. A breakpoint is a debug register (hw, shared with the dbg watchpoints: DR0-3 in all) or an int3 byte
 * (int3, kept in the site table isites). A hit that passes its predicate parks the thread as a stop of kind bp, or,
 * with the log flag, appends its values to the log ring and the thread goes on.
 * Going on past an int3 is a dance: the original byte goes back, the trap flag is set, and the next single step writes
 * CC again (reinstall). A hw hit sets the resume flag instead, so the same instruction does not trap again.
 * Steps, to and the trace run in the single-step exception of the stopped thread (bp_on_single_step). A `to` address
 * and the return address of a traced call are int3 sites owned by one thread.
 * Known limits: during the one instruction after an int3 hit the byte is restored, so another thread can run past
 * the breakpoint then; a fault resumed with step or to retries the faulting instruction; a probe is never added to a
 * breakpoint address. Everything is static: nothing here allocates. */
#define BPS 64u
#define ISITES 96u
#define THREAD_BPS 64u
#define BP_LOG 4096u
#define BP_VALUES 8u
#define TRACE_ENTRIES 4096u
#define TRACE_MAX 200000u
#define BP_FLAG_LOG 1u
#define BP_FLAG_ALL 2u
#define BP_FLAG_ONCE 4u
#define BP_STEP_ID 255u
#define BP_TF 0x100u
#define BP_RF 0x10000u
#define BP_CALL_MAX 15u /* longest instruction: a call's return address is at most this far past the call */
enum { SITE_BP, SITE_TO, SITE_TRACE };
enum { TRACE_NONE, TRACE_RETURNED, TRACE_MAXED };

typedef struct {
    bool used, hw, dr_on, site_on;
    uint32_t addr, pred, flags, hits, dropped, dr_slot;
    uint32_t passed, after; /* hits that passed the predicate; stop only from the after-th one (0 = every one) */
    int site, log_len;
    unsigned char log_code[PRED_BYTES];
} Bp;
typedef struct { bool live; uint32_t addr; unsigned char orig; int bp; DWORD owner; uint32_t purpose; } ISite;
typedef struct { DWORD tid; uint32_t reinstall; bool step; uint32_t step_id; } ThreadBp;
typedef struct { uint32_t n, eip, count, values[BP_VALUES]; } TraceEntry;
typedef struct { uint32_t seq, frame, thread, eip, bp, count, values[BP_VALUES]; } BpEntry;
typedef struct {
    bool pending, active, done;
    DWORD tid;
    uint32_t stop_seq, lo, hi, max, steps, count, reason, start_esp, prev_eip, prev_esp, call_esp, ret_addr;
    int phase, log_len;
    unsigned char log_code[PRED_BYTES];
} Trace;

static Bp bps[BPS];
static uint32_t bp_pending_after[BPS]; /* bp after sent before bp set: taken by the next set of that id */
static ISite isites[ISITES];
static ThreadBp thread_bps[THREAD_BPS];
static BpEntry bp_log[BP_LOG];
static volatile LONG bp_log_seq;
static TraceEntry trace_ring[TRACE_ENTRIES];
static Trace tr;
/* Extra ranges the trace steps into (fnv_flow depth 1: the direct callees with line data); set by `trace ranges`
 * before the resume and cleared when the trace ends. */
#define TRACE_EXTRA 16
static uint32_t tr_extra[TRACE_EXTRA][2];
static int tr_extra_n;

static void tr_begin(CONTEXT *c, DWORD tid); /* defined with the trace below */

static ThreadBp *tb_find(DWORD tid) {
    for (unsigned i = 0; i < THREAD_BPS; i++)
        if (thread_bps[i].tid == tid) return &thread_bps[i];
    return NULL;
}
/* The record of a thread, claimed on first use. A full table shares record 0 (more threads than stops are unlikely). */
static ThreadBp *tb_get(DWORD tid) {
    ThreadBp *t = tb_find(tid);
    if (t) return t;
    for (unsigned i = 0; i < THREAD_BPS; i++) {
        if (!InterlockedCompareExchange((volatile LONG *)&thread_bps[i].tid, (LONG)tid, 0)) {
            thread_bps[i].reinstall = 0;
            thread_bps[i].step = false;
            return &thread_bps[i];
        }
    }
    return &thread_bps[0];
}
static bool tr_on(DWORD tid) { return tr.active && tr.tid == tid; }

/* One code byte, from the trap handlers too. Two threads passing the same site at once (one restoring the original
 * byte, the other putting the 0xCC back) used to fault (N13): VirtualProtect + store races with the other thread
 * putting the page protection back between them. WriteProcessMemory changes the protection itself and fails instead
 * of faulting; it is retried briefly. */
static void byte_set(uint32_t addr, unsigned char value) {
    unsigned char *p = (unsigned char *)(uintptr_t)addr;
    for (int tries = 0; tries < 64 && *p != value; tries++) {
        SIZE_T done = 0;
        if (WriteProcessMemory(GetCurrentProcess(), p, &value, 1, &done) && done == 1) break;
        SwitchToThread();
    }
    FlushInstructionCache(GetCurrentProcess(), p, 1);
}
static bool isite_live(uint32_t addr) {
    for (int i = 0; i < (int)ISITES; i++)
        if (isites[i].live && isites[i].addr == addr) return true;
    return false;
}
/* The live site at addr with this purpose (a to or trace site only for its owner). -1 when none. */
static int isite_find(uint32_t addr, uint32_t purpose, DWORD owner) {
    for (int i = 0; i < (int)ISITES; i++) {
        ISite *s = &isites[i];
        if (s->live && s->addr == addr && s->purpose == purpose && (purpose == SITE_BP || s->owner == owner)) return i;
    }
    return -1;
}
/* Writes CC at addr for a new site. Sites at one address share the original byte, saved by the first of them. */
static int isite_add(uint32_t addr, int bp, DWORD owner, uint32_t purpose) {
    unsigned char orig = 0;
    bool shared = false;
    int slot = -1;
    for (int i = 0; i < (int)ISITES; i++) {
        if (!isites[i].live) {
            if (slot < 0) slot = i;
            continue;
        }
        if (isites[i].addr == addr && !shared) {
            orig = isites[i].orig;
            shared = true;
        }
    }
    if (slot < 0) return -1;
    if (!shared && cov_in_flight(addr, 1)) return -2; /* a coverage hit there would erase the new 0xCC: busy */
    if (!shared) cov_release_range(addr, 1); /* an armed coverage byte is put back first, never saved as orig */
    if (!shared && !read_bytes(addr, &orig, 1)) return -1;
    isites[slot].live = true;
    isites[slot].addr = addr;
    isites[slot].orig = orig;
    isites[slot].bp = bp;
    isites[slot].owner = owner;
    isites[slot].purpose = purpose;
    byte_set(addr, 0xCC);
    return slot;
}
/* Removes a site; the original byte comes back when no other site is left at its address. */
static void isite_remove(int i) {
    uint32_t addr = isites[i].addr;
    unsigned char orig = isites[i].orig;
    isites[i].live = false;
    if (!isite_live(addr)) byte_set(addr, orig);
}

static bool bp_probe_at(uint32_t addr) {
    for (uint32_t i = 0; i < probe_used; i++)
        if (probes[i].target == addr) return true;
    return false;
}
static void trap_context(PredContext *ctx, const CONTEXT *c) {
    ctx->regs[0] = c->Eax;
    ctx->regs[1] = c->Ecx;
    ctx->regs[2] = c->Edx;
    ctx->regs[3] = c->Ebx;
    ctx->regs[4] = c->Esp;
    ctx->regs[5] = c->Ebp;
    ctx->regs[6] = c->Esi;
    ctx->regs[7] = c->Edi;
    ctx->args = (uint32_t)c->Esp + 4; /* the first stack argument of a function entered here */
    ctx->this_ptr = c->Ecx;
}
static bool bp_passes(Bp *b, const CONTEXT *c) {
    PredContext ctx;
    if (b->pred != HOLD_ALWAYS) {
        trap_context(&ctx, c);
        if (!pred_eval((int)b->pred, &ctx, NULL, NULL)) return false;
    }
    b->passed++;
    return !b->after || b->passed >= b->after; /* bp after: the Nth passing hit and every later one */
}
/* Appends one entry to the shared log ring (4096 entries; an overwritten entry counts as dropped for its bp). */
static void bp_log_push(uint32_t id, const CONTEXT *c, Bp *b) {
    BpEntry e;
    PredContext ctx;
    uint32_t values[BP_VALUES];
    bool floats[BP_VALUES];
    memset(&e, 0, sizeof(e));
    trap_context(&ctx, c);
    if (b->log_len > 0) {
        int depth = pred_run_values(b->log_code, (size_t)b->log_len, &ctx, values, floats, (int)BP_VALUES);
        if (depth > 0) {
            e.count = depth < (int)BP_VALUES ? (uint32_t)depth : BP_VALUES;
            memcpy(e.values, values, e.count * sizeof(uint32_t));
        }
    }
    e.seq = (uint32_t)InterlockedIncrement(&bp_log_seq);
    e.frame = (uint32_t)frame;
    e.thread = GetCurrentThreadId();
    e.eip = (uint32_t)c->Eip;
    e.bp = id;
    BpEntry *slot = &bp_log[(e.seq - 1) % BP_LOG];
    if (slot->seq && slot->bp < BPS) bps[slot->bp].dropped++;
    *slot = e;
}

/* The trap flag: set while the thread single-steps (a user step, a trace step, or a pending reinstall). */
static void tf_fix(CONTEXT *c, DWORD tid) {
    ThreadBp *t = tb_find(tid);
    bool want = (tr_on(tid) && tr.phase == 0) || (t && (t->step || t->reinstall));
    if (want) c->EFlags |= BP_TF;
    else c->EFlags &= ~BP_TF;
}
/* Return from the current function now (contract 8.4 ret): eax = value, esp += 4 + pop, eip = [esp]. */
static void bp_return(CONTEXT *c, uint32_t value, uint32_t pop) {
    uint32_t sp = (uint32_t)c->Esp, ret = 0;
    if (!read_u32(sp, &ret)) return;
    c->Eax = value;
    c->Esp = sp + 4 + pop;
    c->Eip = ret;
}
/* Applies the resume that released a bp or step stop of this thread to its live context. */
static void bp_after(CONTEXT *c, const StopOutcome *out, uint32_t id, DWORD tid) {
    ThreadBp *t = tb_get(tid);
    t->step = false;
    switch (out->mode) {
    case RESUME_STEP:
        t->step = true;
        t->step_id = id;
        break;
    case RESUME_EIP:
        c->Eip = out->arg;
        break;
    case RESUME_RET:
        bp_return(c, out->arg, out->arg2);
        break;
    case RESUME_TRACE:
        tr_begin(c, tid);
        break;
    default:
        break;
    }
}
/* Parks the thread as a stop and applies its resume. Returns the stop's seq (0 when the stop was not taken). */
static uint32_t bp_park(CONTEXT *c, EXCEPTION_RECORD *rec, uint32_t kind, uint32_t id, DWORD tid) {
    StopOutcome out = {0, 0, 0, false};
    if (exiting || in_served_call()) return 0;
    int slot = stop_park(kind, id, (uint32_t)c->Eip, 0, (uint32_t)(uintptr_t)c, rec ? (uint32_t)(uintptr_t)rec : 0, 0,
                         true, &out);
    if (slot < 0) return 0;
    uint32_t seq = stops_table[slot].seq;
    stop_free(slot);
    dbg_fill(c, tid); /* breakpoints set or cleared while parked: the CONTEXT is what the thread resumes with */
    bp_after(c, &out, id, tid);
    return seq;
}

/* Steps over a live int3 at addr: the original byte goes back, eip stays at addr, and the next single step writes CC
 * again (reinstall). The trap flag is set by tf_fix. */
static void bp_pass(CONTEXT *c, DWORD tid, uint32_t addr, unsigned char orig) {
    byte_set(addr, orig);
    c->Eip = addr;
    tb_get(tid)->reinstall = addr;
}

/* ---------------------------------------------------------------------------------------------- trace */
static bool tr_in(uint32_t eip) {
    if (eip >= tr.lo && eip < tr.hi) return true;
    for (int i = 0; i < tr_extra_n; i++)
        if (eip >= tr_extra[i][0] && eip < tr_extra[i][1]) return true;
    return false;
}

/* Records the entry of the instruction at the context's eip (before it runs). */
static void tr_record(const CONTEXT *c) {
    TraceEntry *e = &trace_ring[tr.count % TRACE_ENTRIES];
    PredContext ctx;
    bool floats[BP_VALUES];
    trap_context(&ctx, c);
    memset(e, 0, sizeof(*e));
    e->n = tr.count + 1;
    e->eip = (uint32_t)c->Eip;
    if (tr.log_len > 0) {
        int depth = pred_run_values(tr.log_code, (size_t)tr.log_len, &ctx, e->values, floats, (int)BP_VALUES);
        if (depth > 0) e->count = depth < (int)BP_VALUES ? (uint32_t)depth : BP_VALUES;
    }
    tr.count++;
}
/* Arms the next step at the context's eip; false when the step budget is spent. */
static bool tr_arm(CONTEXT *c) {
    if (tr.steps >= tr.max) return false;
    if (tr_in((uint32_t)c->Eip)) tr_record(c);
    tr.steps++;
    tr.prev_eip = (uint32_t)c->Eip;
    tr.prev_esp = (uint32_t)c->Esp;
    c->EFlags |= BP_TF;
    return true;
}
/* Ends the trace and parks the thread as a step stop (id 255). The stop's seq is what trace read takes. */
static void tr_end(CONTEXT *c, DWORD tid, uint32_t reason) {
    uint32_t seq;
    tr.active = false;
    tr.done = true;
    tr.reason = reason;
    tr_extra_n = 0;
    for (int i = 0; i < (int)ISITES; i++)
        if (isites[i].live && isites[i].purpose == SITE_TRACE && isites[i].owner == tid) isite_remove(i);
    /* trace read takes the seq while the thread is parked, so it is set before the park: the seq the stop will get */
    tr.stop_seq = (uint32_t)stop_seq + 1;
    seq = bp_park(c, NULL, STOP_STEP, BP_STEP_ID, tid);
    if (seq) tr.stop_seq = seq;
}
/* Starts the trace prepared by resume trace, from the stop's context. */
static void tr_begin(CONTEXT *c, DWORD tid) {
    if (!tr.pending) return;
    tr.pending = false;
    tr.active = true;
    tr.done = false;
    tr.reason = TRACE_NONE;
    tr.tid = tid;
    tr.phase = 0;
    tr.steps = 0;
    tr.count = 0;
    tr.start_esp = (uint32_t)c->Esp;
    if (!tr_in((uint32_t)c->Eip)) {
        tr_end(c, tid, TRACE_RETURNED);
        return;
    }
    if (!tr_arm(c)) tr_end(c, tid, TRACE_MAXED);
}
/* One single step of the trace has completed: the new eip is in the range (next entry), a call out of it (its callee is
 * run without stepping, and a temporary site on the return address resumes the trace), or anything else (the trace
 * ends: a return out of the range). */
static void tr_step(CONTEXT *c, DWORD tid) {
    uint32_t ret = 0;
    if (tr_in((uint32_t)c->Eip)) {
        if (!tr_arm(c)) tr_end(c, tid, TRACE_MAXED);
        return;
    }
    if ((uint32_t)c->Esp == tr.prev_esp - 4 && read_u32((uint32_t)c->Esp, &ret) && ret > tr.prev_eip &&
        ret <= tr.prev_eip + BP_CALL_MAX) {
        if (isite_add(ret, -1, tid, SITE_TRACE) >= 0) {
            tr.phase = 1;
            tr.call_esp = (uint32_t)c->Esp;
            tr.ret_addr = ret;
            return;
        }
    }
    tr_end(c, tid, TRACE_RETURNED);
}
/* The return site of a traced call was hit by its owner: resume the trace once the callee has returned (esp above the
 * call's esp), or step over it again when a deeper call of the same address returns first. */
static void tr_return(CONTEXT *c, DWORD tid, int site) {
    uint32_t addr = isites[site].addr;
    if ((uint32_t)c->Esp < tr.call_esp + 4) {
        bp_pass(c, tid, addr, isites[site].orig);
        return;
    }
    isite_remove(site);
    tr.phase = 0;
    if (!tr_in((uint32_t)c->Eip)) {
        tr_end(c, tid, TRACE_RETURNED);
        return;
    }
    if (!tr_arm(c)) tr_end(c, tid, TRACE_MAXED);
}

/* ---------------------------------------------------------------------------------------------- traps */
/* An int3 hit of a breakpoint (site bp). Passing hits go on (bp_pass); a hit that passes the predicate parks. */
static void bp_hit(CONTEXT *c, EXCEPTION_RECORD *rec, int site, DWORD tid) {
    uint32_t id = (uint32_t)isites[site].bp, addr = isites[site].addr;
    unsigned char orig = isites[site].orig;
    Bp *b = &bps[id];
    bool hit;
    b->hits++;
    hit = bp_passes(b, c);
    if (hit && (b->flags & BP_FLAG_ONCE)) {
        isite_remove(site);
        b->used = false;
        b->site_on = false;
        c->Eip = addr;
    } else {
        bp_pass(c, tid, addr, orig);
    }
    if (!hit) return;
    if (b->flags & BP_FLAG_LOG) {
        bp_log_push(id, c, b);
        return;
    }
    if (tr_on(tid) || in_served_call() || exiting) return;
    SEAM(3); /* tests: the trap is passed, the stop not registered yet */
    bp_park(c, rec, STOP_BP, id, tid);
}
/* A debug-register hit of breakpoint id. The resume flag is set in every case, so the instruction does not trap again. */
static bool bp_hw_hit(CONTEXT *c, EXCEPTION_RECORD *rec, uint32_t id, DWORD tid) {
    Bp *b = &bps[id];
    b->hits++;
    if (!bp_passes(b, c)) return false;
    if (b->flags & BP_FLAG_ONCE) b->used = false;
    if (b->flags & BP_FLAG_LOG) {
        bp_log_push(id, c, b);
        return false;
    }
    if (tr_on(tid) || in_served_call() || exiting) return false;
    bp_park(c, rec, STOP_BP, id, tid);
    return true;
}

/* Int3 traps that belong to breakpoints, `to` and trace sites. False for every other int3 (probes, the game's own). */
static bool bp_on_breakpoint(EXCEPTION_POINTERS *e, LONG *result) {
    CONTEXT *c = e->ContextRecord;
    uint32_t addr = (uint32_t)(uintptr_t)e->ExceptionRecord->ExceptionAddress;
    DWORD tid = GetCurrentThreadId();
    int bp = -1, to = -1, ret_site = -1, first = -1;
    if (!isite_live(addr)) return false;
    for (int i = 0; i < (int)ISITES; i++) {
        ISite *s = &isites[i];
        if (!s->live || s->addr != addr) continue;
        if (first < 0) first = i;
        if (s->purpose == SITE_BP) bp = i;
        else if (s->owner == tid && s->purpose == SITE_TO) to = i;
        else if (s->owner == tid && s->purpose == SITE_TRACE) ret_site = i;
    }
    c->Eip = addr;
    if (bp >= 0) {
        bp_hit(c, e->ExceptionRecord, bp, tid);
        if (to >= 0 && isites[to].live) isite_remove(to); /* a breakpoint hit ends a pending to at this address */
    } else if (to >= 0) {
        isite_remove(to);
        bp_park(c, NULL, STOP_STEP, BP_STEP_ID, tid);
    } else if (ret_site >= 0 && tr.active && tr.tid == tid && tr.phase == 1) {
        tr_return(c, tid, ret_site);
    } else {
        bp_pass(c, tid, addr, isites[first].orig);
    }
    tf_fix(c, tid);
    *result = EXCEPTION_CONTINUE_EXECUTION;
    return true;
}

/* Single-step exceptions of the breakpoint machinery: hw hits (DR6), reinstalls, user steps and the trace. False when the
 * exception is none of these (the watchpoints then take it). */
static bool bp_on_single_step(EXCEPTION_POINTERS *e, LONG *result) {
    CONTEXT *c = e->ContextRecord;
    DWORD tid = GetCurrentThreadId();
    uint32_t dr6 = (uint32_t)c->Dr6, hw = dr6 & dbg_bp_slots & 0xFu, watch = dr6 & dbg_active & 0xFu & ~dbg_bp_slots;
    ThreadBp *t = tb_find(tid);
    bool parked = false;
    if (!hw && !watch && (dr6 & 0xFu & ~dbg_active) && !tr_on(tid) && !(t && (t->reinstall || t->step))) {
        /* A hit on a debug register that no breakpoint or watchpoint owns any more (cleared while this thread was
         * parked, or before the clear reached it): bring the thread's registers up to date and go on. */
        dbg_fill(c, tid);
        c->Dr6 = 0;
        c->EFlags |= BP_RF;
        *result = EXCEPTION_CONTINUE_EXECUTION;
        return true;
    }
    if (!hw && !tr_on(tid) && !(t && (t->reinstall || t->step))) return false;
    if (watch) dbg_note(e, watch);
    c->Dr6 = 0;
    t = tb_get(tid);
    if (t->reinstall) {
        uint32_t a = t->reinstall;
        t->reinstall = 0;
        if (isite_live(a)) byte_set(a, 0xCC);
    }
    if (hw) {
        c->EFlags |= BP_RF;
        for (uint32_t k = 0; k < 4u && !parked; k++) {
            if (!(hw & (1u << k))) continue;
            for (uint32_t id = 0; id < BPS; id++) {
                if (!bps[id].used || !bps[id].dr_on || bps[id].dr_slot != k) continue;
                parked = bp_hw_hit(c, e->ExceptionRecord, id, tid);
                break;
            }
        }
    }
    if (!parked) {
        if (tr_on(tid)) {
            if (tr.phase == 0) tr_step(c, tid);
        } else if (t->step) {
            t->step = false;
            bp_park(c, NULL, STOP_STEP, t->step_id, tid);
        }
    }
    tf_fix(c, tid);
    *result = EXCEPTION_CONTINUE_EXECUTION;
    return true;
}

/* A fault stop resumed with step, to or trace (w2_stop.c stop_fault). The faulting instruction runs again (known limit). */
static LONG bp_fault_resume(CONTEXT *c, const StopOutcome *out) {
    DWORD tid = GetCurrentThreadId();
    ThreadBp *t = tb_get(tid);
    t->step = out->mode == RESUME_STEP;
    t->step_id = BP_STEP_ID;
    if (out->mode == RESUME_TRACE) tr_begin(c, tid);
    tf_fix(c, tid);
    return EXCEPTION_CONTINUE_EXECUTION;
}

/* Checks and prepares a resume that needs the thread's state: to installs its site, trace checks the arguments and
 * leaves the trace pending (tr_begin starts it in the thread). Returns an error name or NULL. */
static const char *bp_resume_prepare(const Stop *s, uint32_t mode, uint32_t arg, int argc, char **argv) {
    if (mode == RESUME_TO) {
        int site;
        if (arg < 0x10000) return "bad_arguments";
        if (isite_find(arg, SITE_TO, s->thread) >= 0) return NULL;
        if (bp_probe_at(arg)) return "in_use";
        probe_suspend();
        site = isite_add(arg, -1, s->thread, SITE_TO);
        probe_resume();
        return site == -2 ? "busy" : site < 0 ? "unreadable" : NULL;
    }
    if (mode == RESUME_TRACE) {
        uint32_t max = 0, lo = 0, hi = 0;
        long len;
        unsigned ops = 0;
        const char *error;
        if (tr.active || tr.pending) return "in_use";
        if (argc < 6 || argc > 7 || !parse_u32(argv[3], &max) || !parse_u32(argv[4], &lo) || !parse_u32(argv[5], &hi) ||
            max < 1 || max > TRACE_MAX || hi <= lo)
            return "bad_arguments";
        tr.log_len = 0;
        if (argc == 7) {
            len = unhex(argv[6]);
            if (len < 0 || len > (long)PRED_BYTES) return "bad_arguments";
            error = pred_check((const unsigned char *)argv[6], (size_t)len, &ops);
            if (error) return error;
            memcpy(tr.log_code, argv[6], (size_t)len);
            tr.log_len = (int)len;
        }
        tr.lo = lo;
        tr.hi = hi;
        tr.max = max;
        tr.tid = s->thread;
        tr.pending = true;
        return NULL;
    }
    return NULL;
}

/* Undoes the setup of a resume whose release lost the race to another resume. */
static void bp_resume_undo(const Stop *s, uint32_t mode, uint32_t arg) {
    if (mode == RESUME_TRACE) tr.pending = false;
    if (mode == RESUME_TO) {
        int site = isite_find(arg, SITE_TO, s->thread);
        if (site >= 0) {
            probe_suspend();
            isite_remove(site);
            probe_resume();
        }
    }
}

/* ---------------------------------------------------------------------------------------------- ops */
/* Clears one breakpoint (its int3 site or debug register). True when a debug register changed (dbg_apply needed). */
static bool bp_drop(uint32_t id) {
    Bp *b = &bps[id];
    bool dr = false;
    if (b->site_on) {
        probe_suspend();
        isite_remove(b->site);
        probe_resume();
    }
    if (b->dr_on) {
        uint32_t bit = 1u << b->dr_slot;
        dbg_active &= ~bit;
        dbg_bp_slots &= ~bit;
        dr = true;
    }
    memset(b, 0, sizeof(*b));
    return dr;
}
/* Frees the debug registers of breakpoints that fired once (their slot stays until the next op). */
static void bp_reap(void) {
    bool changed = false;
    for (uint32_t id = 0; id < BPS; id++) {
        if (bps[id].used || !bps[id].dr_on) continue;
        if (bp_drop(id)) changed = true;
    }
    if (changed) dbg_apply();
}
/* bp set <id> <addr> hw|int3 <pred|ff> <flags> [log_hex] */
static void op_bp_set(int argc, char **argv) {
    uint32_t id = 0, addr = 0, pred = 0, flags = 0, k = 0;
    bool hw;
    unsigned char probe_byte, log_code[PRED_BYTES] = {0};
    int log_len = 0, site = -1;
    bool dr = false;
    if ((argc != 7 && argc != 8) || !parse_u32(argv[2], &id) || id >= BPS || !parse_u32(argv[3], &addr))
        return fail("bad_arguments");
    if (!strcmp(argv[4], "hw")) hw = true;
    else if (!strcmp(argv[4], "int3")) hw = false;
    else return fail("bad_arguments");
    if (!strcmp(argv[5], "ff")) pred = HOLD_ALWAYS;
    else if (!parse_u32(argv[5], &pred)) return fail("bad_arguments");
    else if (pred >= PRED_SLOTS || !preds[pred].used) return fail("bad_id");
    if (!parse_u32(argv[6], &flags) || flags > 7) return fail("bad_arguments");
    if (argc == 8) {
        unsigned ops = 0;
        long len;
        const char *error;
        if (!(flags & BP_FLAG_LOG)) return fail("bad_arguments");
        len = unhex(argv[7]);
        if (len < 0 || len > (long)PRED_BYTES) return fail("bad_arguments");
        error = pred_check((const unsigned char *)argv[7], (size_t)len, &ops);
        if (error) return fail(error);
        memcpy(log_code, argv[7], (size_t)len);
        log_len = (int)len;
    }
    if (addr < 0x10000 || !read_bytes(addr, &probe_byte, 1)) return fail("unreadable");
    if (!hw && bp_probe_at(addr)) return fail("in_use");
    if (bps[id].used || bps[id].dr_on || bps[id].site_on) dr = bp_drop(id);
    if (hw) {
        while (k < 4u && (dbg_active & (1u << k))) k++; /* the first free debug register */
        if (k == 4) {
            if (dr) dbg_apply();
            return fail("no_debug_register");
        }
    } else {
        probe_suspend();
        site = isite_add(addr, (int)id, 0, SITE_BP);
        probe_resume();
        if (site < 0) return fail(site == -2 ? "busy" : "unreadable");
    }
    Bp *b = &bps[id];
    b->used = true;
    b->hw = hw;
    b->addr = addr;
    b->pred = pred;
    b->flags = flags;
    b->log_len = log_len;
    b->passed = 0;
    b->after = bp_pending_after[id]; /* armed with its count: a hit right after the set cannot stop early */
    bp_pending_after[id] = 0;
    memcpy(b->log_code, log_code, sizeof(log_code));
    if (hw) {
        uint32_t bit = 1u << k;
        dbg_addr[k] = addr;
        dbg_len[k] = 1;
        dbg_rw[k] = 0; /* execute */
        dbg_scope[k] = (flags & BP_FLAG_ALL) ? 1u : 0u;
        dbg_active |= bit;
        dbg_bp_slots |= bit;
        b->dr_on = true;
        b->dr_slot = k;
        dr = true;
    } else {
        b->site_on = true;
        b->site = site;
    }
    if (dr) dbg_apply();
    emit("{\"ok\":true,\"id\":%u,\"kind\":\"%s\"}", id, hw ? "hw" : "int3");
}
/* bp clear <id|ff> */
static void op_bp_clear(int argc, char **argv) {
    uint32_t id = 0;
    unsigned cleared = 0, to_sites = 0;
    bool dr = false;
    if (argc != 3) return fail("bad_arguments");
    if (!strcmp(argv[2], "ff")) {
        for (id = 0; id < BPS; id++)
            if (bps[id].used || bps[id].dr_on || bps[id].site_on) {
                dr |= bp_drop(id);
                cleared++;
            }
        probe_suspend();
        for (int i = 0; i < (int)ISITES; i++)
            if (isites[i].live && isites[i].purpose == SITE_TO) { isite_remove(i); to_sites++; }
        probe_resume();
    } else {
        if (!parse_u32(argv[2], &id) || id >= BPS) return fail("bad_arguments");
        if (!bps[id].used && !bps[id].dr_on && !bps[id].site_on) return fail("bad_id");
        dr = bp_drop(id);
        cleared = 1;
    }
    if (dr) dbg_apply();
    emit("{\"ok\":true,\"cleared\":%u,\"toSites\":%u}", cleared, to_sites);
}
static void op_bp_list(void) {
    bool comma = false;
    emit("{\"bps\":[");
    for (uint32_t i = 0; i < BPS; i++) {
        const Bp *b = &bps[i];
        if (!b->used) continue;
        emit("%s{\"id\":%u,\"addr\":\"0x%08x\",\"kind\":\"%s\",\"hits\":%u,\"pred\":", comma ? "," : "", i, b->addr,
             b->hw ? "hw" : "int3", b->hits);
        if (b->pred == HOLD_ALWAYS) emit("\"ff\"");
        else emit("%u", b->pred);
        emit(",\"flags\":%u,\"passed\":%u,\"after\":%u}", b->flags, b->passed, b->after);
        comma = true;
    }
    emit("]}");
}
/* bp log <id> <since> [max]: the entries of this breakpoint with seq above since. next: resume from here. */
static void op_bp_log(int argc, char **argv) {
    uint32_t id = 0, since = 0, max = 256, count = 0, first, last, upto, q;
    bool comma = false;
    if ((argc != 4 && argc != 5) || !parse_u32(argv[2], &id) || id >= BPS || !parse_u32(argv[3], &since))
        return fail("bad_arguments");
    if (argc == 5 && (!parse_u32(argv[4], &max) || max == 0 || max > BP_LOG)) return fail("bad_arguments");
    last = (uint32_t)bp_log_seq;
    first = since + 1;
    if (last >= BP_LOG && first < last - BP_LOG + 1) first = last - BP_LOG + 1;
    upto = last;
    emit("{\"ok\":true,\"entries\":[");
    for (q = first; q <= last; q++) {
        const BpEntry *e = &bp_log[(q - 1) % BP_LOG];
        if (e->seq != q || e->bp != id) continue;
        if (count == max) {
            upto = q - 1;
            break;
        }
        emit("%s{\"seq\":%u,\"frame\":%u,\"thread\":%u,\"eip\":\"0x%08x\",\"values\":[", comma ? "," : "", e->seq,
             e->frame, e->thread, e->eip);
        for (uint32_t k = 0; k < e->count; k++) emit("%s\"0x%08x\"", k ? "," : "", e->values[k]);
        emit("]}");
        comma = true;
        count++;
    }
    emit("],\"next\":%u,\"dropped\":%u}", upto, bps[id].dropped);
}
/* bp after <id> <n>: the breakpoint acts (stops or logs) only from the n-th hit that passes its predicate (0 = every
   hit). On a set breakpoint it resets the passing count; on a free id it is kept for the next bp set of that id, so the
   count is in place before the first hit (send it first). */
static void op_bp_after(int argc, char **argv) {
    uint32_t id = 0, n = 0;
    if (argc != 4 || !parse_u32(argv[2], &id) || id >= BPS || !parse_u32(argv[3], &n)) return fail("bad_arguments");
    if (!bps[id].used) {
        bp_pending_after[id] = n;
        emit("{\"ok\":true,\"id\":%u,\"after\":%u,\"pending\":true}", id, n);
        return;
    }
    bps[id].passed = 0;
    bps[id].after = n;
    emit("{\"ok\":true,\"id\":%u,\"after\":%u}", id, n);
}
static void op_bp(int argc, char **argv) {
    bp_reap();
    if (argc >= 2 && !strcmp(argv[1], "set")) return op_bp_set(argc, argv);
    if (argc == 3 && !strcmp(argv[1], "clear")) return op_bp_clear(argc, argv);
    if (argc == 2 && !strcmp(argv[1], "list")) return op_bp_list();
    if (argc >= 4 && !strcmp(argv[1], "log")) return op_bp_log(argc, argv);
    if (argc == 4 && !strcmp(argv[1], "after")) return op_bp_after(argc, argv);
    fail("bad_arguments");
}
/* trace ranges <hex>: packed (lo, hi) u32 pairs, at most 16, that a following resume trace also steps into (empty
 * hex "-" clears them). trace read <seq> <since> [max]: the entries of the trace that ended at stop seq, with n above
 * since. */
static void op_trace(int argc, char **argv) {
    uint32_t seq = 0, since = 0, max = 256, first, last, upto, n;
    bool comma = false;
    if (argc == 3 && !strcmp(argv[1], "ranges")) {
        long len = strcmp(argv[2], "-") ? unhex(argv[2]) : 0;
        if (tr.active || tr.pending) return fail("in_use");
        if (len < 0 || len % 8 || len / 8 > TRACE_EXTRA) return fail("bad_arguments");
        tr_extra_n = 0;
        for (long i = 0; i < len / 8; i++) {
            uint32_t lo, hi;
            memcpy(&lo, argv[2] + i * 8, 4);
            memcpy(&hi, argv[2] + i * 8 + 4, 4);
            if (hi <= lo) return fail("bad_arguments");
            tr_extra[tr_extra_n][0] = lo;
            tr_extra[tr_extra_n][1] = hi;
            tr_extra_n++;
        }
        emit("{\"ok\":true,\"ranges\":%d}", tr_extra_n);
        return;
    }
    if (argc < 4 || argc > 5 || strcmp(argv[1], "read") || !parse_u32(argv[2], &seq) || !parse_u32(argv[3], &since))
        return fail("bad_arguments");
    if (argc == 5 && (!parse_u32(argv[4], &max) || max == 0 || max > TRACE_ENTRIES)) return fail("bad_arguments");
    if (!tr.stop_seq || seq != tr.stop_seq) return fail("no_trace");
    last = tr.count;
    first = since + 1;
    if (last > TRACE_ENTRIES && first < last - TRACE_ENTRIES + 1) first = last - TRACE_ENTRIES + 1;
    upto = last;
    emit("{\"ok\":true,\"entries\":[");
    for (n = first; n <= last; n++) {
        const TraceEntry *e = &trace_ring[(n - 1) % TRACE_ENTRIES];
        if (max == 0) {
            upto = n - 1;
            break;
        }
        emit("%s{\"n\":%u,\"eip\":\"0x%08x\",\"values\":[", comma ? "," : "", e->n, e->eip);
        for (uint32_t k = 0; k < e->count; k++) emit("%s\"0x%08x\"", k ? "," : "", e->values[k]);
        emit("]}");
        comma = true;
        max--;
    }
    emit("],\"next\":%u,\"done\":%s,\"reason\":", upto, tr.done ? "true" : "false");
    if (tr.reason == TRACE_RETURNED) emit("\"returned\"");
    else if (tr.reason == TRACE_MAXED) emit("\"max\"");
    else emit("null");
    emit("}");
}
