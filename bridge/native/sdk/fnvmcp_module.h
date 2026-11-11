/* fnvmcp native module API (Plan 2, W5: fnv_native).
 *
 * A module is a 32-bit DLL built with `zig cc -target x86-windows-gnu -shared`. The engine companion loads it on the
 * game's main thread and calls its exported fnvmcp_module_init with this table. Hook requests made in init (or later)
 * are installed by the bridge, which plans the stolen prologue bytes of the target as it does for its own hooks.
 *
 * Rules:
 *  - A hook handler runs on whatever thread calls the function: keep it short, re-entrant, and do not allocate in
 *    handlers of functions that other threads call.
 *  - fnvmcp_module_exit (optional export) runs before unload, on the main thread, after the module's hooks are gone.
 *  - Writes made with api->write are put back when the module unloads.
 *  - A module cannot register script commands (that only works while xNVSE loads plugins).
 */
#ifndef FNVMCP_MODULE_H
#define FNVMCP_MODULE_H

#include <stdint.h>

#define FNVMCP_API_VERSION 1u
#define FNV_CONTINUE 0
#define FNV_SKIP 1 /* do not run the hooked function: return f->eax to the caller (set it), popping `pop` bytes */

/* The registers at the hooked function's entry (as pushed by the hook stub), its return address and its stack
 * arguments. Changing ecx, edx or an arg changes what the function receives; eax is what FNV_SKIP returns. */
typedef struct FnvHookFrame {
    uint32_t eflags, edi, esi, ebp, esp, ebx, edx, ecx, eax;
    uint32_t slot; /* reserved */
    uint32_t ret;  /* the caller's return address */
    uint32_t arg[16];
} FnvHookFrame;

typedef int (*FnvHookFn)(FnvHookFrame *frame, void *user);
typedef void (*FnvFrameFn)(uint32_t frame_number, void *user);

typedef struct FnvmcpApi {
    uint32_t version, size;
    /* printf-style line into the module's log ring (fnv_native log). Any thread. */
    void (*log)(const char *fmt, ...);
    /* Safe read: 1 when every byte was readable. Any thread. */
    int (*read)(uint32_t address, void *out, uint32_t length);
    /* Write bytes (code or data); the old bytes are put back at unload. Main thread. 1 on success. */
    int (*write)(uint32_t address, const void *bytes, uint32_t length);
    /* Ask for a hook on a function's entry: handler(frame, user) before the function runs. pop: the bytes FNV_SKIP
     * pops (the callee's stack arguments for stdcall/thiscall, 0 for cdecl). Returns the request number (>= 0), or
     * -1 when the table is full. Installed by the bridge right after init (fnv_native load). */
    int (*hook)(uint32_t target, FnvHookFn handler, void *user, uint32_t pop);
    /* A callback once per frame on the main thread. */
    int (*on_frame)(FnvFrameFn callback, void *user);
    /* Run a console line (main thread). */
    int (*console)(const char *line);
    /* The xNVSE interface (NVSEInterface *). */
    const void *(*nvse)(void);
    /* The game's own heap (MemoryManager), for memory the engine will own or free. */
    void *(*alloc)(uint32_t size);
    void (*free)(void *pointer);
} FnvmcpApi;

#define FNVMCP_EXPORT __declspec(dllexport)

/* Every module exports this. Return 0 for success; anything else fails the load (and unloads the DLL). */
FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *api);

#endif
