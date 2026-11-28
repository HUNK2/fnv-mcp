/* Input injector (contract FNV-COMPANION-V2.md section 7). Included by fnvmcp_engine.c after w1_guard.c.
 * inpcfg puts a jump at the entry of the engine's input poll (InputPoller::Controls_UpdateValues, thiscall, no stack
 * arguments) to fnvmcp_inp_wrap. The wrapper calls a trampoline (the relocated first bytes of the poll, then a jump
 * back into it), then runs fnvmcp_inp_after, which applies the jobs to OSInputGlobals, and returns to the poll's
 * caller with eax, edx and st0 as the poll left them. Jobs are only ever applied on the main thread. */
#define INP_JOBS 32u
#define INP_KEY 1
#define INP_BTN 2
#define INP_MOUSE 3
#define INP_CTL 4
#define INP_KEYS 0x18F8u        /* OSInputGlobals: key down bytes, indexed by DIK code */
#define INP_MOUSE_DELTA 0x1B24u /* two int32: mouse dx, dy */
#define INP_BUTTONS 0x1B30u     /* mouse button bytes */
#define INP_CONTROL_MAX 12u

typedef struct { uint8_t kind, used; uint32_t code, frames_left; int32_t dx, dy; } InpJob;
static InpJob inp_jobs[INP_JOBS];
static uint32_t inp_osptr, inp_force;
static bool inp_installed;
/* Read by the wrapper's assembly: the trampoline address, and the caller's return address while the poll runs. */
volatile uint32_t fnvmcp_inp_tramp, fnvmcp_inp_saved_ret;
extern void fnvmcp_inp_wrap(void);
__asm__(
    ".text\n"
    ".globl _fnvmcp_inp_wrap\n"
    "_fnvmcp_inp_wrap:\n"
    "  popl _fnvmcp_inp_saved_ret\n"
    "  call *_fnvmcp_inp_tramp\n"
    "  pushal\n"
    "  pushfl\n"
    "  call _fnvmcp_inp_after\n"
    "  popfl\n"
    "  popal\n"
    "  pushl _fnvmcp_inp_saved_ret\n"
    "  ret\n");

static uint32_t inp_globals(void) {
    uint32_t globals = 0;
    if (!inp_osptr || !read_u32(inp_osptr, &globals) || globals < 0x10000u) return 0;
    return globals;
}
/* Ends a job: a key or a button goes back to 0 (the engine's next poll reads the device again anyway). */
static void inp_release_job(InpJob *j, uint32_t globals) {
    uint8_t zero = 0;
    if (globals && j->kind == INP_KEY) write_bytes(globals + INP_KEYS + j->code, &zero, 1);
    if (globals && j->kind == INP_BTN) write_bytes(globals + INP_BUTTONS + j->code, &zero, 1);
    j->used = 0;
}
static void inp_release_all(void) {
    uint32_t globals = inp_globals();
    for (unsigned i = 0; i < INP_JOBS; i++)
        if (inp_jobs[i].used) inp_release_job(&inp_jobs[i], globals);
}
static void inp_fault(const char *reason) {
    inp_release_all();
    record_event(EVENT_INJECT, reason);
}
/* One poll's worth of jobs. Each job applies once per poll and ends after its frame count. */
static void inp_apply(void) {
    uint32_t globals = inp_globals();
    if (!globals) return inp_fault("inject: OSInputGlobals is not readable; jobs released");
    for (unsigned i = 0; i < INP_JOBS; i++) {
        InpJob *j = &inp_jobs[i];
        uint8_t down = 0x80;
        bool ok = true;
        if (!j->used) continue;
        if (j->kind == INP_KEY) {
            ok = write_bytes(globals + INP_KEYS + j->code, &down, 1);
        } else if (j->kind == INP_BTN) {
            ok = write_bytes(globals + INP_BUTTONS + j->code, &down, 1);
        } else if (j->kind == INP_MOUSE) {
            uint32_t delta[2];
            ok = read_bytes(globals + INP_MOUSE_DELTA, delta, sizeof(delta));
            if (ok) {
                delta[0] += (uint32_t)j->dx;
                delta[1] += (uint32_t)j->dy;
                ok = write_bytes(globals + INP_MOUSE_DELTA, delta, sizeof(delta));
            }
        } else {
            GuardFrame g;
            CallResult result;
            uint32_t control = j->code;
            ok = inp_force && guard_invoke(&g, inp_force, &control, 1, globals, 0, false, &result) == 0;
        }
        if (!ok) return inp_fault("inject: a job failed; all jobs released");
        if (--j->frames_left == 0) inp_release_job(j, globals);
    }
}
/* Called by fnvmcp_inp_wrap after every poll. */
void fnvmcp_inp_after(void) {
    for (unsigned i = 0; i < INP_JOBS; i++)
        if (inp_jobs[i].used) {
            inp_apply();
            return;
        }
}

/* inpcfg <poll_fn> <stolen> <osinput_ptr> <force_fn>. stolen is the number of bytes at the poll's entry that the
 * trampoline copies; they must be whole instructions without relative branches (the bridge picks them). */
static void op_inpcfg(int argc, char **argv) {
    uint32_t poll, stolen, osptr, force;
    unsigned char original[16], patch[16];
    MEMORY_BASIC_INFORMATION info;
    if (argc != 5 || !parse_u32(argv[1], &poll) || !parse_u32(argv[2], &stolen) || !parse_u32(argv[3], &osptr) ||
        !parse_u32(argv[4], &force))
        return fail("bad_arguments");
    if (inp_installed) return fail("already_installed");
    if (poll < 0x10000u || stolen < 5 || stolen > 16 || !osptr) return fail("bad_arguments");
    if (!VirtualQuery((LPCVOID)(uintptr_t)poll, &info, sizeof(info)) || info.State != MEM_COMMIT ||
        !(info.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)))
        return fail("not_executable");
    for (int i = 0; i < hook_count; i++)
        if (hooks[i]->target == poll && hooks[i]->stolen) return fail("already_hooked");
    /* (BUG-012) as `code patch` does: no live int3, hook, probe or earlier patch in the bytes, no coverage 0xCC saved */
    if (isite_in(poll, stolen) || patched_range(poll, stolen)) return fail("in_use");
    cov_release_range(poll, stolen);
    if (!read_bytes(poll, original, stolen)) return fail("unreadable");
    if (!hook_code) hook_code = VirtualAlloc(NULL, 65536, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!hook_code || hook_code_used + 32 > 65536) return fail("out_of_memory");
    unsigned char *tramp = hook_code + hook_code_used;
    hook_code_used += 32;
    memcpy(tramp, original, stolen);
    tramp[stolen] = 0xE9;
    uint32_t rel = poll + stolen - (uint32_t)(uintptr_t)(tramp + stolen + 5);
    memcpy(tramp + stolen + 1, &rel, 4);
    memset(patch, 0x90, sizeof(patch));
    patch[0] = 0xE9;
    rel = (uint32_t)(uintptr_t)fnvmcp_inp_wrap - (poll + 5);
    memcpy(patch + 1, &rel, 4);
    fnvmcp_inp_tramp = (uint32_t)(uintptr_t)tramp;
    inp_osptr = osptr;
    inp_force = force;
    probe_suspend(); /* the jump is written with the other threads frozen, as the probes do */
    bool busy = probe_thread_inside(poll, poll + stolen);
    bool written = !busy && code_write(poll, patch, stolen);
    probe_resume();
    if (busy) return fail("busy");
    if (!written) return fail("unwritable");
    code_site_note(poll, stolen, original, patch); /* later hooks, probes, breakpoints and coverage refuse these bytes */
    inp_installed = true;
    emit("{\"ok\":true,\"installed\":true}");
}
/* Adds one job; returns false (after failing the op) when it cannot be added. */
static bool inp_add(uint8_t kind, uint32_t code, uint32_t frames, int32_t dx, int32_t dy) {
    const char *error = NULL;
    if (!inp_installed) error = "not_installed";
    else if (!frames || frames > GATE_FRAMES_MAX) error = "bad_arguments";
    for (unsigned i = 0; !error && i < INP_JOBS; i++)
        if (!inp_jobs[i].used) {
            inp_jobs[i] = (InpJob){kind, 1, code, frames, dx, dy};
            return true;
        }
    fail(error ? error : "too_many_jobs");
    return false;
}
static const char *inp_kind_name(uint8_t kind) {
    return kind == INP_KEY ? "key" : kind == INP_BTN ? "btn" : kind == INP_MOUSE ? "mouse" : "ctl";
}
/* inp key|btn|mouse|ctl ... | inp clear | inp */
static void op_inp(int argc, char **argv) {
    uint32_t code = 0, frames = 0, dx = 0, dy = 0;
    if (argc == 1) {
        bool comma = false;
        emit("{\"jobs\":[");
        for (unsigned i = 0; i < INP_JOBS; i++) {
            if (!inp_jobs[i].used) continue;
            emit("%s{\"kind\":\"%s\",\"code\":%u,\"framesLeft\":%u}", comma ? "," : "", inp_kind_name(inp_jobs[i].kind),
                 inp_jobs[i].code, inp_jobs[i].frames_left);
            comma = true;
        }
        emit("],\"installed\":%s}", inp_installed ? "true" : "false");
        return;
    }
    if (argc == 2 && !strcmp(argv[1], "clear")) {
        unsigned cleared = 0;
        for (unsigned i = 0; i < INP_JOBS; i++) cleared += inp_jobs[i].used;
        inp_release_all();
        return emit("{\"ok\":true,\"cleared\":%u}", cleared);
    }
    if (argc == 4 && (!strcmp(argv[1], "key") || !strcmp(argv[1], "btn") || !strcmp(argv[1], "ctl"))) {
        bool key = !strcmp(argv[1], "key"), btn = !strcmp(argv[1], "btn");
        if (!parse_u32(argv[2], &code) || !parse_u32(argv[3], &frames)) return fail("bad_arguments");
        if (key && code > 255) return fail("bad_arguments");
        if (btn && code > 7) return fail("bad_arguments");
        if (!key && !btn && code > INP_CONTROL_MAX) return fail("bad_arguments");
        if (!inp_add(key ? INP_KEY : btn ? INP_BTN : INP_CTL, code, frames, 0, 0)) return;
    } else if (argc == 5 && !strcmp(argv[1], "mouse")) {
        if (!parse_u32(argv[2], &dx) || !parse_u32(argv[3], &dy) || !parse_u32(argv[4], &frames)) return fail("bad_arguments");
        if (!inp_add(INP_MOUSE, 0, frames, (int32_t)dx, (int32_t)dy)) return;
    } else {
        return fail("bad_arguments");
    }
    emit("{\"ok\":true}");
}
