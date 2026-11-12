/* Plan 2 W5: code placement for fnv_code (swapping an engine function for a recompiled one). Included after w5_mod.c.
 *
 * code alloc <size>          Executable memory (VirtualAlloc, read-write-execute): {"ok":true,"address":"0x.."}
 * code free <address>        Release it (only memory from code alloc; the bridge keeps a swapped blob while it may run)
 * code patch <address> <hex> Write bytes into code or data with the other threads of the game suspended (as probes are
 *                            written), so no thread runs half of a changed instruction: {"ok":true,"old":"<hex>"}
 */
#define CODE_BLOCKS 64u
#define CODE_PATCH_MAX 4096u

static uint32_t code_blocks[CODE_BLOCKS];

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
        emit("{\"ok\":true,\"address\":\"0x%08x\",\"size\":%u}", (uint32_t)(uintptr_t)p, value);
        return;
    }
    if (argc == 3 && !strcmp(argv[1], "free")) {
        if (!parse_u32(argv[2], &value) || !value) return fail("bad_arguments"); /* 0 would match an empty slot */
        for (unsigned slot = 0; slot < CODE_BLOCKS; slot++) {
            if (code_blocks[slot] != value) continue;
            VirtualFree((void *)(uintptr_t)value, 0, MEM_RELEASE);
            code_blocks[slot] = 0;
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
        cov_release_range(value, (uint32_t)length);
        if (!read_bytes(value, old, (size_t)length)) return fail("unreadable");
        int written = code_write_frozen(value, argv[3], (size_t)length);
        if (written == 1) return fail("busy"); /* a thread stands inside the bytes: call again */
        if (written) return fail("unwritable");
        emit("{\"ok\":true,\"length\":%ld,\"old\":\"", length);
        emit_hex_bytes(old, (size_t)length);
        emit("\"}");
        return;
    }
    fail("bad_arguments");
}
