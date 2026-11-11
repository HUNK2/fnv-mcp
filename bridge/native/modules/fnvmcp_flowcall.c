/* fnv_flow call (Plan 2, B-debug.md section 2): makes one engine call from a frame callback, so a breakpoint at the
 * function's entry stops it (the companion ignores stops while it serves a request, but not in a module's frame
 * callback). The bridge writes the call into fnvmcp_flow_slot, arms its breakpoint and the trace, then sets armed;
 * the next frame makes the call, stores eax and clears armed.
 *
 * Slot layout (u32 words): armed, function, this (ecx; 0 for cdecl/stdcall), nargs (0..12), args[12], eax, done.
 * The callee's own convention pops its arguments or not: esp is put back after the call either way. */
#include "fnvmcp_module.h"

typedef struct {
    volatile uint32_t armed, function, self, nargs, args[12], eax, done;
} FlowSlot;

FNVMCP_EXPORT FlowSlot fnvmcp_flow_slot;
static const FnvmcpApi *api;

static uint32_t flow_call(uint32_t function, uint32_t self, uint32_t nargs, const uint32_t *args) {
    uint32_t result, count = nargs;
    uint32_t thisp = self;
    __asm__ volatile(
        "movl %%esp, %%edi\n\t"
        "1:\n\t"
        "testl %%ecx, %%ecx\n\t"
        "jz 2f\n\t"
        "pushl -4(%%esi,%%ecx,4)\n\t"
        "decl %%ecx\n\t"
        "jmp 1b\n\t"
        "2:\n\t"
        "movl %%edx, %%ecx\n\t"
        "call *%%ebx\n\t"
        "movl %%edi, %%esp\n\t"
        : "=a"(result), "+c"(count), "+d"(thisp)
        : "b"(function), "S"(args)
        : "edi", "memory", "cc");
    return result;
}

static void on_frame(uint32_t frame, void *user) {
    uint32_t args[12];
    (void)frame;
    (void)user;
    if (!fnvmcp_flow_slot.armed) return;
    fnvmcp_flow_slot.armed = 0;
    uint32_t n = fnvmcp_flow_slot.nargs > 12 ? 12 : fnvmcp_flow_slot.nargs;
    for (uint32_t i = 0; i < n; i++) args[i] = fnvmcp_flow_slot.args[i];
    fnvmcp_flow_slot.eax = flow_call(fnvmcp_flow_slot.function, fnvmcp_flow_slot.self, n, args);
    fnvmcp_flow_slot.done = 1;
}

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) {
    api = a;
    fnvmcp_flow_slot.armed = 0;
    fnvmcp_flow_slot.done = 0;
    return api->on_frame(on_frame, 0) < 0;
}
