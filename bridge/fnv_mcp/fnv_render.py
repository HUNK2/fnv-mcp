"""fnv_render and fnv_overlay (Plan 2, E-eyes.md sections 7 and 8).

fnv_render   toggle: the console toggles that still do something in retail (research/camera-render.md 6), run through
             their own commands, with the engine flag read before and after where its address is known.
             imagespace: apply or remove an image-space modifier (imod / rimod). shaders: RefreshShaders.
             lights: the scene lights of the world's shadow scene node (0x011F91C8 [0]) found by their RTTI class, with
             world position; which reach a reference by distance.
fnv_overlay  text: words drawn on screen by the engine's debug text layer (DebugText::Print 0x00A0F8B0 on the
             singleton from DebugText_Instance 0x00A0D9E0); clear removes what this tool drew. marker: a visible
             object placed at a position or on a reference (fnv_place: journaled, removed by clear); the trigger and
             collision volumes are shown with fnv_render toggle primitives / collision.
"""
import math
import re
import struct

from .fnv_conv import ConvError, verdict

TOGGLES = {
    # name: (console command, flag address or None, what it shows)
    'wireframe': ('ToggleWireframe', 0x011CAE46, 'the world as wireframe'),
    'sky': ('ToggleSky', 0x011CAE45, 'the sky on or off'),
    'grass': ('ToggleGrass', None, 'grass on or off'),
    'trees': ('ToggleTrees', None, 'trees on or off'),
    'leaves': ('ToggleLeaves', None, 'tree leaves on or off'),
    'lod': ('ToggleLODLand', None, 'distant land (LOD) on or off'),
    'menus': ('ToggleMenus', 0x0118C6F0, 'the HUD and menus hidden or shown'),
    'borders': ('ToggleBorders', 0x011CAE44, 'cell borders drawn on the land'),
    'primitives': ('TogglePrimitives', None, 'trigger boxes, activator volumes and other primitives drawn'),
    'collision': ('ToggleCollisionGeometry', None, 'collision geometry drawn'),
    'test_light': ('ToggleTestLight', 0x011CAE47, 'a light at the camera'),
    'motion_blur': ('ToggleMotionBlur', None, 'motion blur'),
    'eof_imagespace': ('ToggleEOFImageSpace', None, 'end-of-frame image space effects'),
    'debug_text': ('ToggleDebugText', None, 'the debug text layer (fnv_overlay text) and debug pages'),
}
SHADOW_SCENE_NODES = 0x011F91C8
SCENE_LIGHTS = 0xB4
NODE_WORLD_POS = 0x8C
SCENE_LIGHT_SOURCE = 0xF8            # ShadowSceneLight -> its NiPointLight (checked live)
DEBUG_TEXT_INSTANCE, DEBUG_TEXT_PRINT = 0x00A0D9E0, 0x00A0F8B0
PLAIN_WORD = re.compile(r'[A-Za-z0-9_.\-]{1,120}')     # an EditorID, a form id or a toggle argument: never console text
DEFAULT_MARKER = '0011C532'        # GomorrahSphereHangingLight: a small glowing sphere from FalloutNV.esm


class RenderTools:
    def __init__(self, live):
        self.live = live

    def toggle(self, name, arg=None):
        self.live.in_game()
        if name not in TOGGLES:
            raise ConvError('BAD_ARGUMENTS', 'toggle is one of %s.' % ', '.join(sorted(TOGGLES)))
        command, flag, what = TOGGLES[name]
        if arg is not None and not PLAIN_WORD.fullmatch(str(arg)):
            # the argument goes into a console line (bug hunt 2026-11-18)
            raise ConvError('BAD_ARGUMENTS', 'arg is a plain word or number, not console text.')
        before = (self.live.reads([(flag, 1)])[0] or b'\0')[0] if flag else None
        self.live.changing('render toggle')
        out = self.live.console('%s%s' % (command, ' %s' % arg if arg else ''))
        after = (self.live.reads([(flag, 1)])[0] or b'\0')[0] if flag else None
        state = None if after is None else bool(after)     # menus: non-zero while shown (seen live, N20)
        # menus: the HUD back to how it was (hud_set reads the flag; S3 review I3); other toggles stay "run again"
        undo = {'op': 'hud_set', 'on': bool(before)} if name == 'menus' and before is not None else None
        self.live.journal.add({'kind': 'render_toggle', 'toggle': name, 'undo': undo})
        return verdict('%s: %s%s' % (command, 'accepted' if out.get('accepted') else 'refused',
                                     '' if state is None else ' (now %s)' % ('on' if state else 'off')),
                       'The game\'s own console toggle (%s). Run the same toggle again to undo it.' % what,
                       toggle=name, command=command, flagBefore=before, flagAfter=after, on=state,
                       output=out.get('output'))

    def imagespace(self, modifier, remove=False, strength=1.0):
        self.live.in_game()
        if not modifier:
            raise ConvError('BAD_ARGUMENTS', 'imagespace needs modifier (an IMAD form).')
        if not PLAIN_WORD.fullmatch(str(modifier)):
            raise ConvError('BAD_ARGUMENTS', 'modifier is an IMAD EditorID or form id, not console text.')
        line = ('rimod %s' if remove else 'imod %s') % modifier
        self.live.changing('render imagespace')
        out = self.live.console(line)
        return verdict('%s %s' % (line, 'accepted' if out.get('accepted') else 'refused'),
                       'ApplyImageSpaceModifier / RemoveImageSpaceModifier (ImageSpaceModifierInstanceForm::Trigger '
                       '0x005299A0, Stop 0x00529C90).', output=out.get('output'))

    def shaders(self):
        self.live.in_game()
        out = self.live.console('RefreshShaders')
        return verdict('RefreshShaders %s' % ('accepted' if out.get('accepted') else 'refused'),
                       'BSShaderManager_ReloadShaders 0x00B557D0 through its console command.',
                       output=out.get('output'))

    def lights(self, ref=None, limit=64):
        """Scene lights of the world's shadow scene node: each list item whose RTTI class is a light (or a scene light
        holding one), with its world position; with ref, the distance from the reference and whether it is within
        the light's radius."""
        self.live.in_game()
        ssn = self.live.u32s([SHADOW_SCENE_NODES])[0]
        if not ssn:
            raise ConvError('NOT_READY', 'No shadow scene node (no world loaded).')
        head = self.live.reads([(ssn + SCENE_LIGHTS, 12)])[0]
        first = struct.unpack('<I', head[:4])[0] if head else 0
        items, node, guard = [], first, 0
        while node and guard < 512:
            raw = self.live.reads([(node, 12)])[0]
            if not raw:
                break
            nxt, _prev, data = struct.unpack('<III', raw)
            if data:
                items.append(data)
            node, guard = nxt, guard + 1
            if node == first:
                break
        lights = self.live.u32s([p + SCENE_LIGHT_SOURCE for p in items[:limit]]) if items else []
        classes = self.live.rtti([x for x in lights if x]) if lights else {}
        rows = []
        for p, light in zip(items[:limit], lights):
            if not light:
                continue
            cls = (classes.get(light) or {}).get('class')
            pos = struct.unpack('<3f', self.live.reads([(light + NODE_WORLD_POS, 12)])[0] or b'\0' * 12)
            rows.append({'sceneLight': '0x%08X' % p, 'light': '0x%08X' % light, 'class': cls,
                         'position': [round(v, 1) for v in pos]})
        if ref:
            r = self.live.resolve(ref)
            x, y, z = self.live.values(['GetPos X', 'GetPos Y', 'GetPos Z'], r['ptr'])
            for row in rows:
                row['distance'] = round(math.dist((x, y, z), row['position']), 1)
            rows.sort(key=lambda row: row['distance'])
        return verdict('%d scene lights%s' % (len(rows), ' (nearest first from %s)' % ref if ref else ''),
                       'The scene light list of the world\'s shadow scene node (0x011F91C8 [0] +0xB4: nodes of next, '
                       'prev, ShadowSceneLight; its NiLight at +0xF8, checked live); world position at NiAVObject '
                       '+0x8C.',
                       lights=rows)

    def run(self, action, **a):
        if action == 'toggle':
            return self.toggle(a.get('name'), a.get('arg'))
        if action == 'imagespace':
            return self.imagespace(a.get('name'), bool(a.get('remove')))
        if action == 'shaders':
            return self.shaders()
        if action == 'lights':
            return self.lights(a.get('ref'))
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: toggle, imagespace, shaders, lights.' % action)


class OverlayTools:
    def __init__(self, live, place=None):
        self.live = live
        self.place = place
        self.texts = {}                 # (x, y) -> text
        self.markers = []               # form ids placed by marker

    def _debug_text(self):
        out = self.live.engine_call('0x%08X' % DEBUG_TEXT_INSTANCE, None, [{'type': 'int', 'value': 1}], 'ptr',
                                    'cdecl', True)
        return out.get('result')

    def text(self, text, x=40.0, y=120.0):
        self.live.in_game()
        if text is None:
            raise ConvError('BAD_ARGUMENTS', 'text needs text (and x, y in screen pixels).')
        this = self._debug_text()
        if not this or int(this, 16) == 0:
            raise ConvError('NOT_READY', 'The debug text layer is not created.')
        args = [{'type': 'string', 'value': str(text)[:250]}, {'type': 'float', 'value': float(x)},
                {'type': 'float', 'value': float(y)}, {'type': 'int', 'value': 1}, {'type': 'int', 'value': -1},
                {'type': 'float', 'value': -1.0}, {'type': 'int', 'value': 0}, {'type': 'int', 'value': 0}]
        self.live.engine_call('0x%08X' % DEBUG_TEXT_PRINT, this, args, 'int', 'thiscall', True)
        self.texts[(float(x), float(y))] = str(text)
        return verdict('Drawn at (%d, %d): %s' % (x, y, text),
                       'DebugText::Print 0x00A0F8B0 (the layer the console draws with); a second text at the same '
                       'place replaces it; clear removes them. If nothing shows, the layer may need fnv_render toggle '
                       'debug_text.', texts=len(self.texts))

    def marker(self, position=None, ref=None, base=None, scale=1.0):
        self.live.in_game()
        if self.place is None:
            raise ConvError('NOT_AVAILABLE', 'marker needs the placement tools.')
        out = self.place.at(base or DEFAULT_MARKER, position=position, near=ref, scale=scale)
        fid = out.get('ref')
        if fid:
            self.markers.append(fid)
        return verdict('Marker %s placed' % (fid or ''), 'A visible object (default: the small glowing sphere '
                       'GomorrahSphereHangingLight) placed with fnv_place, so it is journaled; clear removes it.',
                       marker=fid, placement=out)

    def clear(self):
        self.live.in_game()
        this = self._debug_text() if self.texts else None
        if self.texts and (not this or int(this, 16) == 0):
            self.texts.clear()      # stale after a restart: the layer is gone, nothing to blank
        cleared = 0
        for (x, y) in list(self.texts):
            args = [{'type': 'string', 'value': ''}, {'type': 'float', 'value': x}, {'type': 'float', 'value': y},
                    {'type': 'int', 'value': 1}, {'type': 'int', 'value': -1}, {'type': 'float', 'value': -1.0},
                    {'type': 'int', 'value': 0}, {'type': 'int', 'value': 0}]
            self.live.engine_call('0x%08X' % DEBUG_TEXT_PRINT, this, args, 'int', 'thiscall', True)
            cleared += 1
        self.texts.clear()
        removed = []
        for fid in self.markers:
            try:
                self.place.remove(fid)
                removed.append(fid)
            except Exception:  # noqa: BLE001 - already gone
                pass
        self.markers = []
        return verdict('Cleared %d texts and %d markers' % (cleared, len(removed)), None, markers=removed)

    def run(self, action, **a):
        if action == 'text':
            return self.text(a.get('text'), a.get('x', 40.0) if a.get('x') is not None else 40.0,
                             a.get('y', 120.0) if a.get('y') is not None else 120.0)
        if action == 'marker':
            return self.marker(a.get('position'), a.get('ref'), a.get('base'), a.get('scale') or 1.0)
        if action == 'clear':
            return self.clear()
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: text, marker, clear.' % action)
