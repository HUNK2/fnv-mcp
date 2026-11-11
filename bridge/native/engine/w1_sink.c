/* Event sinks (contract FNV-COMPANION-V2.md section 5). Included by fnvmcp_engine.c after w1_gate.c. One ring of
 * records; a record comes from an xNVSE event (a static handler per slot, so it knows its event name) or from an
 * xNVSE message of a subscribed type (including the per-form messages the companion used to drop). */
#define SINK_RING 65536u
#define SINK_SLOTS 64u
#define SINK_READ_MAX 4096u
#define SINK_NAME 32u

typedef struct { LONG seq; uint32_t frame; ULONGLONG tick; uint32_t ref; char source[SINK_NAME]; uint32_t words[6]; } SinkRec;
typedef struct { bool used, nvse; uint32_t type; char name[SINK_NAME]; } SinkSlot;
static SinkRec sink_ring[SINK_RING];
static volatile LONG sink_seq;
static SinkSlot sink_slots[SINK_SLOTS];
static EventManagerAPI *event_mgr;

static void sink_write(const char *source, uint32_t ref, const uint32_t *words) {
    LONG seq = InterlockedIncrement(&sink_seq);
    SinkRec *r = &sink_ring[(uint32_t)seq % SINK_RING];
    r->seq = 0;
    r->frame = (uint32_t)frame;
    r->tick = GetTickCount64();
    r->ref = ref;
    snprintf(r->source, sizeof(r->source), "%s", source);
    memcpy(r->words, words, sizeof(r->words));
    MemoryBarrier();
    r->seq = seq;
}
/* Called by the xNVSE event manager for slot `slot`: thisObj is the reference (or NULL), parameters the event's
 * argument block (the first six words are recorded). The ref is the form id of thisObj when it is a game object. */
static void sink_fire(unsigned slot, void *thisObj, void *parameters) {
    uint32_t words[6] = {0, 0, 0, 0, 0, 0}, ref = 0;
    if (!sink_slots[slot].used) return;
    if (parameters) read_bytes((uint32_t)(uintptr_t)parameters, words, sizeof(words));
    if (thisObj && ref_is_object(thisObj)) read_u32((uint32_t)(uintptr_t)thisObj + 0xC, &ref);
    sink_write(sink_slots[slot].name, ref, words);
}
#define SINK_LIST(X) X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7) X(8) X(9) X(10) X(11) X(12) X(13) X(14) X(15) \
    X(16) X(17) X(18) X(19) X(20) X(21) X(22) X(23) X(24) X(25) X(26) X(27) X(28) X(29) X(30) X(31) \
    X(32) X(33) X(34) X(35) X(36) X(37) X(38) X(39) X(40) X(41) X(42) X(43) X(44) X(45) X(46) X(47) \
    X(48) X(49) X(50) X(51) X(52) X(53) X(54) X(55) X(56) X(57) X(58) X(59) X(60) X(61) X(62) X(63)
#define SINK_HANDLER(n) static void sink_fire_##n(void *thisObj, void *parameters) { sink_fire(n, thisObj, parameters); }
SINK_LIST(SINK_HANDLER)
#define SINK_FN(n) sink_fire_##n,
static NativeEventHandler sink_fns[SINK_SLOTS] = {SINK_LIST(SINK_FN)};

/* A message of a subscribed type. dataLen <= 4: data is the value (a pointer); longer: data points to the block,
 * whose first six words are recorded. The ref is taken from the first word when it points to a game object. */
static void sink_message(uint32_t type, const Message *m) {
    uint32_t words[6] = {0, 0, 0, 0, 0, 0}, ref = 0;
    char source[SINK_NAME];
    bool wanted = false;
    for (unsigned i = 0; i < SINK_SLOTS; i++)
        if (sink_slots[i].used && !sink_slots[i].nvse && sink_slots[i].type == type) wanted = true;
    if (!wanted) return;
    if (m->dataLen <= 4) words[0] = (uint32_t)(uintptr_t)m->data;
    else if (m->data) read_bytes((uint32_t)(uintptr_t)m->data, words, m->dataLen < sizeof(words) ? m->dataLen : sizeof(words));
    if (words[0] && ref_is_object((void *)(uintptr_t)words[0])) read_u32(words[0] + 0xC, &ref);
    snprintf(source, sizeof(source), "msg:%u", (unsigned)type);
    sink_write(source, ref, words);
}

static int sink_free_slot(void) {
    for (unsigned i = 0; i < SINK_SLOTS; i++)
        if (!sink_slots[i].used) return (int)i;
    return -1;
}
/* sink add nvse <name_hex> | sink add msg <type> */
static void op_sink_add(int argc, char **argv) {
    if (argc != 4) return fail("bad_arguments");
    if (!strcmp(argv[2], "msg")) {
        uint32_t type;
        if (!parse_u32(argv[3], &type)) return fail("bad_arguments");
        for (unsigned i = 0; i < SINK_SLOTS; i++)
            if (sink_slots[i].used && !sink_slots[i].nvse && sink_slots[i].type == type) return emit("{\"ok\":true,\"slot\":%u}", i);
        int slot = sink_free_slot();
        if (slot < 0) return fail("sinks_full");
        sink_slots[slot].used = true;
        sink_slots[slot].nvse = false;
        sink_slots[slot].type = type;
        sink_slots[slot].name[0] = 0;
        emit("{\"ok\":true,\"slot\":%d}", slot);
    } else if (!strcmp(argv[2], "nvse")) {
        long length = unhex(argv[3]);
        char name[SINK_NAME];
        if (length <= 0 || length >= (long)sizeof(name)) return fail("bad_arguments");
        memcpy(name, argv[3], (size_t)length);
        name[length] = 0;
        if (!event_mgr || !event_mgr->SetNativeEventHandler) return fail("event_api_unavailable");
        for (unsigned i = 0; i < SINK_SLOTS; i++)
            if (sink_slots[i].used && sink_slots[i].nvse && !strcmp(sink_slots[i].name, name))
                return emit("{\"ok\":true,\"slot\":%u}", i);
        int slot = sink_free_slot();
        if (slot < 0) return fail("sinks_full");
        if (!event_mgr->SetNativeEventHandler(name, sink_fns[slot])) return fail("event_refused");
        sink_slots[slot].used = true;
        sink_slots[slot].nvse = true;
        sink_slots[slot].type = 0;
        snprintf(sink_slots[slot].name, sizeof(sink_slots[slot].name), "%s", name);
        emit("{\"ok\":true,\"slot\":%d}", slot);
    } else {
        fail("bad_arguments");
    }
}
/* sink remove <slot|ff> */
static void op_sink_remove(int argc, char **argv) {
    uint32_t slot = 0;
    bool all = argc == 2 && !strcmp(argv[1], "ff");
    unsigned removed = 0;
    if (argc != 2 || (!all && (!parse_u32(argv[1], &slot) || slot >= SINK_SLOTS))) return fail("bad_arguments");
    for (unsigned i = 0; i < SINK_SLOTS; i++) {
        if (!sink_slots[i].used || (!all && i != slot)) continue;
        if (sink_slots[i].nvse && event_mgr && event_mgr->RemoveNativeEventHandler)
            event_mgr->RemoveNativeEventHandler(sink_slots[i].name, sink_fns[i]);
        sink_slots[i].used = false;
        removed++;
    }
    emit("{\"ok\":true,\"removed\":%u}", removed);
}
/* sink read <since> <max> [ref]: records after since, oldest first; ref filters by the form id. */
static void op_sink_read(int argc, char **argv) {
    uint32_t since_text, max, ref_filter = 0;
    if (argc < 3 || argc > 4 || !parse_u32(argv[1], &since_text) || !parse_u32(argv[2], &max) || !max ||
        max > SINK_READ_MAX || (argc == 4 && !parse_u32(argv[3], &ref_filter)))
        return fail("bad_arguments");
    bool filter = argc == 4;
    LONG since = (LONG)since_text, last = sink_seq, first = last - (LONG)SINK_RING + 1, next = last, last_returned = since;
    if (first < 1) first = 1;
    LONG dropped = since + 1 < first ? first - since - 1 : 0;
    uint32_t returned = 0;
    bool truncated = false, comma = false;
    emit("{\"records\":[");
    for (LONG seq = since + 1 > first ? since + 1 : first; seq <= last; seq++) {
        SinkRec *r = &sink_ring[(uint32_t)seq % SINK_RING];
        if (r->seq != seq || (filter && r->ref != ref_filter)) continue;
        if (returned == max) {
            truncated = true;
            break;
        }
        emit("%s{\"seq\":%ld,\"frame\":%u,\"tick\":%llu,\"source\":", comma ? "," : "", seq, r->frame, r->tick);
        emit_text(r->source, strlen(r->source));
        emit(",\"ref\":\"0x%08x\",\"words\":[", r->ref);
        for (unsigned k = 0; k < 6; k++) emit("%s\"0x%08x\"", k ? "," : "", r->words[k]);
        emit("]}");
        comma = true;
        returned++;
        last_returned = seq;
    }
    if (truncated) next = last_returned;
    emit("],\"next\":%ld,\"dropped\":%ld}", next, dropped);
}
static void op_sink(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "add")) op_sink_add(argc, argv);
    else if (argc >= 2 && !strcmp(argv[1], "remove")) op_sink_remove(argc - 1, argv + 1);
    else if (argc >= 2 && !strcmp(argv[1], "read")) op_sink_read(argc - 1, argv + 1);
    else fail("bad_arguments");
}
