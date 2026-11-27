"""The action atlas (fnv-mcp NAMING-PLAN N3): which functions run for which game action, and how many times per
action, measured with the probe set (fnv_probe) and no human playing.

Each action is a short scripted thing the bridge can do on its own (open the Pip-Boy, jump, add an item, advance
the clock...). The runner measures it as dose and response: every repetition runs an idle segment (dose 0) and then
segments doing the action 1, 2 and 4 times, all of the same length. Counters are read with ReadProcessMemory before
and after each segment, so per-frame work cancels out against the idle segment and what is left scales with the
dose. A least-squares fit per function gives calls per action and how well it fits:

  per_unit      the count grows with the dose (slope >= 0.5, R^2 >= 0.9, every acted segment above every idle one)
  with_action   it runs only in acted segments but does not scale with the dose
  background    acted and idle segments look alike (not stored)

Actions driven through the console share the console's own machinery; the `console_noop` action measures it, and
functions that scale with it are marked `driver` in the other console-driven actions.

Everything goes to state/atlas.sqlite: runs, segments, sparse per-segment counts, fits and the probe caller tables.
fnvsym's earlier Frida traces (format fnvsym-trace/1) can be imported as a run of their own (phases, no doses).
More actions can be added in state/atlas_actions.json with the same step vocabulary.
"""
import json
import sqlite3
import time
from pathlib import Path

from .fnv_probe import CALLER_SLOTS, ProbeError

SCHEMA = 1

# Steps: ['con', line] console line (EditorIDs allowed) | ['tap', control] | ['hold', control, ms] | ['wait', s] |
#        ['hours', n] advance the clock | ['close_menus']
# `unit` is one dose; `segment` is the segment length in seconds (stretched to fit the largest dose).
ACTIONS = {
    'idle': {'what': 'nothing: a control for the runner itself (every function should come out as background)',
             'unit': [['wait', 1.0]], 'segment': 6},
    'console_noop': {'what': 'one harmless console line per unit: the console machinery other actions go through',
                     'unit': [['con', 'player.GetPos X'], ['wait', 0.3]], 'segment': 6, 'driver': True},
    'pipboy': {'what': 'open and close the Pip-Boy', 'unit': [['tap', 'pipboy'], ['wait', 1.5], ['tap', 'pipboy'], ['wait', 1.5]],
               'segment': 14, 'teardown': [['close_menus']]},
    'wait_menu': {'what': 'open and close the wait menu', 'unit': [['tap', 'wait'], ['wait', 1.2], ['close_menus'], ['wait', 1.0]],
                  'segment': 10, 'teardown': [['close_menus']]},
    'jump': {'what': 'jump once', 'unit': [['tap', 'jump'], ['wait', 1.6]], 'segment': 8},
    'sneak': {'what': 'crouch and stand up', 'unit': [['tap', 'sneak'], ['wait', 1.0], ['tap', 'sneak'], ['wait', 1.0]],
              'segment': 10},
    'walk': {'what': 'walk forward for one second', 'unit': [['hold', 'forward', 1000], ['wait', 1.4]], 'segment': 8},
    'pov': {'what': 'switch first/third person and back', 'unit': [['tap', 'pov'], ['wait', 1.0], ['tap', 'pov'], ['wait', 1.0]],
            'segment': 10},
    'turn': {'what': 'turn the player 90 degrees', 'unit': [['con', 'player.SetAngle Z 90'], ['wait', 0.4],
                                                            ['con', 'player.SetAngle Z 0'], ['wait', 0.4]], 'segment': 6},
    'add_item': {'what': 'add 5 caps to the inventory', 'unit': [['con', 'player.AddItem Caps001 5 1'], ['wait', 0.4]],
                 'segment': 6},
    'remove_item': {'what': 'remove 5 caps', 'setup': [['con', 'player.AddItem Caps001 200 1']],
                    'unit': [['con', 'player.RemoveItem Caps001 5 1'], ['wait', 0.4]], 'segment': 6},
    'drop_item': {'what': 'drop one cap into the world (creates a reference)', 'setup': [['con', 'player.AddItem Caps001 50 1']],
                  'unit': [['con', 'player.Drop Caps001 1'], ['wait', 0.6]], 'segment': 6},
    'place_item': {'what': 'place a cap next to the player with PlaceAtMe', 'unit': [['con', 'player.PlaceAtMe Caps001 1'], ['wait', 0.6]],
                   'segment': 6},
    'xp_reward': {'what': 'give 5 XP', 'unit': [['con', 'RewardXP 5'], ['wait', 0.5]], 'segment': 6},
    'actor_value': {'what': 'raise and lower Luck by one', 'unit': [['con', 'player.ModAV Luck 1'], ['wait', 0.3],
                                                                    ['con', 'player.ModAV Luck -1'], ['wait', 0.3]],
                    'segment': 6},
    'hours': {'what': 'advance the clock by one hour', 'unit': [['hours', 1], ['wait', 0.6]], 'segment': 6},
    'weather': {'what': 'force a weather, then release it (exterior)', 'requires': 'exterior',
                'unit': [['con', 'FW 0010E4EA'], ['wait', 1.0], ['con', 'ReleaseWeatherOverride'], ['wait', 1.0]],
                'segment': 10},
    'save': {'what': 'save the game to a scratch slot', 'unit': [['con', 'Save fnvmcp_atlas'], ['wait', 3.0]], 'segment': 14,
             'doses': [1, 2]},
    'god_mode': {'what': 'toggle god mode on and off', 'unit': [['con', 'ToggleGodMode'], ['wait', 0.3], ['con', 'ToggleGodMode'],
                                                               ['wait', 0.3]], 'segment': 6},
}


ATLAS_SCHEMA_VERSION = 1


def _connect(path):
    db = sqlite3.connect(str(path), timeout=30)
    db.execute('PRAGMA busy_timeout=30000')
    if db.execute('PRAGMA user_version').fetchone()[0] >= ATLAS_SCHEMA_VERSION:
        return db                 # the schema is there: running the script again would take the write lock (S2.5)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started REAL, ended REAL, source TEXT, game TEXT,
            profile TEXT, probes INTEGER, config TEXT, notes TEXT);
        CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY, run INTEGER, action TEXT, dose INTEGER, rep INTEGER,
            seconds REAL, frames INTEGER);
        CREATE TABLE IF NOT EXISTS counts (segment INTEGER, address INTEGER, calls INTEGER);
        CREATE INDEX IF NOT EXISTS counts_address ON counts(address);
        CREATE INDEX IF NOT EXISTS counts_segment ON counts(segment);
        CREATE TABLE IF NOT EXISTS fits (run INTEGER, action TEXT, address INTEGER, kind TEXT, per_unit REAL,
            intercept REAL, r2 REAL, idle REAL, acted REAL, driver INTEGER);
        CREATE INDEX IF NOT EXISTS fits_address ON fits(address);
        CREATE INDEX IF NOT EXISTS fits_action ON fits(action);
        CREATE TABLE IF NOT EXISTS callers (run INTEGER, address INTEGER, return_address INTEGER, calls INTEGER);
        CREATE INDEX IF NOT EXISTS callers_address ON callers(address);
    ''')
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
    db.execute('PRAGMA user_version = %d' % ATLAS_SCHEMA_VERSION)
    db.commit()
    return db


def fit(points):
    """points: [(dose, calls)]. Least squares calls = a + b*dose; returns (b, a, r2)."""
    n = len(points)
    if n < 2:
        return 0.0, float(points[0][1]) if points else 0.0, 0.0
    mx = sum(d for d, _ in points) / n
    my = sum(c for _, c in points) / n
    sxx = sum((d - mx) ** 2 for d, _ in points)
    if not sxx:
        return 0.0, my, 0.0
    b = sum((d - mx) * (c - my) for d, c in points) / sxx
    a = my - b * mx
    ss = sum((c - my) ** 2 for _, c in points)
    res = sum((c - a - b * d) ** 2 for d, c in points)
    return b, a, (1 - res / ss) if ss else 1.0


def classify(points):
    idle = [c for d, c in points if d == 0]
    acted = [c for d, c in points if d > 0]
    if not acted or not any(acted):
        return None
    b, a, r2 = fit(points)
    idle_mean = sum(idle) / len(idle) if idle else 0.0
    acted_mean = sum(acted) / len(acted)
    above = min(acted) > (max(idle) if idle else 0)
    if b >= 0.5 and r2 >= 0.9 and above:
        kind = 'per_unit'
    elif idle_mean == 0 and min(acted) > 0:
        kind = 'with_action'
    else:
        return None
    return {'kind': kind, 'per_unit': round(b, 3), 'intercept': round(a, 3), 'r2': round(r2, 4),
            'idle': round(idle_mean, 2), 'acted': round(acted_mean, 2)}


class AtlasTools:
    def __init__(self, live, probes, world=None):
        self.live = live
        self.probes = probes
        self.world = world

    @property
    def path(self):
        return Path(self.live.project.state_dir) / 'atlas.sqlite'

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return _connect(self.path)

    def actions(self):
        merged = dict(ACTIONS)
        extra = Path(self.live.project.state_dir) / 'atlas_actions.json'
        if extra.is_file():
            merged.update(json.loads(extra.read_text(encoding='utf-8')))
        return merged

    def list_actions(self):
        return {'actions': [{'name': k, 'what': v['what'], 'doses': v.get('doses', [1, 2, 4]),
                             'segmentSeconds': v.get('segment', 6), 'requires': v.get('requires')}
                            for k, v in self.actions().items()]}

    # ------------------------------------------------------------------ doing an action
    def _step(self, step):
        kind = step[0]
        if kind == 'wait':
            time.sleep(float(step[1]))
        elif kind == 'con':
            line, _ = self.live.substitute_editor_ids(step[1])
            self.live.console(line)
        elif kind == 'tap':
            self.live.input('tap', control=step[1])
        elif kind == 'hold':
            self.live.input('hold', control=step[1], duration_ms=int(step[2]))
        elif kind == 'hours':
            self.live.agent.time_control(advance_hours=float(step[1]))
        elif kind == 'close_menus':
            try:
                visible = [m for m in self.live.agent.menus().get('visible', []) if m.get('id') not in (1004,)]
                for m in visible:
                    self.live.agent.menu_control('close', menu=m['id'])
            except Exception:  # noqa: BLE001 - closing menus is best effort between segments
                pass
        else:
            raise ValueError('unknown atlas step %r' % (step,))

    def _unit_seconds(self, spec):
        return sum(float(s[1]) for s in spec['unit'] if s[0] == 'wait') + \
            sum(int(s[2]) / 1000.0 for s in spec['unit'] if s[0] == 'hold') + 0.25 * len(spec['unit'])

    # ------------------------------------------------------------------ the runner
    def run(self, actions=None, reps=2, doses=None, notes=None):
        self.live.in_game()
        library = self.actions()
        names = actions or [n for n in library if n != 'idle']
        unknown = [n for n in names if n not in library]
        if unknown:
            raise ValueError('Unknown atlas actions: %s (see fnv_atlas actions)' % ', '.join(unknown))
        info = self.probes.info()
        if not info.get('armed'):
            raise ProbeError('NO_PROBES', 'No armed probe set: install one with fnv_probe install first')
        session = self.probes.session()
        addresses = [p[0] for p in session['probes']]
        if self.world is not None:
            try:
                self.world.window('release_mouse')
            except Exception:  # noqa: BLE001 - a free mouse is a convenience, not a requirement
                pass
        unpause = self.live.game.keep_unpaused(3600)
        memory = self.probes._memory()
        counts_at = int(info['counts'], 16)
        n = info['used']
        db = self.db()
        started = time.time()
        cur = db.execute('INSERT INTO runs (started, source, game, profile, probes, config, notes) VALUES (?,?,?,?,?,?,?)',
                         (started, 'probes', 'FalloutNV.exe 1.4.0.525', self.live.project.fnv.mo2_profile, n,
                          json.dumps({'reps': reps, 'doses': doses, 'actions': names, 'probeConfig': session.get('config')}),
                          notes))
        run_id = cur.lastrowid
        db.commit()
        report = {'run': run_id, 'actions': {}}
        try:
            for action_index, name in enumerate(names, 1):
                spec = library[name]
                if spec.get('requires') == 'exterior' and not self._exterior():
                    report['actions'][name] = {'skipped': 'needs an exterior cell'}
                    continue
                action_doses = doses or spec.get('doses') or [1, 2, 4]
                seconds = max(float(spec.get('segment', 6)), self._unit_seconds(spec) * max(action_doses) + 1.0)
                for s in spec.get('setup', []):
                    self._step(s)
                segs = 0
                for rep in range(reps):
                    for dose in [0] + list(action_doses):
                        self.probes.tag(action_index * 16 + min(dose, 15))
                        before = memory.u32s(counts_at, n)
                        frame0 = (self.live.link.ping() or {}).get('frame') or 0
                        t0 = time.monotonic()
                        for _ in range(dose):
                            for s in spec['unit']:
                                self._step(s)
                        left = seconds - (time.monotonic() - t0)
                        if left > 0:
                            time.sleep(left)
                        after = memory.u32s(counts_at, n)
                        elapsed = time.monotonic() - t0
                        frames = ((self.live.link.ping() or {}).get('frame') or 0) - frame0
                        seg = db.execute('INSERT INTO segments (run, action, dose, rep, seconds, frames) VALUES (?,?,?,?,?,?)',
                                         (run_id, name, dose, rep, round(elapsed, 3), frames)).lastrowid
                        db.executemany('INSERT INTO counts VALUES (?,?,?)',
                                       [(seg, addresses[k], (after[k] - before[k]) & 0xFFFFFFFF) for k in range(n) if after[k] != before[k]])
                        db.commit()
                        segs += 1
                for s in spec.get('teardown', []):
                    self._step(s)
                fitted = self._fit_action(db, run_id, name, bool(spec.get('driver')))
                report['actions'][name] = {'segments': segs, 'segmentSeconds': round(seconds, 1), **fitted}
            self.probes.tag(0)
            self._store_callers(db, run_id, memory, info, addresses)
            self._mark_drivers(db, run_id)
        finally:
            memory.close()
            db.execute('UPDATE runs SET ended = ? WHERE id = ?', (time.time(), run_id))
            db.commit()
            db.close()
            if unpause is not None:
                try:
                    self.live.link.one('every', 'cancel', '%X' % unpause)
                except Exception:  # noqa: BLE001
                    pass
        report['seconds'] = round(time.time() - started)
        return report

    def _exterior(self):
        try:
            value = self.live.values(['player.IsInInterior'])[0]
            return value == 0
        except Exception:  # noqa: BLE001
            return False

    def _fit_action(self, db, run_id, name, driver):
        segs = db.execute('SELECT id, dose FROM segments WHERE run = ? AND action = ?', (run_id, name)).fetchall()
        seg_dose = {sid: d for sid, d in segs}
        per_seg = {}
        for sid, address, calls in db.execute('SELECT c.segment, c.address, c.calls FROM counts c JOIN segments s ON '
                                              's.id = c.segment WHERE s.run = ? AND s.action = ?', (run_id, name)):
            per_seg.setdefault(address, {})[sid] = calls
        stats = {'per_unit': 0, 'with_action': 0}
        out = []
        for address, calls in per_seg.items():
            points = [(seg_dose[sid], calls.get(sid, 0)) for sid in seg_dose]
            c = classify(points)
            if c:
                stats[c['kind']] += 1
                out.append((run_id, name, address, c['kind'], c['per_unit'], c['intercept'], c['r2'], c['idle'],
                            c['acted'], 1 if driver else 0))
        db.executemany('INSERT INTO fits VALUES (?,?,?,?,?,?,?,?,?,?)', out)
        db.commit()
        return stats

    def _mark_drivers(self, db, run_id):
        """Functions that scale with console_noop are the console's own machinery: mark them in every action."""
        drivers = {a for (a,) in db.execute("SELECT address FROM fits WHERE run = ? AND action = 'console_noop' "
                                            "AND kind = 'per_unit'", (run_id,))}
        if drivers:
            db.executemany('UPDATE fits SET driver = 1 WHERE run = ? AND address = ?', [(run_id, a) for a in drivers])
            db.commit()

    def _store_callers(self, db, run_id, memory, info, addresses):
        n = info['used']
        table = memory.u32s(int(info['callers'], 16), 2 * CALLER_SLOTS * n)
        rows = []
        for k in range(n):
            base = 2 * CALLER_SLOTS * k
            for j in range(CALLER_SLOTS):
                ret = table[base + 2 * j]
                if ret:
                    rows.append((run_id, addresses[k], ret, table[base + 2 * j + 1]))
        db.executemany('INSERT INTO callers VALUES (?,?,?,?)', rows)
        db.commit()

    # ------------------------------------------------------------------ queries
    def runs(self):
        db = self.db()
        try:
            out = []
            for rid, started, ended, source, profile, probes, config, notes in db.execute(
                    'SELECT id, started, ended, source, profile, probes, config, notes FROM runs ORDER BY id'):
                segs = db.execute('SELECT COUNT(*), COUNT(DISTINCT action) FROM segments WHERE run = ?', (rid,)).fetchone()
                out.append({'run': rid, 'source': source, 'started': time.strftime('%Y-%m-%d %H:%M', time.localtime(started)),
                            'minutes': round((ended or started) - started) // 60 if ended else None, 'profile': profile,
                            'probes': probes, 'segments': segs[0], 'actions': segs[1], 'notes': notes,
                            'config': json.loads(config or '{}')})
            return {'runs': out, 'path': str(self.path)}
        finally:
            db.close()

    def function(self, ref):
        engine = self.live.engine()
        address = engine.resolve(ref)
        db = self.db()
        try:
            fits = [{'run': r, 'action': a, 'kind': k, 'perUnit': b, 'r2': r2, 'idle': idle, 'acted': acted,
                     'consoleMachinery': bool(drv)}
                    for r, a, k, b, r2, idle, acted, drv in db.execute(
                        'SELECT run, action, kind, per_unit, r2, idle, acted, driver FROM fits WHERE address = ? '
                        'ORDER BY run, action', (address,))]
            seen = db.execute('SELECT s.run, s.action, SUM(c.calls), COUNT(*) FROM counts c JOIN segments s ON s.id = '
                              'c.segment WHERE c.address = ? GROUP BY s.run, s.action', (address,)).fetchall()
            callers = db.execute("SELECT c.return_address, SUM(c.calls) FROM callers c JOIN runs r ON r.id = c.run WHERE "
                                 "c.address = ? AND r.source = 'probes' GROUP BY c.return_address ORDER BY 2 DESC LIMIT 16",
                                 (address,)).fetchall()
            frida = db.execute("SELECT c.return_address, SUM(c.calls) FROM callers c JOIN runs r ON r.id = c.run WHERE "
                               "c.address = ? AND r.source = 'frida' GROUP BY c.return_address ORDER BY 2 DESC LIMIT 16",
                               (address,)).fetchall()
        finally:
            db.close()
        from .fnv_probe import describe_caller
        image = self.live.deep._image(required=False)
        out = dict(engine.summary(address), fits=fits,
                   seenIn=[{'run': r, 'action': a, 'calls': c, 'segments': s} for r, a, c, s in seen],
                   callers=[describe_caller(ret, c, engine, image) for ret, c in callers])
        if frida:  # Frida traces store the calling function, not the return address
            out['callerFunctionsFromFrida'] = [dict(engine.summary(a, with_location=False), calls=c)
                                               if a in engine.functions else {'address': '0x%08X' % a, 'calls': c}
                                               for a, c in frida]
        return out

    def action(self, name, kind=None, run=None, statuses=None, exclusive=False, include_drivers=False, limit=60):
        engine = self.live.engine()
        db = self.db()
        try:
            run = run or (db.execute('SELECT MAX(run) FROM segments WHERE action = ?', (name,)).fetchone() or [None])[0]
            if run is None:
                return {'action': name, 'functions': [], 'note': 'no run measured this action yet'}
            q = 'SELECT address, kind, per_unit, r2, idle, acted, driver FROM fits WHERE run = ? AND action = ?'
            args = [run, name]
            if kind:
                q += ' AND kind = ?'
                args.append(kind)
            if not include_drivers:
                q += ' AND driver = 0'
            rows = db.execute(q, args).fetchall()
            others = {}
            if exclusive:
                for (a, n) in db.execute('SELECT address, COUNT(DISTINCT action) FROM fits WHERE run = ? AND action != '
                                         "'console_noop' GROUP BY address", (run,)):
                    others[a] = n
        finally:
            db.close()
        wanted = set(statuses) if statuses else None
        out = []
        for address, k, b, r2, idle, acted, drv in rows:
            f = engine.functions.get(address)
            if not f or (wanted and f['status'] not in wanted):
                continue
            if exclusive and others.get(address, 0) > 1:
                continue
            out.append(dict(engine.summary(address, with_location=False), kind=k, perUnit=b, r2=r2, idle=idle,
                            acted=acted, driver=bool(drv)))
        out.sort(key=lambda x: (x['kind'] != 'per_unit', -x['r2'], abs(x['perUnit'] - round(x['perUnit']))))
        return {'action': name, 'run': run, 'total': len(out), 'functions': out[:limit]}

    # ------------------------------------------------------------------ Frida traces
    def import_frida(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('format') != 'fnvsym-trace/1':
            raise ValueError('%s is not an fnvsym-trace/1 file' % path)
        db = self.db()
        try:
            sessions = data.get('sessions') or []
            cur = db.execute('INSERT INTO runs (started, ended, source, game, profile, probes, config, notes) VALUES '
                             '(?,?,?,?,?,?,?,?)', (time.time(), time.time(), 'frida', data.get('game'), None,
                                                   len(data.get('functions') or {}),
                                                   json.dumps({'sessions': len(sessions), 'file': str(path)}),
                                                   'imported from fnvsym Frida traces (phases played by a human)'))
            run_id = cur.lastrowid
            phase_seconds = data.get('phase_seconds') or {}
            seg_ids = {}
            for phase, secs in phase_seconds.items():
                seg_ids[phase] = db.execute('INSERT INTO segments (run, action, dose, rep, seconds, frames) VALUES '
                                            '(?,?,?,?,?,?)', (run_id, phase, -1, 0, secs, None)).lastrowid
            counts, callers = [], []
            for addr_text, rec in (data.get('functions') or {}).items():
                a = int(addr_text, 16)
                for phase, hits in (rec.get('hits') or {}).items():
                    if phase not in seg_ids:
                        seg_ids[phase] = db.execute('INSERT INTO segments (run, action, dose, rep, seconds, frames) VALUES '
                                                    '(?,?,?,?,?,?)', (run_id, phase, -1, 0, None, None)).lastrowid
                    counts.append((seg_ids[phase], a, hits))
                for caller, n in (rec.get('callers') or {}).items():
                    callers.append((run_id, a, int(caller, 16), n))
            db.executemany('INSERT INTO counts VALUES (?,?,?)', counts)
            db.executemany('INSERT INTO callers VALUES (?,?,?,?)', callers)
            db.commit()
            return {'run': run_id, 'functions': len(data.get('functions') or {}), 'phases': sorted(seg_ids),
                    'counts': len(counts), 'callers': len(callers),
                    'note': 'Frida callers are caller functions, not return addresses'}
        finally:
            db.close()
