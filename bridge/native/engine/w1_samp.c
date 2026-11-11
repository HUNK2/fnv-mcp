/* Sampler (contract FNV-COMPANION-V2.md section 4). Included by fnvmcp_engine.c after w1_gate.c. A sampler runs a
 * predicate program every N frames from the main-loop message and keeps the values left on its stack in a ring.
 * Everything here runs on the main thread (ops and the tick), so no locks are needed. */
#define SAMP_SLOTS 16u
#define SAMP_VALUES 16u
#define SAMP_RING_MAX 0x10000u
#define SAMP_READ_MAX 4096u

typedef struct { LONG seq; uint32_t frame; ULONGLONG tick; uint16_t count, float_mask; uint32_t values[SAMP_VALUES]; } SampRec;
typedef struct {
    bool used, stopped;
    uint16_t length;
    uint32_t every, countdown, ring_size, errors, stop_frame;
    int stop;      /* predicate id that stops the sampler, -1 for none */
    LONG seq;      /* last sequence number written */
    SampRec *ring;
    unsigned char code[PRED_BYTES];
} Sampler;
static Sampler samplers[SAMP_SLOTS];

static void samp_free(Sampler *s) {
    if (s->ring) HeapFree(GetProcessHeap(), 0, s->ring);
    s->ring = NULL;
    s->used = false;
}
/* Records the values of one run of the program (a run that faults counts as an error and records nothing). */
static void samp_sample(Sampler *s) {
    uint32_t values[SAMP_VALUES];
    bool floats[SAMP_VALUES];
    int depth = pred_run_values(s->code, s->length, NULL, values, floats, (int)SAMP_VALUES);
    if (depth < 0) {
        s->errors++;
        return;
    }
    LONG seq = s->seq + 1;
    SampRec *r = &s->ring[(uint32_t)seq % s->ring_size];
    r->seq = 0;
    r->frame = (uint32_t)frame;
    r->tick = GetTickCount64();
    r->count = (uint16_t)depth;
    r->float_mask = 0;
    for (int k = 0; k < depth && k < (int)SAMP_VALUES; k++) {
        r->values[k] = values[k];
        if (floats[k]) r->float_mask |= (uint16_t)(1u << k);
    }
    MemoryBarrier();
    r->seq = seq;
    s->seq = seq;
}
/* Called once per main-loop message (every frame, and never while the gate is held). */
static void samp_tick(void) {
    for (unsigned i = 0; i < SAMP_SLOTS; i++) {
        Sampler *s = &samplers[i];
        if (!s->used || s->stopped) continue;
        if (s->stop >= 0 && pred_eval(s->stop, NULL, NULL, NULL)) {
            s->stopped = true;
            s->stop_frame = (uint32_t)frame;
            continue;
        }
        if (--s->countdown) continue;
        s->countdown = s->every;
        samp_sample(s);
    }
}
/* samp set <id> <every_n> <program_hex> <stop_pred|ff> <ring> */
static void samp_set(int argc, char **argv) {
    uint32_t id, every, ring;
    int stop = -1;
    if (argc != 7 || !parse_u32(argv[2], &id) || id >= SAMP_SLOTS || !parse_u32(argv[3], &every) || !every ||
        every > GATE_FRAMES_MAX || !parse_u32(argv[6], &ring) || !ring || ring > SAMP_RING_MAX)
        return fail("bad_arguments");
    if (strlen(argv[4]) > 2 * PRED_BYTES) return fail("too_long");
    long length = unhex(argv[4]);
    unsigned ops = 0;
    const char *error;
    if (length < 0) return fail("bad_arguments");
    error = pred_check((const unsigned char *)argv[4], (size_t)length, &ops);
    if (error) return fail(error);
    if (strcmp(argv[5], "ff")) {
        uint32_t pred;
        if (!parse_u32(argv[5], &pred) || pred >= PRED_SLOTS || !preds[pred].used) return fail("bad_id");
        stop = (int)pred;
    }
    SampRec *buffer = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, (SIZE_T)ring * sizeof(SampRec));
    if (!buffer) return fail("out_of_memory");
    Sampler *s = &samplers[id];
    if (s->used) samp_free(s);
    memset(s, 0, sizeof(*s));
    memcpy(s->code, argv[4], (size_t)length);
    s->length = (uint16_t)length;
    s->every = s->countdown = every;
    s->ring_size = ring;
    s->ring = buffer;
    s->stop = stop;
    s->used = true;
    (void)ops;
    emit("{\"ok\":true}");
}
/* samp read <id> <since> [max] */
static void samp_read(int argc, char **argv) {
    uint32_t id, since_text, max = 256;
    if (argc < 3 || argc > 4 || !parse_u32(argv[1], &id) || id >= SAMP_SLOTS || !parse_u32(argv[2], &since_text) ||
        (argc == 4 && (!parse_u32(argv[3], &max) || !max || max > SAMP_READ_MAX)))
        return fail("bad_arguments");
    LONG since = (LONG)since_text;
    Sampler *s = &samplers[id];
    if (!s->used) return fail("bad_id");
    LONG last = s->seq, first = last - (LONG)s->ring_size + 1, next = last, last_returned = since;
    if (first < 1) first = 1;
    LONG dropped = since + 1 < first ? first - since - 1 : 0;
    uint32_t returned = 0;
    bool truncated = false, comma = false;
    emit("{\"samples\":[");
    for (LONG seq = since + 1 > first ? since + 1 : first; seq <= last; seq++) {
        SampRec *r = &s->ring[(uint32_t)seq % s->ring_size];
        if (r->seq != seq) continue;
        if (returned == max) {
            truncated = true;
            break;
        }
        emit("%s{\"seq\":%ld,\"frame\":%u,\"tick\":%llu,\"values\":[", comma ? "," : "", seq, r->frame, r->tick);
        for (unsigned k = 0; k < r->count; k++) {
            if (k) emit(",");
            emit_pred_number(r->values[k], (r->float_mask >> k) & 1u);
        }
        emit("]}");
        comma = true;
        returned++;
        last_returned = seq;
    }
    if (truncated) next = last_returned;
    emit("],\"next\":%ld,\"stopped\":%s,\"stopFrame\":", next, s->stopped ? "true" : "false");
    if (s->stopped) emit("%u", s->stop_frame);
    else emit("null");
    emit(",\"dropped\":%ld}", dropped);
}
/* samp clear <id|ff> | samp list */
static void samp_clear(int argc, char **argv) {
    uint32_t id = 0;
    bool all = argc == 2 && !strcmp(argv[1], "ff");
    unsigned cleared = 0;
    if (argc != 2 || (!all && (!parse_u32(argv[1], &id) || id >= SAMP_SLOTS))) return fail("bad_id");
    for (unsigned i = 0; i < SAMP_SLOTS; i++)
        if (samplers[i].used && (all || i == id)) {
            samp_free(&samplers[i]);
            cleared++;
        }
    emit("{\"ok\":true,\"cleared\":%u}", cleared);
}
static void samp_list(void) {
    bool comma = false;
    emit("{\"samplers\":[");
    for (unsigned i = 0; i < SAMP_SLOTS; i++) {
        Sampler *s = &samplers[i];
        if (!s->used) continue;
        LONG kept = s->seq < (LONG)s->ring_size ? s->seq : (LONG)s->ring_size;
        emit("%s{\"id\":%u,\"every\":%u,\"ring\":%u,\"samples\":%ld,\"seq\":%ld,\"errors\":%u,\"stopped\":%s,\"stopFrame\":",
             comma ? "," : "", i, s->every, s->ring_size, kept, s->seq, s->errors, s->stopped ? "true" : "false");
        if (s->stopped) emit("%u", s->stop_frame);
        else emit("null");
        emit("}");
        comma = true;
    }
    emit("]}");
}
static void op_samp(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "set")) samp_set(argc, argv);
    else if (argc >= 2 && !strcmp(argv[1], "read")) samp_read(argc - 1, argv + 1);
    else if (argc >= 2 && !strcmp(argv[1], "clear")) samp_clear(argc - 1, argv + 1);
    else if (argc == 2 && !strcmp(argv[1], "list")) samp_list();
    else fail("bad_arguments");
}
