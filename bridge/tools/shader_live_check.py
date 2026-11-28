"""fnv_shader live check (SHADER-PLAN Task 8): the real tool against the running game, in-process, as a script.

    .venv/Scripts/python.exe tools/shader_live_check.py --project projects/<file>.json [--skip-export]

Run it in the app's Terminal panel (MO2 launches fail from a sandboxed shell), with no MCP client using the game's
pipe at the same time (the companion pipe has one instance). The game must run in the project's dev profile in an
exterior cell (fnv_game launch start=coc:GoodspringsSource); the export step quits and relaunches it.

Checks (the design's live list):
  1. effect_add invert -> changedPercent > 20 and the HUD stays on top (not inverted); effect_set a param -> the
     picture changes again; effect_remove -> under 2% from the first picture; fnv_undo after an add removes it.
  2. replace SKY.pso with a magenta body -> the top of the picture mostly magenta; restore -> back; fnv_undo of a
     replace restores.
  3. An incompatible source -> INCOMPATIBLE, and status shows no replacement.
  4. Effects plus a replacement, then fnv_native unload fnvmcp_shaderfx -> the picture matches the baseline.
  5. export set=livecheck install=true with the game closed, relaunch -> the effect shows while fnvmcp_shaderfx is
     not loaded; the test mods are disabled afterwards.
Exit code 0 only when every check passed. A JSON summary is written next to the pictures."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(BRIDGE / 'tests'))

INVERT = '''sampler2D scene : register(s0);
float4 amount : register(c2) = float4(1, 0, 0, 0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 {
    float4 c = tex2D(scene, uv);
    return float4(lerp(c.rgb, 1 - c.rgb, amount.x), 1);
}
'''
TINT = '''sampler2D scene : register(s0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 {
    float4 c = tex2D(scene, uv);
    return float4(c.r, c.g * 0.4, c.b * 0.4, 1);
}
'''
BAD_SKY = '''float4 huge : register(c20);
float4 main() : COLOR0 { return huge; }
'''
SKY = 'SKY.pso'


class Check:
    def __init__(self, session, out):
        self.s = session
        self.out = out
        self.results = []

    def call(self, tool, args=None, timeout=600):
        r = self.s.call(tool, args or {}, timeout)
        r.pop('_size', None)
        return r

    def ok(self, tool, args=None):
        r = self.call(tool, args)
        if not r.get('ok'):
            raise RuntimeError('%s %s failed: %s' % (tool, json.dumps(args)[:200], json.dumps(r.get('error'))[:600]))
        return r.get('data') or {}

    def record(self, name, passed, **info):
        self.results.append(dict(name=name, passed=bool(passed), **info))
        print('%s  %s  %s' % ('PASS' if passed else 'FAIL', name, json.dumps(info)[:400]), flush=True)

    def photo(self, hide_hud=True):
        time.sleep(0.5)                       # a few frames, so a change made just now is drawn
        d = self.ok('fnv_photo', {'action': 'view', 'hide_hud': hide_hud})
        return d.get('path') or d.get('file')

    def compare(self, baseline):
        time.sleep(0.5)
        return self.ok('fnv_photo', {'action': 'compare', 'baseline': baseline})

    def shader(self, **args):
        return self.call('fnv_shader', args)


def abs_path(session, p):
    p = Path(p)
    return p if p.is_absolute() else Path(session.project.state_dir) / p


def fraction(path, test, box=None):
    from PIL import Image
    im = Image.open(path).convert('RGB')
    if box:
        w, h = im.size
        im = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    px = list(im.getdata())
    return sum(1 for p in px if test(p)) / float(len(px) or 1)


def band_diff(a, b, box=(0, 0.3, 1, 0.82)):
    """Share of pixels that differ clearly between two HUD-less pictures, in the band between the sky (clouds move
    in real time) and the HUD."""
    from PIL import Image
    x = Image.open(a).convert('RGB')
    y = Image.open(b).convert('RGB').resize(x.size)
    w, h = x.size
    crop = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
    pa, pb = x.crop(crop).getdata(), y.crop(crop).getdata()
    n = sum(1 for p, q in zip(pa, pb) if abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) > 60)
    return n / float(len(pa) or 1)


def red_tinted(path, box=(0, 0.3, 1, 0.82)):
    """Mean red over mean green in the band: the TINT effect keeps red and cuts green and blue to 40%."""
    from PIL import Image
    im = Image.open(path).convert('RGB')
    w, h = im.size
    px = list(im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))).getdata())
    r = sum(p[0] for p in px) / float(len(px))
    g = sum(p[1] for p in px) / float(len(px))
    return r / (g or 1)


def magenta(p):
    return p[0] > 180 and p[2] > 180 and p[1] < 90


def hud_on_top(hud_on, hud_off):
    """Among the pixels the HUD changes (HUD on vs off, both with the invert effect), the HUD's amber stays amber
    (red over blue) when the HUD draws after the effect; an inverted HUD would be blue."""
    from PIL import Image
    a = Image.open(hud_on).convert('RGB')
    b = Image.open(hud_off).convert('RGB').resize(a.size)
    reds = blues = n = 0
    for p, q in zip(a.getdata(), b.getdata()):
        if abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) > 120:
            n += 1
            reds += p[0]
            blues += p[2]
    return n, (reds / n if n else 0), (blues / n if n else 0)


def unpaused(c):
    """Out of the pause menu, and kept out of it while the window has no focus."""
    c.call('fnv_window', {'action': 'unpause'})
    c.call('fnv_window', {'action': 'background'})
    # A still view: a free camera where the player looks, a little up so the sky fills the top, the world frozen
    # (the frame still renders, so effects and shaders still draw; clouds and the idle camera stop moving).
    c.call('fnv_camera', {'action': 'pov', 'view': 'first'})
    c.call('fnv_camera', {'action': 'set', 'pitch': -12, 'freeze': True})
    time.sleep(1)


def run_live(c):
    s = c.s
    unpaused(c)
    st = c.ok('fnv_shader', {'action': 'status'})
    c.record('status answers', True, status={k: st.get(k) for k in ('module', 'package', 'renderer', 'inMenu')})
    c.shader(action='effect_remove', name='all', photo=False)
    c.shader(action='restore', name='all')

    base = c.photo()
    base_abs = str(abs_path(s, base))

    # 1. screen effect
    r = c.shader(action='effect_add', name='invert', source=INVERT)
    d = r.get('data') or {}
    c.record('effect_add invert changes the picture', r.get('ok') and (d.get('changedPercent') or 0) > 20,
             changedPercent=d.get('changedPercent'), costUs=d.get('costUs'), note=d.get('note'),
             error=r.get('error'))
    on = c.photo(hide_hud=False)
    off = c.photo(hide_hud=True)
    n, red, blue = hud_on_top(str(abs_path(s, on)), str(abs_path(s, off)))
    c.record('HUD draws over the effect (stays amber)', n > 200 and red > blue, hudPixels=n, red=round(red),
             blue=round(blue), pictures=[on, off])
    after_add = off
    r = c.shader(action='effect_set', name='invert', params={'amount': 0.5})
    d = r.get('data') or {}
    cmp = c.compare(str(abs_path(s, after_add)))
    c.record('effect_set amount 0.5 changes the picture again', r.get('ok') and cmp.get('changedPercent', 0) > 5,
             changedPercent=cmp.get('changedPercent'), error=r.get('error'))
    r = c.shader(action='effect_remove', name='invert')
    now = c.photo()
    diff = band_diff(base_abs, str(abs_path(s, now)))
    c.record('effect_remove: back to the first picture (< 2% below the sky)', r.get('ok') and diff < 0.02,
             changed=round(diff * 100, 2), picture=now)
    r = c.shader(action='effect_add', name='undo-me', source=TINT, photo=False)
    u = c.call('fnv_undo', {})
    lst = c.ok('fnv_shader', {'action': 'effect_list'})
    names = [e.get('name') for e in lst.get('effects') or []]
    c.record('fnv_undo after effect_add removes it', r.get('ok') and u.get('ok') and 'undo-me' not in names,
             effects=names, undo=u.get('error'))

    # 2. engine replacement
    t = c.ok('fnv_shader', {'action': 'template', 'name': SKY})
    src = t.get('source') or ''
    magenta_src = re.sub(r'return\s+[^;]*;\s*}\s*$', 'return float4(1, 0, 1, 1);\n}\n', src.rstrip(), flags=re.S)
    r = c.shader(action='replace', name=SKY, source=magenta_src)
    d = r.get('data') or {}
    shot = c.photo()
    m = fraction(str(abs_path(s, shot)), magenta, (0, 0, 1, 0.25))
    c.record('replace SKY.pso magenta: top quarter mostly magenta', r.get('ok') and m > 0.5, magentaTop=round(m, 3),
             objects=d.get('objects'), names=d.get('names'), frameMs=d.get('frameMs'), warnings=d.get('warnings'),
             error=r.get('error'), picture=shot)
    r = c.shader(action='restore', name=SKY)
    shot = c.photo()
    m2 = fraction(str(abs_path(s, shot)), magenta, (0, 0, 1, 0.25))
    c.record('restore SKY.pso: magenta gone', r.get('ok') and m2 < 0.05, magentaTop=round(m2, 3), picture=shot)
    r = c.shader(action='replace', name=SKY, source=magenta_src, photo=False)
    u = c.call('fnv_undo', {})
    shot = c.photo()
    m3 = fraction(str(abs_path(s, shot)), magenta, (0, 0, 1, 0.25))
    c.record('fnv_undo of a replace restores', r.get('ok') and u.get('ok') and m3 < 0.05, magentaTop=round(m3, 3),
             undo=u.get('error'))

    # 3. incompatible
    r = c.shader(action='replace', name=SKY, source=BAD_SKY)
    st = c.ok('fnv_shader', {'action': 'status'})
    c.record('incompatible source refused, nothing replaced',
             not r.get('ok') and (r.get('error') or {}).get('code') == 'INCOMPATIBLE' and not st.get('replacements'),
             error=(r.get('error') or {}).get('code'), replacements=st.get('replacements'))

    # 4. unload puts everything back (the tint effect and the magenta sky stay in the manifest for the export)
    c.shader(action='effect_remove', name='all', photo=False)
    c.shader(action='effect_add', name='tint', source=TINT, photo=False)
    c.shader(action='replace', name=SKY, source=magenta_src, photo=False)
    mid = c.photo()
    u = c.call('fnv_native', {'action': 'unload', 'name': 'fnvmcp_shaderfx'})
    now = c.photo()
    now_abs = str(abs_path(s, now))
    diff = band_diff(base_abs, now_abs)
    mt = fraction(now_abs, magenta, (0, 0, 1, 0.25))
    c.record('fnv_native unload: the picture matches the baseline (< 2% below the sky, no magenta sky)',
             u.get('ok') and diff < 0.02 and mt < 0.05, changed=round(diff * 100, 2), magentaTop=round(mt, 3),
             withEffects=mid, picture=now, unload=u.get('error'))
    return base_abs


def run_export(c, base_abs, start):
    """Export with the game closed, relaunch, and look: the baked plugin draws the effect without the live module."""
    s = c.s
    from fnv_mcp.fnv_link import disable_mod
    q = c.call('fnv_game', {'action': 'quit'})
    if not q.get('ok'):
        c.record('quit for export', False, error=q.get('error'))
        return
    try:
        # the live manifest has no live game now; export offers the saved sources
        r = c.shader(action='export', set='livecheck', install=True)
        d = r.get('data') or {}
        c.record('export set=livecheck install=true', r.get('ok') and d.get('installed') and d.get('plugin'),
                 plugin=d.get('plugin'), packages=d.get('packages'), skipped=d.get('skipped'), error=r.get('error'))
        if not r.get('ok'):
            return
        ln = c.call('fnv_game', {'action': 'launch', 'start': start}, timeout=900)
        if not ln.get('ok'):
            c.record('relaunch after export', False, error=ln.get('error'))
            return
        time.sleep(5)
        unpaused(c)
        mods = c.ok('fnv_native', {'action': 'list'})
        loaded = json.dumps(mods)
        now = c.photo()
        now_abs = str(abs_path(s, now))
        ratio = red_tinted(now_abs)
        mt = fraction(now_abs, magenta, (0, 0, 1, 0.25))
        c.record('the exported plugin draws the tint effect without fnvmcp_shaderfx',
                 'fnvmcp_shaderfx' not in loaded and ratio > 1.8, redOverGreen=round(ratio, 2), picture=now)
        c.record('the exported Shaders folder draws the magenta sky', mt > 0.3, magentaTop=round(mt, 3))
    finally:
        fnv = s.project.fnv
        if getattr(fnv, 'mo2_root', None):
            for mod in ('fnvmcp shaders livecheck', 'fnvmcp_shaders_livecheck'):
                try:
                    disable_mod(fnv.mo2_root, fnv.mo2_profile, mod)
                except Exception as e:  # noqa: BLE001 - cleanup reports, never hides the results
                    print('cleanup: could not disable %s: %s' % (mod, e))
            print('cleanup: test mods disabled in the %s profile (restart the game to drop the plugin)'
                  % fnv.mo2_profile)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', required=True)
    ap.add_argument('--start', default='coc:GoodspringsSource')
    ap.add_argument('--skip-export', action='store_true')
    a = ap.parse_args()
    from fnv_mcp import fnv_link
    from fnv_mcp.config import Project
    # The game's pipe takes one client: wait until an MCP server that used it lets go (20 s idle), as the regress
    # runner does, before the session's own link takes it.
    probe = fnv_link.EngineLink(getattr(Project.load(a.project).fnv, 'engine_pipe', None))
    try:
        probe.connect(wait=60)
    except fnv_link.LinkError as e:
        print("cannot attach to the game's pipe (%s): stop the other client, then run again" % e)
        return 2
    finally:
        probe.close()
    from live.base import Session
    session = Session(a.project, engine_control='full')
    out = Path(session.project.state_dir) / 'fnv' / 'shader-live-check'
    out.mkdir(parents=True, exist_ok=True)
    c = Check(session, out)
    try:
        base = run_live(c)
        if not a.skip_export:
            run_export(c, base, a.start)
    except Exception as e:  # noqa: BLE001 - a crash in the run is a failed check
        c.record('run', False, error=str(e)[:800])
    finally:
        try:
            c.call('fnv_camera', {'action': 'restore'})
        except Exception:  # noqa: BLE001 - no game, nothing to restore
            pass
    (out / 'summary.json').write_text(json.dumps(c.results, indent=1), encoding='utf-8')
    failed = [r['name'] for r in c.results if not r['passed']]
    print('\nshader live check: %d passed, %d failed%s' % (len(c.results) - len(failed), len(failed),
                                                          (': ' + '; '.join(failed)) if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
