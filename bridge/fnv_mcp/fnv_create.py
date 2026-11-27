"""Plan 2 wave 4, creation loop: fnv_asset, fnv_form, fnv_place, fnv_author.

Facts (research/assets.md, research/forms-plugins.md; checked live where marked):
- ModelLoader *(0x011C3B3C). ClearUnusedModels(bool force) 0x00448620 (thiscall) drops every cached model nobody
  uses. A model reloads from disk when its users let go (disable) and come back (enable) after that purge.
- FileFinder::GetFile(name, mode, bufferSize, archiveType) 0x00AFDF20 is the file open choke point; it runs on
  loader threads too. A record hook with a snapshot of the name (selector 2 = stack argument 0) and the exit value
  (null = not found) gives the file log without any call from inside the hook.
- Script functions present in this setup (checked by compiling): TempCloneForm, SetModelPathEX,
  SetBipedModelPathEX, SetName, SetBaseItemValue, SetWeight, SetHealth, SetArmorAR, SetArmorDT, GetModelPath.
  CloneForm is not.
- TESDataHandler *(0x011C3F2C): CreateFormForType(type) 0x00465110, AddFormToDataHandler(form) 0x004603B0.
"""
import hashlib
import json
import math
import os
import re
import shutil
import struct
import time
import zipfile
from pathlib import Path

from . import config
from .fnv_args import capped
from .fnv_link import hexs, quoted, value_of, word
from .fnv_live import LiveError
from .safe_paths import inside, plain_name, read_roots

MODEL_LOADER = 0x011C3B3C
# TESBoundObject: boundMin then boundMax, three int16 each (the record's OBND), after TESForm (0x18) and TESObject's
# list pointers (0x18..0x24), read live 2026-11-18; TESObjectREFR +0x20: its base form.
# A snap rests the bottom of these bounds on the surface, not the origin: architecture such as WoodShack01 has its
# origin at its centre (bounds z -279..279) and sank half into the ground (seen live 2026-11-18).
BOUND_OFFSET = 0x24
REF_BASE_OFFSET = 0x20
FORM_TYPE_OFFSET = 4                         # TESForm's type byte
ACTOR_BASE_TYPES = (0x2A, 0x2B, 0x2C, 0x2D)  # NPC_, CREA, LVLC, LVLN: placed as actors, which stand on one point
# Footprint snap (2026-11-18): one ray under the centre let a building float on the downhill side of a slope or sit
# on a boulder under its middle. Rays are cast over the bounds' X/Y rectangle instead (turned to the heading, scaled),
# at most FOOT_SPACING apart and at most FOOT_MAX of them, pulled FOOT_INSET in from the edges. Objects narrower than
# FOOT_SMALL both ways keep one ray.
FOOT_SMALL = 48.0
FOOT_SPACING = 128.0
FOOT_MAX = 64
FOOT_INSET = 4.0
FOOT_REST = ('low', 'centre', 'high')
CLEAR_UNUSED_MODELS = 0x00448620
TEXTURE_REMOVE_BY_PATH = 0x00A61FB0      # cdecl (const char *path) -> bool: BSTexturePalette remove by path
FILE_GET = 0x00AFDF20
DATA_HANDLER = 0x011C3F2C
CREATE_FORM_FOR_TYPE = 0x00465110
ADD_FORM = 0x004603B0
IO_MANAGER = 0x01202D98
HF_EXIT, HF_RECORD = 1, 2
FILE_GET_BY_ENTRY = 0x00AF6340
TEXTURE_CREATE = 0x00A5FD70
RECORD_BUFFER, FORM_HEAP_FREE, FORM_DELETED_FLAGS = 0x011C54CC, 0x00401030, 0x20 | 0x4000
MENU_CODE_OF, REMOVE_MENU_ENTRIES, MENU_SYSTEM, RELOAD_MENU = 0x00A01860, 0x00A1CA20, 0x0071E290, 0x00720AA0
SNAP_NAME = 96          # bytes copied at the call: the name (lookup) or the file entry (archive)
# (function, hook label, snapshot selector of the name argument, source name): selector 2 = stack argument 0,
# 5 = stack argument 3 (GetFileByFileEntry(archiveType, entry, bufferSize, name), cdecl).
# The archive open passes no name (seen live): stack argument 1 is the BSA file entry, whose first 8 bytes are the
# name hash; the bridge maps it back to the path with the hashes stored in the archives (bsa_hashes).
FILE_HOOKS = ((FILE_GET, 'file_log', 2, 'lookup'), (FILE_GET_BY_ENTRY, 'file_log_bsa', 3, 'archive'),
              (TEXTURE_CREATE, 'file_log_tex', 2, 'texture'))
# NiSourceTexture::Create(const NiFixedString& name, ...): the snapshot of argument 0 starts with the string handle.


def bsa_hashes(path):
    """{file name hash: ['folder//file' paths]} of a BSA (v103/v104), from its own file records."""
    out = {}
    with open(path, 'rb') as stream:
        header = stream.read(36)
        if len(header) != 36 or header[:4] != b'BSA\0':
            return out
        version, offset, flags, folders, files, _fnl, name_len, _ff = struct.unpack('<8I', header[4:])
        if version not in (103, 104) or not flags & 1 or not flags & 2:
            return out
        stream.seek(offset)
        counts = [struct.unpack('<QII', stream.read(16))[1] for _ in range(folders)]
        dirs, hashes = [], []
        for count in counts:
            length = stream.read(1)[0]
            dirs.append(stream.read(length).rstrip(b'\0').decode('cp1252', 'cp1252-bytes'))
            hashes.append([struct.unpack('<QII', stream.read(16))[0] for _ in range(count)])
        names = stream.read(name_len).split(b'\0')
    i = 0
    for folder, folder_hashes in zip(dirs, hashes):
        for h in folder_hashes:
            if i < len(names):
                out.setdefault(h, []).append((folder + '\\' + names[i].decode('cp1252', 'cp1252-bytes')).lower())
            i += 1
    return out
SAFE_TYPES = {'STAT': 32, 'MISC': 31, 'ARMO': 24, 'AMMO': 41, 'KEYM': 46, 'BOOK': 25, 'LIGH': 30, 'NOTE': 49,
              'GLOB': 6, 'FLST': 85, 'ACTI': 21, 'WEAP': 40, 'ALCH': 47, 'CONT': 27}
REFUSED_TYPES = {'CELL', 'WRLD', 'DIAL', 'INFO', 'QUST', 'NPC_', 'CREA', 'SCPT', 'NAVM'}
SCAFFOLDS = ('item', 'interior', 'quest', 'terminal', 'perk', 'companion')


def _norm(path):
    return str(path).replace('/', '\\').strip('\\').lower()


AUDIO_EXTS = ('.ogg', '.wav', '.mp3')


def _data_rel(path):
    """A path relative to Data: the engine sometimes names files with a leading data\\."""
    p = _norm(path)
    return p[len('data\\'):] if p.startswith('data\\') else p


def _model_rel(path):
    """A model path as GetModelPath prints it: relative to meshes."""
    p = _norm(path)
    return p[len('meshes\\'):] if p.startswith('meshes\\') else p


def _cstr(raw):
    return raw.split(b'\0', 1)[0].decode('cp1252', 'replace')


PATH_TAGS = {'MODL', 'MOD2', 'MOD3', 'MOD4', 'ICON', 'MICO', 'ICO2', 'MIC2', 'TX00', 'TX01', 'TX02', 'TX03',
             'TX04', 'TX05', 'NAM1', 'NIFZ'}
PATHS_VERSION = 1      # of the asset_paths rows in the record index: a higher one makes every plugin read again


def _scan_paths(path):
    """([(path, record type, form id, editor id, tag)], unreadable notes) of one plugin: the files it names."""
    from .esp import codec
    found, notes = [], []
    try:
        plugin = codec.parse(Path(path).read_bytes())
        for rec, _labels in plugin.records():
            try:
                subs = rec.subrecords
            except Exception:  # noqa: BLE001 - a record whose compressed data is bad
                notes.append(':%08X' % rec.form_id)
                continue
            for sub in subs:
                if sub.tag in PATH_TAGS and sub.data:
                    text = _norm(sub.data.split(b'\0', 1)[0].decode('cp1252', 'replace'))
                    found.append((text, rec.type, rec.form_id, rec.editor_id, sub.tag))
    except Exception as exc:  # noqa: BLE001 - kept like a good plugin: read again only when the file changes
        notes.append(': %s' % str(exc)[:80])
    return found, notes


NOT_AV = ('Data', 'Property', 'ExtraData', 'Controller', 'Interpolator', 'SkinInstance', 'SkinPartition',
          'Partition', 'Palette', 'Sequence')


def _is_av_object(kind):
    """Whether a NIF block type is a scene object (a node, a shape, a light, a camera), which a node may hold."""
    if kind.startswith('bhk') or kind.startswith('hk'):
        return False
    return not any(kind.endswith(s) for s in NOT_AV)


def _node_children(data, head, i):
    """Child block indices of node block i (NiObjectNET, NiAVObject, NiNode fields of a 20.2.0.7 / 11 / 34 file)."""
    off, end = head['offsets'][i], head['offsets'][i] + head['sizes'][i]
    try:
        p = off + 4                                         # name (string index)
        n_extra = struct.unpack_from('<I', data, p)[0]
        p += 4 + 4 * n_extra + 4                            # extra data refs, controller
        p += 4                                              # flags (u32 in New Vegas files, user version 2 = 34)
        p += 12 + 36 + 4                                    # translation, rotation, scale
        n_props = struct.unpack_from('<I', data, p)[0]
        p += 4 + 4 * n_props + 4                            # properties, collision object
        n_children = struct.unpack_from('<I', data, p)[0]
        if n_children > 4096 or p + 4 + 4 * n_children > end:
            return [len(head['blocks']) + n_children]       # an impossible count: reported as outside the file
        return list(struct.unpack_from('<%di' % n_children, data, p + 4))
    except struct.error:
        return []




class AssetTools:
    def __init__(self, live, records=None, sleep=time.sleep, clock=time.monotonic):
        self.live = live
        self.records = records
        self.sleep = sleep
        self.clock = clock
        self._watch = {}
        self._log_since = 0

    @property
    def link(self):
        return self.live.link

    # ---------------------------------------------------------------- shared reads
    def _index(self):
        if self.records is None:
            raise LiveError('NO_INDEX', 'The asset index needs the data tools')
        from .fnv_data import load_order
        entries = load_order(self.live.project, self.records.data._live_plugins())
        index, _roots = self.records._asset_index(entries)
        return index

    def _loaded_refs(self):
        """(ptr, form id) of every reference in the loaded cells (the cells' own lists; see fnv_eyes.cell_refs)."""
        from .fnv_eyes import cell_refs
        ptrs = cell_refs(self.live)
        return [(p, (f or {}).get('formId')) for p, f in zip(ptrs, self.live.form_ids(ptrs))]

    def _models_of(self, refs):
        """{ref ptr: (base form id, model path as GetModelPath prints it)} for references."""
        if not refs:
            return {}
        bases = [value_of(r) for r in self.link.batch([('eval', hexs('GetBaseObject'), word(p)) for p, _ in refs])]
        unique = sorted({b for b in bases if b})
        paths = dict(zip(unique, [value_of(r) for r in self.link.batch(
            [('eval', hexs('GetModelPath %s' % b), '0') for b in unique])])) if unique else {}
        return {p: (b, paths.get(b) if isinstance(paths.get(b), str) else '') for (p, _), b in zip(refs, bases)}

    # ---------------------------------------------------------------- reload
    def _purge(self):
        loader = self.live.u32s([MODEL_LOADER])[0]
        if not loader:
            raise LiveError('NO_MODEL_LOADER', 'The model loader does not exist yet')
        result = self.link.one('call', word(CLEAR_UNUSED_MODELS), 'vg', word(loader), '0', '1')
        if result.get('fault'):
            raise LiveError('CALL_FAULTED', 'ClearUnusedModels faulted: %s' % result['fault'], result)
        if not result.get('ok'):
            raise LiveError('CALL_REJECTED', 'ClearUnusedModels failed: %s' % result.get('error'), result)

    def _evict_texture(self, rel):
        """Drop a texture from the engine's path-keyed texture cache (BSTexturePalette), so the next load reads the
        file. The model purge never touches it (live run 2026-11-24: a remade .dds was not read again until this).
        0x00A61FB0 is the engine's own remove-by-path (tesrace.cpp calls it before it rebuilds a body texture)."""
        path = self.live._game_string(rel)
        result = self.link.one('call', word(TEXTURE_REMOVE_BY_PATH), 'i', '0', '0', word(path))
        if result.get('fault'):
            raise LiveError('CALL_FAULTED', 'The texture cache removal faulted: %s' % result['fault'], result)
        return bool(int(result.get('eax', '0'), 16) & 0xFF) if result.get('ok') else False

    def _refresh(self, ptrs, textures=()):
        """Disable the references, drop the textures named from the texture cache and unused models, enable them
        again; wait for their 3D."""
        live_refs = [p for p in ptrs if not (self.live.values(['GetDisabled'], p)[0])]
        for p in live_refs:
            self.live.console('Disable', p)
        try:
            self.sleep(0.2)
            for rel in textures:
                self._evict_texture(rel)
            self._purge()
        finally:
            for p in live_refs:             # always bring the references back, even when the purge failed
                self.live.console('Enable', p)
        deadline = self.clock() + 20
        pending = list(live_refs)
        while pending and self.clock() < deadline:
            self.sleep(0.2)
            renders = self.live.u32s([p + 0x64 for p in pending])
            roots = self.live.u32s([r + 0x14 if r else 0 for r in renders])
            pending = [p for p, root in zip(pending, roots) if not root]
        return live_refs, pending

    def reload(self, path, menu=None, ref=None):
        self.live.in_game()
        low = _norm(path)
        self.live.changing('asset reload')
        if low.endswith('.nif'):
            want = _model_rel(low)
            models = self._models_of(self._loaded_refs())
            users = [p for p, (_, m) in models.items() if _norm(m) == want]
            if not users:
                self._purge()
                return {'path': path, 'refs': 0, 'note': 'No loaded reference uses this model; unused copies were '
                                                          'dropped from the cache, so the next load reads the file.'}
            done, slow = self._refresh(users)
            self.live.journal.add({'kind': 'asset_reload', 'path': path, 'refs': len(done), 'undo': None})
            return {'path': path, 'refs': len(done), 'refsWithout3D': len(slow),
                    'formIds': [f['formId'] for f in self.live.form_ids(done)][:50],
                    'how': 'disable the references, ModelLoader ClearUnusedModels(true) 0x00448620, enable them: the '
                           'model is read from disk again (its textures with it)',
                    'notCovered': 'worn armour and weapons are biped models: re-equip them to rebuild'}
        if low.endswith('.dds'):
            index = self._index()
            models = self._models_of(self._loaded_refs())
            users, checked, unreadable = [], set(), set()
            for p, (_, m) in models.items():
                if not m:
                    continue
                rel = 'meshes\\' + _norm(m)
                if rel in checked:
                    continue
                checked.add(rel)
                hit = index.find(rel)
                if not hit or hit.get('where') != 'loose':
                    unreadable.add(rel)
                    continue
                from .esp.assets import nif_textures
                try:
                    textures = [_norm(t) for t in nif_textures(Path(hit['path']).read_bytes())]
                except Exception:  # noqa: BLE001 - not a NIF we can read
                    continue
                if low in textures:
                    users += [q for q, (_, mm) in models.items() if _norm(mm) == _norm(m)]
            if users:
                done, slow = self._refresh(sorted(set(users)), textures=[low])
            else:
                done, slow = [], []
                self._evict_texture(low)
                self._purge()
            self.live.journal.add({'kind': 'asset_reload', 'path': path, 'refs': len(done), 'undo': None})
            return {'path': path, 'refs': len(done), 'refsWithout3D': len(slow),
                    'modelsInArchives': len(unreadable),
                    'how': 'the texture is dropped from the texture cache (BSTexturePalette remove by path 0x00A61FB0) '
                           'while the loose models of loaded references that name it are disabled, unused models are '
                           'cleared, the references enabled: they read the file again',
                    'note': None if users else 'No loose model of a loaded reference names this texture. Models '
                                               'inside archives cannot be read here: reload such a model by its path.'}
        if low.endswith('.ini'):
            result = self.live.console('RefreshINI')
            return {'path': path, 'output': result['output'], 'how': 'console RefreshINI (values only; code that read '
                                                                      'a value at start does not see the change)'}
        if 'shaders' in low or low.endswith('.sdp'):
            result = self.live.console('RefreshShaders')
            return {'path': path, 'output': result['output'], 'how': 'console RefreshShaders'}
        if low.endswith('.xml'):
            return self._reload_menu(path, menu)
        if low.endswith('.kf'):
            refs = [self.live.resolve(ref)['ptr']] if ref else []
            if refs:
                done, slow = self._refresh(refs)
            else:
                done, slow = [], []
                self._purge()
            self.live.journal.add({'kind': 'asset_reload', 'path': path, 'refs': len(done), 'undo': None})
            return {'path': path, 'refs': len(done), 'refsWithout3D': len(slow),
                    'how': 'ModelLoader ClearUnusedModels(true) 0x00448620 drops unused KF files too (its second '
                           'map); an actor keeps the sequences it holds until its 3D is rebuilt, so ref= is disabled, '
                           'purged and enabled. Then play it with fnv_anim play.',
                    'note': None if refs else 'Only unused copies were dropped: pass ref (the actor) to rebuild '
                                              'one that has the animation loaded.'}
        raise LiveError('UNKNOWN_FILE_TYPE', 'reload handles .nif, .dds, .kf, menu .xml, .ini and shader packages')

    def _reload_menu(self, path, menu):
        """Evict a menu XML file from the tile cache, then rebuild the menu the way the console's
        "reload <menu>" does (MenuManager::ReloadMenu 0x00720AA0 with the code of "&<menu>;")."""
        if not menu:
            raise LiveError('BAD_ARGUMENTS', 'A menu XML reload needs menu (its name, e.g. HUDMainMenu, '
                                             'LockPickMenu), as the console command "reload <menu>" takes it.')
        name = menu if menu.startswith('&') else '&%s;' % menu
        code = self.live.engine_call('0x%08X' % MENU_CODE_OF, None, [{'type': 'string', 'value': name}], 'int',
                                     'cdecl', True).get('result')
        if code in (None, -0x80000000) or (isinstance(code, int) and code & 0xFFFFFFFF == 0x80000000):
            raise LiveError('UNKNOWN_MENU', '%s is not a menu name the interface knows.' % menu)
        rel = _norm(path)
        evict = rel[len('menus\\'):] if rel.startswith('menus\\') else rel
        for candidate in {rel, evict, 'data\\menus\\' + evict, 'menus\\' + evict}:
            self.live.engine_call('0x%08X' % REMOVE_MENU_ENTRIES, None,
                                  [{'type': 'string', 'value': candidate}, {'type': 'int', 'value': 0}], 'int',
                                  'cdecl', True)
        system = self.live.engine_call('0x%08X' % MENU_SYSTEM, None, [{'type': 'int', 'value': 1}], 'ptr', 'cdecl',
                                       True).get('result')
        self.live.engine_call('0x%08X' % RELOAD_MENU, system, [{'type': 'int', 'value': int(code)}], 'ptr',
                              'thiscall', True)
        self.live.journal.add({'kind': 'asset_reload', 'path': path, 'menu': menu, 'undo': None})
        return {'path': path, 'menu': menu, 'menuCode': code,
                'how': 'RemoveMenuEntries 0x00A1CA20 evicts the file from both tile caches, then '
                       'MenuManager::ReloadMenu 0x00720AA0 (the console\'s "reload <menu>") deletes the menu and '
                       'builds it again from the file.'}

    def watch(self, folder):
        """One check of a folder: reload the models and textures whose file time changed since the last check."""
        root = inside(folder, read_roots(self.live.project), 'path')     # a mod folder of the dev profile (H5)
        if not root.is_dir():
            raise ValueError('folder is a mod folder with meshes and textures')
        times = {}
        for p in list(root.rglob('*.nif')) + list(root.rglob('*.dds')):
            times[str(p)] = p.stat().st_mtime
        last = self._watch.get(str(root))
        self._watch[str(root)] = times
        if last is None:
            return {'folder': str(root), 'watching': len(times), 'note': 'First check: file times recorded. Call '
                    'watch again after changing files to reload what changed.', 'changed': False}
        changed = [p for p, t in times.items() if last.get(p) != t]
        results = []
        for p in changed[:20]:
            rel = str(Path(p).relative_to(root))
            results.append(self.reload(rel))
        return {'folder': str(root), 'changedFiles': [str(Path(p).relative_to(root)) for p in changed],
                'reloads': results}

    # ---------------------------------------------------------------- file log
    def _hash_names(self):
        """{hash: [paths]} over the archives of the load order (cached)."""
        cached = getattr(self, '_hashes', None)
        if cached is not None:
            return cached
        merged = {}
        index = self._index()
        for root in index.roots:
            for bsa in sorted(Path(root).glob('*.bsa')):
                try:
                    for h, names in bsa_hashes(bsa).items():
                        merged.setdefault(h, []).extend(n for n in names if n not in merged.get(h, []))
                except OSError:
                    continue
        self._hashes = merged
        return merged

    def _file_hooks(self):
        """{label: hook} of the file log hooks that are installed."""
        labels = {label for _, label, _, _ in FILE_HOOKS}
        return {h['label']: h for h in self.live.link.one('hooks').get('hooks', [])
                if h.get('stolen') and h.get('label') in labels}

    def file_log(self, mode='read', missing=False, ext=None, folder=None, limit=200):
        self.live.ready()
        hooks = self._file_hooks()
        if mode == 'start':
            if hooks:
                return {'started': False, 'note': 'Already recording.'}
            from .fnv_deep import decode_prologue
            self._log_since = {}
            for address, label, selector, _ in FILE_HOOKS:
                raw = self.live.reads([(address, 24)])[0]
                stolen, relocs = decode_prologue(raw)
                result = self.link.one('hook', word(address), word(stolen), ','.join('%x' % r for r in relocs) or '-',
                                       word(HF_EXIT | HF_RECORD), '0', '0', '0', '4', '0', '0', word(selector),
                                       word(SNAP_NAME), hexs(label))
                if not result.get('ok'):
                    for h in self._file_hooks().values():
                        self.link.one('unhook', word(h['id']))
                    raise LiveError('HOOK_REFUSED', 'The companion refused the file hook %s: %s'
                                    % (label, result.get('error')), result)
                h = result['hook']
                self._log_since[label] = int(self.live.u32s([int(h['seqAddress'], 16)])[0] or 0)
            return {'started': True, 'how': 'record hooks on FileFinder::GetFile 0x00AFDF20 (by name: loose files, '
                                            'and missing ones) and the archive open by entry 0x00AF6340 (files read '
                                            'from BSAs by queued loaders) and NiSourceTexture::Create 0x00A5FD70 '
                                            '(textures, which do not pass GetFile), the name copied at the call, any '
                                            'thread'}
        if mode == 'stop':
            for h in hooks.values():
                self.link.one('unhook', word(h['id']))
            return {'stopped': bool(hooks)}
        if not hooks:
            raise LiveError('NOT_RECORDING', 'The file log is off. Next: fnv_asset file_log mode=start, then load the '
                                             'cell or do the action, then read.')
        rows, lost, last = [], 0, {}
        since = self._log_since if isinstance(self._log_since, dict) else {}
        for address, label, _, source in FILE_HOOKS:
            h = hooks.get(label)
            if not h:
                continue
            seq, got, gone = self.live.deep.records(h, since=since.get(label, 0))
            last[label] = seq
            lost += gone
            rows += [(r, source) for r in got]
        rows.sort(key=lambda x: x[0][6] | (x[0][7] << 32))
        out, counts = [], {'opened': 0, 'missing': 0}
        handles = sorted({r[24] for r, source in rows if source == 'texture' and r[24]})
        texture_names = dict(zip(handles, (_cstr(t) if t else '' for t in self.live.reads(
            [(h, 200) for h in handles])))) if handles else {}
        index = None
        for r, source in rows:
            if source == 'texture':
                name = texture_names.get(r[24]) or ''
                if not name:
                    continue
                rel = _data_rel(name)
                rel = rel if rel.startswith('textures\\') else 'textures\\' + rel
                try:
                    index = index or self._index()
                    found = index.find(rel) is not None
                except LiveError:
                    found = None
                if found is False:
                    counts['missing'] += 1
                elif found:
                    counts['opened'] += 1
                if missing and found is not False:
                    continue
                if ext and not rel.endswith(ext.lower().lstrip('*')):
                    continue
                if folder and _norm(folder) not in rel:
                    continue
                out.append({'path': rel, 'found': found, 'source': source, 'thread': r[2],
                            'caller': '0x%08X' % r[5]})
                continue
            if source == 'archive':
                names = self._hash_names().get(r[24] | (r[25] << 32), [])
                name = names[0] if len(names) == 1 else (' | '.join(names[:3]) if names else
                                                         'hash %08X%08X' % (r[25], r[24]))
            else:
                name = _cstr(struct.pack('<24I', *r[24:48]))
            if not name:
                continue
            found = bool(r[8]) if r[1] == 2 else None
            found_as = None
            if found is False and _norm(name).endswith(AUDIO_EXTS):
                # The sound code asks for each audio extension in turn (.ogg first, then .wav...): a miss is only
                # missing when no other extension of the same file exists.
                stem = _data_rel(name).rsplit('.', 1)[0]
                try:
                    index = index or self._index()
                    found_as = next((stem + e for e in AUDIO_EXTS if index.find(stem + e) is not None), None)
                except LiveError:
                    found_as = None
                if found_as:
                    found = True
            if found is False:
                counts['missing'] += 1
            elif found:
                counts['opened'] += 1
            low = _norm(name)
            if missing and found is not False:
                continue
            if ext and not low.endswith(ext.lower().lstrip('*')):
                continue
            if folder and _norm(folder) not in low:
                continue
            entry = {'path': name, 'found': found, 'source': source, 'thread': r[2], 'caller': '0x%08X' % r[5]}
            if found_as:
                entry['foundAs'] = found_as
            out.append(entry)
        names = {}
        try:
            engine = self.live.engine()
            for c in sorted({e['caller'] for e in out})[:40]:
                names[c] = (engine.summary(int(c, 16)) or {}).get('name')
        except Exception:  # noqa: BLE001 - names are extra
            pass
        for e in out:
            e['callerName'] = names.get(e['caller'])
        return {'entries': out[-limit:], 'count': len(out), 'totals': counts, 'lost': lost, 'lastSeq': last,
                'note': 'source "lookup" is FileFinder::GetFile (found false = the file is nowhere), "archive" a file '
                        'read from a BSA by entry, "texture" a texture the renderer asked for (NiSourceTexture::Create; '
                        'found = the file is loose or in an archive of the load order). found null: the call had not '
                        'returned when read.'}

    # ---------------------------------------------------------------- small reads
    def where(self, path):
        index = self._index()
        rel = _norm(path)
        hit = index.find(rel)
        loose = []
        for root in index.roots:
            p = index._loose(root, rel)
            if p:
                loose.append(str(p))
        return {'path': rel, 'winner': hit, 'looseCopies': loose, 'inArchives': rel in index.archived,
                'archivesSearched': index.archives,
                'note': 'A valid archive entry wins over a loose file unless archive invalidation is on; MO2 mods are '
                        'searched in priority order, then Data.'}

    def _file_bytes(self, path):
        """(bytes, where) of a Data-relative file: the winning loose copy, else the first archive that has it."""
        index = self._index()
        rel = _norm(path)
        hit = index.find(rel)
        if not hit:
            raise LiveError('FILE_NOT_FOUND', '%s is not a loose file or in an archive of the load order.' % rel)
        if hit['where'] == 'loose':
            return Path(hit['path']).read_bytes(), hit['path']
        from .esp.assets import bsa_extract
        for root in index.roots:
            for bsa in sorted(Path(root).glob('*.bsa')):
                try:
                    got = bsa_extract(bsa, {rel})
                except Exception:  # noqa: BLE001 - another archive
                    continue
                if rel in got:
                    return got[rel], str(bsa)
        raise LiveError('FILE_NOT_READ', '%s is listed in an archive but could not be extracted.' % rel)

    def nif(self, path):
        """Offline checks of a mesh before it goes in the game (header, root, child links, collision, skin,
        texture paths)."""
        from .esp.assets import nif_block_types, nif_textures, AssetError
        data, where = self._file_bytes(path)
        problems, warnings = [], []
        try:
            head = nif_block_types(data)
        except (AssetError, struct.error, IndexError) as exc:
            return {'path': path, 'from': where, 'ok': False, 'problems': ['header: %s' % exc]}
        blocks = head['blocks']
        if head['userVersion'] != 11 or head['userVersion2'] != 34:
            warnings.append('user version %d/%d: New Vegas meshes are 11/34' % (head['userVersion'],
                                                                                head['userVersion2']))
        if not blocks:
            problems.append('no blocks')
        root = blocks[0] if blocks else None
        if root and not _is_av_object(root):
            problems.append('root block is %s, not a node' % root)
        for i, kind in enumerate(blocks):
            if not kind.endswith('Node') or kind in ('NiBSBoneLODController',):
                continue
            for child in _node_children(data, head, i):
                if child == -1:
                    continue
                if not 0 <= child < len(blocks):
                    problems.append('block %d (%s): child link %d is outside the file (%d blocks)' % (
                        i, kind, child, len(blocks)))
                elif not _is_av_object(blocks[child]):
                    problems.append('block %d (%s): child link %d points at %s, which is not a scene object '
                                    '(the engine faults in NiAVObject::AttachParent on this)' % (
                                        i, kind, child, blocks[child]))
        has_collision = any(b.startswith('bhk') and 'CollisionObject' in b for b in blocks)
        skinned = [b for b in blocks if b in ('NiSkinInstance', 'BSDismemberSkinInstance')]
        if 'NiSkinInstance' in skinned and 'armor' in _norm(path):
            warnings.append('armour skinned with NiSkinInstance: New Vegas armour uses BSDismemberSkinInstance')
        textures = []
        index = self._index()
        for t in nif_textures(data):
            found = index.find(_norm(t))
            textures.append({'path': _norm(t), 'found': bool(found), 'where': (found or {}).get('where')})
            if not found:
                problems.append('texture not found: %s' % _norm(t))
        return {'path': path, 'from': where, 'ok': not problems, 'problems': problems, 'warnings': warnings,
                'blocks': len(blocks), 'root': root, 'collision': has_collision, 'skin': sorted(set(skinned)),
                'blockTypes': dict(sorted({b: blocks.count(b) for b in set(blocks)}.items())),
                'textures': textures,
                'how': 'NIF 20.2.0.7 header; child links of every node block read from the block body (NiNode '
                       'layout of New Vegas); texture paths checked against the loose files and archives.'}

    def dds(self, path):
        """Offline checks of a texture: header, size, format, mip chain."""
        data, where = self._file_bytes(path)
        if data[:4] != b'DDS ' or len(data) < 128:
            return {'path': path, 'from': where, 'ok': False, 'problems': ['not a DDS file']}
        height, width = struct.unpack_from('<II', data, 12)
        mips = struct.unpack_from('<I', data, 28)[0] or 1
        pf_flags, fourcc, bits = struct.unpack_from('<I4sI', data, 80)
        fmt = fourcc.decode('ascii', 'replace') if pf_flags & 0x4 else '%d-bit %s' % (
            bits, 'RGBA' if pf_flags & 0x1 else 'RGB')
        problems, warnings = [], []
        pow2 = lambda n: n > 0 and n & (n - 1) == 0  # noqa: E731
        if not (pow2(width) and pow2(height)):
            problems.append('size %dx%d is not a power of two' % (width, height))
        full = int(math.log2(max(width, height))) + 1 if width and height else 1
        if mips < full:
            warnings.append('%d of %d mip levels: distant surfaces shimmer or show the top level' % (mips, full))
        if pf_flags & 0x4 and fmt not in ('DXT1', 'DXT3', 'DXT5'):
            warnings.append('format %s: New Vegas textures are DXT1, DXT3, DXT5 or uncompressed' % fmt)
        if max(width, height) > 4096:
            warnings.append('larger than 4096: costs memory on a 32-bit game')
        return {'path': path, 'from': where, 'ok': not problems, 'problems': problems, 'warnings': warnings,
                'width': width, 'height': height, 'format': fmt, 'mipLevels': mips, 'fullMipChain': full}

    def users(self, path, limit=100):
        """Records in the load order that name this file (models, icons, texture sets): which record to fix when a
        file is bad. The paths each plugin names are kept in the record index (tables asset_paths, path_files) under
        the plugin's file id, which changes when the file does: a new server reads no plugin."""
        from .fnv_data import load_order
        want = _norm(path)
        bare = want.split('\\', 1)[1] if want.split('\\', 1)[0] in ('meshes', 'textures', 'sound', 'music') else want
        rows, unreadable = [], []
        entries = load_order(self.live.project, self.records.data._live_plugins())
        db = self.records.db
        for e in entries:
            if not e.get('path'):
                continue
            try:
                file_id, notes = self._plugin_paths(db, e['path'])
            except Exception as exc:  # noqa: BLE001 - one bad plugin does not stop the search
                unreadable.append('%s: %s' % (e['name'], str(exc)[:80]))
                continue
            unreadable += ['%s%s' % (e['name'], note) for note in notes]
            for rtype, fid, edid, tag in db.execute('select type, form_id, edid, tag from asset_paths where file = ? '
                                                    'and path in (?, ?) order by rowid', (file_id, want, bare)):
                rows.append({'plugin': e['name'], 'type': rtype, 'formId': '%08X' % fid, 'editorId': edid,
                             'field': tag})
        return {'path': want, 'users': rows[:max(1, int(limit or 100))], 'count': len(rows), 'unreadable': unreadable[:20] or None,
                'how': 'every record of the load order plugins with a model, icon or texture field naming the file '
                       '(form ids as in each plugin, as fnv_record action=find shows them); each plugin is read once and '
                       'kept in the record index until its file changes'}

    def _plugin_paths(self, db, path):
        """(file id, unreadable notes) of a plugin whose named paths are in the index, reading it first if not."""
        from .fnv_state import write_txn
        file_id = self.records.data.index.ensure(path)
        row = db.execute('select version, unreadable from path_files where file = ?', (file_id,)).fetchone()
        if row and row[0] >= PATHS_VERSION:
            return file_id, json.loads(row[1] or '[]')
        found, notes = _scan_paths(path)
        if db.in_transaction:      # never build on top of a transaction an earlier failure left open
            db.rollback()
        with write_txn(db):        # one transaction: another server that built it meanwhile is kept
            row = db.execute('select version, unreadable from path_files where file = ?', (file_id,)).fetchone()
            if row and row[0] >= PATHS_VERSION:
                return file_id, json.loads(row[1] or '[]')
            db.execute('delete from asset_paths where file = ?', (file_id,))
            db.executemany('insert into asset_paths values (?, ?, ?, ?, ?, ?)', ((file_id,) + r for r in found))
            db.execute('insert or replace into path_files values (?, ?, ?, ?, ?)',
                       (file_id, PATHS_VERSION, time.time(), len(found), json.dumps(notes)))
        return file_id, notes

    def queue(self, wait_idle=False, timeout=30):
        self.live.ready()
        io = self.live.u32s([IO_MANAGER])[0]
        if not io:
            return {'exists': False}

        def counts():
            raw = self.live.reads([(io, 0x70)])[0] or b''
            return {'inFlight': struct.unpack_from('<I', raw, 0x18)[0] if len(raw) >= 0x1C else None}

        now = counts()
        waited = 0.0
        if wait_idle:
            start = self.clock()
            while (now['inFlight'] or 0) and self.clock() - start < timeout:
                self.sleep(0.2)
                now = counts()
            waited = round(self.clock() - start, 2)
        return {'ioManager': '0x%08X' % io, **now, 'waited': waited,
                'note': 'inFlight is the IO manager field +0x18 (from research, not proven): 0 when idle.'}

    def models(self, limit=200):
        result = self.live.console('DumpModelMap')
        lines = result.get('output') or []
        return {'lines': lines[:limit], 'count': len(lines), 'how': 'console DumpModelMap (retail code)'}

    def archives(self):
        index = self._index()
        return {'archives': index.archives, 'looseRoots': [str(r) for r in index.roots],
                'note': 'From the MO2 profile on disk, in load order; the running game loaded the same set at start.'}

    # ---------------------------------------------------------------- make (tool gaps 3)
    def _make_root(self, options):
        """(Data folder the made file goes in, mod name or None, notes). MO2: mods\\<options.mod, else the project
        plugin's name>, turned on when no game runs. Direct install: the game's Data, only with
        fnv.allow_direct_install_changes."""
        from . import fnv_layout
        from .fnv_link import SAFE_NAME, enable_mod
        project = self.live.project
        layout = fnv_layout.current(project)
        if layout.kind == fnv_layout.DIRECT:
            fnv_layout.guard(layout, "write meshes, animations and textures into the game's Data folder")
            return layout.data_dir, None, []
        plugin = getattr(project, 'plugin', None)
        mod = options.get('mod') or (Path(plugin).stem if plugin else 'fnvmcp assets')
        if not isinstance(mod, str) or not SAFE_NAME.match(mod) or '..' in mod or not mod.strip(' .'):
            raise ValueError("options.mod is a mod folder name: letters, digits, space and _ . ' - only")
        notes = []
        if mod not in layout.enabled_mods():
            if fnv_layout.running_game(project):
                notes.append('The mod "%s" is off: the running game does not see its files. Quit, then make again '
                             '(it is turned on when no game runs), or fnv_install the plugin into it.' % mod)
            else:
                enable_mod(layout.mo2_root, layout.profile, mod)
                notes.append('The mod "%s" was turned on in the profile.' % mod)
        elif fnv_layout.running_game(project):
            notes.append('A file new to the mod folder is seen by a game started after it was made (MO2 lays the mod '
                         'folders over Data at launch); a file the game already sees is re-read by fnv_asset reload.')
        return layout.mods_dir / mod, mod, notes

    def _source(self, path, what='path'):
        if not path:
            raise ValueError('%s is the source file (a .glb, .json or image) inside the project, state or examples '
                             'folders' % what)
        return inside(path, read_roots(self.live.project), what)

    def make(self, kind, path, out, options=None):
        """make_nif / make_kf / make_dds: a file made from a source, written under the mod folder's Data tree."""
        from .assets import convert
        options = dict(options or {})
        src = self._source(path)
        if not out:
            raise ValueError('out is the Data-relative path to write (meshes\\mymod\\thing.nif, '
                             'textures\\mymod\\thing.dds)')
        if kind == 'dds' and options.get('specular'):
            options['specular'] = str(self._source(options['specular'], 'options.specular'))
        skeleton = None
        if kind == 'kf' and options.get('skeleton'):
            skel = str(options.pop('skeleton'))
            if Path(skel).suffix.lower() != '.nif':
                raise ValueError('options.skeleton is a .nif: a Data path (meshes/characters/_male/skeleton.nif) or '
                                 'a file in the project, state or examples folders')
            skeleton = (self._source(skel, 'options.skeleton').read_bytes() if Path(skel).is_absolute()
                        else self._file_bytes(skel)[0])
        convert.target(Path('.'), out, {'nif': '.nif', 'kf': '.kf', 'dds': '.dds'}[kind])   # refuse before any write
        root, mod, notes = self._make_root(options)
        options.pop('mod', None)
        report = convert.make(kind, src, root, out, options, skeleton)
        if mod:
            report['mod'] = mod
        steps = {'nif': 'fnv_asset nif path=%s checks it; a STAT or other record names it as its model (fnv_spec); '
                        'a file the running game already reads is re-read by fnv_asset reload.',
                 'kf': 'fnv_anim play takes it on an actor whose skeleton has its nodes; fnv_asset reload drops '
                       'the cached copy.',
                 'dds': 'fnv_asset dds path=%s checks it; fnv_asset reload path=%s shows the change in the running '
                        'game.'}[kind]
        report['next'] = steps.replace('%s', report['out'])
        report['notes'] = notes
        self.live.journal.add({'kind': 'asset_make', 'path': report['path'], 'undo': None})
        return report

    def extract(self, path):
        """A file of the load order (loose or from an archive) copied into the state folder, to look at or edit."""
        from .assets import convert
        if not path:
            raise ValueError('extract needs path (a Data path such as meshes\\clutter\\toolbox.nif)')
        data, where = self._file_bytes(path)
        rel = _norm(path)
        target = convert.data_path(Path(self.live.project.state_dir) / 'assets' / 'extract', rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {'path': rel, 'from': where, 'file': str(target), 'bytes': len(data),
                'next': 'Edit it, or use it as a start; to put a changed copy in the game write it under a mod '
                        'folder (make_* actions write there).'}

    def preview(self, path):
        """An offline PNG of a NIF (a Data path, or a file in the project, state or examples folders)."""
        from .assets import render
        if not path:
            raise ValueError('preview needs path (a .nif)')
        if Path(path).is_absolute():
            src = self._source(path)
            data, where = src.read_bytes(), str(src)
        else:
            data, where = self._file_bytes(path)
        name = Path(_norm(path).replace('\\', '/')).stem
        out = Path(self.live.project.state_dir) / 'assets' / 'preview' / ('%s.png' % name)
        report = render.preview(path, out, data)
        report['from'] = where
        report['note'] = 'Flat shaded, no textures, skin or animation; fnv_photo shows the game\'s own render.'
        return report

    def run(self, action, **kw):
        if action == 'reload':
            return self.reload(kw['path'], kw.get('menu'), kw.get('ref'))
        if action == 'watch':
            return self.watch(kw['path'])
        if action == 'file_log':
            return self.file_log(kw.get('mode') or 'read', kw.get('missing', False), kw.get('ext'), kw.get('folder'),
                                 kw.get('limit') or 200)
        if action == 'queue':
            return self.queue(kw.get('wait_idle', False))
        if action == 'where':
            return self.where(kw['path'])
        if action == 'models':
            return self.models()
        if action == 'archives':
            return self.archives()
        if action in ('nif', 'dds', 'users'):
            if not kw.get('path'):
                raise ValueError('%s needs path' % action)
            return getattr(self, action)(kw['path'])
        if action in ('make_nif', 'make_kf', 'make_dds'):
            return self.make(action[5:], kw.get('path'), kw.get('out'), kw.get('options'))
        if action == 'extract':
            return self.extract(kw.get('path'))
        if action == 'preview':
            return self.preview(kw.get('path'))
        raise ValueError('action is reload, watch, file_log, queue, where, models, archives, nif, dds, users, '
                         'make_nif, make_kf, make_dds, extract or preview')


class FormTools:
    """Forms at run time: create (clone), reload changed records from a rebuilt plugin, changes this session."""

    def __init__(self, live, data=None, assets=None):
        self.live = live
        self.data = data
        self.assets = assets
        self.created = []

    @property
    def link(self):
        return self.live.link

    def create(self, type_=None, clone=None, name=None):
        self.live.in_game()
        if clone:
            src = self.live.resolve(clone)
            head = self.live.form_ids([src['ptr']])[0]
            if (head or {}).get('formType') in (57, 58, 59, 60):
                raise ValueError('clone a base form, not a placed reference')
            self.live.changing('form create')
            result = self.link.eval('TempCloneForm %s' % src['formId'])
            element = result.get('result') or {}
            if not result.get('ok') or element.get('type') != 'form' or not element.get('ptr'):
                raise LiveError('CLONE_FAILED', 'TempCloneForm returned no form', result)
            ptr, form_id = int(element['ptr'], 16), element.get('formId')
            how = 'xNVSE TempCloneForm (a copy that lives until the game quits; not written to the save)'
        else:
            t = (type_ or '').upper()
            if t in REFUSED_TYPES:
                raise ValueError('%s forms cannot be created at run time here' % t)
            if t not in SAFE_TYPES:
                raise ValueError('type is one of %s, or give clone' % ', '.join(sorted(SAFE_TYPES)))
            dh = self.live.u32s([DATA_HANDLER])[0]
            self.live.changing('form create')
            made = self.link.one('call', word(CREATE_FORM_FOR_TYPE), 'ig', word(dh), '0', word(SAFE_TYPES[t]))
            ptr = int(made.get('eax', '0x0'), 16) if made.get('ok') and not made.get('fault') else 0
            if not ptr:
                raise LiveError('CREATE_FAILED', 'CreateFormForType returned nothing', made)
            added = self.link.one('call', word(ADD_FORM), 'ig', word(dh), '0', word(ptr))
            if added.get('fault'):
                raise LiveError('CALL_FAULTED', 'AddFormToDataHandler faulted', added)
            form_id = self.live.form_ids([ptr])[0]['formId']
            how = 'TESDataHandler::CreateFormForType 0x00465110 + AddFormToDataHandler 0x004603B0'
        if name:
            self.live.console('SetName "%s" %s' % (re.sub(r'["\r\n]', '', name), form_id))
        entry = {'formId': form_id, 'ptr': '0x%08X' % ptr, 'clone': clone, 'type': type_, 'name': name}
        self.created.append(entry)
        self.live.journal.add({'kind': 'form_create', **entry, 'undo': None})
        return {'created': entry, 'how': how,
                'next': 'fnv_form_edit changes fields; fnv_place at export=false or fnv_place at puts it in the world; fnv_author '
                        'export writes it to the plugin spec.'}

    # The fields fnv_form reload applies, per record type: (subrecord, decoder, setter template).
    def _field_ops(self, rtype, rec, form_id):
        ops = []
        full = rec.get('FULL')
        if full is not None:
            ops.append(('name', full.split(b'\0', 1)[0].decode('cp1252', 'cp1252-bytes'),
                        'SetName "%s" ' + form_id))
        if rtype == 'ARMO':
            for tag, which, female in (('MODL', 0, 0), ('MOD2', 1, 0), ('MOD3', 0, 1), ('MOD4', 1, 1)):
                v = rec.get(tag)
                if v is not None:
                    ops.append(('model.%s' % tag, v.split(b'\0', 1)[0].decode('cp1252', 'cp1252-bytes'),
                                'SetBipedModelPathEX "%%s" %d %d %s' % (which, female, form_id)))
            data = rec.get('DATA')
            if data and len(data) >= 12:
                value, health, weight = struct.unpack_from('<iif', data, 0)
                ops += [('value', value, 'SetBaseItemValue %s %%s' % form_id),
                        ('health', health, 'SetHealth %%s %s' % form_id),
                        ('weight', round(weight, 4), 'SetWeight %%s %s' % form_id)]
            dnam = rec.get('DNAM')
            if dnam and len(dnam) >= 8:
                dr, _flags, dt = struct.unpack_from('<hHf', dnam, 0)
                ops += [('armorRating', dr, 'SetArmorAR %%s %s' % form_id),
                        ('damageThreshold', round(dt, 4), 'SetArmorDT %%s %s' % form_id)]
            return ops
        modl = rec.get('MODL')
        if modl is not None:
            ops.append(('model', modl.split(b'\0', 1)[0].decode('cp1252', 'cp1252-bytes'), 'SetModelPathEX "%%s" %s' % form_id))
        data = rec.get('DATA')
        if rtype == 'AMMO':
            # AMMO DATA is speed f32, flags u8, 3 unused, value i32, clip rounds u8; weight is DAT2 at +8.
            if data and len(data) >= 12:
                ops.append(('value', struct.unpack_from('<i', data, 8)[0], 'SetBaseItemValue %s %%s' % form_id))
            dat2 = rec.get('DAT2')
            if dat2 and len(dat2) >= 12:
                ops.append(('weight', round(struct.unpack_from('<f', dat2, 8)[0], 4),
                            'SetWeight %%s %s' % form_id))
        elif rtype in ('MISC', 'KEYM') and data and len(data) >= 8:
            value, weight = struct.unpack_from('<if', data, 0)
            ops += [('value', value, 'SetBaseItemValue %s %%s' % form_id),
                    ('weight', round(weight, 4), 'SetWeight %%s %s' % form_id)]
        return ops

    def _live_values(self, rtype, form_id):
        names = ['GetName %s' % form_id, 'GetModelPath %s' % form_id, 'GetValue %s' % form_id,
                 'GetWeight %s' % form_id]
        if rtype == 'ARMO':
            names += ['GetHealth %s' % form_id, 'GetArmorAR %s' % form_id, 'GetArmorDT %s' % form_id]
        vals = self.live.values(names)
        out = {'name': vals[0], 'model': vals[1], 'value': vals[2], 'weight': vals[3]}
        if rtype == 'ARMO':
            out.update(health=vals[4], armorRating=vals[5], damageThreshold=vals[6])
        return out

    def reload(self, plugin, records=None):
        """Apply the changed fields of a rebuilt plugin's records to the live forms (safe types only)."""
        self.live.in_game()
        from .esp import codec
        from .fnv_data import load_order
        # The running game keeps its plugin files open with no write sharing (seen live), so a rebuilt plugin is
        # written somewhere else (the build folder) and given here as a path; its file name says which loaded
        # plugin it replaces.
        built = Path(str(plugin)) if any(c in str(plugin) for c in '/\\') else None
        if built is not None and not built.is_file():
            raise LiveError('PLUGIN_NOT_FOUND', 'No rebuilt plugin at %s' % built)
        name = built.name if built is not None else str(plugin)
        entries = load_order(self.live.project, self.data._live_plugins())
        target = next((e for e in entries if e['name'].lower() == name.lower()), None)
        if not target or (built is None and not target.get('path')):
            raise LiveError('PLUGIN_NOT_FOUND', '%s is not in the load order (or its file is missing)' % name)
        if built is not None:
            target = dict(target, path=str(built))
        index = int(value_of(self.link.eval('GetModIndex %s' % quoted(target['name']))))
        if index < 0 or index > 254:
            raise LiveError('NOT_LOADED', '%s is not loaded in the running game' % target['name'])
        data = Path(target['path']).read_bytes()
        plugin_obj = codec.parse(data, lazy=True)
        tes4 = next(iter(plugin_obj.records()), None)
        masters = [s.data.split(b'\0', 1)[0].decode('cp1252', 'cp1252-bytes') for s in tes4[0].subrecords if s.tag == 'MAST'] \
            if tes4 else []
        wanted = {str(r).lower() for r in records} if records else None
        applied, skipped, restart = [], [], []
        self.live.changing('form reload')
        for rec, _labels in codec.iter_records(data):
            if rec.type == 'TES4' or rec.type == 'GRUP':
                continue
            edid = rec.editor_id
            if wanted and (edid or '').lower() not in wanted and ('%08X' % rec.form_id).lower() not in wanted:
                continue
            if rec.type not in ('ARMO', 'MISC', 'KEYM', 'AMMO', 'STAT', 'ACTI', 'BOOK', 'LIGH', 'NOTE'):
                if rec.type in ('CELL', 'REFR', 'ACHR', 'ACRE', 'NAVM', 'LAND', 'PGRD'):
                    continue
                restart.append({'record': edid or '%08X' % rec.form_id, 'type': rec.type})
                continue
            mod = rec.form_id >> 24
            local = rec.form_id & 0xFFFFFF
            owner_index = index if mod >= len(masters) else int(value_of(self.link.eval(
                'GetModIndex %s' % quoted(masters[mod]))))
            form_id = '%02X%06X' % (owner_index, local)
            exists = self.link.eval('(BuildRef %d %d)' % (owner_index, local))
            if not exists.get('ok') or (exists.get('result') or {}).get('type') != 'form':
                skipped.append({'record': edid, 'formId': form_id, 'reason': 'new record: not in the running game; '
                                'restart to load it (or fnv_form create clone=...)'})
                continue
            before = self._live_values(rec.type, form_id)
            changes = []
            for field, value, template in self._field_ops(rec.type, rec, form_id):
                current = before.get(field.split('.')[0]) if not field.startswith('model.') else None
                if field == 'model' and isinstance(current, str) and _norm(current) == _norm(value):
                    continue
                if field not in ('model',) and not field.startswith('model.') and current is not None and \
                        isinstance(value, (int, float)) and abs(float(current) - float(value)) < 1e-4:
                    continue
                if field == 'name' and current == value:
                    continue
                if isinstance(value, str) and any(c in value for c in '"\r\n'):
                    # plugin text inside a quoted console argument: a quote would end the string and run the rest
                    # (W4; bug hunt 2026-11-18). Not applied, and said so.
                    changes.append({'field': field, 'from': current, 'to': value, 'accepted': False,
                                    'skipped': 'it contains a double quote or a line break, which a console '
                                               'string cannot hold'})
                    continue
                line = template % value if '%s' in template else template
                result = self.live.console(line)
                changes.append({'field': field, 'from': current, 'to': value, 'accepted': result['accepted']})
            if not changes:
                continue
            refreshed = None
            if any(c['field'].startswith('model') for c in changes) and self.assets is not None:
                users = [p for p, (b, _) in self.assets._models_of(self.assets._loaded_refs()).items()
                         if (b or '').upper() == form_id.upper()]
                if users:
                    done, _ = self.assets._refresh(users)
                    refreshed = len(done)
            applied.append({'record': edid, 'formId': form_id, 'type': rec.type, 'changes': changes,
                            'refsRefreshed': refreshed, 'after': self._live_values(rec.type, form_id)})
        self.live.journal.add({'kind': 'form_reload', 'plugin': target['name'], 'records': len(applied), 'undo': None})
        return {'plugin': target['name'], 'applied': applied, 'skipped': skipped, 'needsRestart': restart[:50],
                'how': 'the rebuilt record\'s fields applied to the live form with the engine\'s own setters (name, '
                       'models, value, weight, health, armour); references using a changed model rebuilt their 3D',
                'notCovered': 'other fields of these types, and other record types (listed in needsRestart); worn '
                              'armour shows a new model after it is re-equipped'}

    def changes(self, since=None):
        entries = [e for e in self.live.journal.entries() if e['kind'] in ('form_create', 'form_reload', 'form_edit',
                                                                          'mem_write', 'memory', 'place', 'asset_reload')]
        if since:
            entries = [e for e in entries if e['time'] >= float(since)]
        return {'changes': entries[-200:], 'count': len(entries), 'created': self.created,
                'note': 'What this bridge changed (its journal). The engine\'s own change map (what the save will '
                        'record) is not read yet.'}

    def run(self, action, **kw):
        if action == 'create':
            return self.create(kw.get('type'), kw.get('clone'), kw.get('name'))
        if action == 'reload':
            return self.reload(kw['plugin'], kw.get('records'))
        if action == 'changes':
            return self.changes(kw.get('since'))
        if action == 'bytes':
            return self.form_bytes(kw.get('form') or kw.get('clone'))
        if action == 'delete':
            return self.delete(kw.get('form') or kw.get('clone'))
        raise ValueError('action is create, reload, changes, bytes or delete')

    def form_bytes(self, form):
        """The live form as record bytes from the engine's own writer (vtable slot 0xB SaveForm), decoded."""
        self.live.in_game()
        if not form:
            raise ValueError('bytes needs form')
        f = self.live.resolve(form)
        ptr = f['ptr']
        head = self.live.form_ids([ptr])[0] or {}
        if head.get('flags', 0) & 0x4000:
            raise LiveError('TEMPORARY_FORM', '%s is a temporary form: the engine writes no record for those '
                                              '(TESForm_StartForm returns at once).' % f['formId'])
        vtable = self.live.u32s([ptr])[0]
        save_form = self.live.u32s([vtable + 0xB * 4])[0]
        old = self.live.reads([(RECORD_BUFFER, 8)])[0]
        self.link.write(RECORD_BUFFER, b'\0' * 8)
        try:
            self.live.engine_call('0x%08X' % save_form, '0x%08X' % ptr, [], 'int', 'thiscall', True)
            buf, size = struct.unpack('<II', self.live.reads([(RECORD_BUFFER, 8)])[0])
            data = self.live.reads([(buf, size)])[0] if buf and 0x18 <= size < 0x100000 else None
        finally:
            buf_now = self.live.u32s([RECORD_BUFFER])[0]
            if buf_now:
                self.link.one('call', word(FORM_HEAP_FREE), 'c', '0', '0', word(buf_now))
            self.link.write(RECORD_BUFFER, old or b'\0' * 8)
        if not data:
            return {'form': f['formId'], 'written': False, 'saveForm': '0x%08X' % save_form,
                    'note': 'The class wrote no record: its SaveForm is the base one (no writer found in the decomp '
                            'for WEAP, CONT, ALCH, INGR, KEYM, FACT, PACK, SPEL, ENCH). Use fnv_form_live_diff.'}
        sig = data[0:4].decode('ascii', 'replace')
        flags, fid = struct.unpack_from('<II', data, 8)
        from .esp import codec
        from .fnv_data import decode_subrecord
        fields = []
        for sub in codec.parse_subrecords(data[0x18:]):
            item = {'tag': sub.tag, 'size': len(sub.data)}
            value = decode_subrecord(sig, sub.tag, sub.data)
            if value is not None:
                item['value'] = value
            elif len(sub.data) == 4 and sub.tag not in ('DATA', 'OBND'):
                item['formIdRuntime'] = '%08X' % struct.unpack('<I', sub.data)[0]
            else:
                item['hex'] = sub.data[:64].hex(' ') + (' ...' if len(sub.data) > 64 else '')
            fields.append(item)
        return {'form': f['formId'], 'written': True, 'type': sig, 'flags': '0x%08X' % flags, 'formIdInRecord':
                '%08X' % fid, 'size': len(data), 'fields': fields,
                'how': 'vtable slot 0xB (SaveForm) called on the live form: TESForm_StartForm 0x004855A0 opens the '
                       'record in the buffer at 0x011C54CC, the class appends its fields; the buffer is read, freed '
                       '(FormHeap_Free) and the globals restored. Form ids are runtime ids (load order prefix); '
                       'there is no EDID (the game keeps none).'}

    def delete(self, form):
        """Remove a form this session created: flagged deleted and temporary (no save record, no change entry)."""
        self.live.in_game()
        if not form:
            raise ValueError('delete needs form')
        f = self.live.resolve(form)
        mine = next((c for c in self.created if c['formId'].upper() == f['formId'].upper()), None)
        if not mine:
            raise LiveError('NOT_CREATED_HERE', '%s was not created in this session; only forms fnv_form create made '
                                                'can be deleted.' % f['formId'])
        flags = self.live.u32s([f['ptr'] + 8])[0]
        self.live.changing('form delete')
        self.link.write(f['ptr'] + 8, struct.pack('<I', flags | FORM_DELETED_FLAGS))
        self.created.remove(mine)
        made = next((e for e in reversed(self.live.journal.entries(process=self.live.game_identity()))
                     if e['kind'] == 'form_create' and not e['undone']
                     and (e.get('formId') or '').upper() == f['formId'].upper()), None)
        # supersedes: export leaves the deleted form out (N4)
        self.live.journal.add({'kind': 'form_delete', 'formId': f['formId'], 'supersedes': made and made['id'],
                               'undo': None})
        return {'deleted': f['formId'], 'flagsBefore': '0x%08X' % flags,
                'how': 'form flags |= 0x20 (deleted) and 0x4000 (temporary: no record in a save, no change entry). '
                       'The engine has no "remove from the data handler" function that was found, so the object '
                       'stays allocated until the game quits; references to it should be removed first '
                       '(fnv_place remove).'}


class PlaceTools:
    """Level work in the running game: placements are journaled so fnv_author export can turn them into cell
    objects."""

    def __init__(self, live, hands=None, sleep=time.sleep):
        self.live = live
        self.hands = hands
        self.sleep = sleep

    @property
    def link(self):
        return self.live.link

    def _placed(self):
        return [e for e in self.live.journal.entries(process=self.live.game_identity())
                if e['kind'] == 'place' and not e['undone']]

    def _pose_exact(self, ptr):
        """The pose as the game reports it, unrounded: what the journal stores (an undo restores it exactly)."""
        v = self.live.values(['GetPos X', 'GetPos Y', 'GetPos Z', 'GetAngle X', 'GetAngle Y', 'GetAngle Z', 'GetScale'],
                             ptr)
        return {'position': list(v[0:3]), 'rotation': list(v[3:6]), 'scale': v[6]}

    @staticmethod
    def _rounded(pose):
        """A pose for display: position and rotation to 2 places, scale to 3."""
        def r(x, n):
            return round(x, n) if isinstance(x, (int, float)) else x
        return {'position': [r(x, 2) for x in pose.get('position') or []],
                'rotation': [r(x, 2) for x in pose.get('rotation') or []], 'scale': r(pose.get('scale'), 3)}

    def _pose(self, ptr):
        return self._rounded(self._pose_exact(ptr))

    def _set(self, ptr, position=None, rotation=None, scale=None):
        lines = []
        if position:
            lines += ['SetPos %s %.3f' % (a, float(v)) for a, v in zip('XYZ', position)]
        if rotation:
            lines += ['SetAngle %s %.3f' % (a, float(v)) for a, v in zip('XYZ', rotation)]
        if scale is not None:
            lines.append('SetScale %.4f' % float(scale))
        for line in lines:
            self.live.console(line, ptr)

    def _place_one(self, base, position, rotation=None, scale=None):
        player = self.live.resolve('player')
        result = self.link.eval('PlaceAtMe %s 1' % base['formId'], player['ptr'])
        element = result.get('result') or {}
        if not result.get('ok') or element.get('type') != 'form' or not element.get('ptr'):
            raise LiveError('PLACE_FAILED', 'PlaceAtMe returned no reference', result)
        ptr = int(element['ptr'], 16)
        # Journaled as soon as the ref exists (G3): a failing SetPos/SetAngle must not leave a ref undo cannot see.
        entry = self.live.journal.add({'kind': 'place', 'state': 'pending', 'ref': element.get('formId'),
                                       'base': base['formId'],
                                       'undo': {'op': 'console_ref', 'ref': element.get('formId'),
                                                'lines': ['Disable', 'MarkForDelete']}})
        self._set(ptr, position, rotation or [0, 0, 0], scale)
        pose = self._pose_exact(ptr)
        cell = value_of(self.link.eval('GetParentCell', ptr))
        self.live.journal.confirm(entry['id'], {'cell': cell, **pose})
        return {'ref': element.get('formId'), 'base': base['formId'], **self._rounded(pose), 'journalId': entry['id']}

    def _bounds(self, base_ptr):
        """(minX, minY, minZ, maxX, maxY, maxZ) of a base form, read from the engine (its OBND), or None when it
        cannot be read or is not a bound (min over max)."""
        reads = getattr(self.live, 'reads', None) if self.live is not None else None
        if not reads or not base_ptr:
            return None
        try:
            raw = reads([(base_ptr + BOUND_OFFSET, 12)])[0]
        except Exception:  # noqa: BLE001 - no bounds: snap by the origin as before
            return None
        if not raw or len(raw) != 12:
            return None
        b = struct.unpack('<6h', raw)
        return b if all(b[i] <= b[i + 3] for i in range(3)) else None

    def _base_of(self, ref_ptr):
        u32s = getattr(self.live, 'u32s', None) if self.live is not None else None
        if not u32s or not ref_ptr:
            return 0
        try:
            return u32s([ref_ptr + REF_BASE_OFFSET])[0] or 0
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _lift(bounds, scale=None, rotation=None):
        """How far over the surface the origin sits when the bottom of the bounds rests on it: -minZ times the
        scale, tilted by the X and Y angles (cos X cos Y is the world height of the object's up axis)."""
        if not bounds:
            return 0.0
        rx, ry = (list(rotation or []) + [0, 0])[:2]
        return -bounds[2] * float(scale or 1.0) * math.cos(math.radians(rx or 0)) * math.cos(math.radians(ry or 0))

    @staticmethod
    def _lift_on_plane(bounds, scale, pitch, roll):
        """How far over a sloped surface the origin sits when the object is laid on it (align): the bottom lies on the
        plane, so the origin is -minZ * scale along the normal, which is that divided by cos X cos Y straight up.
        (_lift's cos X cos Y is the height of the bottom's centre, right for a tilted object on flat ground.)"""
        if not bounds:
            return 0.0
        nz = math.cos(math.radians(pitch)) * math.cos(math.radians(roll))
        return -bounds[2] * float(scale or 1.0) / max(nz, 0.1)

    @staticmethod
    def _face_heights(bounds, pose, points, top=False):
        """Height of the object's bottom face (the plane minZ * scale along its up axis from the origin), or with
        `top` its top face (maxZ), over each (x, y) point. Up axis from the X and Y angles: (-sin Y, sin X cos Y,
        cos X cos Y)."""
        x, y, z = pose['position']
        rx, ry = (math.radians(a or 0) for a in (list(pose.get('rotation') or []) + [0, 0])[:2])
        nx, ny, nz = -math.sin(ry), math.sin(rx) * math.cos(ry), max(0.1, math.cos(rx) * math.cos(ry))
        d = bounds[5 if top else 2] * float(pose.get('scale') or 1.0)
        return [z + (d - nx * (px - x) - ny * (py - y)) / nz for px, py in points]

    def _form_type(self, ptr):
        reads = getattr(self.live, 'reads', None) if self.live is not None else None
        if not reads or not ptr:
            return None
        try:
            raw = reads([(ptr + FORM_TYPE_OFFSET, 1)])[0]
        except Exception:  # noqa: BLE001 - unknown type: treated as an object
            return None
        return raw[0] if raw else None

    @staticmethod
    def _footprint_points(bounds, x, y, heading, scale):
        """The (x, y) points a footprint snap casts from: the origin first, then a grid over the bounds' X/Y rectangle
        (scaled, pulled FOOT_INSET in, turned to the heading; corners and edges included), or None for an object
        narrower than FOOT_SMALL both ways."""
        s = float(scale or 1.0)
        x0, y0, x1, y1 = bounds[0] * s, bounds[1] * s, bounds[3] * s, bounds[4] * s
        if x1 - x0 < FOOT_SMALL and y1 - y0 < FOOT_SMALL:
            return None

        def axis(lo, hi):
            if hi - lo > 2 * FOOT_INSET:
                lo, hi = lo + FOOT_INSET, hi - FOOT_INSET
            return lo, hi, max(3, math.ceil((hi - lo) / FOOT_SPACING) + 1)

        ax0, ax1, nx = axis(x0, x1)
        ay0, ay1, ny = axis(y0, y1)
        while nx * ny > FOOT_MAX - 1:
            if nx >= ny:
                nx -= 1
            else:
                ny -= 1
        xs = [ax0 + (ax1 - ax0) * k / (nx - 1) for k in range(nx)]
        ys = [ay0 + (ay1 - ay0) * k / (ny - 1) for k in range(ny)]
        h = math.radians(heading or 0)
        c, sn = math.cos(h), math.sin(h)
        # local x (the object's right) is world (cos h, -sin h), local y (forward) is (sin h, cos h)
        return [(x, y)] + [(x + lx * c + ly * sn, y - lx * sn + ly * c) for ly in ys for lx in xs]

    @staticmethod
    def _split_overhangs(values, height):
        """The cut under which samples are overhangs: a group of the lowest samples (fewer than half) more than
        max(64, a quarter of the object's height, 3 x the spread of the rest) under the rest, as where one edge runs
        past a cliff or over a gully. A smaller step is rested on (the high side sinks a little) rather than left
        floating over. None when there is none."""
        v = sorted(values)
        n = len(v)
        best, cut = 0.0, None
        for k in range(1, (n + 1) // 2):
            gap = v[k] - v[k - 1]
            if gap > max(64.0, 0.25 * height, 3 * (v[-1] - v[k])) and gap > best:
                best, cut = gap, v[k]
        return cut

    def _footprint(self, bounds, x, y, z, heading, scale, ignore=0, reach=200.0, sink=64.0, points=None,
                   plane=False, pose=None, rest=None):
        """Cast the footprint and measure it. Returns the samples and, per sample, the ground (None: no ground
        found), which are overhangs, and with `plane` a least-squares plane through the kept samples (the slope
        align lays the object on). `pose`: the object stands there (snap, check): the ground at a sample is the
        first surface at or below its top there. `rest` (a placement: nothing stands there yet): the seat is found
        by the centre's rules, then every sample is judged again against the top the object will have, from the
        surfaces its ray already met, as check will judge it (live: a boulder top inside the shack was overhead to
        the placement, which said sunk 336 where check said 530). None when no sample found ground."""
        pts = points or self._footprint_points(bounds, x, y, heading, scale)
        s = float(scale or 1.0)
        radius = max(math.hypot(px - x, py - y) for px, py in pts)
        tops = self._face_heights(bounds, pose, pts, top=True) if pose else None
        # start over the object's own top (its collision, for snap and check) and over ground rising up to ~63
        # degrees across the footprint
        keep = {} if rest and pose is None else None
        found = self._ground_many(pts, z, ignore, reach + max(0.0, bounds[5] * s) + 2 * radius, sink, tops=tops,
                                  land=True, keep=keep)
        grounds = [f[0] for f in found]
        valid = [g for g in grounds if g is not None]
        if not valid:
            return None
        seat = None
        if keep is not None:
            height = max(1.0, (bounds[5] - bounds[2]) * s)
            first = {'points': pts, 'values': grounds, 'plane': None, 'missed': 0, 'heading': heading or 0.0,
                     'overhang': self._overhang_flags(grounds, height)}
            seat = self._rest_on(first, rest, height)[0]
            for i, (surfaces, landed) in keep.items():
                again = self._ground_under_top(surfaces, seat + height, landed)
                if again is not None:
                    grounds[i] = again[0]
        fit = None
        values = list(grounds)
        if plane:
            fit = self._fit_plane([(px - x, py - y, g) for (px, py), g in zip(pts, grounds) if g is not None])
            if fit is not None:
                a, b, c = fit
                values = [None if g is None else g - (a * (px - x) + b * (py - y) + c)
                          for (px, py), g in zip(pts, grounds)]
        return {'points': pts, 'grounds': grounds, 'values': values, 'plane': fit, 'seat': seat,
                'overhang': self._overhang_flags(values, (bounds[5] - bounds[2]) * s),
                'missed': sum(1 for g in grounds if g is None), 'heading': heading or 0.0}

    @classmethod
    def _overhang_flags(cls, values, height):
        cut = cls._split_overhangs([v for v in values if v is not None], height)
        return [v is not None and cut is not None and v < cut for v in values]

    @staticmethod
    def _fit_plane(samples):
        """(a, b, c) of z = a dx + b dy + c through (dx, dy, z) samples by least squares, or None when they do not
        span a plane."""
        if len(samples) < 3:
            return None
        sxx = sum(dx * dx for dx, dy, _ in samples)
        sxy = sum(dx * dy for dx, dy, _ in samples)
        syy = sum(dy * dy for dx, dy, _ in samples)
        sx = sum(dx for dx, _, _ in samples)
        sy = sum(dy for _, dy, _ in samples)
        n = float(len(samples))
        sxz = sum(dx * z for dx, _, z in samples)
        syz = sum(dy * z for _, dy, z in samples)
        sz = sum(z for _, _, z in samples)
        m = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
        r = [sxz, syz, sz]

        def det(a):
            return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                    + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
        d = det(m)
        if abs(d) < 1e-9 * max(1.0, n * sxx * syy):
            return None
        out = []
        for col in range(3):
            mc = [row[:] for row in m]
            for k in range(3):
                mc[k][col] = r[k]
            out.append(det(mc) / d)
        return tuple(out)

    @staticmethod
    def _rest_on(fp, rest, height, bottom=None):
        """Where the bottom goes on a measured footprint and what that leaves: (bottom value, report). `rest`: low =
        the lowest kept sample (nothing floats; the high side sinks), high = the highest (nothing sinks), centre =
        the origin's sample. `bottom` given: judge that bottom instead (check). Values are ground heights, or with a
        plane, heights over that plane."""
        values, over = fp['values'], fp['overhang']
        kept = [v for v, o in zip(values, over) if v is not None and not o]
        lo, hi = min(kept), max(kept)
        if bottom is None:
            if rest == 'high':
                bottom = hi
            elif rest == 'centre':
                bottom = values[0] if values[0] is not None else lo
            else:
                bottom = lo
        sunk, floats = max(0.0, hi - bottom), max(0.0, bottom - lo)
        report = {'samples': len(values), 'rest': rest, 'heading': round(fp['heading'], 1),
                  'spread': round(hi - lo, 2), 'sunk': round(sunk, 2), 'floats': round(floats, 2)}
        if fp['plane'] is None:
            report.update({'groundMin': round(lo, 2), 'groundMax': round(hi, 2)})
        overhangs = [{'at': [round(p[0], 1), round(p[1], 1)], 'under': round(lo - v, 1)}
                     for p, v, o in zip(fp['points'], values, over) if o]
        report['overhangs'] = overhangs
        if fp['missed']:
            report['missed'] = fp['missed']
        warn = []
        if overhangs:
            warn.append('%d of %d samples are %.0f or more units under the rest: that part overhangs a drop (a cliff '
                        'edge or a gully) and is left hanging' % (len(overhangs), len(values),
                                                                   min(o['under'] for o in overhangs)))
        if fp['missed']:
            warn.append('%d of %d samples found no ground (an edge, a gap, or ground over the rays\' start)'
                        % (fp['missed'], len(values)))
        if sunk > 0.25 * height:
            warn.append('its high side is sunk %.0f units into the ground (%.0f%% of its %.0f-unit height)'
                        % (sunk, 100 * sunk / height, height))
        if floats > 10:
            warn.append('its low side floats %.0f units over the ground' % floats)
        if warn:
            report['warning'] = '; '.join(warn)
        steep = hi - lo > 0.25 * height
        return bottom, report, steep, sunk > height

    def _suggest_heading(self, bounds, x, y, z, scale, ignore, reach, sink, current, current_spread, pose=None,
                         rest=None):
        """The heading of the eight 45 degrees apart that leaves the least spread under the footprint (a
        rectangle is the same turned 180, so four are cast)."""
        best = {'heading': round(current % 180.0, 1), 'spread': round(current_spread, 2)}
        for h in (0.0, 45.0, 90.0, 135.0):
            if abs(((h - current) + 90.0) % 180.0 - 90.0) < 1e-6:
                continue
            fp = self._footprint(bounds, x, y, z, h, scale, ignore, reach, sink, pose=pose, rest=rest)
            if fp is None:
                continue
            kept = [v for v, o in zip(fp['values'], fp['overhang']) if v is not None and not o]
            spread = max(kept) - min(kept)
            if spread < best['spread'] - 1e-6:
                best = {'heading': h, 'spread': round(spread, 2)}
        # Live check 2026-11-18: the suggestion was the heading it already had. Say so: turning will not help.
        best['helps'] = abs(((best['heading'] - current) + 90.0) % 180.0 - 90.0) > 1e-6
        if best['helps']:
            best['note'] = ('the narrow side facing downhill spreads least; turn it with rotation=[0, 0, %g] (or %g)'
                            % (best['heading'], best['heading'] + 180))
        else:
            best['note'] = ('no turn helps: its heading %g already spreads least of the four tried; move it to flatter '
                            'ground or rest it "centre" or "high"' % best['heading'])
        return best

    @staticmethod
    def _turn_hint(report):
        """'Turn it (see suggestHeading), ' when turning helps, else ''."""
        return 'Turn it to heading %g, ' % report['suggestHeading']['heading'] \
            if (report.get('suggestHeading') or {}).get('helps') else ''

    def _snap_foot(self, base_ptr, bounds, x, y, z, rotation, scale, rest, ignore=0, reach=200.0, sink=64.0):
        """Footprint snap of one object: (ground the bottom rests on, footprint report), or (None, None) when the
        footprint does not apply (no bounds, an actor, a small object) or found no ground. Raises TOO_STEEP when
        resting low would sink it deeper than its whole height."""
        if not bounds or self._form_type(base_ptr) in ACTOR_BASE_TYPES:
            return None, None
        heading = (list(rotation or []) + [0, 0, 0])[2] or 0.0
        pts = self._footprint_points(bounds, x, y, heading, scale)
        if not pts:
            return None, None
        fp = self._footprint(bounds, x, y, z, heading, scale, ignore, reach, sink, points=pts, rest=rest)
        if fp is None:
            return None, None
        height = max(1.0, (bounds[5] - bounds[2]) * float(scale or 1.0))
        bottom, report, steep, too_deep = self._rest_on(fp, rest, height, bottom=fp['seat'])
        if steep:
            report['suggestHeading'] = self._suggest_heading(bounds, x, y, z, scale, ignore, reach, sink, heading,
                                                             report['spread'], rest=rest)
        if too_deep and rest == 'low':
            hint = self._turn_hint(report)
            raise LiveError('TOO_STEEP', 'The ground under it rises %.0f units across its footprint, more than its '
                            '%.0f-unit height: resting it low would bury its high side. %smove it to flatter ground, '
                            'or pass rest="centre" or "high" to place it anyway.'
                            % (report['spread'], height, hint or 'No turn helps: '), report)
        return bottom, report

    def _ground(self, x, y, z, ignore=0, reach=200.0, sink=64.0):
        """Height of the surface under the point (x, y, z), or None (see _ground_info)."""
        return self._ground_info(x, y, z, ignore, reach, sink)[0]

    def _ground_info(self, x, y, z, ignore=0, reach=200.0, sink=64.0):
        """(surface height or None, the nearest surface left overhead or None, the pick layer they were found on) at
        (x, y, z). The ray starts `reach`
        above z, but the first hit from above is not taken as the surface (P1: in a low interior that was the
        ceiling):
        - with `ignore` (the reference being snapped or checked) and its own collision met by the ray: a surface hit
          before the object is wholly above it and is left overhead (a ceiling, a shelf); the first surface hit after
          it is the one it rests on or is sunk into. A wholly buried object cannot be told from one under a ceiling
          by vertical rays: _side decides that from the player's eye;
        - otherwise (a point, or an object whose own collision the ray misses): the first surface at or below
          z + `sink` (a curb, a stair tread or a slope the point is in), one above only when none is under it (a
          point inside a hill)."""
        return self._ground_many([(x, y)], z, ignore, reach, sink)[0]

    def _ground_many(self, points, z, ignore=0, reach=200.0, sink=64.0, tops=None, land=False, keep=None):
        """_ground_info for many (x, y) points at one height z, the rays of every point batched into one cast per
        step down (a footprint costs a few casts, not a few per point). Same rules, point by point, and for a
        footprint two more (both found live 2026-11-18 at a boulder):
        - `land`: the landscape (a hit that names no reference) ends the way down: nothing under it is ground. A
          boulder's buried underside under it was taken for ground and the snap sank a shack under the ground;
        - `tops` (each point's height of the top of the object being judged, its own collision passed through): the
          first surface at or below that top is the ground there, one above it is overhead. Where the object is
          sunk into a slope the ground is met before its own collision, and the own-collision rule left it
          overhead and the point without ground.
        `keep` (a dict): filled with each point's surfaces, top down, and whether the ray ended on the landscape."""
        rays = self.hands.rays if self.hands else None
        out = [(None, None, None)] * len(points)
        if rays is None or not points:
            return out
        pending = list(range(len(points)))
        for layer in (0x25, 0x26, 6):
            if not pending:
                break
            top = {i: z + reach for i in pending}
            surfaces = {i: [] for i in pending}
            own_at = {i: None for i in pending}
            landed = set()
            active = list(pending)
            for _ in range(8 if tops is None else 12):
                if not active:
                    break
                hits = rays.cast([((points[i][0], points[i][1], top[i]), (points[i][0], points[i][1], z - 2000.0))
                                  for i in active], layer=layer, refs=bool(ignore) or land)
                still = []
                for i, hit in zip(active, hits):
                    if not hit.get('point'):
                        continue
                    h = hit['point'][2]
                    if ignore and hit.get('ref') == ignore:
                        if own_at[i] is None:
                            own_at[i] = len(surfaces[i])
                    else:
                        surfaces[i].append(h)
                        if land and not hit.get('ref'):
                            landed.add(i)
                            continue      # the landscape: nothing under it is ground
                        if h <= (z if tops is None else tops[i] + 2.0):
                            continue      # a surface under the point: nothing below it matters
                    top[i] = h - 1.0
                    still.append(i)
                active = still
            unresolved = []
            for i in pending:
                if tops is not None:
                    found = self._ground_under_top(surfaces[i], tops[i], i in landed)
                else:
                    found = self._ground_rule(surfaces[i], own_at[i], z, sink)
                if found is None:
                    unresolved.append(i)
                else:
                    out[i] = (found[0], found[1], layer)
                    if keep is not None:
                        keep[i] = (list(surfaces[i]), i in landed)
            pending = unresolved
        return out

    @staticmethod
    def _ground_under_top(surfaces, top, landed):
        """(under, over) for a point of an object whose top there is `top`: the first surface at or below the top
        (inside the object where it is sunk, under it where it is not); over it, overhead. With none under the top,
        the landscape over it when the ray ended on it (that part is buried), else None."""
        for k, h in enumerate(surfaces):
            if h <= top + 2.0:
                return h, (surfaces[k - 1] if k else None)
        if landed and surfaces:
            return surfaces[-1], None
        return None

    @staticmethod
    def _ground_rule(surfaces, own_at, z, sink):
        """(under, over) from the surfaces one ray met going down (own collision left out, `own_at` = how many came
        before it), or None when this layer found nothing to go by."""
        if own_at is not None:
            over = surfaces[own_at - 1] if own_at else None
            after = surfaces[own_at:]
            if after or surfaces:
                return (after[0] if after else None), over
            return None
        if surfaces:
            below = [h for h in surfaces if h <= z + sink]
            if not below:
                return surfaces[-1], None
            over = [h for h in surfaces if h > z + sink]
            return below[0], (over[-1] if over else None)
        return None

    EYE_HEIGHT = 100.0     # over the player's feet: inside its capsule, under any ceiling it stands under
    EYE_REACH = 3000.0

    def _eye(self):
        """(the player's reference pointer, a point at its eye) or None: a point known to be in open air."""
        hands = self.hands
        if hands is None or not hasattr(hands, '_player'):
            return None
        try:
            player = hands._player()
            x, y, z = hands._pos(player)
        except LiveError:
            return None
        return player, (x, y, z + self.EYE_HEIGHT)

    def _crossings(self, a, b, skip, layer):
        """Distances from a of the surfaces the segment a-b crosses, or None when a ray could not tell. Hits on
        `skip` (references) are passed through. The segment is cast from both ends, because a ray that starts inside
        a convex shape does not hit it: from one end it meets the entry face, from the other the exit face (a
        triangle mesh is met from either side). Hits less than 1.5 units apart count once."""
        rays = self.hands.rays
        length = math.dist(a, b)
        if length < 1.0:
            return []
        found = []
        for s, e, back in ((a, b, False), (b, a, True)):
            d = [(e[i] - s[i]) / length for i in range(3)]
            start, done = s, 0.0
            for _ in range(8):
                hit = rays.cast([(start, e)], layer=layer, refs=True)[0]
                if hit.get('throttled'):
                    return None
                if not hit.get('hit'):
                    break
                point = hit.get('point')
                if point is None:
                    return None
                t = math.dist(s, point)
                if hit.get('ref') not in skip:
                    found.append(length - t if back else t)
                done = t + 1.0
                if done >= length:
                    break
                start = tuple(s[i] + d[i] * done for i in range(3))
            else:
                return None                     # more surfaces than it follows: do not decide
        merged = []
        for t in sorted(found):
            if not merged or t - merged[-1] > 1.5:
                merged.append(t)
        return merged

    def _side(self, x, y, z, over, own, layer):
        """Round-4 V2: which side of the surface `over` (close over the point) the point is on, seen from the
        player's eye. 'open': the eye sees the point with nothing between, so it hangs in the player's air under a
        ceiling or shelf. 'buried': the eye sees the point just over that surface and exactly one surface lies
        between the eye and the point, so it is under a surface the player is over. None: anything else (no
        player, too far, a ray could not tell, furniture or a closed slab in the way)."""
        eye = self._eye()
        if eye is None or layer is None:
            return None
        player, e = eye
        point = (x, y, z)
        if math.dist(e, point) > self.EYE_REACH:
            return None
        skip = {own, player}
        to_point = self._crossings(e, point, skip, layer)
        if to_point == []:
            return 'open'
        if to_point is not None and len(to_point) == 1 and self._crossings(e, (x, y, over + 2.0), skip, layer) == []:
            return 'buried'
        return None

    def _settle(self, x, y, z, own):
        """(surface under, surface over, side) for snap and check: side is _side's answer when a surface is close
        over the object and the ground is well under it or missing, else None."""
        g, over, layer = self._ground_info(x, y, z, ignore=own, reach=250.0, sink=50.0)
        side = None
        if over is not None and over - z <= self.CLOSE_OVER and (g is None or g < z - 5):
            side = self._side(x, y, z, over, own, layer)
        return g, over, side

    @staticmethod
    def _rest_mode(rest):
        rest = (rest or 'low').lower()
        rest = 'centre' if rest == 'center' else rest
        if rest not in FOOT_REST:
            raise ValueError('rest is low, centre or high')
        return rest

    def _snapped(self, b, bounds, position, rotation, scale, rest):
        """(position snapped onto the ground, footprint report or None). Big objects rest by their footprint, the
        rest by the ground under the origin; either way the bottom of the bounds goes on it."""
        g, report = self._snap_foot(b.get('ptr'), bounds, position[0], position[1], position[2], rotation, scale,
                                    rest)
        if report is None:
            g = self._ground(position[0], position[1], position[2])
        if g is None:
            return position, None
        return [position[0], position[1], g + self._lift(bounds, scale, rotation)], report

    @staticmethod
    def _vec3(value, what):
        """Three numbers as floats, or None when not given. A list of another length used to place or turn on
        some axes only, or end in a Python unpacking error (bug hunt 2026-11-18)."""
        if value is None:
            return None
        try:
            out = [float(v) for v in value]
        except (TypeError, ValueError):
            raise ValueError('%s is three numbers [x, y, z]' % what) from None
        if len(out) != 3 or not all(math.isfinite(v) for v in out):
            raise ValueError('%s is three numbers [x, y, z]' % what)
        return out

    @staticmethod
    def _scale_value(scale):
        if scale is None:
            return None
        try:
            value = float(scale)
        except (TypeError, ValueError):
            raise ValueError('scale is a number above 0') from None
        if not value > 0 or not math.isfinite(value):
            raise ValueError('scale is a number above 0 (1 is the size the base form has)')
        return value

    def at(self, base, position=None, near=None, offset=None, rotation=None, scale=None, snap=False, rest=None):
        self.live.in_game()
        rest = self._rest_mode(rest)
        position, offset, rotation = (self._vec3(position, 'position'), self._vec3(offset, 'offset'),
                                      self._vec3(rotation, 'rotation'))
        scale = self._scale_value(scale)
        b = self.live.resolve(base)
        if position is None:
            anchor = self.live.resolve(near or 'player')
            x, y, z = self.live.values(['GetPos X', 'GetPos Y', 'GetPos Z'], anchor['ptr'])
            if near is None and offset is None:
                heading = math.radians(self.live.values(['GetAngle Z'], anchor['ptr'])[0] or 0)
                offset = [math.sin(heading) * 150, math.cos(heading) * 150, 0]
            off = offset or [0, 0, 0]
            position = [x + off[0], y + off[1], z + off[2]]
        report = None
        if snap:
            position, report = self._snapped(b, self._bounds(b.get('ptr')), position, rotation, scale, rest)
        self.live.changing('place')
        out = self._place_one(b, position, rotation, scale)
        return {**out, 'footprint': report} if report else out

    def array(self, base, origin=None, shape='row', count=5, spacing=128.0, columns=None, radius=None, rotation=None,
              snap=False, rest=None):
        self.live.in_game()
        rest = self._rest_mode(rest)
        if not 1 <= int(count) <= 100:
            raise ValueError('count is 1..100')
        origin, rotation = self._vec3(origin, 'position'), self._vec3(rotation, 'rotation')
        b = self.live.resolve(base)
        if origin is None:
            player = self.live.resolve('player')
            origin = self.live.values(['GetPos X', 'GetPos Y', 'GetPos Z'], player['ptr'])
        ox, oy, oz = origin
        points = []
        if shape == 'row':
            points = [(ox + k * spacing, oy, oz) for k in range(count)]
        elif shape == 'grid':
            cols = int(columns or math.ceil(math.sqrt(count)))
            points = [(ox + (k % cols) * spacing, oy + (k // cols) * spacing, oz) for k in range(count)]
        elif shape == 'circle':
            r = float(radius or spacing * count / (2 * math.pi))
            points = [(ox + r * math.sin(2 * math.pi * k / count), oy + r * math.cos(2 * math.pi * k / count), oz)
                      for k in range(count)]
        else:
            raise ValueError('shape is row, grid or circle')
        self.live.changing('place array')
        bounds = self._bounds(b.get('ptr')) if snap else None
        out, skipped = [], []
        for p in points:
            pos, report = list(p), None
            if snap:
                try:
                    pos, report = self._snapped(b, bounds, pos, rotation, None, rest)
                except LiveError as e:
                    if e.code != 'TOO_STEEP':
                        raise
                    skipped.append({'position': [round(v, 2) for v in p], 'code': e.code, 'message': str(e),
                                    'footprint': e.evidence})
                    continue
            placed = self._place_one(b, pos, rotation)
            out.append({**placed, 'footprint': report} if report else placed)
        result = {'placed': out, 'count': len(out), 'shape': shape}
        if skipped:
            result['skipped'] = skipped
        return result

    def adjust(self, ref, move=None, rotate=None, scale=None, local=False):
        self.live.in_game()
        move, rotate, scale = self._vec3(move, 'offset'), self._vec3(rotate, 'rotation'), self._scale_value(scale)
        if move is None and rotate is None and scale is None:
            raise ValueError('nothing to change: nudge needs offset=[dx, dy, dz], rotate needs rotation=[dx, dy, dz] '
                             'degrees, scale needs scale')
        r = self.live.resolve(ref)
        before = self._pose_exact(r['ptr'])
        position = rotation = None
        if move:
            dx, dy, dz = move
            if local:
                h = math.radians(before['rotation'][2])
                dx, dy = dx * math.cos(h) + dy * math.sin(h), -dx * math.sin(h) + dy * math.cos(h)
            position = [before['position'][0] + dx, before['position'][1] + dy, before['position'][2] + dz]
        if rotate:
            rotation = [(before['rotation'][i] + rotate[i]) % 360 for i in range(3)]
        self.live.changing('place adjust')
        undo = ['SetPos %s %.3f' % (a, v) for a, v in zip('XYZ', before['position'])] + \
               ['SetAngle %s %.3f' % (a, v) for a, v in zip('XYZ', before['rotation'])] + \
               ['SetScale %.4f' % before['scale']]
        entry = self.live.journal.add({'kind': 'place_adjust', 'state': 'pending', 'ref': r['formId'], 'before': before,
                                       'undo': {'op': 'console_ref', 'ref': r['formId'], 'lines': undo}})
        self._set(r['ptr'], position, rotation, scale)
        after = self._pose_exact(r['ptr'])
        self.live.journal.confirm(entry['id'], {'after': after})
        self._update_placed(r['formId'], after, entry['id'])
        return {'ref': r['formId'], 'before': self._rounded(before), 'after': self._rounded(after),
                'journalId': entry['id']}

    def _update_placed(self, form_id, pose, source):
        """Keep the newest pose of a placed reference in the journal (export reads it). `source` is the adjust
        entry that made it: when that adjust is undone, the pose before it counts again (G1)."""
        for e in self._placed():
            if e.get('ref') == form_id:
                self.live.journal.add({'kind': 'place_pose', 'ref': form_id, 'source': source, **pose, 'undo': None})
                return

    def _surface(self, x, y, z, ignore=0, d=24.0, reach=200.0, sink=64.0):
        """The surface under (x, y): its height and the slope (dz/dx, dz/dy) from four more rays d units around
        (the pick rays give no normal). None when the centre ray finds nothing."""
        g = self._ground(x, y, z, ignore=ignore, reach=reach, sink=sink)
        if g is None:
            return None
        around = [self._ground(x + dx, y + dy, z, ignore=ignore, reach=reach, sink=sink)
                  for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d))]
        xp, xm, yp, ym = [g if v is None or abs(v - g) > 4 * d else v for v in around]   # a step or a wall: flat
        return g, (xp - xm) / (2 * d), (yp - ym) / (2 * d)

    @staticmethod
    def _tilt(dzdx, dzdy, heading):
        """SetAngle X and Y (degrees) that turn an object's up axis onto the surface normal. Measured in the game from
        the 3D's world rotation (+0x68): the up axis is (-sin Y, sin X cos Y, cos X cos Y) whatever the heading (Z
        turns first, so it keeps the heading)."""
        nx, ny, nz = -dzdx, -dzdy, 1.0
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        nx, ny = nx / length, ny / length
        y = -math.asin(max(-1.0, min(1.0, nx)))
        x = math.asin(max(-1.0, min(1.0, ny / max(1e-6, math.cos(y)))))
        return math.degrees(x), math.degrees(y)

    CLOSE_OVER = 64.0

    def _ambiguous(self, g, over, z, side=None):
        """A surface close over the object and the ground found well under it: it is buried under the one over it,
        or floats under a low ceiling or shelf. Vertical rays cannot tell which (round-4 V2, review I1); it stays
        ambiguous when the player's eye could not tell either (`side`, from _side)."""
        return side is None and g is not None and over is not None and over - z <= self.CLOSE_OVER and g < z - 5

    @staticmethod
    def _ambiguous_text(g, over, z):
        return ('ambiguous: a surface %.0f units over it and the next %.0f under it: it is buried under the one over '
                'it, or floats under a low ceiling or shelf' % (over - z, z - g))

    def snap(self, ref, align=False, rest=None):
        self.live.in_game()
        rest = self._rest_mode(rest)
        r = self.live.resolve(ref)
        pose = self._pose(r['ptr'])
        x, y, z = pose['position']
        centre, over, side = self._settle(x, y, z, r['ptr'])
        if self._ambiguous(centre, over, z, side):
            return {'ref': r['formId'], 'snapped': False, 'surface': round(centre, 2), 'over': round(over, 2),
                    'note': self._ambiguous_text(centre, over, z) + ', and the player\'s eye cannot tell which (no '
                            'clear line to it, or to the top of the surface over it); snap does not guess. Look '
                            '(fnv_photo), then move it with fnv_place nudge offset=[0, 0, dz].'}
        extra = {}
        if side == 'buried':
            # onto the surface it is buried under; the slope is read from just over that surface
            centre, at, reach = over, over + 1.0, 250.0
            extra = {'buried': True, 'over': round(over, 2), 'side': 'the player\'s eye sees the top of the surface '
                     'over it, and that surface lies between the eye and the object: it was buried under it'}
        elif side == 'open' and centre is not None:
            # in the player's air under a ceiling or shelf: slope rays start under that surface, never on it
            at, reach = centre + 1.0, max(1.0, over - centre - 2.0)
            extra = {'over': round(over, 2), 'side': 'the player\'s eye sees it with nothing between: it floats '
                     'under the surface over it, a ceiling or shelf'}
        else:
            at, reach = z, 250.0
        base_ptr = self._base_of(r['ptr'])
        bounds = self._bounds(base_ptr)
        if bounds:
            extra['bounds'] = list(bounds)
        # the footprint, in the plain case only: buried or under a ceiling, the centre's answer stands
        foot = None
        if side is None and bounds and self._form_type(base_ptr) not in ACTOR_BASE_TYPES:
            pts = self._footprint_points(bounds, x, y, pose['rotation'][2], pose['scale'])
            if pts:
                foot = self._footprint(bounds, x, y, z, pose['rotation'][2], pose['scale'], ignore=r['ptr'],
                                       reach=250.0, sink=50.0, points=pts, plane=align, pose=pose)
        if foot is not None and not (align and foot['plane'] is None):
            return self._snap_by_footprint(ref, r, pose, bounds, foot, rest, align, extra)
        surface = self._surface(x, y, at, ignore=r['ptr'], reach=reach, sink=50.0) if align else None
        g = surface[0] if surface else centre
        if g is None:
            return {'ref': r['formId'], 'snapped': False, 'note': 'No surface found under it on the pick layers.'}
        if not align:
            lift = self._lift(bounds, pose['scale'], pose['rotation'])
            return {**self.adjust(ref, move=[0, 0, g + lift - z]), 'snapped': True, 'ground': round(g, 2),
                    'lift': round(lift, 2), **extra}
        _, dzdx, dzdy = surface
        return self._lay(ref, pose, bounds, g, dzdx, dzdy, extra,
                         'the surface slope from five rays 24 units apart (the pick rays carry no normal); SetAngle X '
                         'and Y lay the object on it at its heading')

    def _lay(self, ref, pose, bounds, g, dzdx, dzdy, extra, how):
        """Turn the object onto the slope (dzdx, dzdy) at its heading and rest its bottom on the plane through g."""
        z = pose['position'][2]
        pitch, roll = self._tilt(dzdx, dzdy, pose['rotation'][2])
        rotate = [((pitch - pose['rotation'][0] + 180) % 360) - 180, ((roll - pose['rotation'][1] + 180) % 360) - 180,
                  0]
        lift = self._lift_on_plane(bounds, pose['scale'], pitch, roll)
        out = self.adjust(ref, move=[0, 0, g + lift - z], rotate=rotate)
        slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
        return {**out, 'snapped': True, 'ground': round(g, 2), **extra, 'lift': round(lift, 2), 'aligned': True,
                'slopeDegrees': round(slope, 1), 'how': how}

    def _snap_by_footprint(self, ref, r, pose, bounds, foot, rest, align, extra):
        x, y, z = pose['position']
        height = max(1.0, (bounds[5] - bounds[2]) * float(pose['scale'] or 1.0))
        bottom, report, steep, too_deep = self._rest_on(foot, rest, height)
        if steep and not align:
            report['suggestHeading'] = self._suggest_heading(bounds, x, y, z, pose['scale'], r['ptr'], 250.0, 50.0,
                                                             pose['rotation'][2], report['spread'], pose=pose)
        extra['footprint'] = report
        if too_deep and rest == 'low':
            return {'ref': r['formId'], 'snapped': False, **extra,
                    'note': 'too steep: the ground under it rises %.0f units across its footprint, more than its '
                            '%.0f-unit height; resting it low would bury its high side. %smove it, align it '
                            '(align=true), or snap with rest="centre" or "high".'
                            % (report['spread'], height, self._turn_hint(report))}
        if not align:
            lift = self._lift(bounds, pose['scale'], pose['rotation'])
            return {**self.adjust(ref, move=[0, 0, bottom + lift - z]), 'snapped': True, 'ground': round(bottom, 2),
                    'lift': round(lift, 2), **extra}
        a, b, c = foot['plane']
        return self._lay(ref, pose, bounds, c + bottom, a, b, extra,
                         'a plane fitted through %d samples over its footprint (the pick rays carry no normal), '
                         'moved to rest %s on them; SetAngle X and Y lay the object on it at its heading'
                         % (report['samples'], {'low': 'on the lowest', 'high': 'on the highest',
                                                'centre': 'through the centre one'}[rest]))

    def check(self, ref):
        self.live.in_game()
        r = self.live.resolve(ref)
        pose = self._pose(r['ptr'])
        x, y, z = pose['position']
        g, over, side = self._settle(x, y, z, r['ptr'])
        # floating and sinking are judged by the bottom of its bounds, not its origin (a centred origin sat on the
        # ground with half the object under it, and check saw nothing)
        base_ptr = self._base_of(r['ptr'])
        bounds = self._bounds(base_ptr)
        bottom = z - self._lift(bounds, pose['scale'], pose['rotation'])
        issues = []
        foot = None
        if side is None and g is not None and bounds and self._form_type(base_ptr) not in ACTOR_BASE_TYPES \
                and not self._ambiguous(g, over, z, side):
            pts = self._footprint_points(bounds, x, y, pose['rotation'][2], pose['scale'])
            if pts:
                foot = self._footprint(bounds, x, y, z, pose['rotation'][2], pose['scale'], ignore=r['ptr'],
                                       reach=250.0, sink=50.0, points=pts, pose=pose)
        report = None
        if side == 'buried':
            issues.append('buried %.0f units under the surface over it' % (over - z))
            g = over
        elif self._ambiguous(g, over, z, side):
            issues.append(self._ambiguous_text(g, over, z) + ', and the player\'s eye cannot tell which')
        elif foot is not None:
            # judged over the footprint: a corner floating over a dip is a fault; the high side sinking into a slope
            # is how a building sits on uneven ground, and only a fault past its whole height
            # Each sample is judged against the bottom face at that point: tilted (after align) that is a sloped
            # plane, not one height (seen live: an aligned shack was called floating 133 units).
            height = max(1.0, (bounds[5] - bounds[2]) * float(pose['scale'] or 1.0))
            under = self._face_heights(bounds, pose, foot['points'])
            res = [None if gi is None else gi - b for gi, b in zip(foot['grounds'], under)]
            cut = self._split_overhangs([v for v in res if v is not None], height)
            judged = {**foot, 'values': res, 'plane': 'bottom',
                      'overhang': [v is not None and cut is not None and v < cut for v in res]}
            _, report, _, _ = self._rest_on(judged, 'low', height, bottom=0.0)
            report.pop('rest', None)
            report.pop('warning', None)
            kept = [(v, p, gi) for v, p, gi, o in zip(res, foot['points'], foot['grounds'], judged['overhang'])
                    if v is not None and not o]
            report['groundMin'] = round(min(k[2] for k in kept), 2)
            report['groundMax'] = round(max(k[2] for k in kept), 2)
            lo, lo_at, _ = min(kept, key=lambda k: k[0])
            hi = max(k[0] for k in kept)
            if lo > 5:
                issues.append('below the surface under it by %.0f units' % lo)
            if lo < -10:
                issues.append('floats %.0f units above the surface under it' % -lo
                              + (' at its lowest point (%.0f, %.0f)' % lo_at if hi - lo > 10 else ''))
            if hi > height:
                issues.append('its high side is sunk %.0f units, more than its %.0f-unit height' % (hi, height))
            if report['overhangs']:
                issues.append('%d footprint samples overhang a drop (%.0f or more units under the rest)'
                              % (len(report['overhangs']), min(o['under'] for o in report['overhangs'])))
        elif g is not None and bottom > g + 10:
            issues.append('floats %.0f units above the surface under it' % (bottom - g)
                          + (', under a ceiling or shelf %.0f over it' % (over - z) if side == 'open' else ''))
        if g is not None and bottom < g - 5 and side != 'buried' and foot is None:
            issues.append('below the surface under it by %.0f units' % (g - bottom))
        if g is None:
            issues.append('no surface found under it')
            if over is not None and over - z <= self.CLOSE_OVER:
                issues.append('a surface %.0f units over it: a low ceiling or shelf, or it is buried under it'
                              % (over - z))
        overlaps = self._overlaps(r['ptr'])
        for o in overlaps:
            issues.append('overlaps %s (%s, %.0f units inside its bound)' % (o['ref'], o['how'], o['depth']))
        return {'ref': r['formId'], **pose, 'surface': None if g is None else round(g, 2), 'issues': issues,
                'overlaps': overlaps, 'bottom': round(bottom, 2), 'bounds': list(bounds) if bounds else None,
                **({'footprint': report} if report else {}),
                'note': 'Overlap: the world bound spheres of the loaded references are compared (a sphere is '
                        'coarse); a candidate counts when a ray from this object\'s centre to the other\'s hits the '
                        'other inside both bounds ("collision"), or when the centres nearly coincide ("centres").'}

    def _overlaps(self, ptr, limit=8):
        eyes = getattr(self.hands, 'eyes', None) if self.hands else None
        if eyes is None:
            return []
        from .fnv_eyes import cell_refs
        refs = cell_refs(self.live)
        bounds = eyes._bounds(sorted(set(refs) | {ptr}))
        me = bounds.get(ptr) or {}
        if not me.get('centre'):
            return []
        mx, my, mz = me['centre']
        mr = me['radius']
        cands = []
        for p, info in bounds.items():
            if p == ptr or not info.get('centre') or info.get('disabled') or not info.get('radius'):
                continue
            if info['radius'] > 4000:          # cells, landscape, skies: bounds that cover everything
                continue
            d = math.dist((mx, my, mz), info['centre'])
            depth = mr + info['radius'] - d
            if depth > 0.25 * min(mr, info['radius']):
                cands.append((depth, d, p, info))
        cands.sort(key=lambda c: -c[0])
        out = []
        for depth, d, p, info in cands[:limit * 2]:
            how = None
            if d < 0.25 * min(mr, info['radius']):
                how = 'centres'
            else:
                hit = self._ray(me['centre'], info['centre'])
                if hit is not None and hit.get('formId') == info['formId'] and hit['distance'] < min(mr, d):
                    how = 'collision'
            if how:
                out.append({'ref': info['formId'], 'how': how, 'depth': round(depth, 1),
                            'distance': round(d, 1)})
            if len(out) >= limit:
                break
        return out

    def _ray(self, start, end):
        engine_ai = getattr(self.live, 'engine_ai', None)
        if engine_ai is None:
            return None
        out = engine_ai.raycast(dict(zip('xyz', start)), dict(zip('xyz', end)), None, None, 'line_of_sight')
        if not out.get('hit'):
            return None
        ref = ((out.get('hitObject') or {}).get('reference') or {})
        return {'formId': ref.get('formId') if isinstance(ref, dict) else None,
                'distance': out.get('distanceToHit') or 0.0}

    def list(self, exact=False):
        """Placed refs of this game with the pose the game shows: the newest pose whose adjust was not undone, else
        the pose at placement (G1). exact: unrounded (export); otherwise rounded for display."""
        entries = self.live.journal.entries(process=self.live.game_identity())
        undone = {e['id'] for e in entries if e['undone']}
        latest = {}
        for e in entries:
            if e['kind'] == 'place' and not e['undone']:
                latest[e['ref']] = dict(e)
            elif e['kind'] == 'place_pose' and e['ref'] in latest and e.get('source') not in undone:
                latest[e['ref']].update({k: e[k] for k in ('position', 'rotation', 'scale') if k in e})
        out = []
        for e in latest.values():
            if e.get('position') is None:    # pending: the tool failed after PlaceAtMe (S3 review I4)
                try:
                    e.update(self._pose_exact(self.live.resolve(e['ref'])['ptr']))
                except Exception:  # noqa: BLE001 - the ref is gone or the game does not answer: listed without a pose
                    pass
            row = {k: e.get(k) for k in ('ref', 'base', 'cell', 'position', 'rotation', 'scale', 'id')}
            if not exact:
                row.update(self._rounded(row))
            out.append(row)
        return {'placed': out, 'changed': False}

    @staticmethod
    def _same_form(a, b):
        """Two form ids that name the same form: '0xFF001234', 'ff001234' and 'FF001234' are one."""
        try:
            return int(str(a), 16) == int(str(b), 16)
        except ValueError:
            return str(a).upper() == str(b).upper()

    def remove(self, ref=None):
        self.live.in_game()
        targets = [p for p in self.list()['placed'] if ref is None or self._same_form(p['ref'], ref)]
        if ref is not None and not targets:
            raise LiveError('NOT_PLACED', '%s was not placed by this session' % ref)
        self.live.changing('place remove')
        removed = []
        try:
            for p in targets:
                r = self.live.resolve(p['ref'])
                self.live.console('Disable', r['ptr'])
                self.live.console('MarkForDelete', r['ptr'])
                removed.append(p)
        finally:
            # Bug hunt 2026-11-18: a failure on the second ref left the first one deleted in the game but still
            # listed (and exported) as placed. Every ref that was removed leaves the journal, whatever happens next.
            ids = [p['id'] for p in removed if p.get('id')]
            # ...and so do its pose changes: undoing one later would replay SetPos on a deleted ref (bug hunt)
            gone = {str(p['ref']).upper() for p in removed}
            ids += [e['id'] for e in self.live.journal.entries(process=self.live.game_identity())
                    if e['kind'] == 'place_adjust' and not e['undone'] and str(e.get('ref')).upper() in gone]
            if ids:
                self.live.journal.mark_undone(ids)
        return {'removed': [p['ref'] for p in removed]}

    def run(self, action, **kw):
        if action == 'at':
            return self.at(kw['base'], kw.get('position'), kw.get('near'), kw.get('offset'), kw.get('rotation'),
                           kw.get('scale'), kw.get('snap', False), kw.get('rest'))
        if action == 'array':
            return self.array(kw['base'], kw.get('position'), kw.get('shape') or 'row', kw.get('count') or 5,
                              kw.get('spacing') or 128.0, kw.get('columns'), kw.get('radius'), kw.get('rotation'),
                              kw.get('snap', False), kw.get('rest'))
        if action == 'nudge':
            return self.adjust(kw['ref'], move=kw.get('offset'), local=kw.get('local', False))
        if action == 'rotate':
            return self.adjust(kw['ref'], rotate=kw.get('rotation'))
        if action == 'scale':
            return self.adjust(kw['ref'], scale=kw.get('scale'))
        if action == 'snap':
            return self.snap(kw['ref'], kw.get('align', False), kw.get('rest'))
        if action == 'check':
            return self.check(kw['ref'])
        if action == 'list':
            placed = self.list()['placed']
            shown = capped(placed, newest_first=True)    # export and remove read list() whole (S5.3, review M9)
            return {'placed': shown['items'], 'total': shown['total'], 'truncated': shown['truncated'],
                    'changed': False}
        if action == 'remove':
            return self.remove(kw.get('ref'))
        raise ValueError('action is at, array, nudge, rotate, scale, snap, check, list or remove')


# tool gaps 6: what a release must not hold, as the robot project's package.py checks it: decompiler output, and the
# places and name of this machine (found at run time, never written here)
DECOMPILER_TEXT = [rb'FUN_[0-9a-fA-F]{6,8}', rb'DAT_[0-9a-fA-F]{6,8}', rb'undefined[1248]\b', rb'(?i)ghidra',
                   rb'(?i)fnvdecomp']


def _path_pattern(path):
    parts = [re.escape(part.strip('\\/')).encode('utf-8') for part in Path(path).parts if part.strip('\\/')]
    return b'(?i)' + rb'[\\/]+'.join(parts) if parts else None


def private_markers(project):
    """[(what, bytes regex)] for the private places of this machine: the user's profile, the folder the checkout sits
    in, the MO2 root, and the Windows user name."""
    places = [('your user profile', Path.home())]
    checkout = Path(__file__).resolve().parents[2]
    places.append(('the fnv-mcp checkout', checkout))
    if checkout.parent != Path(checkout.anchor):
        places.append(('the folder of your checkouts', checkout.parent))
    mo2 = getattr(getattr(project, 'fnv', None), 'mo2_root', None)
    if mo2:
        places.append(('the MO2 folder', Path(mo2)))
    out = [(what, _path_pattern(path)) for what, path in places if _path_pattern(path)]
    user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
    if len(user) >= 4:
        out.append(('your user name', b'(?i)\\b' + re.escape(user).encode('utf-8') + b'\\b'))
    return out


def package_leaks(files, project):
    """Lines naming each file (path in the package, source path) that holds decompiler text or a private place."""
    private = private_markers(project)
    found = []
    for arc, src in files:
        data = Path(src).read_bytes()
        for pattern in DECOMPILER_TEXT:
            m = re.search(pattern, data)
            if m:
                found.append('%s holds decompiler text %r' % (arc, m.group().decode('latin-1')))
        for what, pattern in private:
            if re.search(pattern, data):
                found.append('%s holds a path or name of this machine (%s)' % (arc, what))
    return found


def _new_package_folder(packages, name):
    """A folder for a new package under packages that does not exist yet: name, else name-<stamp>, name-<stamp>-2..."""
    packages.mkdir(parents=True, exist_ok=True)

    def taken(path):
        return path.exists() or path.parent.joinpath(path.name + '.zip').exists()
    out = config.contained(packages, name)
    if taken(out):
        base = '%s-%s' % (name, time.strftime('%Y%m%d-%H%M%S'))
        out, n = config.contained(packages, base), 2
        while taken(out):
            out, n = config.contained(packages, '%s-%d' % (base, n)), n + 1
    return out


def _tidy(values, places):
    """Numbers to `places` decimals, whole numbers as ints (74.99999 -> 75, 10.0 -> 10)."""
    out = []
    for v in values or []:
        if isinstance(v, (int, float)):
            v = round(float(v), places)
            v = int(v) if v == int(v) else v
        out.append(v)
    return out


class AuthorTools:
    """Turn the session's live work into the plugin's source spec, check a plugin, scaffold, package."""

    def __init__(self, live, data=None, records=None, place=None, forms=None):
        self.live = live
        self.data = data
        self.records = records
        self.place = place
        self.forms = forms

    def _editor_id(self, form_id):
        """The EditorID of a record from the load-order index (the running game keeps few)."""
        if self.data is None:
            return None
        try:
            entries = self.data._active()[0]
            files = ','.join(str(e['fileId']) for e in entries)
            row = None
            for e in entries:
                index = value_of(self.live.link.eval('GetModIndex %s' % quoted(e['name'])))
                if index is not None and int(index) == int(form_id, 16) >> 24:
                    row = self.data.index.db.execute(
                        'select edid from records where file = ? and owner = ? and local = ? limit 1',
                        (e['fileId'], e['name'].lower(), int(form_id, 16) & 0xFFFFFF)).fetchone()
                    break
            return row[0] if row else None
        except Exception:  # noqa: BLE001 - names are best effort
            return None
        finally:
            if getattr(self.data, '_index', None) is not None:
                self.data._index.db.close()
                self.data._index = None

    def export(self, cell=None, name=None):
        # positions and angles to 3 places: GetAngle answers 74.99999 for a ref turned to 75
        placed = self.place.list(exact=True)['placed'] if self.place else []
        mine = self.live.journal.entries(process=self.live.game_identity())
        deleted = {e.get('supersedes') for e in mine if e['kind'] == 'form_delete'}     # N4
        created = [e for e in mine if e['kind'] == 'form_create' and not e['undone'] and e['id'] not in deleted]
        records, objects, problems = {}, [], []
        new_ids = {}
        for k, c in enumerate(created):
            edid = re.sub(r'[^A-Za-z0-9_]', '', (c.get('name') or 'NewForm')) + 'Live%d' % (k + 1)
            if c.get('clone'):
                from .fnv_eyes import TYPE_NAMES
                resolved = self.live.resolve(c['clone'])
                src = self._editor_id(resolved['formId']) or c['clone']
                rtype = TYPE_NAMES.get((self.live.form_ids([resolved['ptr']])[0] or {}).get('formType'), '????')
                records[edid] = {'type': rtype, 'template': src, 'fields': [{'tag': 'FULL', 'text': c['name']}]
                                 if c.get('name') else []}
                problems.append('%s: the template %s must exist as a dump in state/templates (tools/esm_research.py '
                                'dump)' % (edid, src))
            else:
                records[edid] = {'type': (c.get('type') or '').upper()}
                if c.get('name'):      # bug hunt 2026-11-18: the name given at create was lost for a typed record
                    records[edid]['fields'] = [{'tag': 'FULL', 'text': c['name']}]
            new_ids[c['formId'].upper()] = edid
        from_game = 0
        for k, p in enumerate(placed):
            base = new_ids.get((p['base'] or '').upper())
            if base:
                base_ref = '@' + base
            else:
                edid = self._editor_id(p['base'])      # once: each call reads the index again
                base_ref = '@' + edid if edid else p['base']
            pose = self._live_pose(p['ref'])      # moved since (fnv_move, fnv_world_edit): the game's pose wins
            if pose:
                from_game += 1
                p = dict(p, **pose)
            if len(p.get('position') or []) != 3 or len(p.get('rotation') or []) != 3:
                # a placement the game never confirmed and can no longer answer for: no pose to write
                problems.append('%s: no pose (the placement was not confirmed and the game does not answer); left '
                                'out' % p.get('ref'))
                continue
            scale = p.get('scale')
            objects.append({'ref_id': 'live.%d' % (k + 1), 'base': base_ref, 'position': _tidy(p['position'], 3),
                            'rotation': _tidy(p['rotation'], 3),
                            **({'scale': round(scale, 4)} if isinstance(scale, (int, float)) and abs(scale - 1) > 1e-4
                               else {})})
        cell_id = cell or 'LiveSessionCell'
        spec = {'records': records, 'cells': {cell_id: {'objects': objects}} if objects else {}}
        folder = Path(self.live.project.state_dir) / 'fnv' / 'author'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('%s.spec-part.json' % re.sub(r'[^A-Za-z0-9_-]', '_', name or 'session'))
        path.write_text(json.dumps(spec, indent=1), encoding='utf-8')
        return {'path': str(path), 'records': len(records), 'objects': len(objects), 'problems': problems,
                'posesFromGame': from_game,
                'note': 'A part to merge into the project spec: records by EditorID, the placed references as objects '
                        'of cell %s (positions read from the running game for %d of %d objects, the rest from the '
                        'journal; the cell must be the plugin\'s own interior cell or renamed to it). Runtime form ids '
                        'of vanilla bases were turned into @EditorID where the index knows them.'
                        % (cell_id, from_game, len(objects))}

    def _live_pose(self, ref):
        """The pose the running game shows for a placed ref, or None when the game does not answer (closed, ref
        gone). Live check 2026-11-18: export gave the placement pose of a barrel moved later with fnv_move."""
        try:
            ptr = self.live.resolve(ref)['ptr']
            pose = self.place._pose_exact(ptr) if self.place else None
        except Exception:  # noqa: BLE001 - best effort: the journal pose stands
            return None
        if not pose or any(not isinstance(v, (int, float)) for v in pose['position'] + pose['rotation']):
            return None
        return pose

    def check(self, plugin):
        report = {'plugin': plugin}
        if self.records is not None:
            try:
                report['assets'] = self.records.asset_check(plugin)
            except Exception as e:  # noqa: BLE001 - each part reports on its own
                report['assets'] = {'error': str(e)}
            try:
                report['conflicts'] = self.records.conflicts(plugin=plugin, limit=20)
            except Exception as e:  # noqa: BLE001
                report['conflicts'] = {'error': str(e)}
        try:
            from .fnv_warnings import for_live as warnings_for_live
            report['warnings'] = warnings_for_live(self.live).summary()
        except Exception as e:  # noqa: BLE001
            report['warnings'] = {'error': str(e)}
        missing = (report.get('assets') or {}).get('missingCount')
        report['verdict'] = ('%s referenced files are missing' % missing) if missing else 'no missing files found'
        report['note'] = ('Engine warnings come from xNVSE\'s error log (fnv_warnings): load the plugin in the game with '
                          'the warning capture on for its per-record complaints.')
        return report

    def scaffold(self, kind, name):
        if kind not in SCAFFOLDS:
            raise ValueError('kind is one of %s' % ', '.join(SCAFFOLDS))
        if not re.match(r'^[A-Za-z][A-Za-z0-9_]{1,40}$', name or ''):
            raise ValueError('name: letters, digits and _ (it becomes the EditorID prefix)')
        folder = Path(self.live.project.state_dir) / 'fnv' / 'scaffolds' / name
        folder.mkdir(parents=True, exist_ok=True)
        spec = {'schema': 1, 'project_id': name.lower(), 'plugin': name + '.esp', 'author': 'fnv-mcp scaffold',
                'masters': ['FalloutNV.esm'], 'records': {}, 'cells': {}}
        test = {'name': name + ' smoke test', 'steps': []}
        # Templates are "@EditorID" records of FalloutNV.esm, read from the master itself (tool gaps 1c: before,
        # item and quest named template dumps that did not exist, and TERM and PERK were not builder types).
        valid = {'expect': {'expr': 'IsFormValid @%s' % (name + kind.title()), 'equals': 1}}
        if kind == 'item':
            spec['records'][name + 'Item'] = {'type': 'MISC', 'template': '@Clipboard02',
                                              'fields': [{'tag': 'FULL', 'text': name}]}
            test['steps'] = [{'console': 'player.AddItem @%sItem 1' % name},
                             {'expect': {'expr': 'player.GetItemCount @%sItem' % name, 'equals': 1}}]
        elif kind == 'interior':
            spec['cells'][name + 'Cell'] = {'name': name, 'objects': [
                {'ref_id': 'floor', 'base': '@FacRmFloor01', 'position': [0, 0, 0]}]}
            test['steps'] = [{'console': 'coc %sCell' % name}, {'wait': {'loaded': True}}]
        elif kind == 'quest':
            spec['records'][name + 'Quest'] = {'type': 'QUST', 'template': '@VMS01',
                                               'fields': [{'tag': 'FULL', 'text': name}]}
            test['steps'] = [{'console': 'StartQuest %sQuest' % name},
                             {'expect': {'expr': 'GetQuestRunning %sQuest' % name, 'equals': 1}}]
        elif kind == 'terminal':
            spec['records'][name + 'Terminal'] = {'type': 'TERM', 'template': '@CommuneTerm', 'set': {
                'name': name, 'welcome_text': 'Welcome to %s.' % name, 'difficulty': 'very_easy',
                'flags': ['unlocked'],
                'menu': [{'text': 'Read the log', 'result': 'Nothing to report.'}]}}
            test['steps'] = [{'console': 'player.PlaceAtMe @%sTerminal 1' % name}, valid]
        elif kind == 'perk':
            spec['records'][name + 'Perk'] = {'type': 'PERK', 'set': {
                'name': name, 'description': 'Your weapons do 10% more damage.', 'ranks': 1, 'playable': 1,
                'min_level': 2, 'effects': [{'entry_point': 'Calculate Weapon Damage', 'function': 'multiply',
                                             'value': 1.1}]}}
            test['steps'] = [{'console': 'player.AddPerk @%sPerk' % name},
                             {'expect': {'expr': 'player.HasPerk @%sPerk' % name, 'equals': 1}}]
        else:
            spec['records'][name + 'Companion'] = {'type': 'NPC_', 'template': '@GSSunnySmiles', 'fields': [
                {'tag': 'FULL', 'text': name}, {'tag': 'SCRI', 'remove': True}]}
            test['steps'] = [{'console': 'player.PlaceAtMe @%sCompanion 1' % name}, valid]
        (folder / (name + '.spec.json')).write_text(json.dumps(spec, indent=1), encoding='utf-8')
        (folder / (name + '.test.json')).write_text(json.dumps(test, indent=1), encoding='utf-8')
        (folder / 'README.md').write_text('# %s\n\nScaffold (%s) made by fnv_author. Build with fnv_spec build, '
                                          'install with fnv_install, test with fnv_test.\n' % (name, kind),
                                          encoding='utf-8')
        return {'folder': str(folder), 'files': sorted(p.name for p in folder.iterdir()),
                'note': 'Templates are FalloutNV.esm records ("@EditorID"), read from the master file; change them '
                        'to suit. fnv_spec action=fields lists the named fields a record type takes in "set".'}

    def package(self, plugin, name=None, module=None, build=False, version=None):
        """A release folder and zip: the plugin, its loose files, the xNVSE module of fnv_plugin_build (module=), a
        manifest; build=true builds the spec (when it makes this plugin) and the module first. Refused when a file
        holds decompiler text or a private path of this machine."""
        version = version or '1.0'
        if not re.match(r'^[0-9A-Za-z][0-9A-Za-z._-]{0,19}$', str(version)):
            raise ValueError('version: up to 20 letters, digits, . _ - (such as 1.0)')
        if name is not None:
            name = plain_name(name)
        else:
            try:
                name = plain_name(Path(plugin).stem, 'the plugin name')
            except ValueError:
                raise ValueError('The plugin name %r is not a plain folder name; pass name (letters, digits, space, '
                                 '_ - .) for the package folder' % Path(plugin).stem) from None
        if self.records is None:
            raise LiveError('NO_INDEX', 'Packaging needs the data tools')
        built = {}
        plugin_file = None
        if build:
            plugin_file, built['spec'] = self._build_spec(plugin)
        dll = source = None
        if module:
            from .fnv_plugin_build import PluginTools
            from .fnv_native import c_name
            tools = PluginTools(self.live)
            if build:
                saved = tools.saved_hooks(module)
                result = tools.build(module, hooks=saved)
                built['module'] = {'dll': Path(result['dll']).name, 'hooks': saved}
            folder = tools._folder(module)
            dll, source = folder / ('%s.dll' % c_name(module)), folder / ('%s.c' % c_name(module))
            if not dll.is_file():
                raise LiveError('MODULE_NOT_BUILT', 'No built module %s. Next: fnv_plugin_build action=build name=%s '
                                '(or package with build=true).' % (dll.name, module))
        # a built plugin is what is packaged, so its assets are checked, not the installed copy's (BUG-112)
        report = self.records.asset_check(str(plugin_file) if plugin_file else plugin)
        from .fnv_data import load_order, plugin_masters
        entries = load_order(self.live.project, self.records.data._live_plugins())
        target = next((e for e in entries if e['name'].lower() == Path(plugin).name.lower()), None)
        if not target or not target.get('path'):
            raise LiveError('PLUGIN_NOT_FOUND', '%s is not in the load order' % plugin)
        out = _new_package_folder(Path(self.live.project.state_dir) / 'fnv' / 'packages', name)
        try:
            return self._fill_package(out, target, plugin_file, dll, source, version, report, built, entries,
                                      plugin_masters)
        except BaseException:
            shutil.rmtree(out, ignore_errors=True)
            raise

    def _build_spec(self, plugin):
        """(built plugin file, receipt summary) when the project's spec makes this plugin, else (None, note)."""
        project = self.live.project
        if not getattr(project, 'spec', None) or Path(project.plugin or '').name.lower() != Path(plugin).name.lower():
            return None, {'built': False, 'note': 'the project spec does not make %s: the installed plugin is '
                                                  'packaged' % Path(plugin).name}
        from .authoring import Authoring
        authoring = Authoring(project)
        receipt, _ = authoring.build(write=True)
        if not receipt.get('written'):
            raise LiveError('BUILD_FAILED', 'The spec did not build: %s' % json.dumps(receipt.get('validation'))[:1500],
                            {'validation': receipt.get('validation')})
        return authoring.built_path, {'built': True, 'records': receipt.get('records') or receipt.get('count')}

    def _fill_package(self, out, target, plugin_file, dll, source, version, report, built, entries, plugin_masters):
        (out / 'Data').mkdir(parents=True)
        plugin_src = Path(plugin_file) if plugin_file else Path(target['path'])
        shutil.copy2(plugin_src, out / 'Data' / target['name'])
        mod_root = Path(target['path']).parent
        game_data = Path(self.live.project.game_root) / 'Data'
        copied, skipped = [], []
        if mod_root.resolve() == game_data.resolve():
            skipped.append("the plugin is in the game's Data folder: no loose files are packaged from there (they "
                           "would be the game's own files); put the mod in its own MO2 mod folder")
        else:
            from .fnv_records import _plugin_assets
            index = self.records._asset_index(entries)[0]
            names = set(_plugin_assets(plugin_src))
            from .esp.assets import nif_textures, AssetError
            for rel in sorted(names):            # textures the packaged meshes name (BUG-112)
                nif = index._loose(mod_root, rel.replace('/', '\\')) if rel.lower().endswith('.nif') else None
                if nif:
                    try:
                        names.update(nif_textures(nif.read_bytes()))
                    except (OSError, AssetError):
                        pass
            for rel in sorted(names):
                src = index._loose(mod_root, rel.replace('/', '\\'))
                if not src:
                    continue
                dst = out / 'Data' / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
        if dll:
            plugins = out / 'Data' / 'NVSE' / 'Plugins'
            plugins.mkdir(parents=True)
            shutil.copy2(dll, plugins / dll.name)
        (out / 'README.txt').write_text('%s %s\n\nInstall: copy Data into the game folder or install the archive '
                                        'with a mod manager.%s\n' % (target['name'], version, (
                                            ' Needs xNVSE (the module in Data\\NVSE\\Plugins).' if dll else '')),
                                        encoding='utf-8')
        files = sorted((p.relative_to(out).as_posix(), p) for p in out.rglob('*') if p.is_file())
        checked = files + ([('(module source %s)' % source.name, source)] if source and source.is_file() else [])
        leaks = package_leaks(checked, self.live.project)
        if leaks:
            raise LiveError('PACKAGE_REFUSED', 'Not packaged: %s. A release must hold no decompiled code and no path '
                            'of this machine; fnv_code output and modules built from decompiled functions are for '
                            'local use only.' % '; '.join(leaks[:20]), {'leaks': leaks})
        manifest = {'name': out.name, 'version': version, 'plugin': target['name'],
                    'masters': plugin_masters(out / 'Data' / target['name']),
                    'module': ('Data/NVSE/Plugins/' + dll.name) if dll else None,
                    'made': time.strftime('%Y-%m-%dT%H:%M:%S'), 'by': 'fnv-mcp fnv_author package',
                    'files': [{'path': arc, 'bytes': p.stat().st_size,
                               'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for arc, p in files]}
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        archive = out.parent / (out.name + '.zip')   # with_suffix would cut a name such as my.mod
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(out.rglob('*')):
                if p.is_file():
                    z.write(p, p.relative_to(out))
        return {'folder': str(out), 'archive': str(archive), 'plugin': target['name'], 'version': version,
                'module': manifest['module'], 'built': built or None, 'looseFiles': copied, 'skipped': skipped,
                'files': len(files) + 1, 'missing': report.get('missing'), 'ok': not report.get('missingCount'),
                'note': 'Only files the plugin names, found loose in its own mod folder, are packaged (never files of '
                        'the game or other mods), with manifest.json (sizes, SHA-256, masters). The check found no '
                        'decompiler text and no path of this machine. The package stays in the bridge state folder; '
                        'an existing package is never replaced.'}

    def run(self, action, **kw):
        if action == 'export':
            return self.export(kw.get('cell'), kw.get('name'))
        if action == 'check':
            return self.check(kw['plugin'])
        if action == 'scaffold':
            return self.scaffold(kw['kind'], kw['name'])
        if action == 'package':
            return self.package(kw['plugin'], kw.get('name'), kw.get('module'), bool(kw.get('build')),
                                kw.get('version'))
        raise ValueError('action is export, check, scaffold or package')
