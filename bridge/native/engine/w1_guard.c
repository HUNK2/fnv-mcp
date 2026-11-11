/* Guarded call (contract FNV-COMPANION-V2.md section 6). Included by fnvmcp_engine.c after w1_gate.c. A guard saves
 * the frame of fnvmcp_invoke_guarded. A fault on the guard's thread that is one of the engine-call faults makes the
 * vectored handler on_guard move the CONTEXT to the recovery label (the epilogue of the invoke), so the call returns
 * normally. The handler runs before on_trap and on_exception, so a guarded fault writes no crash-context file.
 * Stack overflow is never caught. */
#define GUARD_MAX 16u

/* ebp and resume must stay the first two fields: the assembly writes them. */
typedef struct { uint32_t ebp, resume, thread; volatile LONG faulted; DWORD code; uint32_t address, access, target; } GuardFrame;
static GuardFrame *guard_stack[GUARD_MAX];
static unsigned guard_depth;
static const char *guard_access_names[] = {"read", "write", "execute", "unknown"};

/* Same as fnvmcp_invoke, plus: saves ebp and the resume address (the label before the epilogue) in guard, and
 * writes the results to out only when the call returns normally. Arguments: 8 target, 12 words, 16 count,
 * 20 ecx, 24 edx, 28 float_return, 32 out, 36 guard. */
extern void fnvmcp_invoke_guarded(uint32_t target, const uint32_t *words, uint32_t count, uint32_t ecx, uint32_t edx,
                                  uint32_t float_return, CallResult *out, GuardFrame *guard);
__asm__(
    ".text\n"
    ".globl _fnvmcp_invoke_guarded\n"
    "_fnvmcp_invoke_guarded:\n"
    "  pushl %ebp\n"
    "  movl %esp, %ebp\n"
    "  pushl %ebx\n"
    "  pushl %esi\n"
    "  pushl %edi\n"
    "  movl 36(%ebp), %edi\n"
    "  movl %ebp, 0(%edi)\n"
    "  movl $1f, 4(%edi)\n"
    "  movl 12(%ebp), %esi\n"
    "  movl 16(%ebp), %ecx\n"
    "2:\n"
    "  testl %ecx, %ecx\n"
    "  jz 3f\n"
    "  decl %ecx\n"
    "  pushl (%esi,%ecx,4)\n"
    "  jmp 2b\n"
    "3:\n"
    "  movl 20(%ebp), %ecx\n"
    "  movl 24(%ebp), %edx\n"
    "  movl 8(%ebp), %eax\n"
    "  call *%eax\n"
    "  movl 32(%ebp), %edi\n"
    "  movl %eax, 0(%edi)\n"
    "  movl %edx, 4(%edi)\n"
    "  cmpl $0, 28(%ebp)\n"
    "  je 1f\n"
    "  fstpl 8(%edi)\n"
    "1:\n"
    "  leal -12(%ebp), %esp\n"
    "  popl %edi\n"
    "  popl %esi\n"
    "  popl %ebx\n"
    "  popl %ebp\n"
    "  ret\n");

static bool guard_catches(DWORD code) {
    return code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_ILLEGAL_INSTRUCTION ||
           code == EXCEPTION_INT_DIVIDE_BY_ZERO || code == EXCEPTION_PRIV_INSTRUCTION ||
           code == EXCEPTION_ARRAY_BOUNDS_EXCEEDED;
}
/* Only the top guard of this thread is considered; every other exception is left to the handlers after it. */
static LONG CALLBACK on_guard(EXCEPTION_POINTERS *e) {
    if (!guard_depth) return EXCEPTION_CONTINUE_SEARCH;
    GuardFrame *g = guard_stack[guard_depth - 1];
    DWORD code = e->ExceptionRecord->ExceptionCode;
    if (g->thread != GetCurrentThreadId() || !guard_catches(code)) return EXCEPTION_CONTINUE_SEARCH;
    g->code = code;
    if (code == EXCEPTION_ACCESS_VIOLATION && e->ExceptionRecord->NumberParameters >= 2) {
        ULONG_PTR kind = e->ExceptionRecord->ExceptionInformation[0];
        g->access = kind == 0 ? 0u : kind == 1 ? 1u : kind == 8 ? 2u : 3u;
        g->address = (uint32_t)e->ExceptionRecord->ExceptionInformation[1];
    } else {
        g->access = 2u; /* an instruction fault: the fault is at executing it */
        g->address = (uint32_t)(uintptr_t)e->ExceptionRecord->ExceptionAddress;
    }
    CONTEXT *c = e->ContextRecord;
    c->Eip = g->resume;
    c->Ebp = g->ebp;
    c->Esp = g->ebp - 12; /* the three saved registers below ebp; the epilogue recomputes esp anyway */
    /* A fault in the middle of float code can leave values on the x87 stack, and later game code would then see a
     * stack overflow (NaN). Empty the stack (every tag empty, TOP 0) but keep the control word: the game runs with
     * its own precision setting, which fninit would reset. */
    if ((c->ContextFlags & CONTEXT_FLOATING_POINT) == CONTEXT_FLOATING_POINT) {
        c->FloatSave.TagWord = 0xFFFF;
        c->FloatSave.StatusWord &= ~0x3800u;
    }
    InterlockedExchange(&g->faulted, 1);
    return EXCEPTION_CONTINUE_EXECUTION;
}

/* Calls under a guard. Returns 0 when the call returned, 1 when it faulted (g holds the fault), -1 when the guard
 * stack is full (nothing was called). */
static int guard_invoke(GuardFrame *g, uint32_t target, const uint32_t *words, uint32_t count, uint32_t ecx,
                        uint32_t edx, bool is_float, CallResult *out) {
    if (guard_depth >= GUARD_MAX) return -1;
    memset(g, 0, sizeof(*g));
    g->thread = GetCurrentThreadId();
    g->target = target;
    guard_stack[guard_depth++] = g;
    fnvmcp_invoke_guarded(target, words, count, ecx, edx, is_float, out, g);
    guard_depth--;
    return g->faulted ? 1 : 0;
}

/* call <target> <ig|fg|vg> <ecx> <edx> [word...] (the guarded form; the plain form is in op_call). */
static void call_guarded(uint32_t target, bool is_float, uint32_t ecx, uint32_t edx, const uint32_t *words, uint32_t count) {
    GuardFrame g;
    CallResult result = {0, 0, NAN};
    LONG before = script_errors;
    int status = guard_invoke(&g, target, words, count, ecx, edx, is_float, &result);
    if (status < 0) return fail("guard_full");
    if (status == 0) {
        emit("{\"ok\":true,\"eax\":\"0x%08x\",\"edx\":\"0x%08x\"", result.eax, result.edx);
        if (is_float) emit(isfinite(result.st0) ? ",\"st0\":%.17g" : ",\"st0\":null", result.st0);
        emit(",\"scriptErrors\":%ld}", script_errors - before);
        return;
    }
    emit("{\"ok\":false,\"fault\":{\"code\":\"0x%08lx\",\"address\":\"0x%08x\",\"access\":\"%s\",\"target\":\"0x%08x\"},"
         "\"stateSuspect\":true}",
         (unsigned long)g.code, g.address, guard_access_names[g.access > 3 ? 3 : g.access], target);
}
