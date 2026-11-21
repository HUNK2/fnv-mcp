"""fnv_native (Plan 2, W5): C modules that run inside the game, built with Zig and loaded by the engine companion.

    headers  functions=[...] classes=[...]: state/fnv/sdk/ gets fnvmcp_module.h (the module API) and fnv_engine.h:
             FNV_ADDR_<name> for each function, a typed pointer FNV_<name> with its calling convention when the
             symbol database knows the signature (class and struct pointers become void *), and C structs with
             explicit padding for the classes (members the database knows; the rest is padding).
    build    source (C text, or a .c path) and name: zig cc -target x86-windows-gnu -shared -O2 into
             state/fnv/modules/<name>-<hash>.dll; compiler errors come back as they are.
    load     the module (by name: its newest build; or a .dll path): the companion LoadLibrary's it on the main thread
             and runs fnvmcp_module_init; each hook the module asked for is installed here (the stolen prologue is
             planned by the bridge, as for every hook) and its handler attached.
    unload, reload (unload, build when source is given, load), call export args, log, list.

Compiled module DLLs stay in the bridge state folder: they never enter git.
"""
import hashlib
import re
import shutil
import struct
import subprocess
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_link import hexs, word
from .safe_paths import check_includes, no_unc

SDK_SOURCE = Path(__file__).resolve().parents[1] / 'native' / 'sdk'
C_BASIC = {
    'bool': 'unsigned char', 'char': 'char', 'signed char': 'signed char', 'unsigned char': 'unsigned char',
    'short': 'short', 'unsigned short': 'unsigned short', 'int': 'int', 'unsigned int': 'unsigned int',
    'long': 'long', 'unsigned long': 'unsigned long', 'float': 'float', 'double': 'double',
    '__int64': 'long long', 'unsigned __int64': 'unsigned long long', 'wchar_t': 'unsigned short',
    'void': 'void', 'long double': 'double',
}
CONV = {'cdecl': '__attribute__((cdecl))', 'stdcall': '__attribute__((stdcall))',
        'thiscall': '__attribute__((thiscall))', 'fastcall': '__attribute__((fastcall))'}
IDENT = re.compile(r'[^A-Za-z0-9_]+')


def c_name(name):
    text = IDENT.sub('_', str(name)).strip('_')
    return ('_' + text) if text[:1].isdigit() else text


def c_type(text, enums=()):
    """A C spelling for a type the symbol database prints, or None when it cannot be passed by value in C."""
    t = (text or '').strip()
    for q in ('const ', 'volatile '):
        t = t.replace(q, '')
    t = t.strip()
    if t.endswith(('*', '&')) or '(' in t:
        return 'void *'
    if t in C_BASIC:
        return C_BASIC[t]
    if t.startswith('enum ') or t in enums:
        return 'int'
    return None


def source_path(source):
    """The .c file a build's source argument names, or None when the argument is C text. A one-line argument that
    ends in .c is a path: a network path is refused before anything touches it (H5)."""
    text = source.strip()
    if len(source) >= 400 or '\n' in text or not text.endswith('.c'):
        return None
    path = no_unc(text, 'source')
    return path if path.is_file() else None


_QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
LOG_PAGE = 200          # the companion's own page size (op_mod_log, native/engine/w5_mod.c)
CALL_LOG_LINES = 40     # log lines an action's answer carries; logNext reads the rest


def source_digest(text, path, sdk):
    """12 hex digits naming a build: the source, every file it includes in quotes (found beside the including file,
    then in the SDK folder, each once, so a cycle ends), and the SDK headers. A changed included file gives a new DLL
    name (critique fix 2026-11-21: only the main file was hashed). A missing include is left to the compiler."""
    sdk = Path(sdk)
    h = hashlib.sha256(text.encode('utf-8'))
    for name in ('fnvmcp_module.h', 'fnv_engine.h'):
        if (sdk / name).is_file():
            h.update(b'\0sdk:' + name.encode() + b'\0' + (sdk / name).read_bytes())
    seen = set()
    todo = [(text, Path(path).parent if path else None)]
    while todo:
        body, folder = todo.pop()
        for name in _QUOTED_INCLUDE.findall(body):
            for base in ([folder] if folder else []) + [sdk]:
                found = base / name
                try:
                    key = str(found.resolve()).lower()
                    if not found.is_file():
                        continue
                except OSError:
                    continue
                if key not in seen:
                    seen.add(key)
                    data = found.read_bytes()
                    h.update(b'\0inc:' + name.encode('utf-8', 'replace') + b'\0' + data)
                    todo.append((data.decode('utf-8', 'replace'), found.parent))
                break
    return h.hexdigest()[:12]


class NativeTools:
    def __init__(self, live, zig=None):
        self.live = live
        self.zig = zig
        self.loaded = {}          # module name -> {id, dll, exports, hooks}

    # ------------------------------------------------------------------ paths
    @property
    def root(self):
        return Path(self.live.project.state_dir) / 'fnv'

    @property
    def sdk(self):
        return self.root / 'sdk'

    @property
    def modules(self):
        return self.root / 'modules'

    def _zig(self):
        if self.zig:
            return str(self.zig)
        from .fnv_link import find_zig
        return str(find_zig(self.live.project))

    # ------------------------------------------------------------------ headers
    def headers(self, functions=None, classes=None):
        self.sdk.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SDK_SOURCE / 'fnvmcp_module.h', self.sdk / 'fnvmcp_module.h')
        engine = self.live.engine()
        from .fnv_symdb import load as load_symdb
        db = None
        try:
            db = load_symdb(Path(self.live.project.state_dir) / 'symdb.sqlite')
        except Exception:  # noqa: BLE001 - addresses alone without the database
            db = None
        lines = ['/* Generated by fnv_native headers: engine addresses, typed pointers and structs for FalloutNV.exe',
                 ' * 1.4.0.525. Local use; regenerate with fnv_native headers. */',
                 '#ifndef FNV_ENGINE_H', '#define FNV_ENGINE_H', '#include <stdint.h>', '']
        done, untyped, missing = [], [], []
        for name in functions or []:
            try:
                address = engine.resolve(name)
            except Exception:  # noqa: BLE001
                missing.append(name)
                continue
            ident = c_name(name)
            lines.append('#define FNV_ADDR_%s 0x%08Xu' % (ident, address))
            sig = self._signature(db, address) if db else None
            if sig:
                ret, conv, args = sig
                lines.append('typedef %s (%s *FnvFn_%s)(%s);' % (ret, CONV[conv], ident, ', '.join(args) or 'void'))
                lines.append('#define FNV_%s ((FnvFn_%s)FNV_ADDR_%s)' % (ident, ident, ident))
                done.append({'name': name, 'address': '0x%08X' % address, 'call': conv,
                             'type': '%s (%s)' % (ret, ', '.join(args))})
            else:
                untyped.append({'name': name, 'address': '0x%08X' % address})
            lines.append('')
        structs, unknown = [], []
        for cls in classes or []:
            text = self._struct(db, cls) if db else None
            if text is None:
                unknown.append(cls)
                continue
            lines += text
            structs.append(cls)
        lines += ['#endif', '']
        (self.sdk / 'fnv_engine.h').write_text('\n'.join(lines), encoding='utf-8')
        return verdict('Headers in %s: %d typed functions, %d addresses only, %d structs'
                       % (self.sdk, len(done), len(untyped), len(structs)),
                       'Include "fnvmcp_module.h" and "fnv_engine.h"; build adds this folder to the include path.',
                       sdk=str(self.sdk), functions=done, addressOnly=untyped or None, notFound=missing or None,
                       structs=structs, structsNotFound=unknown or None)

    @staticmethod
    def _signature(db, address):
        fn = db.function(address)
        if not fn or not fn.get('type_id'):
            return None
        row = db._type(fn['type_id'])
        if row is None or row['kind'] not in ('proc', 'mfunc', 'procedure'):
            return None
        attrs = db._attrs(row)
        conv = (attrs.get('call') or 'cdecl').lower().lstrip('_')
        if conv not in CONV:
            return None
        ret = c_type(db.type_text(row['target_id'])) if row['target_id'] else 'void'
        if ret is None:
            return None
        args = []
        if row['kind'] == 'mfunc' and attrs.get('this'):
            args.append('void *self')
        for i, r in enumerate(db._all('SELECT type_id2 FROM args WHERE type_id = ? ORDER BY seq', (row['id'],))):
            t = c_type(db.type_text(r['type_id2']))
            if t is None:
                return None
            if t == 'void':
                continue
            args.append('%s a%d' % (t, i))
        return ret, conv, args

    @staticmethod
    def _struct(db, name):
        layout = db.layout(name)
        if not layout or layout.get('kind') == 'enum' or not layout.get('size'):
            return None
        ident = c_name(layout['name'])
        out = ['/* %s: %d bytes (symbol database) */' % (layout['name'], layout['size']),
               'typedef struct %s {' % ident]
        at, pad = 0, 0
        for m in sorted(layout.get('members') or [], key=lambda m: m['offset']):
            if m.get('bitLen') or m['offset'] < at or not m.get('size'):
                continue
            if m['offset'] > at:
                out.append('    unsigned char _pad%d[%d];' % (pad, m['offset'] - at))
                pad += 1
            t = c_type(m['type'])
            field = c_name(m['name'] or 'field_%x' % m['offset'])
            if t and t != 'void' and (t == 'void *' or struct.calcsize(
                    {'unsigned char': 'B', 'char': 'b', 'signed char': 'b', 'short': 'h', 'unsigned short': 'H',
                     'int': 'i', 'unsigned int': 'I', 'long': 'l', 'unsigned long': 'L', 'float': 'f',
                     'double': 'd', 'long long': 'q', 'unsigned long long': 'Q'}.get(t, 'x')) == m['size']):
                out.append('    %s %s; /* +0x%X %s */' % (t, field, m['offset'], m['type']))
            else:
                out.append('    unsigned char %s[%d]; /* +0x%X %s */' % (field, m['size'], m['offset'], m['type']))
            at = m['offset'] + m['size']
        if layout['size'] > at:
            out.append('    unsigned char _pad%d[%d];' % (pad, layout['size'] - at))
        out += ['} %s;' % ident, '_Static_assert(sizeof(%s) == %d, "%s size");' % (ident, layout['size'], ident), '']
        return out

    # ------------------------------------------------------------------ build
    def build(self, source, name):
        if not source or not name:
            raise ConvError('BAD_ARGUMENTS', 'build needs source (C text or a .c path) and name.')
        name = c_name(name)
        if not name:
            raise ConvError('BAD_ARGUMENTS', 'build needs a name with letters or digits (it names the module and its '
                            'files).')
        src_dir = self.modules / 'src'
        src_dir.mkdir(parents=True, exist_ok=True)
        path = source_path(source)
        text = path.read_text(encoding='utf-8') if path else source
        check_includes(text)          # no #include of a file outside the include folders (H5)
        target = src_dir / ('%s.c' % name)
        if not path:
            target.write_text(text, encoding='utf-8')
        if not (self.sdk / 'fnvmcp_module.h').is_file():
            self.sdk.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SDK_SOURCE / 'fnvmcp_module.h', self.sdk / 'fnvmcp_module.h')
        digest = source_digest(text, path, self.sdk)
        out = self.modules / ('%s-%s.dll' % (name, digest))
        if out.is_file():
            # The same digest is the same build, and when it is loaded Windows will not let it be overwritten
            # (seen live 2026-11-21: "failed to write output ... Permission denied").
            return verdict('Built %s (unchanged: the existing build is reused)' % out.name,
                           'Load it with fnv_native load name=%s.' % name, dll=str(out), name=name, reused=True)
        cmd = [self._zig(), 'cc', '-target', 'x86-windows-gnu', '-shared', '-O2', '-std=c11', '-Wall',
               '-I', str(self.sdk), '-o', str(out), str(path or target)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode:
            text = (result.stderr or '').strip() or (result.stdout or '').strip()
            if not text:
                # The linker can stop with no message at all (seen with a .safeseh directive in inline asm).
                text = ('no message (exit code %d). The compiler or linker stopped silently: check inline asm '
                        'directives (.safeseh, sections) and run the command by hand to see more.' % result.returncode)
            raise ConvError('BUILD_FAILED', 'zig cc failed:\n%s' % text[-6000:],
                            {'stderr': result.stderr[-6000:], 'stdout': (result.stdout or '')[-2000:],
                             'exitCode': result.returncode, 'command': ' '.join(cmd)})
        return verdict('Built %s' % out.name, 'Load it with fnv_native load name=%s.' % name, dll=str(out), name=name,
                       warnings=result.stderr.strip()[-3000:] or None)

    def _sync(self):
        """The companion's loaded modules by name. Records in self.loaded that the running game does not hold (a
        game restart or crash ended them, or another server unloaded them) are dropped first: the companion is the
        truth, this map is a cache (critique fix 2026-11-21: load said ALREADY_LOADED and call used dead addresses
        after a restart)."""
        listed = {}
        for m in self.live.link.one('mod', 'list').get('modules', []) or []:
            listed[c_name(str(m['name']).rsplit('-', 1)[0])] = m
        for key, rec in list(self.loaded.items()):
            m = listed.get(key)
            same = m is not None and str(m.get('id')) == str(rec.get('id'))
            if same and rec.get('base') is not None and m.get('base') is not None:
                same = int(str(m['base']), 16) == int(str(rec['base']), 16)
            if not same:
                self.loaded.pop(key, None)
        return listed

    def _newest(self, name):
        hits = sorted(self.modules.glob('%s-*.dll' % c_name(name)), key=lambda p: p.stat().st_mtime)
        if not hits:
            raise ConvError('NOT_BUILT', 'No build of %s. Next: fnv_native build.' % name)
        return hits[-1]

    # ------------------------------------------------------------------ load / unload
    def load(self, name):
        self.live.ready()
        dll = Path(name) if str(name).lower().endswith('.dll') else self._newest(name)
        key = c_name(dll.stem.rsplit('-', 1)[0]) if '-' in dll.stem else c_name(dll.stem)
        listed = self._sync()
        if key in self.loaded:
            raise ConvError('ALREADY_LOADED', '%s is loaded (module %d). Next: reload or unload.'
                            % (key, self.loaded[key]['id']))
        listed = listed.get(key)
        if listed:   # loaded by an earlier server process (the supervisor restarts it on code changes)
            raise ConvError('ALREADY_LOADED', '%s is loaded (module %s, by an earlier server process). Next: call it, '
                            'or reload / unload it.' % (key, listed['id']))
        self.live.changing('native load')
        mark = self._log_mark()
        out = self.live.link.one('mod', 'load', hexs(str(dll.resolve())), timeout=60)
        if not out.get('ok'):
            raise ConvError('LOAD_FAILED', 'The companion could not load %s (%s%s).' % (
                dll.name, out.get('error'), ', Windows error %s' % out['winError'] if out.get('winError') else ''),
                out)
        mod = out['module']
        installed, failed = [], []
        from .fnv_deep import decode_prologue
        for req in mod.get('hooks', []):
            target = int(req['target'], 16)
            hid = None
            try:
                raw = self.live.reads([(target, 24)])[0]
                stolen, relocs = decode_prologue(raw)
                hook = self.live.link.one('hook', word(target), word(stolen),
                                          ','.join('%x' % r for r in relocs) or '-', '0', '0', '0', '0', '0', '0',
                                          '0', '0', '0', hexs('mod:%s:%d' % (key, req['request'])))
                if not hook.get('ok'):
                    raise ConvError('HOOK_REFUSED', str(hook.get('error')))
                hid = hook['hook']['id']
                attach = self.live.link.one('hooknative', word(hid), word(mod['id']), word(req['request']))
                if not attach.get('ok'):
                    self.live.link.one('unhook', word(hid))
                    hid = None
                    raise ConvError('HOOK_REFUSED', str(attach.get('error')))
                installed.append({'request': req['request'], 'target': req['target'],
                                  'function': self._fname(target), 'hook': hid})
            except Exception as e:  # noqa: BLE001 - report each request
                if hid is not None and not any(i['hook'] == hid for i in installed):
                    try:        # a hook installed but not attached (the attach call raised) goes back
                        self.live.link.one('unhook', word(hid))
                    except Exception:  # noqa: BLE001
                        pass
                failed.append({'request': req['request'], 'target': req['target'], 'error': str(e)[:200]})
        exports = {e['name']: e['address'] for e in out.get('exports', [])}
        self.loaded[key] = {'id': mod['id'], 'base': mod.get('base'), 'dll': str(dll), 'exports': exports,
                            'hooks': installed}
        self.live.journal.add({'kind': 'native_load', 'module': key, 'moduleId': mod['id'], 'undo': None})
        return verdict('Loaded %s as module %d: %d hooks installed%s' % (
            key, mod['id'], len(installed), (', %d failed' % len(failed)) if failed else ''),
            'Its init ran on the main thread; fnv_native log shows what it logged.', module=key, id=mod['id'],
            base=mod['base'], exports=exports, hooks=installed, hooksFailed=failed or None, **self._log_answer(mark))

    def _fname(self, address):
        try:
            return self.live.engine().summary(address, with_location=False).get('name')
        except Exception:  # noqa: BLE001
            return None

    def unload(self, name):
        self.live.ready()
        self._sync()
        key, mod_id = self._which(name)
        mark = self._log_mark()
        out = self.live.link.one('mod', 'unload', word(mod_id), timeout=30)
        if not out.get('ok'):
            raise ConvError('UNLOAD_FAILED', 'The companion could not unload module %d (%s).'
                            % (mod_id, out.get('error')), out)
        self.loaded.pop(key, None)
        return verdict('Unloaded %s' % key, 'Its hooks are gone (original bytes back) and its writes put back.',
                       hooksRemoved=out.get('hooksRemoved'), writesRestored=out.get('writesRestored'),
                       **self._log_answer(mark))

    def _listed(self, key):
        """The companion's record of a loaded module by name (it outlives a server restart), or None."""
        for m in self.live.link.one('mod', 'list').get('modules', []):
            if c_name(str(m['name']).rsplit('-', 1)[0]) == key:
                return m
        return None

    def _which(self, name):
        if name is None and len(self.loaded) == 1:
            key = next(iter(self.loaded))
            return key, self.loaded[key]['id']
        key = c_name(name or '')
        if key in self.loaded:
            return key, self.loaded[key]['id']
        listed = self.live.link.one('mod', 'list').get('modules', [])
        for m in listed:
            if c_name(m['name'].rsplit('-', 1)[0]) == key or str(m['id']) == str(name):
                # By id the key above is the number: name the module by its own name, so unload forgets its
                # record in self.loaded (bug hunt 2026-11-18: load then said ALREADY_LOADED after an unload by id).
                return c_name(str(m['name']).rsplit('-', 1)[0]), m['id']
        raise ConvError('NOT_LOADED', 'No loaded module %s. Next: fnv_native list.' % name)

    def reload(self, name, source=None):
        built = self.build(source, name) if source else None
        try:
            self.unload(name)
        except ConvError as e:
            if e.code != 'NOT_LOADED':
                raise
        out = self.load(name)
        out['rebuilt'] = bool(built)
        return out

    def _export_in_memory(self, base, name):
        """Address of an export of a loaded DLL, read from its PE export directory in the game's memory."""
        try:
            pe = struct.unpack('<I', self.live.reads([(base + 0x3C, 4)])[0])[0]
            rva, size = struct.unpack('<II', self.live.reads([(base + pe + 0x78, 8)])[0])
            raw = self.live.reads([(base + rva, 40)])[0]
            count, names = struct.unpack_from('<I', raw, 24)[0], struct.unpack_from('<I', raw, 32)[0]  # NumberOfNames
            funcs, ords = struct.unpack_from('<I', raw, 28)[0], struct.unpack_from('<I', raw, 36)[0]
            name_rvas = struct.unpack('<%dI' % count, self.live.reads([(base + names, 4 * count)])[0])
            texts = self.live.reads([(base + r, 64) for r in name_rvas])
            for i, t in enumerate(texts):
                if t and t.split(b'\0', 1)[0].decode('ascii', 'replace') == name:
                    ordinal = struct.unpack('<H', self.live.reads([(base + ords + 2 * i, 2)])[0])[0]
                    func = struct.unpack('<I', self.live.reads([(base + funcs + 4 * ordinal, 4)])[0])[0]
                    return base + func
        except (struct.error, TypeError, IndexError):
            return None
        return None

    def flow_slot(self):
        """Address of the flow-call module's slot (fnv_flow call), building and loading the module when needed."""
        key = 'fnvmcp_flowcall'
        self._sync()
        if key not in self.loaded:
            listed = self.live.link.one('mod', 'list').get('modules', [])
            for m in listed:      # loaded by an earlier bridge: find the slot in its export table in memory
                if c_name(m['name'].rsplit('-', 1)[0]) == key:
                    base = int(str(m['base']), 16)
                    address = self._export_in_memory(base, 'fnvmcp_flow_slot')
                    version = self._export_in_memory(base, 'fnvmcp_flow_version')
                    if address and version:
                        self.loaded[key] = {'id': m['id'], 'dll': m['name'], 'hooks': [],
                                            'exports': {'fnvmcp_flow_slot': '0x%08X' % address,
                                                        'fnvmcp_flow_version': '0x%08X' % version}}
                        break
                    # an older build (its slot has no edx word): unload it, the current one is loaded below
                    self.live.link.one('mod', 'unload', word(int(m['id'])))
            if key not in self.loaded:
                source = Path(__file__).resolve().parent.parent / 'native' / 'modules' / 'fnvmcp_flowcall.c'
                try:
                    stale = self._newest(key).stat().st_mtime < source.stat().st_mtime
                except ConvError:
                    stale = True
                if stale:  # never load a build older than the module's source
                    self.build(str(source), key)
                self.load(key)
        exports = self.loaded.get(key, {}).get('exports', {})
        if 'fnvmcp_flow_slot' not in exports:
            raise ConvError('NO_EXPORT', 'The flow-call module has no fnvmcp_flow_slot export; unload it '
                            '(fnv_native unload name=fnvmcp_flowcall) and try again.')
        return int(exports['fnvmcp_flow_slot'], 16)

    # ------------------------------------------------------------------ call / log / list
    def call(self, name, export, args=None):
        self.live.ready()
        self._sync()
        key, _ = self._which(name)
        exports = self.loaded.get(key, {}).get('exports', {})
        if export not in exports and key not in self.loaded:
            # Loaded by an earlier server process: read the export from the DLL's export table in the game's memory.
            listed = self._listed(key)
            address = self._export_in_memory(int(str(listed['base']), 16), export) if listed else None
            if address:
                exports = {export: '0x%08X' % address}
        if export not in exports:
            raise ConvError('NO_EXPORT', '%s has no export %r (exports: %s).' % (key, export, ', '.join(exports)
                                                                                 or 'none known'))
        words = []
        for a in args or []:
            if isinstance(a, float):
                words.append(word(struct.unpack('<I', struct.pack('<f', a))[0]))
            else:
                words.append(word(int(a)))
        mark = self._log_mark()
        out = self.live.link.one('call', word(int(exports[export], 16)), 'ig', '0', '0', *words, timeout=60)
        if out.get('fault'):
            fault = out['fault']
            raise ConvError('CALL_FAULTED', '%s.%s faulted at %s (%s %s); the guard caught it and the game kept '
                            'running, but state the function had half changed may remain (stateSuspect).'
                            % (key, export, fault.get('address'), fault.get('access'), fault.get('code')), out)
        if not out.get('ok', True) or 'eax' not in out:
            raise ConvError('CALL_FAILED', 'The call failed (%s).' % (out.get('error') or out), out)
        eax = int(out['eax'], 16)
        return verdict('%s.%s returned %d' % (key, export, eax - (1 << 32) if eax >= 1 << 31 else eax),
                       'Run on the main thread, guarded (a fault comes back as an error).', eax=out['eax'],
                       value=eax - (1 << 32) if eax >= 1 << 31 else eax, **self._log_answer(mark))

    def _log_mark(self):
        """The sequence number of the newest module log line now (lines after it are new)."""
        # Ask for lines after 0x7FFFFFFE: none exist, and the reply's "last" is the newest. Not 0x7FFFFFFF: the
        # companion computes since + 1 as a signed 32-bit number.
        out = self.live.link.one('mod', 'log', word(0x7FFFFFFE))
        return int(out.get('last') or 0)

    def _log_since(self, since, limit):
        """(lines, next, dropped): up to limit lines after since, oldest first, paged LOG_PAGE at a time. dropped
        counts lines after since that the ring (1,024 lines) no longer holds."""
        lines, dropped, last = [], 0, None
        cursor = since
        while len(lines) < limit:
            out = self.live.link.one('mod', 'log', word(cursor), str(min(LOG_PAGE, limit - len(lines))))
            page = out.get('lines') or []
            last = out.get('last', last)
            if not page:
                break
            if not lines and page[0]['seq'] > since + 1:
                dropped = page[0]['seq'] - since - 1
            lines.extend({'seq': line['seq'], 'module': line['module'], 'frame': line['frame'], 'text': line['text']}
                         for line in page)
            cursor = page[-1]['seq']
            if last is not None and cursor >= int(last):
                break
        return lines, (lines[-1]['seq'] if lines else max(since, int(last or 0))), dropped

    def _log_answer(self, mark):
        """The log part of an action's answer: only the lines written since mark (taken just before the action)."""
        lines, nxt, dropped = self._log_since(mark, CALL_LOG_LINES)
        out = {'log': lines, 'logNext': nxt}
        try:
            newest = self._log_mark()
        except Exception:  # noqa: BLE001 - only the cut count depends on it
            newest = nxt
        if newest > nxt:
            out['logCut'] = newest - nxt
        if dropped:
            out['logDropped'] = dropped
        return out

    def log(self, since=0):
        """Module log lines after since (at most 500, oldest first); since=0 gives the newest LOG_PAGE lines."""
        self.live.ready()
        if not since:
            since = max(0, self._log_mark() - LOG_PAGE)
        lines, nxt, dropped = self._log_since(since, 500)
        return verdict('%d log lines' % len(lines),
                       'More after %d: fnv_native log since=%d.' % (nxt, nxt) if len(lines) == 500 else None,
                       lines=lines, next=nxt, dropped=dropped or None)

    def list(self):
        self.live.ready()
        mods = list(self._sync().values())
        builds = {}
        for p in (self.modules.glob('*.dll') if self.modules.exists() else []):
            builds.setdefault(c_name(p.stem.rsplit('-', 1)[0]), []).append(p)
        built = []
        for name, paths in sorted(builds.items()):
            newest = max(paths, key=lambda p: p.stat().st_mtime)
            built.append({'name': name, 'newest': newest.name, 'builds': len(paths)})
        return verdict('%d modules loaded, %d module names built' % (len(mods), len(built)), None, modules=mods,
                       built=built)

    def run(self, action, **a):
        if action == 'headers':
            return self.headers(a.get('functions'), a.get('classes'))
        if action == 'build':
            return self.build(a.get('source'), a.get('name'))
        if action == 'load':
            return self.load(a.get('name'))
        if action == 'unload':
            return self.unload(a.get('name'))
        if action == 'reload':
            return self.reload(a.get('name'), a.get('source'))
        if action == 'call':
            return self.call(a.get('name'), a.get('export'), a.get('args'))
        if action == 'log':
            return self.log(a.get('since') or 0)
        if action == 'list':
            return self.list()
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: headers, build, load, unload, reload, call, log, '
                        'list.' % action)

