"""fnv_coverage (Plan 2, W6): which decomp source lines ran.

start files=[...] or functions=[...] puts a one-shot int3 at every line start of those functions (verified functions
only: their line tables match the game's bytes); each site restores its byte on its first hit, so the steady cost is
zero. stop reads what ran, puts the rest back and saves the run; diff compares two saved runs.
"""
import json
import struct
import time
from collections import defaultdict
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_link import word

CHUNK = 3000


class CoverageTools:
    def __init__(self, live):
        self.live = live
        self.active = None

    @property
    def folder(self):
        return Path(self.live.project.state_dir) / 'fnv' / 'coverage'

    def _db(self):
        db = getattr(self.live.engine(), 'symdb', None)
        if db is None:
            raise ConvError('NO_SYMDB', 'Coverage needs the symbol database (fnv_symbols build).')
        return db

    def _sites(self, files=None, functions=None):
        db = self._db()
        rows = []
        for f in files or []:
            rows += db._all('SELECT l.func_addr + l.offset AS a, l.line, p.path, l.func_addr FROM lines l '
                            'JOIN files p ON p.id = l.file_id JOIN functions fn ON fn.addr = l.func_addr '
                            'WHERE fn.verified = 1 AND lower(p.path) LIKE ?',
                            ('%' + str(f).replace('\\', '/').lower() + '%',))
        engine = self.live.engine()
        for name in functions or []:
            address = engine.resolve(name)
            rows += db._all('SELECT l.func_addr + l.offset AS a, l.line, p.path, l.func_addr FROM lines l '
                            'JOIN files p ON p.id = l.file_id WHERE l.func_addr = ?', (address,))
        sites = {}
        for r in rows:
            sites.setdefault(int(r['a']), (r['path'], int(r['line']), int(r['func_addr'])))
        return sites

    def start(self, files=None, functions=None, name=None):
        self.live.ready()
        if self.active:
            raise ConvError('RUNNING', 'Coverage %s is running. Next: fnv_coverage stop.' % self.active['name'])
        if not files and not functions:
            raise ConvError('BAD_ARGUMENTS', 'start needs files=[decomp paths] or functions=[names].')
        sites = self._sites(files, functions)
        if not sites:
            raise ConvError('NO_LINES', 'No verified line starts for that selection.')
        self.live.link.one('cov', 'reset')
        addresses = sorted(sites)
        added = skipped = 0
        for i in range(0, len(addresses), CHUNK):
            packed = b''.join(struct.pack('<I', a) for a in addresses[i:i + CHUNK]).hex()
            out = self.live.link.one('cov', 'add', packed)
            if not out.get('ok'):
                raise ConvError('COV_REFUSED', 'The companion refused the sites (%s).' % out.get('error'), out)
            added += out['added']
            skipped += out['skipped']
        self.live.changing('coverage')
        armed = self.live.link.one('cov', 'arm')
        self.active = {'name': name or 'coverage-%s' % time.strftime('%Y%m%d-%H%M%S'), 'sites': sites,
                       'started': time.time()}
        return verdict('Coverage %s armed at %d line starts' % (self.active['name'], armed.get('armed', added)),
                       'Run the scenario, then fnv_coverage stop. Each site costs one trap, once.', armed=added,
                       skipped=skipped, functions=len({v[2] for v in sites.values()}))

    def _hits(self):
        hits, since = set(), 0
        while True:
            out = self.live.link.one('cov', 'read', word(since))
            text = out.get('addresses') or ''
            hits |= {struct.unpack('<I', bytes.fromhex(text[i:i + 8]))[0] for i in range(0, len(text), 8)}
            nxt = int(out.get('next', since))
            if nxt == since or nxt >= int(out.get('hits', 0)):
                return hits
            since = nxt

    def stop(self):
        self.live.ready()
        if not self.active:
            raise ConvError('NOT_RUNNING', 'No coverage run. Next: fnv_coverage start.')
        hits = self._hits()
        self.live.link.one('cov', 'disarm')
        self.live.link.one('cov', 'reset')
        run, self.active = self.active, None
        engine = self.live.engine()
        per_file = defaultdict(lambda: {'run': set(), 'all': set()})
        per_fn = defaultdict(lambda: {'run': 0, 'all': 0})
        for address, (path, line, fn) in run['sites'].items():
            per_file[path]['all'].add(line)
            per_fn[fn]['all'] += 1
            if address in hits:
                per_file[path]['run'].add(line)
                per_fn[fn]['run'] += 1
        files = [{'file': p, 'lines': len(v['all']), 'run': len(v['run']),
                  'runLines': sorted(v['run'])[:400], 'neverRun': sorted(v['all'] - v['run'])[:400]}
                 for p, v in sorted(per_file.items())]
        fns = []
        for fn, v in sorted(per_fn.items(), key=lambda kv: -kv[1]['run']):
            if v['run']:
                try:
                    nm = engine.summary(fn, with_location=False).get('name')
                except Exception:  # noqa: BLE001 - names are extra
                    nm = None
                fns.append({'function': nm or '0x%08X' % fn, 'linesRun': v['run'], 'lines': v['all']})
        ran_lines = sorted('%s:%d' % (p, l) for p, v in per_file.items() for l in v['run'])
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / ('%s.json' % run['name'])
        path.write_text(json.dumps({'name': run['name'], 'files': files, 'functions': fns, 'run': ran_lines},
                                   indent=1), encoding='utf-8')
        total = sum(f['lines'] for f in files)
        ran = sum(f['run'] for f in files)
        return verdict('%d of %d lines ran (%d functions entered)' % (ran, total, len(fns)),
                       'Saved as %s; fnv_coverage diff compares two runs.' % path.name, path=str(path), files=files,
                       functionsRun=fns[:60])

    def diff(self, a, b):
        def load(ref):
            p = Path(ref)
            if not p.is_file():
                p = self.folder / (ref if str(ref).endswith('.json') else '%s.json' % ref)
            if not p.is_file():
                raise ConvError('NOT_FOUND', 'No saved coverage %s.' % ref)
            return set(json.loads(p.read_text(encoding='utf-8'))['run'])
        ra, rb = load(a), load(b)
        only_a, only_b = sorted(ra - rb), sorted(rb - ra)
        return verdict('%d lines ran only in %s, %d only in %s' % (len(only_a), a, len(only_b), b),
                       'Lines as file:line.', onlyInA=only_a[:300], onlyInB=only_b[:300])

    def run(self, action, **a):
        if action == 'start':
            return self.start(a.get('files'), a.get('functions'), a.get('name'))
        if action == 'stop':
            return self.stop()
        if action == 'diff':
            if not a.get('a') or not a.get('b'):
                raise ConvError('BAD_ARGUMENTS', 'diff needs a and b (saved run names).')
            return self.diff(a['a'], a['b'])
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: start, stop, diff.' % action)
