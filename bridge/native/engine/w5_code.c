/* Plan 2 W5: code placement for fnv_code (swapping an engine function for a recompiled one). Included after w5_mod.c.
 *
 * code alloc <size>          Executable memory (VirtualAlloc, read-write-execute): {"ok":true,"address":"0x.."}
 * code free <address>        Release it (only memory from code alloc; the bridge keeps a swapped blob while it may run)
 * code patch <address> <hex> Write bytes into code or data with the other threads of the game suspended (as probes are
 *                            written), so no thread runs half of a changed instruction: {"ok":true,"old":"<hex>"}
 */
#define CODE_BLOCKS 64u
#define CODE_PATCH_MAX 4096u
#define CODE_SITES 1024u

static uint32_t code_blocks[CODE_BLOCKS], code_block_size[CODE_BLOCKS];

/* The game's bytes a code patch changed and has not put back (a swap jump, a call-site displacement): an int3 or a
 * coverage byte inside one would be saved into the patch's "old" or written over its jump (round 4, the K1 minor
 * raised to medium). Patches into code alloc memory are not kept: the swapped-in code may be debugged. A patch that
 * writes back exactly what the first patch at that address and length replaced (its "old", by hash) ends the site. */
typedef struct { uint32_t addr, len, old_hash; bool live; } CodeSite;
static CodeSite code_sites[CODE_SITES];

static uint32_t code_hash(const unsigned char *bytes, size_t length) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < length; i++) h = (h ^ bytes[i]) * 16777619u;
    return h;
}
static bool code_in_block(uint32_t addr) {
    for (unsigned slot = 0; slot < CODE_BLOCKS; slot++)
        if (code_blocks[slot] && addr >= code_blocks[slot] && addr - code_blocks[slot] < code_block_size[slot]) return true;
    return false;
}
static bool code_patch_range(uint32_t addr, uint32_t len) {
    for (unsigned i = 0; i < CODE_SITES; i++)
        if (code_sites[i].live && addr < code_sites[i].addr + code_sites[i].len && code_sites[i].addr < addr + len)
            return true;
    return false;
}
/* After a patch of `length` bytes at addr wrote `now` over `old`. */
static void code_site_note(uint32_t addr, uint32_t length, const unsigned char *old, const unsigned char *now) {
    if (code_in_block(addr)) return;
    uint32_t now_hash = code_hash(now, length);
    for (unsigned i = 0; i < CODE_SITES; i++) {
        CodeSite *c = &code_sites[i];
        if (c->live && c->addr == addr && c->len == length) {
            if (c->old_hash == now_hash) c->live = false; /* put back: the bytes are the game's own again */
            return;                                        /* patched again: the first "old" stays the one to restore */
        }
    }
    for (unsigned i = 0; i < CODE_SITES; i++) {
        if (code_sites[i].live) continue;
        code_sites[i] = (CodeSite){addr, length, code_hash(old, length), true};
        return;
    }
}

static void emit_hex_bytes(const unsigned char *bytes, size_t length) {
    static const char digits[] = "0123456789abcdef";
    char chunk[129];
    size_t i = 0;
    while (i < length) {
        size_t n = 0;
        for (; n < 64 && i < length; n++, i++) {
            chunk[2 * n] = digits[bytes[i] >> 4];
            chunk[2 * n + 1] = digits[bytes[i] & 15];
        }
        chunk[2 * n] = 0;
        emit("%s", chunk);
    }
}
static void op_code(int argc, char **argv) {
    uint32_t value = 0;
    if (argc == 3 && !strcmp(argv[1], "alloc")) {
        if (!parse_u32(argv[2], &value) || !value || value > 0x100000) return fail("bad_arguments");
        unsigned slot = 0;
        while (slot < CODE_BLOCKS && code_blocks[slot]) slot++;
        if (slot == CODE_BLOCKS) return fail("too_many_blocks");
        void *p = VirtualAlloc(NULL, value, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!p) return fail("out_of_memory");
        code_blocks[slot] = (uint32_t)(uintptr_t)p;
        code_block_size[slot] = value;
        emit("{\"ok\":true,\"address\":\"0x%08x\",\"size\":%u}", (uint32_t)(uintptr_t)p, value);
        return;
    }
    if (argc == 3 && !strcmp(argv[1], "free")) {
        if (!parse_u32(argv[2], &value) || !value) return fail("bad_arguments"); /* 0 would match an empty slot */
        for (unsigned slot = 0; slot < CODE_BLOCKS; slot++) {
            if (code_blocks[slot] != value) continue;
            VirtualFree((void *)(uintptr_t)value, 0, MEM_RELEASE);
            code_blocks[slot] = 0;
            code_block_size[slot] = 0;
            emit("{\"ok\":true}");
            return;
        }
        return fail("unknown_block");
    }
    if (argc == 4 && !strcmp(argv[1], "patch")) {
        unsigned char old[CODE_PATCH_MAX];
        if (!parse_u32(argv[2], &value) || value < 0x10000) return fail("bad_arguments");
        long length = unhex(argv[3]);
        if (length <= 0 || length > (long)CODE_PATCH_MAX) return fail("bad_arguments");
        /* a live int3 there would go into "old" (and its removal would cut the new jump); a hook's or probe's bytes
         * would be torn: refuse (round 4) */
        if (isite_in(value, (uint32_t)length) || hook_range(value, (uint32_t)length) || probe_range(value, (uint32_t)length))
            return fail("in_use");
        cov_release_range(value, (uint32_t)length);
        if (!read_bytes(value, old, (size_t)length)) return fail("unreadable");
        int written = code_write_frozen(value, argv[3], (size_t)length);
        if (written == 1) return fail("busy"); /* a thread stands inside the bytes: call again */
        if (written) return fail("unwritable");
        code_site_note(value, (uint32_t)length, old, (const unsigned char *)argv[3]);
        emit("{\"ok\":true,\"length\":%ld,\"old\":\"", length);
        emit_hex_bytes(old, (size_t)length);
        emit("\"}");
        return;
    }
    fail("bad_arguments");
}
