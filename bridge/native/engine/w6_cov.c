/* Plan 2 W6: line coverage (fnv_coverage). Included after w5_code.c.
 *
 * cov add <hex>     Append addresses (packed little-endian u32s, hex) to the table; kept sorted. Addresses whose
 *                   byte is already 0xCC (a breakpoint, a probe, the game's own) are skipped.
 * cov arm           Write 0xCC at every address not hit yet (other threads suspended). The first hit of an address
 *                   restores its byte, marks it hit and re-runs the instruction: the steady cost is zero.
 * cov read <since>  Hit addresses (packed hex) from the hit order since <since>, and counts.
 * cov disarm        Put back every byte not hit yet. The table stays (a trap already in flight is still recognised);
 *                   cov reset empties it once nothing is armed.
 */
#define COV_MAX 262144u

static uint32_t *cov_addr;      /* sorted */
static unsigned char *cov_orig;
static volatile LONG *cov_state; /* 0 idle, 1 armed, 2 hit */
static uint32_t *cov_order;      /* hit order */
static volatile LONG cov_count, cov_hits;
static bool cov_armed;

static int cov_find(uint32_t address) {
    int lo = 0, hi = (int)cov_count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (cov_addr[mid] == address) return mid;
        if (cov_addr[mid] < address) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}
/* From on_trap: true when the int3 is a coverage site (hit or in flight). */
static bool cov_on_breakpoint(EXCEPTION_POINTERS *e, LONG *result) {
    if (!cov_count) return false;
    uint32_t address = (uint32_t)(uintptr_t)e->ExceptionRecord->ExceptionAddress;
    int i = cov_find(address);
    if (i < 0) return false;
    if (InterlockedCompareExchange(&cov_state[i], 2, 1) == 1) {
        unsigned char *at = (unsigned char *)(uintptr_t)address;
        DWORD old;
        if (VirtualProtect(at, 1, PAGE_EXECUTE_READWRITE, &old)) {
            if (*at == 0xCC) *at = cov_orig[i];
            VirtualProtect(at, 1, old, &old);
            FlushInstructionCache(GetCurrentProcess(), at, 1);
        }
        LONG n = InterlockedIncrement(&cov_hits);
        cov_order[(uint32_t)(n - 1) % COV_MAX] = address;
    }
    e->ContextRecord->Eip = address; /* run the original instruction */
    *result = EXCEPTION_CONTINUE_EXECUTION;
    return true;
}
static int cov_cmp(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}
static void cov_write_all(bool arm) {
    probe_suspend();
    for (uint32_t i = 0; i < (uint32_t)cov_count; i++) {
        if (arm ? cov_state[i] != 0 : cov_state[i] != 1) continue;
        unsigned char *at = (unsigned char *)(uintptr_t)cov_addr[i];
        DWORD old;
        if (!VirtualProtect(at, 1, PAGE_EXECUTE_READWRITE, &old)) continue;
        if (arm) {
            if (*at != 0xCC) {
                cov_orig[i] = *at;
                *at = 0xCC;
                cov_state[i] = 1;
            }
        } else if (*at == 0xCC) {
            *at = cov_orig[i];
            cov_state[i] = 0;
        }
        VirtualProtect(at, 1, old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    probe_resume();
}
static void op_cov(int argc, char **argv) {
    if (!cov_addr) {
        cov_addr = VirtualAlloc(NULL, COV_MAX * 4, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        cov_orig = VirtualAlloc(NULL, COV_MAX, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        cov_state = VirtualAlloc(NULL, COV_MAX * 4, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        cov_order = VirtualAlloc(NULL, COV_MAX * 4, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (!cov_addr || !cov_orig || !cov_state || !cov_order) return fail("out_of_memory");
    }
    if (argc == 3 && !strcmp(argv[1], "add")) {
        if (cov_armed) return fail("armed");
        long length = unhex(argv[2]);
        if (length < 0 || length % 4) return fail("bad_arguments");
        uint32_t added = 0, skipped = 0;
        for (long k = 0; k < length && (uint32_t)cov_count < COV_MAX; k += 4) {
            uint32_t a;
            unsigned char b;
            memcpy(&a, argv[2] + k, 4);
            if (!read_bytes(a, &b, 1) || b == 0xCC || bp_probe_at(a) || isite_live(a)) {
                skipped++;
                continue;
            }
            cov_addr[cov_count] = a;
            cov_state[cov_count] = 0;
            cov_count++;
            added++;
        }
        qsort(cov_addr, (size_t)cov_count, 4, cov_cmp);
        uint32_t w = 0; /* drop duplicates */
        for (uint32_t i = 0; i < (uint32_t)cov_count; i++)
            if (!w || cov_addr[w - 1] != cov_addr[i]) cov_addr[w++] = cov_addr[i];
        cov_count = (LONG)w;
        emit("{\"ok\":true,\"added\":%u,\"skipped\":%u,\"total\":%ld}", added, skipped, (long)cov_count);
        return;
    }
    if (argc == 2 && !strcmp(argv[1], "arm")) {
        cov_write_all(true);
        cov_armed = true;
        emit("{\"ok\":true,\"armed\":%ld}", (long)cov_count);
        return;
    }
    if (argc == 2 && !strcmp(argv[1], "disarm")) {
        cov_write_all(false);
        cov_armed = false;
        emit("{\"ok\":true,\"hits\":%ld}", (long)cov_hits);
        return;
    }
    if (argc == 2 && !strcmp(argv[1], "reset")) {
        if (cov_armed) return fail("armed");
        cov_count = 0;
        cov_hits = 0;
        emit("{\"ok\":true}");
        return;
    }
    if (argc == 3 && !strcmp(argv[1], "read")) {
        uint32_t since = 0;
        if (!parse_u32(argv[2], &since)) return fail("bad_arguments");
        LONG last = cov_hits;
        uint32_t upto = (uint32_t)last, from = since, max = 8000;
        if (upto - from > max) upto = from + max;
        emit("{\"ok\":true,\"total\":%ld,\"hits\":%ld,\"next\":%u,\"addresses\":\"", (long)cov_count, (long)last, upto);
        for (uint32_t n = from; n < upto; n++) {
            uint32_t a = cov_order[n % COV_MAX];
            emit_hex_bytes((const unsigned char *)&a, 4);
        }
        emit("\"}");
        return;
    }
    fail("bad_arguments");
}
