/* Plan 2 W5: native modules (fnv_native). Included by fnvmcp_engine.c after w2_bp.c.
 *
 * mod load <path_hex>             LoadLibrary on the main thread, run fnvmcp_module_init(api). Returns the module id,
 *                                 its image range, its exports and the hook requests init made (not installed yet).
 * mod unload <id>                 Remove the module's hooks (original bytes back), wait for running handlers, run
 *                                 fnvmcp_module_exit, put back its writes, FreeLibrary.
 * mod list                        Modules, their hooks (installed or pending) and frame callbacks.
 * mod log <since> [max]           Log lines after seq since: {seq, module, frame, text}.
 * hooknative <hook> <mod> <req>   Attach a module's hook request to a hook the bridge installed (hook op, flags 0):
 *                                 the handler runs at each call; FNV_SKIP returns the frame's eax.
 */
#include <stdarg.h>
#include "../sdk/fnvmcp_module.h"

#define MODS 16u
#define MOD_HOOKS 32u
#define MOD_FRAMES 8u
#define MOD_WRITES 64u
#define MOD_WRITE_MAX 64u
#define MOD_LOG 1024u
#define MOD_LOG_TEXT 240u

typedef struct { uint32_t target, pop, hook_id; FnvHookFn fn; void *user; bool installed; } ModHook;
typedef struct { uint32_t address, length; unsigned char old[MOD_WRITE_MAX]; } ModWrite;
typedef struct { FnvFrameFn fn; void *user; } ModFrame;
typedef struct {
    bool used;
    uint32_t id, base, size;
    HMODULE dll;
    char name[64];
    ModHook hooks[MOD_HOOKS];
    int nhooks;
    ModFrame frames[MOD_FRAMES];
    int nframes;
    ModWrite writes[MOD_WRITES];
    int nwrites;
    volatile LONG busy; /* handlers and frame callbacks running now */
} Mod;
typedef struct { volatile LONG seq; uint32_t mod, frame; char text[MOD_LOG_TEXT]; } ModLine;

static Mod mods[MODS];
static ModLine mod_log_ring[MOD_LOG];
static volatile LONG mod_log_seq;
static Mod *mod_loading; /* the module whose init runs now (its calls come from inside its image anyway) */
static const NVSEInterface *nvse_iface;

static Mod *mod_find(uint32_t id) {
    for (unsigned i = 0; i < MODS; i++)
        if (mods[i].used && mods[i].id == id) return &mods[i];
    return NULL;
}
/* The module whose image holds a code address (the caller of an API function). */
static Mod *mod_of_address(uint32_t address) {
    for (unsigned i = 0; i < MODS; i++)
        if (mods[i].used && address >= mods[i].base && address < mods[i].base + mods[i].size) return &mods[i];
    return mod_loading;
}

/* ------------------------------------------------------------------------------------ the API table */
static void mod_api_log(const char *fmt, ...) {
    Mod *m = mod_of_address((uint32_t)(uintptr_t)__builtin_return_address(0));
    LONG seq = InterlockedIncrement(&mod_log_seq);
    ModLine *line = &mod_log_ring[(uint32_t)(seq - 1) % MOD_LOG];
    InterlockedExchange(&line->seq, 0);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line->text, sizeof(line->text), fmt ? fmt : "", ap);
    va_end(ap);
    line->mod = m ? m->id : 0;
    line->frame = (uint32_t)frame;
    MemoryBarrier();
    InterlockedExchange(&line->seq, seq);
}
static int mod_api_read(uint32_t address, void *out, uint32_t length) {
    return out && read_bytes(address, out, length) ? 1 : 0;
}
static int mod_api_write(uint32_t address, const void *bytes, uint32_t length) {
    Mod *m = mod_of_address((uint32_t)(uintptr_t)__builtin_return_address(0));
    if (!bytes || !length) return 0;
    if (m && length <= MOD_WRITE_MAX && m->nwrites < (int)MOD_WRITES) {
        ModWrite *w = &m->writes[m->nwrites];
        if (!read_bytes(address, w->old, length)) return 0;
        w->address = address;
        w->length = length;
        m->nwrites++;
    } else if (m) {
        return 0; /* only writes that can be put back */
    }
    return code_write(address, bytes, length) ? 1 : 0;
}
static int mod_api_hook(uint32_t target, FnvHookFn handler, void *user, uint32_t pop) {
    Mod *m = mod_of_address((uint32_t)(uintptr_t)__builtin_return_address(0));
    if (!m || !handler || m->nhooks >= (int)MOD_HOOKS || pop > 0x400) return -1;
    ModHook *h = &m->hooks[m->nhooks];
    h->target = target;
    h->pop = pop;
    h->fn = handler;
    h->user = user;
    h->hook_id = 0;
    h->installed = false;
    return m->nhooks++;
}
static int mod_api_on_frame(FnvFrameFn callback, void *user) {
    Mod *m = mod_of_address((uint32_t)(uintptr_t)__builtin_return_address(0));
    if (!m || !callback || m->nframes >= (int)MOD_FRAMES) return -1;
    m->frames[m->nframes].user = user;
    MemoryBarrier();
    m->frames[m->nframes].fn = callback;
    return m->nframes++;
}
static int mod_api_console(const char *line) {
    return line && console ? (console->RunScriptLine(line, NULL) ? 1 : 0) : 0;
}
static const void *mod_api_nvse(void) { return nvse_iface; }
typedef void *(__attribute__((thiscall)) *HeapAllocFn)(void *heap, uint32_t size);
typedef void (__attribute__((thiscall)) *HeapFreeFn)(void *heap, void *pointer);
#define GAME_HEAP ((void *)0x011F6238)        /* MemoryManager */
#define GAME_HEAP_ALLOC ((HeapAllocFn)0x00AA3E40)
#define GAME_HEAP_FREE ((HeapFreeFn)0x00AA4060)
static void *mod_api_alloc(uint32_t size) { return size ? GAME_HEAP_ALLOC(GAME_HEAP, size) : NULL; }
static void mod_api_free(void *pointer) {
    if (pointer) GAME_HEAP_FREE(GAME_HEAP, pointer);
}

static const FnvmcpApi mod_api = {
    FNVMCP_API_VERSION, sizeof(FnvmcpApi), mod_api_log, mod_api_read, mod_api_write, mod_api_hook,
    mod_api_on_frame, mod_api_console, mod_api_nvse, mod_api_alloc, mod_api_free,
};

/* ------------------------------------------------------------------------------------ hooks */
/* Called from fnvmcp_on_enter for a hook with a native handler: 0 to run the call, or the skip stub. */
static uint32_t mod_hook_enter(Hook *h, uint32_t *fr) {
    volatile LONG *busy = h->native_busy;
    if (!busy) return 0;
    InterlockedIncrement(busy);
    int (*fn)(uint32_t *, void *) = h->native;
    int result = fn ? fn(fr, h->native_user) : FNV_CONTINUE;
    InterlockedDecrement(busy);
    return result == FNV_SKIP && fn && h->native_skip ? (uint32_t)(uintptr_t)h->native_skip : 0;
}
/* Puts a hook's original bytes back (the unhook op without its reply). */
static bool hook_remove(Hook *h) {
    unsigned char now[5], expected[5] = {0xE9};
    uint32_t value = (uint32_t)(uintptr_t)h->entry - (h->target + 5);
    memcpy(expected + 1, &value, 4);
    h->active = 0;
    h->act_mode = ACT_NONE;
    h->native = NULL;
    if (!h->stolen) return true;
    if (!read_bytes(h->target, now, 5) || memcmp(now, expected, 5)) return false;
    if (code_write_frozen(h->target, h->original, h->stolen)) return false;
    h->stolen = 0;
    return true;
}
/* hooknative <hook> <mod> <request> */
static void op_hooknative(int argc, char **argv) {
    uint32_t hook_id, mod_id, index;
    if (argc != 4 || !parse_u32(argv[1], &hook_id) || !parse_u32(argv[2], &mod_id) || !parse_u32(argv[3], &index))
        return fail("bad_arguments");
    Hook *h = hook_find(hook_id);
    Mod *m = mod_find(mod_id);
    if (!h || !h->stolen) return fail("unknown_hook");
    if (!m || index >= (uint32_t)m->nhooks) return fail("bad_id");
    ModHook *req = &m->hooks[index];
    if (req->target != h->target) return fail("wrong_target");
    if (!h->native_skip) {
        if (hook_code_used + 16 > 65536) return fail("out_of_memory");
        h->native_skip = hook_code + hook_code_used;
        hook_code_used += 16;
    }
    { /* written every time: a reused hook's stub may hold another module's pop (K3 review I2) */
        unsigned char stub[4], *q = stub;
        if (req->pop) { *q++ = 0xC2; *q++ = (unsigned char)(req->pop & 0xFF); *q++ = (unsigned char)(req->pop >> 8); }
        else *q++ = 0xC3; /* eax comes back from the frame (popal), so the stub only returns */
        if (!code_write((uint32_t)(uintptr_t)h->native_skip, stub, (size_t)(q - stub))) return fail("unwritable");
    }
    h->native_user = req->user;
    h->native_busy = &m->busy;
    MemoryBarrier();
    h->native = (int (*)(uint32_t *, void *))req->fn;
    req->hook_id = hook_id;
    req->installed = true;
    emit("{\"ok\":true,\"hook\":%u,\"module\":%u,\"request\":%u}", hook_id, mod_id, index);
}

/* ------------------------------------------------------------------------------------ frames */
static void mod_frame_tick(void) {
    for (unsigned i = 0; i < MODS; i++) {
        Mod *m = &mods[i];
        if (!m->used || !m->nframes) continue;
        InterlockedIncrement(&m->busy);
        for (int k = 0; k < m->nframes; k++)
            if (m->frames[k].fn) m->frames[k].fn((uint32_t)frame, m->frames[k].user);
        InterlockedDecrement(&m->busy);
    }
}

/* ------------------------------------------------------------------------------------ ops */
static void emit_mod(const Mod *m) {
    emit("{\"id\":%u,\"name\":", m->id);
    emit_text(m->name, strlen(m->name));
    emit(",\"base\":\"0x%08x\",\"size\":%u,\"busy\":%ld,\"frames\":%d,\"writes\":%d,\"hooks\":[", m->base, m->size,
         (long)m->busy, m->nframes, m->nwrites);
    for (int k = 0; k < m->nhooks; k++)
        emit("%s{\"request\":%d,\"target\":\"0x%08x\",\"pop\":%u,\"handler\":\"0x%08x\",\"installed\":%s,\"hook\":%u}",
             k ? "," : "", k, m->hooks[k].target, m->hooks[k].pop, (uint32_t)(uintptr_t)m->hooks[k].fn,
             m->hooks[k].installed ? "true" : "false", m->hooks[k].hook_id);
    emit("]}");
}
static void emit_exports(uint32_t base) {
    uint32_t e_lfanew = 0, dir_rva = 0, count = 0, names = 0, ordinals = 0, functions = 0;
    bool comma = false;
    emit("[");
    if (read_u32(base + 0x3C, &e_lfanew) && read_u32(base + e_lfanew + 0x78, &dir_rva) && dir_rva &&
        read_u32(base + dir_rva + 0x18, &count) && read_u32(base + dir_rva + 0x1C, &functions) &&
        read_u32(base + dir_rva + 0x20, &names) && read_u32(base + dir_rva + 0x24, &ordinals)) {
        for (uint32_t i = 0; i < count && i < 256; i++) {
            uint32_t name_rva = 0, fn_rva = 0;
            uint16_t ordinal = 0;
            char name[96];
            if (!read_u32(base + names + 4 * i, &name_rva) ||
                !read_bytes(base + ordinals + 2 * i, &ordinal, 2) ||
                !read_u32(base + functions + 4 * ordinal, &fn_rva) ||
                !read_bytes(base + name_rva, name, sizeof(name) - 1))
                continue;
            name[sizeof(name) - 1] = 0;
            emit("%s{\"name\":", comma ? "," : "");
            emit_text(name, strnlen(name, sizeof(name) - 1));
            emit(",\"address\":\"0x%08x\"}", base + fn_rva);
            comma = true;
        }
    }
    emit("]");
}
static void mod_release(Mod *m) {
    for (int k = m->nwrites - 1; k >= 0; k--) code_write(m->writes[k].address, m->writes[k].old, m->writes[k].length);
    if (m->dll) FreeLibrary(m->dll);
    memset(m, 0, sizeof(*m));
}
static void op_mod_load(int argc, char **argv) {
    static uint32_t next_id = 1;
    char path[MAX_PATH];
    if (argc != 3) return fail("bad_arguments");
    long length = unhex(argv[2]);
    if (length <= 0 || length >= (long)sizeof(path)) return fail("bad_arguments");
    memcpy(path, argv[2], (size_t)length);
    path[length] = 0;
    Mod *m = NULL;
    for (unsigned i = 0; i < MODS && !m; i++)
        if (!mods[i].used) m = &mods[i];
    if (!m) return fail("too_many_modules");
    HMODULE dll = LoadLibraryA(path);
    if (!dll) {
        emit("{\"ok\":false,\"error\":\"load_failed\",\"winError\":%lu}", (unsigned long)GetLastError());
        return;
    }
    int (*init)(const FnvmcpApi *) = (int (*)(const FnvmcpApi *))(void *)GetProcAddress(dll, "fnvmcp_module_init");
    if (!init) {
        FreeLibrary(dll);
        return fail("no_init_export");
    }
    uint32_t base = (uint32_t)(uintptr_t)dll, e_lfanew = 0, size = 0;
    read_u32(base + 0x3C, &e_lfanew);
    read_u32(base + e_lfanew + 0x50, &size); /* OptionalHeader.SizeOfImage */
    memset(m, 0, sizeof(*m));
    m->used = true;
    m->id = next_id++;
    m->dll = dll;
    m->base = base;
    m->size = size;
    const char *slash = strrchr(path, '\\');
    snprintf(m->name, sizeof(m->name), "%s", slash ? slash + 1 : path);
    mod_loading = m;
    int rc = init(&mod_api);
    mod_loading = NULL;
    if (rc != 0) {
        mod_release(m);
        emit("{\"ok\":false,\"error\":\"init_failed\",\"result\":%d}", rc);
        return;
    }
    emit("{\"ok\":true,\"module\":");
    emit_mod(m);
    emit(",\"exports\":");
    emit_exports(base);
    emit("}");
}
static void op_mod_unload(int argc, char **argv) {
    uint32_t id;
    if (argc != 3 || !parse_u32(argv[2], &id)) return fail("bad_arguments");
    Mod *m = mod_find(id);
    if (!m) return fail("bad_id");
    unsigned removed = 0, stuck = 0;
    for (int k = 0; k < m->nhooks; k++) {
        if (!m->hooks[k].installed) continue;
        Hook *h = hook_find(m->hooks[k].hook_id);
        if (h && hook_remove(h)) removed++;
        else stuck++;
        m->hooks[k].installed = false;
    }
    m->nframes = 0;
    ULONGLONG until = GetTickCount64() + 2000;
    while (m->busy && GetTickCount64() < until) Sleep(1); /* handlers already inside run to their end */
    if (m->busy) {
        emit("{\"ok\":false,\"error\":\"busy\",\"hooksRemoved\":%u}", removed);
        return;
    }
    void (*exit_fn)(void) = (void (*)(void))(void *)GetProcAddress(m->dll, "fnvmcp_module_exit");
    if (exit_fn) exit_fn();
    int writes = m->nwrites;
    mod_release(m);
    emit("{\"ok\":true,\"id\":%u,\"hooksRemoved\":%u,\"hooksStuck\":%u,\"writesRestored\":%d}", id, removed, stuck,
         writes);
}
static void op_mod_log(int argc, char **argv) {
    uint32_t since = 0, max = 200, returned = 0;
    LONG last = mod_log_seq, first = last - (LONG)MOD_LOG + 1, next = (LONG)since;
    if (argc < 3 || argc > 4 || !parse_u32(argv[2], &since) || (argc == 4 && (!parse_u32(argv[3], &max) || !max)))
        return fail("bad_arguments");
    if (first < 1) first = 1;
    next = (LONG)since;
    emit("{\"ok\":true,\"lines\":[");
    for (LONG seq = (LONG)since + 1 > first ? (LONG)since + 1 : first; seq <= last && returned < max; seq++) {
        ModLine *line = &mod_log_ring[(uint32_t)(seq - 1) % MOD_LOG];
        if (line->seq != seq) continue;
        emit("%s{\"seq\":%ld,\"module\":%u,\"frame\":%u,\"text\":", returned ? "," : "", (long)seq, line->mod,
             line->frame);
        emit_text(line->text, strnlen(line->text, sizeof(line->text)));
        emit("}");
        returned++;
        next = seq;
    }
    emit("],\"next\":%ld,\"last\":%ld}", (long)(returned ? next : (last > (LONG)since ? last : (LONG)since)), (long)last);
}
static void op_mod(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "load")) return op_mod_load(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "unload")) return op_mod_unload(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "log")) return op_mod_log(argc, argv);
    if (argc == 2 && !strcmp(argv[1], "list")) {
        bool comma = false;
        emit("{\"ok\":true,\"modules\":[");
        for (unsigned i = 0; i < MODS; i++) {
            if (!mods[i].used) continue;
            if (comma) emit(",");
            emit_mod(&mods[i]);
            comma = true;
        }
        emit("]}");
        return;
    }
    fail("bad_arguments");
}
