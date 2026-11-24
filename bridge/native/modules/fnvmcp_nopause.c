/* fnv_window action=background (tool gaps 7): the game keeps running when its window loses focus. Ported from the
 * robot project's devtools/nopause.c (the user's own code). The main window procedure (0x0086A0A0, stdcall
 * hwnd, msg, wParam, lParam) opens the pause menu on WM_ACTIVATE with WA_INACTIVE; this module skips that one message
 * and returns 0, as a window procedure that handled it does. fnv_window action=foreground unloads it, which removes
 * the hook. */
#include "fnvmcp_module.h"

#define FN_MAIN_WNDPROC 0x0086A0A0u
#define WM_ACTIVATE_MSG 6u

static const FnvmcpApi *A;
static volatile long g_skipped;

static int on_wndproc(FnvHookFrame *f, void *user) {
    (void)user;
    if (f->arg[1] == WM_ACTIVATE_MSG && (f->arg[2] & 0xFFFFu) == 0) {
        f->eax = 0;
        g_skipped++;
        return FNV_SKIP;
    }
    return FNV_CONTINUE;
}

/* How many focus losses were kept from pausing the game since the module was loaded. */
FNVMCP_EXPORT int fnvmcp_nopause_count(void) { return (int)g_skipped; }

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *api) {
    A = api;
    if (A->hook(FN_MAIN_WNDPROC, on_wndproc, 0, 16) < 0) return 1;
    A->log("nopause: focus loss no longer opens the pause menu");
    return 0;
}
