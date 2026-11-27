/* fnv_shader (SHADER-PLAN Task 3): screen-effect pixel shaders and engine shader replacement.
 *
 * Evidence: research/SHADER-RESEARCH.md sections 8 (drawing a pass after the end-of-frame effects) and 9 (replacing
 * the D3D shader inside the engine's own shader objects), both proven live on 2026-11-28.
 *
 * Screen effects (section 8):
 *  - RenderEndOfFrameEffects 0x00B97900 is called once a frame, on the main thread, only at 0x00B55AD6 (E8 rel32, the
 *    return address is 0x00B55ADB). The last pass writes the back buffer (*ppDst is NULL) and the HUD is drawn later,
 *    so a pass made right after the call sits under the HUD. The call is pointed at eof_wrap, which makes the original
 *    call and then draws. This is the only code patch; the device vtable is never written (it is a heap copy that
 *    something else already re-pointed), the device is only called through it.
 *  - Per enabled effect, in order: StretchRect the back buffer into a copy texture, bind it to s0 (and the engine HDR
 *    scene texture, src -> +0x30 -> +0x24 -> +0x64, to s1; a 1x1 black texture when it is missing), set c0 = (time s,
 *    frame delta s, width, height), c1 = (1/width, 1/height, frame mod 65536, 0) and the params from c2, and draw a
 *    half-pixel-offset quad (FVF XYZ|TEX1, a vs_3_0 pass-through) with the effect pixel shader. A D3DSBT_ALL state
 *    block is captured before and applied after, which keeps the engine render-state cache honest. At most 8 effects.
 *  - Device loss (not testable in windowed mode): the main loop calls 0x0086BA10 before it resets the device. A hook
 *    there releases everything in the DEFAULT pool (copy texture, state block); they are made again lazily. Shaders
 *    are not pool resources and are kept, and the 1x1 black texture is MANAGED, so it survives a reset too.
 *    TestCooperativeLevel is called before every draw.
 *
 * Engine shader replacement (section 9):
 *  - NiD3DPixelShader (vtable 0x010EF7D4) holds its IDirect3DPixelShader9 at +0x2C, NiD3DVertexShader (vtable
 *    0x010EF87C) its IDirect3DVertexShader9 at +0x34. One pass over read-write memory finds them (about 960 objects);
 *    a D3D object is told from junk by its vtable QueryInterface lying inside d3d9.dll (resource vtables live in the
 *    heap). The table lists the FNV-1a 32 of each bytecode; a swap acts on every object with that hash.
 *  - A swap stores a new D3D shader pointer straight into the object (heap data). The module keeps its own table of
 *    every swapped object with its first (vanilla) pointer; sfx_restore, sfx_restore_all and fnvmcp_module_exit put
 *    the originals back from it. The companion's write log (api write) is not used for this: it holds only 64 entries
 *    per module and a swap can touch hundreds of objects. api write is used once, for the call site (code).
 */
#include <windows.h>
#include <string.h>
#include "fnvmcp_module.h"

#define ADDR_CALL_SITE 0x00B55AD6u   /* E8 rel32 to RenderEndOfFrameEffects */
#define ADDR_CALL_RET 0x00B55ADBu    /* the return address of that call */
#define ADDR_EOF 0x00B97900u         /* RenderEndOfFrameEffects */
#define ADDR_DEVICE_LOST 0x0086BA10u /* called by the main loop before the device is reset */
#define ADDR_RENDERER 0x011F9508u    /* *(renderer + 0x288) is the IDirect3DDevice9 */
#define OFF_DEVICE 0x288u
#define VT_PS 0x010EF7D4u            /* NiD3DPixelShader */
#define VT_VS 0x010EF87Cu            /* NiD3DVertexShader */
#define OFF_PS_D3D 0x2Cu
#define OFF_VS_D3D 0x34u

#define SLOTS 8
#define FIRST_PARAM_REG 2
#define LAST_PARAM_REG 223
#define MAX_BYTES (1u << 20)
#define MAX_ROWS 16384
#define MAX_SWAPS MAX_ROWS
#define E_INVALID ((int)0x80070057)
#define E_NODEVICE ((int)0x8876086C)

#define D (void *)(uintptr_t)g_dev
#define P(x) ((void *)(uintptr_t)(x))

/* Device vtable slots used (standard D3D9 numbering; section 8 checked them against d3d9.dll). */
enum {
    DV_TEST_COOP = 3, DV_CREATE_TEXTURE = 23, DV_STRETCH_RECT = 34, DV_GET_RT = 38, DV_SET_VIEWPORT = 47,
    DV_SET_RENDER_STATE = 57, DV_CREATE_STATE_BLOCK = 59, DV_SET_TEXTURE = 65, DV_SET_SAMPLER_STATE = 69,
    DV_DRAW_PRIM_UP = 83, DV_SET_FVF = 89, DV_CREATE_VS = 91, DV_SET_VS = 92, DV_CREATE_PS = 106, DV_SET_PS = 107,
    DV_SET_PS_CONST_F = 109
};

typedef struct {
    void *ps;
    int exists, enabled, failed, uses_s1, order, max_reg, logged_fail;
    float c[LAST_PARAM_REG + 1][4];
} Slot;

typedef struct { uint32_t obj, kind, d3d, size, hash; } Row; /* the exported table row (kind 0 ps, 1 vs) */
typedef struct { uint32_t obj, off, orig, cur, hash, size, ver; } Swap;

static const FnvmcpApi *A;
static CRITICAL_SECTION g_cs;
static uint32_t g_dev, g_d3d_lo, g_d3d_hi;
static volatile LONG g_shutdown, g_patched, g_non_system;
static Slot g_slot[SLOTS];
static void *g_vs, *g_copy, *g_copy_surf, *g_sb, *g_black;
static uint32_t g_w, g_h, g_fmt;
static uint32_t g_hdr;           /* the HDR scene texture of this frame, 0 when missing */
static uint32_t g_frame, g_last_draw_frame;
static int g_ever_drew, g_no_hdr, g_cost_us, g_setup_logged, g_black_failed;
static LARGE_INTEGER g_t0, g_prev, g_freq;
static unsigned char *g_buf;
static uint32_t g_buf_size;
static Row g_row[MAX_ROWS];
static uint32_t g_row_ver[MAX_ROWS];
static int g_nrow;
static Swap g_swap[MAX_SWAPS];
static int g_nswap;
static unsigned char g_scan_buf[262144];

/* The vs_3_0 pass-through the effects draw with: HLSL below, compiled once with d3dcompiler_47 (zig cc cannot compile
 * HLSL); a test recompiles it and compares the bytes. Position in, position out; uv in, uv out. */
/* VS_HLSL_BEGIN
struct VO { float4 pos : POSITION; float2 uv : TEXCOORD0; };
VO main(float4 pos : POSITION, float2 uv : TEXCOORD0) {
    VO o;
    o.pos = pos;
    o.uv = uv;
    return o;
}
VS_HLSL_END */
static const unsigned char VS_BYTES[] = {
    0x00, 0x03, 0xFE, 0xFF, 0xFE, 0xFF, 0x14, 0x00, 0x43, 0x54, 0x41, 0x42,
    0x1C, 0x00, 0x00, 0x00, 0x23, 0x00, 0x00, 0x00, 0x00, 0x03, 0xFE, 0xFF,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
    0x1C, 0x00, 0x00, 0x00, 0x76, 0x73, 0x5F, 0x33, 0x5F, 0x30, 0x00, 0x4D,
    0x69, 0x63, 0x72, 0x6F, 0x73, 0x6F, 0x66, 0x74, 0x20, 0x28, 0x52, 0x29,
    0x20, 0x48, 0x4C, 0x53, 0x4C, 0x20, 0x53, 0x68, 0x61, 0x64, 0x65, 0x72,
    0x20, 0x43, 0x6F, 0x6D, 0x70, 0x69, 0x6C, 0x65, 0x72, 0x20, 0x31, 0x30,
    0x2E, 0x31, 0x00, 0xAB, 0x1F, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x00, 0x0F, 0x90, 0x1F, 0x00, 0x00, 0x02, 0x05, 0x00, 0x00, 0x80,
    0x01, 0x00, 0x0F, 0x90, 0x1F, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x80,
    0x00, 0x00, 0x0F, 0xE0, 0x1F, 0x00, 0x00, 0x02, 0x05, 0x00, 0x00, 0x80,
    0x01, 0x00, 0x03, 0xE0, 0x01, 0x00, 0x00, 0x02, 0x00, 0x00, 0x0F, 0xE0,
    0x00, 0x00, 0xE4, 0x90, 0x01, 0x00, 0x00, 0x02, 0x01, 0x00, 0x03, 0xE0,
    0x01, 0x00, 0xE4, 0x90, 0xFF, 0xFF, 0x00, 0x00,
};

static uint32_t rd(uint32_t a) {
    uint32_t v = 0;
    if (!A->read(a, &v, 4)) return 0;
    return v;
}
static void *vf(void *o, int slot) { return P(rd(rd((uint32_t)(uintptr_t)o) + 4u * (uint32_t)slot)); }

typedef ULONG(WINAPI *Rel_t)(void *);
typedef HRESULT(WINAPI *Dev1_t)(void *);
typedef HRESULT(WINAPI *GetRT_t)(void *, DWORD, void **);
typedef HRESULT(WINAPI *SurfDesc_t)(void *, uint32_t *);
typedef HRESULT(WINAPI *CreateTex_t)(void *, UINT, UINT, UINT, DWORD, DWORD, DWORD, void **, HANDLE *);
typedef HRESULT(WINAPI *LockRect_t)(void *, UINT, void *, const RECT *, DWORD);
typedef HRESULT(WINAPI *Unlock_t)(void *, UINT);
typedef HRESULT(WINAPI *GetSurfLvl_t)(void *, UINT, void **);
typedef HRESULT(WINAPI *CreateShader_t)(void *, const DWORD *, void **);
typedef HRESULT(WINAPI *CreateSB_t)(void *, DWORD, void **);
typedef HRESULT(WINAPI *SB_t)(void *);
typedef HRESULT(WINAPI *Stretch_t)(void *, void *, const RECT *, void *, const RECT *, DWORD);
typedef HRESULT(WINAPI *SetPtr_t)(void *, void *);
typedef HRESULT(WINAPI *SetTex_t)(void *, DWORD, void *);
typedef HRESULT(WINAPI *SetRS_t)(void *, DWORD, DWORD);
typedef HRESULT(WINAPI *SetSS_t)(void *, DWORD, DWORD, DWORD);
typedef HRESULT(WINAPI *SetFVF_t)(void *, DWORD);
typedef HRESULT(WINAPI *SetVP_t)(void *, const uint32_t *);
typedef HRESULT(WINAPI *DrawUP_t)(void *, DWORD, UINT, const void *, UINT);
typedef HRESULT(WINAPI *SetConstF_t)(void *, UINT, const float *, UINT);
typedef HRESULT(WINAPI *GetFn_t)(void *, void *, UINT *);

static void rel(void **p) {
    if (*p) {
        ((Rel_t)vf(*p, 2))(*p);
        *p = 0;
    }
}
static void relp(uint32_t p) {
    if (p) ((Rel_t)vf(P(p), 2))(P(p));
}
static void addref(uint32_t p) {
    if (p) ((Rel_t)vf(P(p), 1))(P(p));
}
static uint32_t get_dev(void) {
    g_dev = rd(rd(ADDR_RENDERER) + OFF_DEVICE);
    return g_dev;
}
/* A D3D9 object, told from junk: the QueryInterface slot of its vtable lies inside d3d9.dll. */
static int is_d3d(uint32_t p) {
    if (p < 0x10000) return 0;
    uint32_t qi = rd(rd(p));
    return qi >= g_d3d_lo && qi < g_d3d_hi;
}
static uint32_t fnv1a(const unsigned char *b, UINT n) {
    uint32_t h = 2166136261u;
    for (UINT i = 0; i < n; i++) {
        h ^= b[i];
        h *= 16777619u;
    }
    return h;
}

/* ------------------------------------------------------------------------------------------------ the only writes */

static void __fastcall eof_wrap(void *mgr, void *edx, uint32_t renderer, uint32_t *src, uint32_t *dst);
#ifdef SFX_BAKED
static void bake_load(void);
#endif

/* The one code patch: the call at 0x00B55AD6 goes to eof_wrap. Returns 0 when patched. */
static int patch_call_site(void) {
    unsigned char cur[5];
    if (!A->read(ADDR_CALL_SITE, cur, 5) || cur[0] != 0xE8 ||
        (uint32_t)(ADDR_CALL_RET + (cur[1] | cur[2] << 8 | cur[3] << 16 | (uint32_t)cur[4] << 24)) != ADDR_EOF) {
        A->log("shaderfx: call site 0x00B55AD6 is not the expected call");
        return 1;
    }
    uint32_t r = (uint32_t)(uintptr_t)eof_wrap - ADDR_CALL_RET;
    unsigned char p5[5] = {0xE8, (unsigned char)r, (unsigned char)(r >> 8), (unsigned char)(r >> 16),
                           (unsigned char)(r >> 24)};
    if (!A->write(ADDR_CALL_SITE, p5, 5)) {
        A->log("shaderfx: could not write the call site");
        return 2;
    }
    g_patched = 1;
    return 0;
}

/* Stores a D3D shader pointer into an engine shader object (plain heap data). Callers keep the vanilla pointer in
 * g_swap, which is what restore uses. Returns 1 on success. */
static int swap_pointer(uint32_t obj, uint32_t off, uint32_t value) {
    uint32_t cur;
    if (!A->read(obj + off, &cur, 4)) return 0;
    *(volatile uint32_t *)(uintptr_t)(obj + off) = value;
    return 1;
}

/* ------------------------------------------------------------------------------------------------ resources */

/* Everything in the DEFAULT pool, plus the state block. Called on device loss and at exit. */
static void release_default(void) {
    EnterCriticalSection(&g_cs);
    rel(&g_sb);
    rel(&g_copy_surf);
    rel(&g_copy);
    g_w = g_h = g_fmt = 0;
    LeaveCriticalSection(&g_cs);
}

static int on_device_lost(FnvHookFrame *f, void *user) {
    (void)f;
    (void)user;
    release_default();
    return FNV_CONTINUE;
}

/* The vertex shader and the 1x1 black texture survive a reset (a shader is no pool resource, the texture is
 * managed); the copy texture and the state block do not, and are made here. 1 when ready. */
static int ensure_resources(void *bb) {
    uint32_t d[8];
    if (((SurfDesc_t)vf(bb, 12))(bb, d)) return 0;
    if (g_copy && (g_w != d[6] || g_h != d[7] || g_fmt != d[0])) {
        rel(&g_sb);
        rel(&g_copy_surf);
        rel(&g_copy);
    }
    if (!g_copy) {
        void *tex = 0;
        HRESULT hr = ((CreateTex_t)vf(D, DV_CREATE_TEXTURE))(D, d[6], d[7], 1, 1 /*RENDERTARGET*/, d[0], 0 /*DEFAULT*/,
                                                              &tex, 0);
        if (hr || !tex) {
            if (!g_setup_logged++) A->log("shaderfx: CreateTexture 0x%lX", (unsigned long)hr);
            return 0;
        }
        g_copy = tex;
        ((GetSurfLvl_t)vf(g_copy, 18))(g_copy, 0, &g_copy_surf);
        g_w = d[6];
        g_h = d[7];
        g_fmt = d[0];
    }
    if (!g_copy_surf) return 0;
    if (!g_sb) {
        HRESULT hr = ((CreateSB_t)vf(D, DV_CREATE_STATE_BLOCK))(D, 1 /*D3DSBT_ALL*/, &g_sb);
        if (hr || !g_sb) {
            if (!g_setup_logged++) A->log("shaderfx: CreateStateBlock 0x%lX", (unsigned long)hr);
            return 0;
        }
    }
    if (!g_vs) {
        HRESULT hr = ((CreateShader_t)vf(D, DV_CREATE_VS))(D, (const DWORD *)VS_BYTES, &g_vs);
        if (hr || !g_vs) {
            if (!g_setup_logged++) A->log("shaderfx: CreateVertexShader 0x%lX", (unsigned long)hr);
            return 0;
        }
    }
    if (!g_black && !g_black_failed) {
        void *tex = 0;
        if (((CreateTex_t)vf(D, DV_CREATE_TEXTURE))(D, 1, 1, 1, 0, 21 /*A8R8G8B8*/, 1 /*MANAGED*/, &tex, 0) == 0 && tex) {
            struct { INT pitch; void *bits; } lr;
            if (((LockRect_t)vf(tex, 19))(tex, 0, &lr, 0, 0) == 0) {
                *(uint32_t *)lr.bits = 0xFF000000u;
                ((Unlock_t)vf(tex, 20))(tex, 0);
                g_black = tex;
            } else {
                ((Rel_t)vf(tex, 2))(tex);
            }
        }
        if (!g_black) g_black_failed = 1;
    }
    return 1;
}

/* True when the pixel shader declares sampler s1 (dcl_* s1: opcode 0x1F, destination register type 10, number 1). */
static int uses_sampler1(const uint32_t *t, uint32_t n) {
    for (uint32_t i = 1; i < n;) {
        uint32_t tok = t[i], op = tok & 0xFFFFu;
        if (op == 0xFFFEu) {
            i += 1 + ((tok >> 16) & 0x7FFFu);
            continue;
        }
        if (op == 0xFFFFu) break;
        if (op == 0x1Fu && i + 2 < n) {
            uint32_t dst = t[i + 2];
            if ((((dst >> 28) & 7u) | ((dst >> 8) & 0x18u)) == 10u && (dst & 0x7FFu) == 1u) return 1;
        }
        i += 1 + ((tok >> 24) & 0xFu);
    }
    return 0;
}

/* ------------------------------------------------------------------------------------------------ the draw */

static void draw_effects(void) {
    void *bb = 0;
    int order[SLOTS], n = 0;
    if (!get_dev()) return;
    if (((Dev1_t)vf(D, 3))(D)) return; /* device lost or not yet reset: draw nothing */
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < SLOTS; i++) {
        if (!g_slot[i].exists || !g_slot[i].enabled) continue;
        int k = n++;
        while (k > 0 && g_slot[order[k - 1]].order > g_slot[i].order) {
            order[k] = order[k - 1];
            k--;
        }
        order[k] = i;
    }
    if (!n) {
        g_cost_us = 0;
        LeaveCriticalSection(&g_cs);
        return;
    }
    if (((GetRT_t)vf(D, DV_GET_RT))(D, 0, &bb) || !bb) {
        LeaveCriticalSection(&g_cs);
        return;
    }
    if (!ensure_resources(bb)) {
        rel(&bb);
        LeaveCriticalSection(&g_cs);
        return;
    }
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    double now = (double)(t0.QuadPart - g_t0.QuadPart) / (double)g_freq.QuadPart;
    double delta = g_prev.QuadPart ? (double)(t0.QuadPart - g_prev.QuadPart) / (double)g_freq.QuadPart : 0;
    g_prev = t0;
    g_no_hdr = !(g_hdr && is_d3d(g_hdr));
    void *s1 = g_no_hdr ? g_black : P(g_hdr);
    float c01[8] = {(float)now, (float)delta, (float)g_w, (float)g_h, 1.0f / (float)g_w, 1.0f / (float)g_h,
                    (float)(g_frame & 0xFFFFu), 0};
    ((SB_t)vf(g_sb, 4))(g_sb); /* Capture */
    uint32_t vp[6];
    float *vpf = (float *)vp;
    vp[0] = vp[1] = 0;
    vp[2] = g_w;
    vp[3] = g_h;
    vpf[4] = 0;
    vpf[5] = 1;
    ((SetVP_t)vf(D, DV_SET_VIEWPORT))(D, vp);
    static const DWORD rs[][2] = {{7, 0}, {14, 0}, {27, 0}, {22, 1}, {15, 0}, {52, 0}, {174, 0}, {168, 0xF}, {194, 0}, {28, 0}};
    for (unsigned i = 0; i < sizeof rs / sizeof *rs; i++) ((SetRS_t)vf(D, DV_SET_RENDER_STATE))(D, rs[i][0], rs[i][1]);
    static const DWORD ss[][2] = {{1, 3}, {2, 3}, {5, 2}, {6, 2}, {7, 0}, {11, 0}};
    for (DWORD st = 0; st < 2; st++)
        for (unsigned i = 0; i < sizeof ss / sizeof *ss; i++)
            ((SetSS_t)vf(D, DV_SET_SAMPLER_STATE))(D, st, ss[i][0], ss[i][1]);
    ((SetPtr_t)vf(D, DV_SET_VS))(D, g_vs);
    ((SetFVF_t)vf(D, DV_SET_FVF))(D, 0x102 /*XYZ|TEX1*/);
    float ox = -1.0f / (float)g_w, oy = 1.0f / (float)g_h;
    float v[4][5] = {{-1 + ox, 1 + oy, 0, 0, 0}, {1 + ox, 1 + oy, 0, 1, 0}, {-1 + ox, -1 + oy, 0, 0, 1}, {1 + ox, -1 + oy, 0, 1, 1}};
    for (int k = 0; k < n; k++) {
        Slot *s = &g_slot[order[k]];
        HRESULT hr = ((Stretch_t)vf(D, DV_STRETCH_RECT))(D, bb, 0, g_copy_surf, 0, 0 /*D3DTEXF_NONE*/);
        HRESULT hd = 1;
        if (!hr) {
            ((SetTex_t)vf(D, DV_SET_TEXTURE))(D, 0, g_copy);
            ((SetTex_t)vf(D, DV_SET_TEXTURE))(D, 1, s->uses_s1 ? s1 : 0);
            ((SetPtr_t)vf(D, DV_SET_PS))(D, s->ps);
            ((SetConstF_t)vf(D, DV_SET_PS_CONST_F))(D, 0, c01, 2);
            if (s->max_reg >= FIRST_PARAM_REG)
                ((SetConstF_t)vf(D, DV_SET_PS_CONST_F))(D, FIRST_PARAM_REG, &s->c[FIRST_PARAM_REG][0],
                                                        (UINT)(s->max_reg - FIRST_PARAM_REG + 1));
            hd = ((DrawUP_t)vf(D, DV_DRAW_PRIM_UP))(D, 5 /*TRIANGLESTRIP*/, 2, v, sizeof v[0]);
        }
        if (hr || hd) { /* an effect whose pass fails is switched off for good (bit 4 of its state) */
            s->failed = 1;
            s->enabled = 0;
            if (!s->logged_fail) {
                s->logged_fail = 1;
                A->log("shaderfx: effect %d disabled: StretchRect 0x%lX draw 0x%lX", order[k], (unsigned long)hr,
                       (unsigned long)hd);
            }
        }
    }
    ((SB_t)vf(g_sb, 5))(g_sb); /* Apply */
    QueryPerformanceCounter(&t1);
    g_cost_us = (int)((t1.QuadPart - t0.QuadPart) * 1000000 / g_freq.QuadPart);
    g_last_draw_frame = g_frame;
    g_ever_drew = 1;
    rel(&bb);
    LeaveCriticalSection(&g_cs);
}

typedef void(__attribute__((thiscall)) *EOF_t)(void *, uint32_t, uint32_t *, uint32_t *);
/* The call site pushes three arguments to a thiscall-like function: this is __fastcall (mgr in ecx, edx unused, the
 * three arguments on the stack, popped by us) and makes the original call itself (proven live in the spike). */
static void __fastcall eof_wrap(void *mgr, void *edx, uint32_t renderer, uint32_t *src, uint32_t *dst) {
    (void)edx;
    ((EOF_t)ADDR_EOF)(mgr, renderer, src, dst);
    g_frame++;
    if (g_shutdown) return;
#ifdef SFX_BAKED
    bake_load();
#endif
    g_hdr = src ? rd(rd(rd((uint32_t)(uintptr_t)src + 0x30) + 0x24) + 0x64) : 0;
    draw_effects();
}

/* ------------------------------------------------------------------------------------------------ effect exports */

FNVMCP_EXPORT int sfx_buffer(int size) {
    if (size <= 0 || (uint32_t)size > MAX_BYTES) return 0;
    if ((uint32_t)size > g_buf_size) {
        unsigned char *b = (unsigned char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)size);
        if (!b) return 0;
        if (g_buf) HeapFree(GetProcessHeap(), 0, g_buf);
        g_buf = b;
        g_buf_size = (uint32_t)size;
    }
    return (int)(uintptr_t)g_buf;
}

/* Creates or replaces slot's effect from the pixel shader bytecode at addr (size bytes, normally the sfx_buffer). */
FNVMCP_EXPORT int sfx_effect_set(int slot, int addr, int size, int order) {
    if (slot < 0 || slot >= SLOTS || size < 8 || size > (int)MAX_BYTES || (size & 3)) return E_INVALID;
    DWORD *code = (DWORD *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)size);
    if (!code) return E_INVALID;
    if (!A->read((uint32_t)addr, code, (uint32_t)size) || (code[0] >> 16) != 0xFFFFu || (code[0] & 0xFFFFu) < 0x0200u) {
        HeapFree(GetProcessHeap(), 0, code);
        return E_INVALID;
    }
    if (!get_dev()) {
        HeapFree(GetProcessHeap(), 0, code);
        return E_NODEVICE;
    }
    void *ps = 0;
    HRESULT hr = ((CreateShader_t)vf(D, DV_CREATE_PS))(D, code, &ps);
    if (hr || !ps) {
        HeapFree(GetProcessHeap(), 0, code);
        return hr ? (int)hr : E_NODEVICE;
    }
    EnterCriticalSection(&g_cs);
    Slot *s = &g_slot[slot];
    void *old = s->ps;
    s->ps = ps;
    s->exists = 1;
    s->enabled = 1;
    s->failed = 0;
    s->logged_fail = 0;
    s->uses_s1 = uses_sampler1((const uint32_t *)code, (uint32_t)size / 4);
    s->order = order < 0 ? 0 : (order > 0xFFFFFF ? 0xFFFFFF : order);
    s->max_reg = 0;
    memset(s->c, 0, sizeof s->c);
    LeaveCriticalSection(&g_cs);
    if (old) ((Rel_t)vf(old, 2))(old);
    HeapFree(GetProcessHeap(), 0, code);
    return 0;
}

/* 0 ok; -1 bad slot (or no effect in it); -2 when reg < 2 or reg > 223. */
FNVMCP_EXPORT int sfx_effect_param(int slot, int reg, float x, float y, float z, float w) {
    if (slot < 0 || slot >= SLOTS || !g_slot[slot].exists) return -1;
    if (reg < FIRST_PARAM_REG || reg > LAST_PARAM_REG) return -2;
    EnterCriticalSection(&g_cs);
    float *c = g_slot[slot].c[reg];
    c[0] = x;
    c[1] = y;
    c[2] = z;
    c[3] = w;
    if (reg > g_slot[slot].max_reg) g_slot[slot].max_reg = reg;
    LeaveCriticalSection(&g_cs);
    return 0;
}

#ifdef SFX_BAKED
/* The exported plugin (fnv_shader export): the effects are in sfx_baked[] (the generated sfx_baked.h, put before this
 * file with SFX_BAKED defined). A plugin's init runs before the D3D device exists, so the D3D shaders are made on the
 * first frame that has a device, once. No new code patch: the same call site as the live module. */
static int g_baked_done;
static void bake_load(void) {
    if (g_baked_done || !get_dev()) return;
    g_baked_done = 1;
    for (int i = 0; i < SFX_BAKED_COUNT && i < SLOTS; i++) {
        const SfxBakedEffect *e = &sfx_baked[i];
        int hr = sfx_effect_set(i, (int)(uintptr_t)e->code, e->size, e->order);
        if (hr < 0) {
            A->log("shaderfx: baked effect %d refused by the game (0x%08X)", i, (unsigned)hr);
            continue;
        }
        for (int k = 0; k < e->nparams; k++)
            sfx_effect_param(i, e->params[k].reg, e->params[k].v[0], e->params[k].v[1], e->params[k].v[2],
                             e->params[k].v[3]);
    }
    A->log("shaderfx: %d baked effect(s) loaded", SFX_BAKED_COUNT);
}
#endif

FNVMCP_EXPORT int sfx_effect_enable(int slot, int on) {
    if (slot < 0 || slot >= SLOTS || !g_slot[slot].exists) return -1;
    EnterCriticalSection(&g_cs);
    g_slot[slot].enabled = on ? 1 : 0;
    if (on) {
        g_slot[slot].failed = 0;
        g_slot[slot].logged_fail = 0;
    }
    LeaveCriticalSection(&g_cs);
    return 0;
}

FNVMCP_EXPORT int sfx_effect_free(int slot) {
    if (slot < 0 || slot >= SLOTS) return -1;
    EnterCriticalSection(&g_cs);
    void *old = g_slot[slot].ps;
    memset(&g_slot[slot], 0, sizeof g_slot[slot]);
    LeaveCriticalSection(&g_cs);
    if (old) ((Rel_t)vf(old, 2))(old);
    return 0;
}

/* bits 1 exists, 2 enabled, 4 failed (a draw error switched it off), 8 uses s1; order << 8. */
FNVMCP_EXPORT int sfx_effect_state(int slot) {
    if (slot < 0 || slot >= SLOTS) return 0;
    Slot *s = &g_slot[slot];
    if (!s->exists) return 0;
    return 1 | (s->enabled ? 2 : 0) | (s->failed ? 4 : 0) | (s->uses_s1 ? 8 : 0) | (s->order << 8);
}

FNVMCP_EXPORT int sfx_cost_us(void) { return g_cost_us; }

/* bits 1 call site patched, 2 non-system d3d9.dll, 4 device lost now, 8 no HDR input last frame, 16 drew in the last
 * 60 frames. */
FNVMCP_EXPORT int sfx_info(void) {
    int bits = 0;
    if (g_patched) bits |= 1;
    if (g_non_system) bits |= 2;
    if (get_dev() && ((Dev1_t)vf(D, DV_TEST_COOP))(D)) bits |= 4;
    if (g_no_hdr) bits |= 8;
    if (g_ever_drew && g_frame - g_last_draw_frame < 60) bits |= 16;
    return bits;
}

/* ------------------------------------------------------------------------------------------------ engine shaders */

/* The swap entry of an object, given the pointer the object holds now. An entry whose replacement is no longer in the
 * object is stale (the engine freed the object and reused the address, or changed the pointer): it is dropped without
 * releasing anything and the object counts as unswapped. Call with g_cs held. */
static Swap *find_swap(uint32_t obj, uint32_t off, uint32_t live) {
    for (int i = 0; i < g_nswap; i++) {
        if (g_swap[i].obj != obj || g_swap[i].off != off) continue;
        if (g_swap[i].cur == live) return &g_swap[i];
        g_swap[i] = g_swap[--g_nswap];
        return 0;
    }
    return 0;
}

/* Finds every engine shader object in read-write memory and lists it with its bytecode hash. Objects this module has
 * swapped keep the hash, size and version of the original. Returns the number of objects. */
FNVMCP_EXPORT int sfx_scan(void) {
    MEMORY_BASIC_INFORMATION mi;
    EnterCriticalSection(&g_cs);
    g_nrow = 0;
    for (uintptr_t a = 0x10000; a < 0x7FFF0000 && VirtualQuery((void *)a, &mi, sizeof mi);
         a = (uintptr_t)mi.BaseAddress + mi.RegionSize) {
        if (mi.State != MEM_COMMIT || (mi.Protect & (PAGE_GUARD | PAGE_NOACCESS))) continue;
        if (!(mi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE))) continue;
        uint32_t *p = (uint32_t *)mi.BaseAddress, *e = (uint32_t *)((char *)mi.BaseAddress + mi.RegionSize);
        for (; p < e && g_nrow < MAX_ROWS; p++) {
            if (*p != VT_PS && *p != VT_VS) continue;
            uint32_t obj = (uint32_t)(uintptr_t)p, kind = *p == VT_VS, off = kind ? OFF_VS_D3D : OFF_PS_D3D;
            uint32_t d3d = rd(obj + off), size = 0, hash = 0, ver = 0;
            if (!is_d3d(d3d)) continue;
            Swap *sw = find_swap(obj, off, d3d);
            if (sw) {
                size = sw->size;
                hash = sw->hash;
                ver = sw->ver;
            } else {
                UINT n = 0;
                void *fn = P(d3d);
                if (((GetFn_t)vf(fn, 4))(fn, 0, &n) || !n || n > sizeof g_scan_buf) continue;
                if (((GetFn_t)vf(fn, 4))(fn, g_scan_buf, &n)) continue;
                size = n;
                hash = fnv1a(g_scan_buf, n);
                ver = *(uint32_t *)g_scan_buf;
            }
            g_row_ver[g_nrow] = ver;
            Row *r = &g_row[g_nrow++];
            r->obj = obj;
            r->kind = kind;
            r->d3d = d3d;
            r->size = size;
            r->hash = hash;
        }
    }
    int count = g_nrow;
    LeaveCriticalSection(&g_cs);
    A->log("shaderfx: %d engine shader objects", count);
    return count;
}

/* The address of the rows of the last scan: {u32 obj, u32 kind (0 ps, 1 vs), u32 d3d (current), u32 size, u32 hash}. */
FNVMCP_EXPORT int sfx_table(void) { return (int)(uintptr_t)g_row; }

static void row_set_d3d(uint32_t obj, uint32_t kind, uint32_t d3d) {
    for (int i = 0; i < g_nrow; i++)
        if (g_row[i].obj == obj && g_row[i].kind == kind) g_row[i].d3d = d3d;
}

/* Replaces the D3D shader of every object whose original bytecode hash is `hash` with the shader at addr (same
 * shader model as the original). Returns the number of objects changed, or negative: -1 no such objects (scan first),
 * -2 bad bytecode or another shader model, -3 no device, -4 an object could not be written or the swap table is full, else a CreateShader HRESULT. */
FNVMCP_EXPORT int sfx_swap(int hash, int addr, int size) {
    if (size < 8 || size > (int)MAX_BYTES || (size & 3)) return -2;
    int first = -1;
    uint32_t kind = 0, first_ver = 0;
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < g_nrow; i++)
        if (g_row[i].hash == (uint32_t)hash) {
            first = i;
            kind = g_row[i].kind;
            first_ver = g_row_ver[i];
            break;
        }
    LeaveCriticalSection(&g_cs);
    if (first < 0) return -1;
    uint32_t vt = kind ? VT_VS : VT_PS, off = kind ? OFF_VS_D3D : OFF_PS_D3D;
    DWORD *code = (DWORD *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)size);
    if (!code) return -2;
    if (!A->read((uint32_t)addr, code, (uint32_t)size) || code[0] != first_ver) {
        HeapFree(GetProcessHeap(), 0, code);
        return -2;
    }
    if (!get_dev()) {
        HeapFree(GetProcessHeap(), 0, code);
        return -3;
    }
    void *shader = 0;
    HRESULT hr = ((CreateShader_t)vf(D, kind ? DV_CREATE_VS : DV_CREATE_PS))(D, code, &shader);
    HeapFree(GetProcessHeap(), 0, code);
    if (hr || !shader) return hr ? (int)hr : -3;
    int changed = 0, refused = 0;
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < g_nrow; i++) {
        Row *r = &g_row[i];
        if (r->hash != (uint32_t)hash || r->kind != kind || rd(r->obj) != vt) continue;
        uint32_t live = rd(r->obj + off), mine = (uint32_t)(uintptr_t)shader;
        Swap *sw = find_swap(r->obj, off, live);
        if (live != r->d3d) continue; /* the engine changed it since the scan */
        if (!sw && g_nswap >= MAX_SWAPS) {
            refused++;
            continue;
        }
        if (!swap_pointer(r->obj, off, mine)) {
            refused++;
            continue;
        }
        addref(mine); /* the object owns one reference, as it did to the shader it held */
        if (sw) {
            relp(sw->cur);
            sw->cur = mine;
        } else {
            Swap *n = &g_swap[g_nswap++];
            n->obj = r->obj;
            n->off = off;
            n->orig = live;
            n->cur = mine;
            n->hash = r->hash;
            n->size = r->size;
            n->ver = g_row_ver[i];
        }
        r->d3d = mine;
        changed++;
    }
    LeaveCriticalSection(&g_cs);
    relp((uint32_t)(uintptr_t)shader); /* ours: the objects keep it alive */
    if (!changed && refused) return -4;
    if (changed) A->log("shaderfx: swapped %d objects with hash %08X", changed, (unsigned)hash);
    return changed;
}

/* Puts the original shader back into every swapped object whose original hash is `hash` (all when all is set). */
static int restore_hash(uint32_t hash, int all) {
    int done = 0;
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < g_nswap;) {
        Swap *sw = &g_swap[i];
        if (!all && sw->hash != hash) {
            i++;
            continue;
        }
        uint32_t vt = sw->off == OFF_VS_D3D ? VT_VS : VT_PS;
        if (rd(sw->obj) == vt && rd(sw->obj + sw->off) == sw->cur) {
            if (!swap_pointer(sw->obj, sw->off, sw->orig)) {
                i++;
                continue;
            }
            relp(sw->cur);
            row_set_d3d(sw->obj, sw->off == OFF_VS_D3D, sw->orig);
            done++;
        }
        g_swap[i] = g_swap[--g_nswap];
    }
    LeaveCriticalSection(&g_cs);
    return done;
}

FNVMCP_EXPORT int sfx_restore(int hash) { return restore_hash((uint32_t)hash, 0); }
FNVMCP_EXPORT int sfx_restore_all(void) { return restore_hash(0, 1); }

/* ------------------------------------------------------------------------------------------------ init / exit */

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *api) {
    A = api;
    InitializeCriticalSection(&g_cs);
    QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&g_t0);
    HMODULE d3d = GetModuleHandleA("d3d9.dll");
    if (!d3d) {
        A->log("shaderfx: d3d9.dll is not loaded");
        return 3;
    }
    IMAGE_NT_HEADERS32 *nt = (IMAGE_NT_HEADERS32 *)((char *)d3d + ((IMAGE_DOS_HEADER *)d3d)->e_lfanew);
    g_d3d_lo = (uint32_t)(uintptr_t)d3d;
    g_d3d_hi = g_d3d_lo + nt->OptionalHeader.SizeOfImage;
    char mod[MAX_PATH], sys[MAX_PATH];
    UINT n = GetSystemDirectoryA(sys, sizeof sys);
    DWORD m = GetModuleFileNameA(d3d, mod, sizeof mod);
    g_non_system = !(n && m > n && _strnicmp(mod, sys, n) == 0 && mod[n] == '\\');
    if (g_non_system) A->log("shaderfx: d3d9.dll is not the system one (another renderer is loaded)");
    if (A->hook(ADDR_DEVICE_LOST, on_device_lost, 0, 0) < 0) {
        A->log("shaderfx: could not hook the device-lost path");
        return 4;
    }
    int bad = patch_call_site();
    if (bad) return bad;
    A->log("shaderfx: ready (8 effects, engine shader swap)");
    return 0;
}

FNVMCP_EXPORT void fnvmcp_module_exit(void) {
    g_shutdown = 1;
    sfx_restore_all();
    EnterCriticalSection(&g_cs);
    for (int i = 0; i < SLOTS; i++) {
        void *ps = g_slot[i].ps;
        memset(&g_slot[i], 0, sizeof g_slot[i]);
        if (ps) ((Rel_t)vf(ps, 2))(ps);
    }
    LeaveCriticalSection(&g_cs);
    release_default();
    rel(&g_vs);
    rel(&g_black);
}
