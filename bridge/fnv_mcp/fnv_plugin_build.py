"""fnv_plugin_build (Plan 2, W5): ship a native module as a normal xNVSE plugin that needs no engine companion.

The module source is the same as for fnv_native (fnvmcp_module.h); native/runtime/fnvmcp_rt.c provides the API inside
the plugin. Hooks need the prologue of each target planned before the game runs, so `hooks=[...]` lists the functions
the module may hook; the bridge plans them from the running game's code (or, when the game is closed, from the local
unpacked game image, which never leaves the machine) into fnvmcp_plan.h.

    scaffold name          a starting module source (state/fnv/plugins/<name>/<name>.c)
    build name [source] hooks=[...]
                           zig cc: runtime + module + plan into state/fnv/plugins/<name>/<name>.dll
    install name           as the MO2 mod "fnvmcp plugin <name>" in the dev profile, enabled (game closed); then
                           fnv_game restart loads it
    log name               the plugin's log file (written next to its DLL; MO2 puts new files in overwrite)
"""
import json
import re
import struct
import subprocess
import time
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_native import SDK_SOURCE, c_name, source_path
from .safe_paths import check_includes

RUNTIME = Path(__file__).resolve().parents[1] / 'native' / 'runtime'
TEMPLATE = '''/* %(name)s: a native module for Fallout: New Vegas (fnvmcp_module.h). Runs inside the game as a hot module
 * (fnv_native load) or as its own xNVSE plugin (fnv_plugin_build). */
#include "fnvmcp_module.h"

static const FnvmcpApi *api;

static void on_frame(uint32_t frame, void *user) {
    (void)user;
    if (frame %% 600 == 1) api->log("%(name)s: frame %%u", frame);
}

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) {
    api = a;
    api->on_frame(on_frame, 0);
    api->log("%(name)s ready");
    return 0;
}
'''


SHARING_NOTE = ('Local use only if the source holds decompiled game code (fnv_code output, or functions copied from '
                'the decomp): such a DLL should not be shared. A plugin made only of your own code calling the '
                'engine is yours to share.')


def mod_name(name):
    return 'fnvmcp plugin %s' % name


class PluginTools:
    def __init__(self, live, zig=None):
        self.live = live
        self.zig = zig

    def _folder(self, name):
        if not c_name(name):           # '---' or '..' would put the files in the plugins folder itself (BUG-029c)
            raise ConvError('BAD_NAME', 'The module name %r has no letter or digit to name the C file after. Next: '
                            'use a name such as my_hooks.' % (name,))
        return Path(self.live.project.state_dir) / 'fnv' / 'plugins' / c_name(name)

    def _zig(self):
        if self.zig:
            return str(self.zig)
        from .fnv_link import find_zig
        return str(find_zig(self.live.project))

    def _image_path(self):
        """The unpacked game image: fnv.clean_exe, then FNV_CLEAN_EXE, then ~/.fnvdecomp/FalloutNV.exe."""
        from .locate import clean_image
        return clean_image(self.live.project)

    def scaffold(self, name):
        folder = self._folder(name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('%s.c' % c_name(name))
        if path.exists():
            raise ConvError('EXISTS', '%s exists; edit it or pick another name.' % path)
        path.write_text(TEMPLATE % {'name': c_name(name)}, encoding='utf-8')
        return verdict('Scaffolded %s' % path, 'Edit it, then fnv_native load it hot, or fnv_plugin_build build.',
                       source=str(path))

    # ------------------------------------------------------------------ prologue plans
    def _code_bytes(self, address, length=24):
        """The original bytes at address: from the local game image when there is one (critique fix 2026-11-21: the
        running game's bytes may already be a hook of a loaded module), else from the running game, refused when the
        companion has a hook there."""
        try:
            return self._image_code(address, length), 'local game image'
        except ConvError as missing:
            no_image = missing
        try:
            running = self.live.game.process(fresh=True)
        except Exception:  # noqa: BLE001
            running = None
        if not running:
            raise no_image
        try:
            hooks = self.live.link.one('hooks').get('hooks') or []
        except Exception:  # noqa: BLE001
            hooks = []
        for h in hooks:
            target, stolen = int(str(h.get('target')), 16), int(h.get('stolen') or 5)
            if target <= address < target + max(stolen, 5):
                raise ConvError('PROLOGUE_HOOKED', '0x%08X is hooked in the running game (hook %s), so its bytes '
                                'there are not the original prologue. Next: unload the module that hooks it '
                                '(fnv_native list, unload) and build again, or set fnv.clean_exe to your unpacked '
                                'FalloutNV.exe so plans come from it.' % (address, h.get('id')))
        raw = self.live.reads([(address, length)])[0]
        if not raw:
            raise no_image
        return raw, 'running game'

    def _image_code(self, address, length=24):
        image = self._image_path()
        if not image.is_file():
            raise ConvError('NO_CODE', 'The game is not running and no local game image is at %s (fnv.clean_exe, '
                            'FNV_CLEAN_EXE), so the prologue of 0x%08X cannot be planned. Next: start the game and '
                            'build again.' % (image, address))
        data = image.read_bytes()
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        count = struct.unpack_from('<H', data, pe + 6)[0]
        opt = struct.unpack_from('<H', data, pe + 20)[0]
        base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
        for i in range(count):
            off = pe + 24 + opt + 40 * i
            vsize, va, rsize, raw_ptr = struct.unpack_from('<IIII', data, off + 8)
            if va <= address - base < va + max(vsize, rsize):
                at = raw_ptr + address - base - va
                return data[at:at + length], 'local game image'
        raise ConvError('NO_CODE', '0x%08X is outside the game image.' % address)

    def _plans(self, hooks):
        from .fnv_deep import decode_prologue
        engine = self.live.engine()
        rows, out = [], []
        for h in hooks or []:
            text = str(h).strip()
            address = int(text, 16) if re.fullmatch(r'(0x)?[0-9A-Fa-f]{6,8}', text) else engine.resolve(text)
            raw, where = self._code_bytes(address)
            stolen, relocs = decode_prologue(raw)
            if len(relocs) > 4:
                raise ConvError('PROLOGUE', 'The prologue of %s has more than four relative operands.' % text)
            rows.append('    {0x%08Xu, %du, {%s}, %du},' % (address, stolen, ', '.join(str(r) for r in relocs) or '0',
                                                          len(relocs)))
            out.append({'function': text, 'address': '0x%08X' % address, 'stolen': stolen, 'relocs': relocs,
                        'from': where})
        return rows, out

    # ------------------------------------------------------------------ build / install
    def build(self, name, source=None, hooks=None):
        folder = self._folder(name)
        ident = c_name(name)
        if source:
            path = source_path(source)
            check_includes(path.read_text(encoding='utf-8') if path else source)     # H5
            folder.mkdir(parents=True, exist_ok=True)
            if path is None:
                path = folder / ('%s.c' % ident)
                path.write_text(source, encoding='utf-8')
        else:
            path = folder / ('%s.c' % ident)
            if not path.is_file():
                raise ConvError('NO_SOURCE', 'No source for %s. Next: scaffold, or pass source.' % name)
            check_includes(path.read_text(encoding='utf-8'))
        rows, plans = self._plans(hooks)
        hooks_warning = self._hooks_warning(path.read_text(encoding='utf-8', errors='replace'), hooks)
        header = ['/* Generated by fnv_plugin_build: the plugin name and the prologue plans of the hookable functions. */',
                  '#pragma once', '#include <stdint.h>',
                  'typedef struct { uint32_t target, stolen; uint8_t relocs[4]; uint8_t nrelocs; } RtPlan;',
                  '#define FNVMCP_PLUGIN_NAME "%s"' % ident, 'static const RtPlan rt_plans[] = {']
        header += rows or ['    {0u, 0u, {0}, 0u},']
        header += ['};', '']
        (folder / 'fnvmcp_plan.h').write_text('\n'.join(header), encoding='utf-8')
        sdk = Path(self.live.project.state_dir) / 'fnv' / 'sdk'
        out = folder / ('%s.dll' % ident)
        cmd = [self._zig(), 'cc', '-target', 'x86-windows-gnu', '-shared', '-O2', '-std=c11', '-Wall',
               '-I', str(folder), '-I', str(SDK_SOURCE), '-I', str(sdk), '-I', str(RUNTIME), '-o', str(out),
               str(RUNTIME / 'fnvmcp_rt.c'), str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ConvError('BUILD_FAILED', 'zig cc failed:\n%s' % result.stderr[-6000:],
                            {'stderr': result.stderr[-6000:]})
        # the hooks of this build, so fnv_author package build=true can build the module the same way (tool gaps 6)
        (folder / 'build.json').write_text(json.dumps({'hooks': list(hooks or [])}), encoding='utf-8')
        return verdict('Built the xNVSE plugin %s' % out.name, 'Next: install (with the game closed), then fnv_game restart.',
                       dll=str(out), hooks=plans, warnings=result.stderr.strip()[-2000:] or None, note=SHARING_NOTE,
                       warning=hooks_warning)

    def saved_hooks(self, name):
        """The hooks the last build of this module listed (build.json beside its DLL), or []."""
        try:
            return list(json.loads((self._folder(name) / 'build.json').read_text(encoding='utf-8')).get('hooks') or [])
        except (OSError, ValueError, AttributeError):
            return []

    @staticmethod
    def _hooks_warning(text, hooks):
        """A sentence when the module asks for hooks (api->hook) but the build planned none: the plugin's runtime
        refuses a hook with no prologue plan, so it would load and do nothing."""
        if hooks or not re.search(r'->\s*hook\s*\(', text or ''):
            return None
        return ('The source calls api->hook but hooks=[...] is empty, so no prologue is planned and every hook will be '
                'refused when the plugin loads. Next: build again with hooks listing each function it hooks.')

    def install(self, name):
        from .fnv_link import enable_mod
        game = self.live.game
        if game.process(fresh=True):
            raise ConvError('GAME_RUNNING', 'Quit the game first (fnv_game quit): a loaded plugin DLL cannot be replaced.')
        ident = c_name(name)
        dll = self._folder(name) / ('%s.dll' % ident)
        if not dll.is_file():
            raise ConvError('NOT_BUILT', 'Build it first (fnv_plugin_build build).')
        layout = game.layout() if hasattr(game, 'layout') else None
        if layout is not None and layout.kind == 'direct':
            game.guard_direct('install a plugin DLL into your real Data folder', layout)
            target = layout.nvse_plugins_dir / dll.name
            backup = None
            if target.exists() and target.read_bytes() != dll.read_bytes():
                backup = Path(self.live.project.state_dir) / 'fnv' / 'install-backups' / ('%d-%s' % (time.time(), dll.name))
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.read_bytes())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(dll.read_bytes())
            self.live.journal.add({'kind': 'plugin_install', 'plugin': ident, 'target': str(target), 'undo': None})
            return verdict('Installed %s into the game folder' % dll.name,
                           'It loads at the next start of the game. Remove the file to uninstall it.',
                           target=str(target), backup=str(backup) if backup else None, note=SHARING_NOTE)
        fnv = self.live.project.fnv
        mo2_root, profile = (layout.mo2_root, layout.profile) if layout is not None else (fnv.mo2_root, fnv.mo2_profile)
        target = Path(mo2_root) / 'mods' / mod_name(ident) / 'NVSE' / 'Plugins' / dll.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dll.read_bytes())
        enabled = enable_mod(mo2_root, profile, mod_name(ident))
        self.live.journal.add({'kind': 'plugin_install', 'plugin': ident, 'target': str(target), 'undo': None})
        return verdict('Installed %s as the MO2 mod "%s"' % (dll.name, mod_name(ident)),
                       'It loads at the next start: fnv_game restart (or fnv_game launch).', target=str(target),
                       enabledNow=enabled, note=SHARING_NOTE)

    def log(self, name):
        ident = c_name(name)
        fnv = self.live.project.fnv
        game_root = getattr(self.live.project, 'game_root', None)
        candidates = [Path(game_root) / 'Data' / 'NVSE' / 'Plugins' / ('%s.log' % ident),
                      Path(game_root) / ('%s.log' % ident)] if game_root else []
        if fnv and fnv.mo2_root:
            candidates = [Path(fnv.mo2_root) / 'overwrite' / 'NVSE' / 'Plugins' / ('%s.log' % ident),
                          Path(fnv.mo2_root) / 'mods' / mod_name(ident) / 'NVSE' / 'Plugins' / ('%s.log' % ident)] \
                + candidates
        for p in candidates:
            if p.is_file():
                lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
                return verdict('%d log lines in %s' % (len(lines), p), None, path=str(p), lines=lines[-200:])
        return verdict('No log yet', 'The plugin writes its log when the game loads it.',
                       searched=[str(p) for p in candidates], lines=[])

    def run(self, action, name=None, source=None, hooks=None):
        if not name:
            raise ConvError('BAD_ARGUMENTS', 'name is needed.')
        if action == 'scaffold':
            return self.scaffold(name)
        if action == 'build':
            return self.build(name, source, hooks)
        if action == 'install':
            return self.install(name)
        if action == 'log':
            return self.log(name)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: scaffold, build, install, log.' % action)
