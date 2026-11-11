/* fnvmcp_rt: the native module API (fnvmcp_module.h) as a normal xNVSE plugin, with no engine companion and no bridge.
 *
 * fnv_plugin_build compiles this file together with the module's source and a generated plan header
 * (fnvmcp_plan.h: the plugin name, and for each function the module may hook, the length of the prologue to move and
 * its rel32 offsets, planned by the bridge from the game's code). The module's fnvmcp_module_init runs when xNVSE
 * loads the plugin, before the game runs, so hooks are written while nothing executes them. Frame callbacks run on
 * xNVSE's main-loop message. The log goes to <plugin>.log next to the DLL.
 */
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "nvse_abi.h"
#include "fnvmcp_module.h"
#include "fnvmcp_plan.h"

#define RT_HOOKS 32
#define RT_FRAMES 8

typedef struct {
    uint32_t target, stolen, pop;
    FnvHookFn fn;
    void *user;
    unsigned char *entry, *trampoline, *skip;
} RtHook;

static RtHook rt_hooks[RT_HOOKS];
static int rt_hook_count;
static struct { FnvFrameFn fn; void *user; } rt_frames[RT_FRAMES];
static int rt_frame_count;
static unsigned char *rt_code;
static uint32_t rt_code_used, rt_frame_number;
static const NVSEInterface *rt_nvse;
static ConsoleAPI *rt_console;
static char rt_log_path[MAX_PATH];
static CRITICAL_SECTION rt_log_lock;

uint32_t fnvmcp_rt_enter(uint32_t *fr);
extern void fnvmcp_rt_common(void);
__asm__(
    ".globl _fnvmcp_rt_common\n"
    "_fnvmcp_rt_common:\n"
    "  pushal\n"
    "  pushfl\n"
    "  movl %esp, %eax\n"
    "  cld\n"
    "  pushl %eax\n"
    "  call _fnvmcp_rt_enter\n"
    "  addl $4, %esp\n"
    "  movl %eax, 36(%esp)\n"
    "  popfl\n"
    "  popal\n"
    "  ret\n");

/* frame (low to high): eflags edi esi ebp esp ebx edx ecx eax slot(hook) ret args... (FnvHookFrame) */
uint32_t fnvmcp_rt_enter(uint32_t *fr) {
    RtHook *h = (RtHook *)(uintptr_t)fr[9];
    DWORD error = GetLastError();
    int result = h->fn((FnvHookFrame *)fr, h->user);
    SetLastError(error);
    return (uint32_t)(uintptr_t)(result == FNV_SKIP ? h->skip : h->trampoline);
}

static bool rt_write(uint32_t address, const void *bytes, size_t length) {
    DWORD old;
    if (!VirtualProtect((void *)(uintptr_t)address, length, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void *)(uintptr_t)address, bytes, length);
    VirtualProtect((void *)(uintptr_t)address, length, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void *)(uintptr_t)address, length);
    return true;
}

static void rt_log(const char *fmt, ...) {
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt ? fmt : "", ap);
    va_end(ap);
    EnterCriticalSection(&rt_log_lock);
    FILE *f = fopen(rt_log_path, "ab");
    if (f) {
        fprintf(f, "[%u] %s\r\n", rt_frame_number, line);
        fclose(f);
    }
    LeaveCriticalSection(&rt_log_lock);
}
static int rt_read(uint32_t address, void *out, uint32_t length) {
    if (!out) return 0;
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), (LPCVOID)(uintptr_t)address, out, length, &got) && got == length;
}
static int rt_api_write(uint32_t address, const void *bytes, uint32_t length) {
    return bytes && length && rt_write(address, bytes, length) ? 1 : 0;
}
static const RtPlan *rt_plan(uint32_t target) {
    for (unsigned i = 0; i < sizeof(rt_plans) / sizeof(rt_plans[0]); i++)
        if (rt_plans[i].target == target) return &rt_plans[i];
    return NULL;
}
static int rt_hook(uint32_t target, FnvHookFn handler, void *user, uint32_t pop) {
    const RtPlan *plan = rt_plan(target);
    if (!plan || !plan->stolen) {
        rt_log("hook 0x%08X refused: no prologue plan (list the function in hooks= when building)", target);
        return -1;
    }
    if (!handler || rt_hook_count >= RT_HOOKS || pop > 0x400) return -1;
    if (!rt_code) rt_code = VirtualAlloc(NULL, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!rt_code || rt_code_used + 64 > 4096) return -1;
    RtHook *h = &rt_hooks[rt_hook_count];
    unsigned char *entry = rt_code + rt_code_used, *tramp = entry + 16, *skip = entry + 48;
    rt_code_used += 64;
    h->target = target;
    h->stolen = plan->stolen;
    h->pop = pop;
    h->fn = handler;
    h->user = user;
    entry[0] = 0x68; /* push hook */
    uint32_t value = (uint32_t)(uintptr_t)h;
    memcpy(entry + 1, &value, 4);
    entry[5] = 0xE9; /* jmp common */
    value = (uint32_t)(uintptr_t)fnvmcp_rt_common - (uint32_t)(uintptr_t)(entry + 10);
    memcpy(entry + 6, &value, 4);
    memcpy(tramp, (const void *)(uintptr_t)target, plan->stolen);
    for (unsigned r = 0; r < plan->nrelocs; r++) { /* rel32 operands of the moved instructions */
        uint32_t rel;
        memcpy(&rel, tramp + plan->relocs[r], 4);
        rel += target - (uint32_t)(uintptr_t)tramp;
        memcpy(tramp + plan->relocs[r], &rel, 4);
    }
    tramp[plan->stolen] = 0xE9;
    value = target + plan->stolen - (uint32_t)(uintptr_t)(tramp + plan->stolen + 5);
    memcpy(tramp + plan->stolen + 1, &value, 4);
    if (pop) { skip[0] = 0xC2; skip[1] = (unsigned char)(pop & 0xFF); skip[2] = (unsigned char)(pop >> 8); }
    else skip[0] = 0xC3;
    h->entry = entry;
    h->trampoline = tramp;
    h->skip = skip;
    unsigned char patch[16];
    memset(patch, 0x90, sizeof(patch));
    patch[0] = 0xE9;
    value = (uint32_t)(uintptr_t)entry - (target + 5);
    memcpy(patch + 1, &value, 4);
    if (!rt_write(target, patch, plan->stolen)) return -1;
    return rt_hook_count++;
}
static int rt_on_frame(FnvFrameFn callback, void *user) {
    if (!callback || rt_frame_count >= RT_FRAMES) return -1;
    rt_frames[rt_frame_count].fn = callback;
    rt_frames[rt_frame_count].user = user;
    return rt_frame_count++;
}
static int rt_run_console(const char *line) {
    return line && rt_console ? (rt_console->RunScriptLine(line, NULL) ? 1 : 0) : 0;
}
static const void *rt_nvse_iface(void) { return rt_nvse; }
typedef void *(__attribute__((thiscall)) *RtAlloc)(void *heap, uint32_t size);
typedef void (__attribute__((thiscall)) *RtFree)(void *heap, void *pointer);
static void *rt_alloc(uint32_t size) { return size ? ((RtAlloc)0x00AA3E40)((void *)0x011F6238, size) : NULL; }
static void rt_free(void *p) {
    if (p) ((RtFree)0x00AA4060)((void *)0x011F6238, p);
}

static const FnvmcpApi rt_api = {
    FNVMCP_API_VERSION, sizeof(FnvmcpApi), rt_log, rt_read, rt_api_write, rt_hook, rt_on_frame, rt_run_console,
    rt_nvse_iface, rt_alloc, rt_free,
};

static void rt_message(Message *m) {
    if (m->type == 20) { /* main game loop */
        rt_frame_number++;
        for (int i = 0; i < rt_frame_count; i++) rt_frames[i].fn(rt_frame_number, rt_frames[i].user);
    }
}

__declspec(dllexport) bool NVSEPlugin_Query(const NVSEInterface *nvse, PluginInfo *info) {
    info->infoVersion = 1;
    info->name = FNVMCP_PLUGIN_NAME;
    info->version = 1;
    return !nvse->isEditor;
}
__declspec(dllexport) bool NVSEPlugin_Load(const NVSEInterface *nvse) {
    HMODULE self = NULL;
    InitializeCriticalSection(&rt_log_lock);
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)(void *)NVSEPlugin_Load, &self);
    DWORD n = GetModuleFileNameA(self, rt_log_path, sizeof(rt_log_path) - 8);
    if (n > 4 && n < sizeof(rt_log_path) - 8) strcpy(rt_log_path + n - 4, ".log");
    rt_nvse = nvse;
    rt_console = nvse->QueryInterface(1);
    Messaging *messages = nvse->QueryInterface(2);
    if (messages) messages->RegisterListener(nvse->GetPluginHandle(), "NVSE", rt_message);
    int rc = fnvmcp_module_init(&rt_api);
    rt_log("%s loaded: init returned %d, %d hooks, %d frame callbacks", FNVMCP_PLUGIN_NAME, rc, rt_hook_count,
           rt_frame_count);
    return rc == 0;
}
