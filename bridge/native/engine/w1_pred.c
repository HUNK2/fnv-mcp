/* Predicates (contract FNV-COMPANION-V2.md section 2): small stack programs stored by id. Included by
 * fnvmcp_engine.c, which provides read_bytes, read_u32, emit, the xNVSE script API and the compile cache.
 * A program is a byte string of ops; each stack slot is an int or a float (the bit pattern is kept). */
#define PRED_SLOTS 64u
#define PRED_OPS 64u
#define PRED_BYTES 1024u
#define PRED_STACK 16u
#define PRED_FAULT (-1)      /* a safe read failed: the predicate is false and its error count goes up */
#define PRED_NO_CONTEXT (-2) /* a register, argument or this load without a context: false, not an error */
#define PRED_MALFORMED (-3)  /* an op that does not check (the runner checks again, so bad input cannot crash it) */

/* Register values of a hook or breakpoint (eax ecx edx ebx esp ebp esi edi), the address of its first stack
 * argument (0 = none) and its this pointer (ecx). A NULL context makes every context load false. */
typedef struct { uint32_t regs[8]; uint32_t args; uint32_t this_ptr; } PredContext;
typedef struct { uint32_t bits; bool is_float; } PredSlot;
typedef struct { bool used; uint8_t ops; uint16_t length; uint32_t evals, errors; unsigned char code[PRED_BYTES]; } Pred;
static Pred preds[PRED_SLOTS];

static uint32_t pred_u32(const unsigned char *p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}
static uint32_t float_bits(float f) {
    uint32_t bits;
    memcpy(&bits, &f, 4);
    return bits;
}
static float pred_float(PredSlot s) {
    if (!s.is_float) return (float)(int32_t)s.bits;
    float f;
    memcpy(&f, &s.bits, 4);
    return f;
}
/* Integer view of a slot (a float truncates to an int32, 0 when out of range). */
static int32_t pred_int(PredSlot s) {
    if (!s.is_float) return (int32_t)s.bits;
    float f = pred_float(s);
    return f > -2147483648.0f && f < 2147483648.0f ? (int32_t)f : 0;
}
static bool pred_true(PredSlot s) { return s.is_float ? pred_float(s) != 0.0f : s.bits != 0; }

/* Byte length of the op at code[i], or 0 when the op is unknown or its operands run past len. */
static size_t pred_op_size(const unsigned char *code, size_t len, size_t i) {
    size_t size;
    switch (code[i]) {
    case 0x01: case 0x02:
        size = 5;
        break;
    case 0x10: /* load: op, base, value (u32), n, n offsets (i32), type */
        if (i + 7 > len) return 0;
        size = 8 + 4 * (size_t)code[i + 6];
        break;
    case 0x20: case 0x21: case 0x22: case 0x23: case 0x24: case 0x25:
    case 0x30: case 0x31: case 0x32:
    case 0x40: case 0x41: case 0x42: case 0x43: case 0x44:
    case 0x50: case 0x51:
        size = 1;
        break;
    case 0x60: /* expression: u16 slot */
        size = 3;
        break;
    default:
        return 0;
    }
    return i + size <= len ? size : 0;
}
/* Pops and pushes of an op. Every op pushes one value; `not` pops one, the other value ops pop two or none. */
static void pred_stack_effect(unsigned char op, int *pops, int *pushes) {
    *pushes = 1;
    if (op == 0x32) *pops = 1;
    else if (op == 0x01 || op == 0x02 || op == 0x10 || op == 0x50 || op == 0x51 || op == 0x60) *pops = 0;
    else *pops = 2;
}

/* Checks a whole program: NULL when valid, else "bad_program" or "too_long". */
static const char *pred_check(const unsigned char *code, size_t len, unsigned *ops_out) {
    unsigned ops = 0;
    int depth = 0;
    if (!len) return "bad_program";
    if (len > PRED_BYTES) return "too_long";
    for (size_t i = 0; i < len; ops++) {
        size_t size = pred_op_size(code, len, i);
        if (!size) return "bad_program";
        i += size;
    }
    if (ops > PRED_OPS) return "too_long";
    for (size_t i = 0; i < len; i += pred_op_size(code, len, i)) {
        const unsigned char *op = code + i;
        int pops, pushes;
        if (op[0] == 0x10) {
            unsigned n = op[6], base = op[1], type = op[7 + 4 * n];
            if (base > 4 || !n || type > 6 || (base == 1 && pred_u32(op + 2) > 7)) return "bad_program";
        }
        pred_stack_effect(op[0], &pops, &pushes);
        if (depth < pops) return "bad_program";
        depth += pushes - pops;
        if (depth > (int)PRED_STACK) return "bad_program";
    }
    if (ops_out) *ops_out = ops;
    return NULL;
}

/* Reads one value of a load type (0 u8, 1 i8, 2 u16, 3 i16, 4 u32, 5 i32, 6 f32). */
static int pred_read(uint32_t address, unsigned type, PredSlot *out) {
    unsigned char b[4] = {0, 0, 0, 0};
    size_t size = type <= 1 ? 1 : type <= 3 ? 2 : 4;
    if (type > 6) return PRED_MALFORMED;
    if (!read_bytes(address, b, size)) return PRED_FAULT;
    out->is_float = type == 6;
    switch (type) {
    case 0: out->bits = b[0]; break;
    case 1: out->bits = (uint32_t)(int32_t)(int8_t)b[0]; break;
    case 2: out->bits = (uint32_t)b[0] | ((uint32_t)b[1] << 8); break;
    case 3: out->bits = (uint32_t)(int32_t)(int16_t)((uint32_t)b[0] | ((uint32_t)b[1] << 8)); break;
    default: out->bits = pred_u32(b); break;
    }
    return 0;
}
/* Load op: p = base value, then each offset but the last is a pointer step; the last reads the final type. */
static int pred_load(const unsigned char *op, const PredContext *ctx, PredSlot *out) {
    unsigned base = op[1], n = op[6], type = op[7 + 4 * n];
    uint32_t value = pred_u32(op + 2), p = 0;
    if (!n) return PRED_MALFORMED;
    if (base == 0) {
        p = value;
    } else if (!ctx) {
        return PRED_NO_CONTEXT;
    } else if (base == 1) {
        if (value > 7) return PRED_MALFORMED;
        p = ctx->regs[value];
    } else if (base == 2) {
        if (!ctx->args) return PRED_NO_CONTEXT;
        if (!read_u32(ctx->args + 4 * value, &p)) return PRED_FAULT;
    } else if (base == 4) { /* the argument's stack slot itself: offset 0 reads the argument's own value */
        if (!ctx->args) return PRED_NO_CONTEXT;
        p = ctx->args + 4 * value;
    } else {
        p = ctx->this_ptr;
    }
    for (unsigned k = 0; k + 1 < n; k++)
        if (!read_u32(p + pred_u32(op + 7 + 4 * k), &p)) return PRED_FAULT;
    return pred_read(p + pred_u32(op + 7 + 4 * (n - 1)), type, out);
}
/* Expression op: the compiled xNVSE expression in the compile cache slot. Main thread only, else 0. */
static int pred_expression(unsigned slot, PredSlot *out) {
    Element result;
    out->bits = 0;
    out->is_float = false;
    if (!main_thread || GetCurrentThreadId() != main_thread) return 0;
    if (slot >= compiled_count || !scripts || !scripts->CallFunction) return PRED_FAULT;
    memset(&result, 0, sizeof(result));
    scripts->CallFunction(compiled[slot].script, NULL, NULL, &result, 0);
    if (result.type == 1) {
        out->bits = float_bits((float)result.number);
        out->is_float = true;
    } else if (result.type == 2 || result.type == 3 || result.type == 4) {
        out->bits = element_pointer(&result);
    }
    return 0;
}
static uint32_t pred_compare(unsigned op, PredSlot a, PredSlot b) {
    bool r = false;
    if (a.is_float || b.is_float) {
        float x = pred_float(a), y = pred_float(b);
        switch (op) {
        case 0: r = x == y; break;
        case 1: r = x != y; break;
        case 2: r = x < y; break;
        case 3: r = x <= y; break;
        case 4: r = x > y; break;
        default: r = x >= y; break;
        }
    } else {
        int32_t x = (int32_t)a.bits, y = (int32_t)b.bits;
        switch (op) {
        case 0: r = x == y; break;
        case 1: r = x != y; break;
        case 2: r = x < y; break;
        case 3: r = x <= y; break;
        case 4: r = x > y; break;
        default: r = x >= y; break;
        }
    }
    return r ? 1u : 0u;
}

/* Runs a program. Leaves the stack in values[0..n-1] (top last, at most max), floats[] marks the floats and
 * returns the stack depth; a negative value is PRED_FAULT, PRED_NO_CONTEXT or PRED_MALFORMED. */
int pred_run_values(const uint8_t *code, size_t len, const PredContext *ctx, uint32_t *values, bool *floats, int max) {
    PredSlot stack[PRED_STACK];
    int depth = 0;
    for (size_t i = 0; i < len;) {
        size_t size = pred_op_size(code, len, i);
        const unsigned char *op = code + i;
        PredSlot a = {0, false}, b = {0, false}, r = {0, false};
        int pops, pushes, status = 0;
        if (!size) return PRED_MALFORMED;
        pred_stack_effect(op[0], &pops, &pushes);
        if (depth < pops || depth - pops + pushes > (int)PRED_STACK) return PRED_MALFORMED;
        if (pops == 2) b = stack[--depth];
        if (pops >= 1) a = stack[--depth];
        switch (op[0]) {
        case 0x01: r.bits = pred_u32(op + 1); break;
        case 0x02: r.bits = pred_u32(op + 1); r.is_float = true; break;
        case 0x10: status = pred_load(op, ctx, &r); break;
        case 0x20: case 0x21: case 0x22: case 0x23: case 0x24: case 0x25:
            r.bits = pred_compare(op[0] - 0x20u, a, b);
            break;
        case 0x30: r.bits = pred_true(a) && pred_true(b); break;
        case 0x31: r.bits = pred_true(a) || pred_true(b); break;
        case 0x32: r.bits = !pred_true(a); break;
        case 0x40: case 0x41:
            if (a.is_float || b.is_float) {
                float x = pred_float(a), y = pred_float(b);
                r.is_float = true;
                r.bits = float_bits(op[0] == 0x40 ? x + y : x - y);
            } else {
                r.bits = op[0] == 0x40 ? a.bits + b.bits : a.bits - b.bits;
            }
            break;
        case 0x42: r.bits = (uint32_t)pred_int(a) & (uint32_t)pred_int(b); break;
        case 0x43: r.bits = (uint32_t)pred_int(a) >> ((uint32_t)pred_int(b) & 31u); break;
        case 0x44: r.bits = (uint32_t)pred_int(a) << ((uint32_t)pred_int(b) & 31u); break;
        case 0x50: r.bits = (uint32_t)frame; break;
        case 0x51: r.bits = main_thread && GetCurrentThreadId() == main_thread ? 1u : 0u; break;
        case 0x60: status = pred_expression(op[1] | ((unsigned)op[2] << 8), &r); break;
        default: return PRED_MALFORMED;
        }
        if (status) return status;
        stack[depth++] = r;
        i += size;
    }
    for (int k = 0; k < depth && k < max; k++) {
        values[k] = stack[k].bits;
        floats[k] = stack[k].is_float;
    }
    return depth;
}

/* Evaluates predicate id: true when the program leaves a non-zero value on top. top and is_float (optional)
 * receive that top value. Errors (bad reads) count against the predicate and make it false. */
bool pred_eval(int id, const PredContext *ctx, uint32_t *top, bool *is_float) {
    uint32_t values[PRED_STACK];
    bool floats[PRED_STACK];
    Pred *p;
    int depth;
    if (top) *top = 0;
    if (is_float) *is_float = false;
    if (id < 0 || id >= (int)PRED_SLOTS || !preds[id].used) return false;
    p = &preds[id];
    InterlockedIncrement((volatile LONG *)&p->evals); /* (BUG-017) several threads evaluate at once */
    depth = pred_run_values(p->code, p->length, ctx, values, floats, (int)PRED_STACK);
    if (depth == PRED_FAULT || depth == PRED_MALFORMED) p->errors++;
    if (depth <= 0) return false;
    if (top) *top = values[depth - 1];
    if (is_float) *is_float = floats[depth - 1];
    return floats[depth - 1] ? pred_float((PredSlot){values[depth - 1], true}) != 0.0f : values[depth - 1] != 0;
}

static void emit_pred_number(uint32_t bits, bool is_float) {
    float f;
    if (!is_float) {
        emit("%d", (int)(int32_t)bits);
        return;
    }
    memcpy(&f, &bits, 4);
    if (isfinite(f)) emit("%.9g", (double)f);
    else emit("null");
}

/* pred set <id> <hex> | pred clear <id|ff> | pred list | pred eval <id> */
static void op_pred(int argc, char **argv) {
    uint32_t id = 0;
    if (argc == 4 && !strcmp(argv[1], "set")) {
        if (!parse_u32(argv[2], &id)) return fail("bad_arguments");
        if (id >= PRED_SLOTS) return fail("bad_id");
        if (strlen(argv[3]) > 2 * PRED_BYTES) return fail("too_long");
        long length = unhex(argv[3]);
        unsigned ops = 0;
        const char *error;
        if (length < 0) return fail("bad_arguments");
        error = pred_check((const unsigned char *)argv[3], (size_t)length, &ops);
        if (error) return fail(error);
        Pred *p = &preds[id];
        p->used = false; /* (BUG-017) a thread trapping meanwhile sees no predicate, not half old and half new code */
        MemoryBarrier();
        memcpy(p->code, argv[3], (size_t)length);
        p->length = (uint16_t)length;
        p->ops = (uint8_t)ops;
        p->evals = p->errors = 0;
        MemoryBarrier();
        p->used = true;
        emit("{\"ok\":true,\"id\":%u,\"ops\":%u}", id, ops);
    } else if (argc == 3 && !strcmp(argv[1], "clear")) {
        bool all = !strcmp(argv[2], "ff");
        unsigned cleared = 0;
        if (!all && (!parse_u32(argv[2], &id) || id >= PRED_SLOTS)) return fail("bad_id");
        for (unsigned i = 0; i < PRED_SLOTS; i++)
            if (preds[i].used && (all || i == id)) {
                preds[i].used = false;
                cleared++;
            }
        emit("{\"ok\":true,\"cleared\":%u}", cleared);
    } else if (argc == 2 && !strcmp(argv[1], "list")) {
        bool comma = false;
        emit("{\"preds\":[");
        for (unsigned i = 0; i < PRED_SLOTS; i++)
            if (preds[i].used) {
                emit("%s{\"id\":%u,\"ops\":%u,\"evals\":%u,\"errors\":%u}", comma ? "," : "", i, preds[i].ops,
                     preds[i].evals, preds[i].errors);
                comma = true;
            }
        emit("]}");
    } else if (argc == 3 && !strcmp(argv[1], "eval")) {
        uint32_t top = 0;
        bool is_float = false, value;
        if (!parse_u32(argv[2], &id) || id >= PRED_SLOTS || !preds[id].used) return fail("bad_id");
        value = pred_eval((int)id, NULL, &top, &is_float);
        emit("{\"value\":%s,\"top\":", value ? "true" : "false");
        emit_pred_number(top, is_float);
        emit(",\"errors\":%u}", preds[id].errors);
    } else {
        fail("bad_arguments");
    }
}
