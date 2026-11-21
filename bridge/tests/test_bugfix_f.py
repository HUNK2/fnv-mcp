"""Regression tests for BUG-LOG F1-F19 and S2 (agents, scenarios, crawl, bisect, authoring). Offline: fakes only."""
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_agent, fnv_experiment, fnv_scenario  # noqa: E402
from fnv_mcp.esp import codec, records  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_coverage import CoverageTools  # noqa: E402
from fnv_mcp.fnv_crawl import CrawlTools  # noqa: E402
from fnv_mcp.fnv_link import LinkError  # noqa: E402
from fnv_mcp.fnv_suite import BisectTools  # noqa: E402


def make_plugin(path, ids):
    statics = [records.stat(i, 'Stat%08X' % i, {'model': 'clutter\\box.nif'}) for i in ids]
    plugin = codec.Plugin(records.tes4(['FalloutNV.esm'], len(ids), 0x803), [codec.Group(b'STAT', 0, children=statics)])
    data = codec.write(plugin)
    Path(path).write_bytes(data)
    return data


def stat_ids(path):
    return {'%08X' % r.form_id for r, _ in codec.parse(Path(path).read_bytes()).records() if r.type == 'STAT'}


class SuiteFixture:
    """A bisect tool over real plugin files in a temp dir, with a runner that fails while `culprit` is in the file."""

    def __init__(self, test, files, culprit, culprit_plugin):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = {}
        self.originals = {}
        for name, ids in files.items():
            path = self.root / name
            self.originals[name] = make_plugin(path, ids)
            self.paths[name] = path
        self.running = [True]
        self.quits = []
        self.reports = []
        game = SimpleNamespace(process=lambda: (1,) if self.running[0] else None, profile_dir=self.root)
        self.live = SimpleNamespace(game=game, project=SimpleNamespace(state_dir=self.root / 'state'))
        self.culprit = culprit
        self.culprit_plugin = culprit_plugin
        fixture = self

        class Runner:
            def run(self, data):
                fixture.running[0] = False
                present = fixture.culprit in stat_ids(fixture.paths[fixture.culprit_plugin])
                report = {'passed': not present, 'summary': 'x', 'failures': [] if not present else [{'phase': 'steps'}]}
                fixture.reports.append(report)
                return report
        self.tools = BisectTools(self.live, None, lambda: Runner(), quit_game=self._quit)
        self.tools._plugin_file = lambda: self.paths[self.tools.plugin]

    def _quit(self):
        self.quits.append(1)
        self.running[0] = False


class PluginsTxtTests(unittest.TestCase):                     # F1
    def candidates(self, text):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'plugins.txt').write_text(text, encoding='utf-8')
            tools = BisectTools(SimpleNamespace(game=SimpleNamespace(profile_dir=Path(d))), None, None)
            return tools._candidates('plugins', None)

    def test_mo2_style_without_stars(self):
        out = self.candidates('# header\nFalloutNV.esm\nHelloWasteland.esp\nOther.esp\n')
        self.assertEqual(out, ['HelloWasteland.esp', 'Other.esp'])

    def test_star_style_keeps_only_starred(self):
        out = self.candidates('*FalloutNV.esm\n*A.esp\nB.esp\n')
        self.assertEqual(out, ['A.esp'])


class RecordsBisectTests(unittest.TestCase):                  # F2 F3 F15
    def test_finds_the_record_restores_the_file_and_backs_up_first(self):
        ids = [0x01000800 + i for i in range(4)]
        fx = SuiteFixture(self, {'A.esp': ids}, '01000802', 'A.esp')
        out = fx.tools.bisect('records', [{'wait': 1}], plugin='A.esp')
        self.assertEqual(out['culprit'], '01000802')
        self.assertEqual(fx.paths['A.esp'].read_bytes(), fx.originals['A.esp'])
        self.assertNotIn('restoreFailed', out)
        backup = Path(out['originalBackup']['path'])
        self.assertEqual(backup.read_bytes(), fx.originals['A.esp'])
        self.assertEqual(out['originalBackup']['sha256'], hashlib.sha256(fx.originals['A.esp']).hexdigest())
        self.assertIsNone(fx.tools._original)

    def test_a_failed_call_does_not_leak_its_bytes_into_the_next_plugin(self):
        fx = SuiteFixture(self, {'A.esp': [0x01000800, 0x01000801], 'B.esp': [0x02000800, 0x02000801, 0x02000802]},
                          '02000801', 'B.esp')
        with self.assertRaises(ConvError):                                         # every record kept: NO_CANDIDATES
            fx.tools.bisect('records', [{'wait': 1}], plugin='A.esp', keep=['01000800', '01000801'])
        self.assertIsNone(fx.tools._original)
        out = fx.tools.bisect('records', [{'wait': 1}], plugin='B.esp')
        self.assertEqual(out['culprit'], '02000801')
        self.assertEqual(stat_ids(fx.paths['A.esp']), {'01000800', '01000801'})
        self.assertEqual(fx.paths['A.esp'].read_bytes(), fx.originals['A.esp'])
        self.assertEqual(fx.paths['B.esp'].read_bytes(), fx.originals['B.esp'])

    def test_arguments_are_checked_before_the_game_is_closed(self):          # F15
        fx = SuiteFixture(self, {'A.esp': [0x01000800, 0x01000801]}, '01000800', 'A.esp')
        with self.assertRaises(ConvError):
            fx.tools.bisect('records', [], plugin='A.esp')
        with self.assertRaises(ConvError):
            fx.tools.bisect('records', [{'wait': 1}], plugin=None)
        fx.tools._plugin_file = lambda: (_ for _ in ()).throw(ConvError('PLUGIN_NOT_FOUND', 'nope'))
        with self.assertRaises(ConvError):
            fx.tools.bisect('records', [{'wait': 1}], plugin='Zzz.esp')
        self.assertEqual(fx.quits, [])

    def test_a_restore_failure_is_reported(self):
        fx = SuiteFixture(self, {'A.esp': [0x01000800, 0x01000801]}, '01000800', 'A.esp')
        real = fx.tools._restore_plugin

        def broken():
            raise OSError('disk full')
        fx.tools._restore_plugin = broken
        out = fx.tools.bisect('records', [{'wait': 1}], plugin='A.esp')
        self.assertTrue(out['restoreFailed'])
        self.assertIn('RESTORE FAILED', out['verdict'])
        fx.tools._restore_plugin = real
        real()                                              # the test's own cleanup of the file
        self.assertEqual(fx.paths['A.esp'].read_bytes(), fx.originals['A.esp'])


class BisectVerdictTests(unittest.TestCase):                  # F5
    def tools(self, rule):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        profile = Path(tmp.name)
        (profile / 'modlist.txt').write_text('# h\n' + '\n'.join('+M%d' % i for i in range(4)) + '\n')
        state = {'M%d' % i: True for i in range(4)}

        class Records:
            def profile_set(self, mods, plugins, game, dry):
                state.update(mods or plugins)

        class Runner:
            def run(self, data):
                return rule(dict(state))
        game = SimpleNamespace(profile_dir=profile, process=lambda: None, quit=lambda: None)
        return BisectTools(SimpleNamespace(game=game), Records(), lambda: Runner())

    def test_launch_failures_are_inconclusive(self):
        def rule(state):
            if not state['M0']:
                return {'passed': False, 'failures': [{'phase': 'prepare', 'reason': 'GAME_EXITED'}]}
            return {'passed': not state['M2'], 'failures': [{'phase': 'steps'}]}
        out = self.tools(rule).bisect('mods', [{'wait': 1}])
        self.assertIsNone(out.get('culprit'))
        self.assertIn('Inconclusive', out['verdict'])

    def test_an_interaction_is_not_named_as_one_culprit(self):
        def rule(state):
            fails = state['M1'] and state['M3']                    # needs both
            return {'passed': not fails, 'failures': [] if not fails else [{'phase': 'steps'}]}
        out = self.tools(rule).bisect('mods', [{'wait': 1}])
        self.assertIsNone(out.get('culprit'))

    def test_the_culprit_is_confirmed_alone(self):
        out = self.tools(lambda s: {'passed': not s['M2'], 'failures': [{'phase': 'steps'}]}).bisect(
            'mods', [{'wait': 1}])
        self.assertEqual(out['culprit'], 'M2')
        self.assertTrue(out['confirmed'])


class CrawlTests(unittest.TestCase):                          # F4 F11 F13 F16
    def test_exterior_cell_is_visited_with_the_worldspace_edid(self):
        console = []
        live = SimpleNamespace(console=lambda line: console.append(line) or {'accepted': True})
        crawl = CrawlTools(live, data=SimpleNamespace())
        crawl._edid = lambda form: {'0100ABCD': 'MyWorld'}.get(form)
        crawl._wait_loaded = lambda: None
        crawl._go({'interior': False, 'editorId': None, 'grid': (3, -4), 'world': 0x0100ABCD, 'formId': 5})
        self.assertEqual(console, ['cow MyWorld 3 -4'])
        with self.assertRaises(ConvError):
            crawl._go({'interior': False, 'editorId': None, 'grid': (3, -4), 'world': 0x0100FFFF, 'formId': 5})

    def test_file_form_ids_map_through_the_master_list(self):
        entries = [{'name': 'FalloutNV.esm', 'index': 0, 'active': True},
                   {'name': 'DeadMoney.esm', 'index': 1, 'active': True},
                   {'name': 'Mod.esp', 'index': 5, 'active': True, 'masters': ['FalloutNV.esm', 'DeadMoney.esm']}]
        target = entries[2]
        f = CrawlTools._load_order_id
        self.assertEqual(f(target, entries, 0x00123456), 0x00123456)      # override of a FalloutNV.esm item
        self.assertEqual(f(target, entries, 0x01000123), 0x01000123)
        self.assertEqual(f(target, entries, 0x02000800), 0x05000800)      # its own record
        entries[1]['active'] = False
        self.assertIsNone(f(target, entries, 0x01000123))

    def test_cells_stops_what_it_started_even_when_a_cell_raises(self):
        stopped = []
        capture = SimpleNamespace(start=lambda: {'started': True}, stop=lambda: stopped.append('warn'))
        assets = SimpleNamespace(file_log=lambda mode, **k: stopped.append('file:' + mode))
        live = SimpleNamespace(in_game=lambda: None)
        crawl = CrawlTools(live, assets=assets, warnings=lambda l: SimpleNamespace(capture=capture))
        crawl._plugin_cells = lambda p: ('P.esp', [{'formId': 1, 'editorId': 'X', 'interior': True, 'grid': None,
                                                    'world': None}])

        def boom(*a, **k):
            raise RuntimeError('x')
        crawl._cells_rows = boom
        with self.assertRaises(RuntimeError):
            crawl.cells('P.esp')
        self.assertIn('warn', stopped)
        self.assertIn('file:stop', stopped)

    def test_items_restore_equipment_and_remove_only_added_copies(self):
        log = []
        counts = {'0100AA01': 2}

        def console(line, ref=None):
            log.append(line)
            if 'AddItem' in line:
                counts['0100AA01'] += 1
            return {'accepted': True}
        live = SimpleNamespace(
            console=console, changing=lambda r: None, events=lambda s: {'seq': 0, 'events': []},
            values=lambda exprs, ref=None: [counts['0100AA01']],
            inventory=lambda ref: {'items': [{'formId': '0000AAAA', 'equipped': True}, {'formId': '0000BBBB'}]})
        crawl = CrawlTools(live)
        crawl._settle = lambda n: None
        rows = []
        crawl._items_rows(rows, [('WEAP', 0x0100AA01, 'W', None)], 5, False, {'name': 'P.esp'})
        self.assertIn('player.RemoveItem 0100AA01 1 1', log)           # 3 after the test, 2 before: remove 1
        self.assertIn('player.EquipItem 0000AAAA 0 1', log)            # what the player wore is put back
        self.assertNotIn('player.EquipItem 0000BBBB 0 1', log)


class YamlTests(unittest.TestCase):                           # F12
    def test_apostrophes_in_plain_text_do_not_open_a_quote(self):
        data = fnv_scenario.parse_yaml("steps:\n  - run: MessageBox Mitchell's  # note\n")
        self.assertEqual(data['steps'][0]['run'], "MessageBox Mitchell's")
        data = fnv_scenario.parse_yaml("steps:\n  - {message_box: Doc Mitchell's house}  # x\n")
        self.assertEqual(data['steps'][0]['message_box'], "Doc Mitchell's house")

    def test_quoted_hash_survives_and_exponent_text_stays_text(self):
        data = fnv_scenario.parse_yaml("a: 'x # y'  # c\nb: 1E5\nc: 1.5e3\n")
        self.assertEqual(data, {'a': 'x # y', 'b': '1E5', 'c': 1500.0})

    def test_bad_double_quoted_path_is_a_scenario_error(self):
        with self.assertRaises(fnv_scenario.ScenarioError):
            fnv_scenario.parse_yaml('a: "C:\\Mods\\x"\n')


class ScenarioTests(unittest.TestCase):                       # F8 F19
    def test_an_unexpected_error_still_cleans_up(self):
        stops = []
        game = SimpleNamespace(stop_task=lambda t: stops.append(t), process=lambda: None)
        runner = fnv_scenario.ScenarioRunner(SimpleNamespace(game=game, project=None))
        settled = []

        def prepare(scenario):
            raise ValueError('boom')
        runner.prepare = prepare
        runner._settle = lambda: settled.append(1)
        runner.save_report = lambda report: None
        runner.screenshot = lambda name: None
        report = runner._run_once({'name': 't', '_source': 'x', 'steps': [], 'deterministic': True}, True)
        self.assertFalse(report['passed'])
        self.assertEqual(report['failures'][0]['phase'], 'prepare')
        self.assertEqual(settled, [1])
        self.assertEqual(len(stops), 1)

    def test_reports_in_the_same_second_get_different_files(self):
        with tempfile.TemporaryDirectory() as d:
            runner = fnv_scenario.ScenarioRunner(SimpleNamespace(game=None, project=SimpleNamespace(state_dir=d)))
            paths = set()
            for _ in range(5):
                report = {'name': 'same'}
                runner.save_report(report)
                paths.add(report['report'])
            self.assertEqual(len(paths), 5)


class CoverageNameTests(unittest.TestCase):                   # F17
    def test_run_names_with_path_parts_are_refused(self):
        tools = CoverageTools(SimpleNamespace(ready=lambda: self.fail('must refuse first')))
        for bad in ('..\\..\\x', '../x', 'a/b', 'a\\b', '..', 'C:x'):
            with self.assertRaises(ConvError):
                tools.start(functions=['f'], name=bad)


class FakeSpawnLive:
    def __init__(self, honours_distance, fail_on=None):
        self.honours = honours_distance
        self.fail_on = fail_on
        self.journal = SimpleNamespace(entries=[], add=self._add, confirm=lambda i, after=None: None)
        self.pos = {1: (100.0, 200.0, 10.0)}
        self.heading = 90.0
        self.n = 0
        self.lines = []
        self.link = SimpleNamespace(eval=self._eval)

    def _add(self, entry):
        entry = dict(entry, id='j%d' % len(self.journal.entries))
        self.journal.entries.append(entry)
        return entry

    REFERENCE_TYPES = range(58, 66)

    def in_game(self):
        pass

    def changing(self, reason):
        pass

    def form_ids(self, pointers):          # the spawned form (ptr 99) is a base form, not a reference
        return [{'formId': '00ABCDEF', 'formType': 31} if p == 99 else {'formId': '00000014', 'formType': 59}
                for p in pointers]

    def resolve(self, ref):
        return {'ptr': 1, 'formId': '00000014'} if ref in ('player',) else {'ptr': 99, 'formId': '00ABCDEF'}

    def _eval(self, expr, ptr):
        self.n += 1
        if self.n == self.fail_on:
            return {'ok': False}
        ptr_new = 100 + self.n
        x, y, z = self.pos[1]
        if self.honours:
            x += 300.0                       # heading 90: east
        self.pos[ptr_new] = (x, y, z)
        return {'ok': True, 'result': {'type': 'form', 'ptr': '0x%X' % ptr_new, 'formId': '00FF%04X' % self.n}}

    def values(self, exprs, ptr):
        x, y, z = self.pos[ptr]
        ax, ay, az = self.pos[1]
        dist = ((x - ax) ** 2 + (y - ay) ** 2 + (z - az) ** 2) ** 0.5
        table = {'GetPos X': x, 'GetPos Y': y, 'GetPos Z': z, 'GetAngle Z': self.heading, 'GetName': 'Barrel'}
        return [dist if e.startswith('GetDistance') else table[e] for e in exprs]

    def console(self, line, ptr):
        self.lines.append(line)
        axis, value = line.split()[1:3]
        x, y, z = self.pos[ptr]
        self.pos[ptr] = tuple(float(value) if a == axis else v for a, v in zip('XYZ', (x, y, z)))
        return {'accepted': True}


class SpawnTests(unittest.TestCase):                          # S2 F10
    def make(self, live):
        return fnv_agent.AgentTools(live, None)

    def test_refs_land_at_the_requested_distance_when_the_engine_ignores_it(self):
        live = FakeSpawnLive(honours_distance=False)
        out = self.make(live).spawn('Barrel03', count=2, distance=300, direction=0)
        self.assertEqual(len(out['refs']), 2)
        for ref in out['refs']:
            self.assertAlmostEqual(ref['distanceToAnchor'], 300.0, places=1)
            self.assertTrue(ref['placedAtDistance'])
            self.assertTrue(ref['movedAfterPlacing'])
            self.assertAlmostEqual(ref['position']['x'], 400.0, places=1)       # heading 90 = east, in front

    def test_no_move_when_the_engine_applied_the_distance(self):
        live = FakeSpawnLive(honours_distance=True)
        out = self.make(live).spawn('Barrel03', distance=300)
        self.assertEqual(live.lines, [])
        self.assertTrue(out['refs'][0]['placedAtDistance'])

    def test_refs_made_before_a_failure_are_journaled(self):
        live = FakeSpawnLive(honours_distance=True, fail_on=3)
        with self.assertRaises(fnv_agent.LiveError):
            self.make(live).spawn('Barrel03', count=5, distance=300)
        self.assertEqual(len(live.journal.entries), 2)


class EngineWaitTests(unittest.TestCase):                     # F18
    def test_the_counter_is_restored_when_a_middle_batch_fails(self):
        writes = []
        calls = []

        def batch(ops, timeout=None):
            calls.append(len(ops))
            if ops and ops[0][0] == 'wr' and len(ops) == 1:
                writes.append(ops[0])
                return [{'ok': True}]
            if len(calls) == 2:
                raise LinkError('X', 'link died')
            return [{'ok': True} for _ in ops]
        live = SimpleNamespace(
            reads=lambda reqs: [struct.pack('<i', 7) if r[1] == 4 else b'\x55' * 16 for r in reqs],
            resolve=lambda ref: {'ptr': 0x1000}, journal=SimpleNamespace(add=lambda e: {'id': 'j'}),
            link=SimpleNamespace(batch=batch))
        deep = SimpleNamespace(_clean_bytes=lambda a, n: b'\x55' * 16)
        tools = fnv_agent.AgentTools(live, deep)
        with self.assertRaises(LinkError):
            tools._engine_wait(60, True)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], struct.pack('<i', 7).hex())


class ExperimentArmTests(unittest.TestCase):                  # F9
    def test_a_failed_arm_removes_the_skip(self):
        calls = []
        control = SimpleNamespace(hook_set=lambda f, **k: calls.append(k.get('action')),
                                  deep=SimpleNamespace(hook=lambda label: None))      # lookup finds nothing
        live = SimpleNamespace(engine=lambda: None, deep=SimpleNamespace(_image=lambda required=True: None))
        tools = fnv_experiment.ExperimentTools(live, control, None)
        original = fnv_experiment.ret_pop
        fnv_experiment.ret_pop = lambda *a: 0
        try:
            with self.assertRaises(Exception):
                tools._arm('00401000')
        finally:
            fnv_experiment.ret_pop = original
        self.assertEqual(calls, ['skip', 'remove'])


class AuthoringEncodingTests(unittest.TestCase):              # F6
    def test_spec_is_read_as_utf8(self):
        from fnv_mcp.authoring import Authoring
        spec = json.loads((Path(__file__).parent / 'fixtures' / 'coolworld.spec.json').read_text(encoding='utf-8'))
        spec['description'] = 'caf\u00e9 \u00fcber'
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'x.spec.json'
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
            fake = SimpleNamespace(spec_path=path, project=SimpleNamespace(plugin=spec['plugin']))
            loaded = Authoring.load_spec(fake)
        self.assertEqual(loaded['description'], 'caf\u00e9 \u00fcber')


if __name__ == '__main__':
    unittest.main()
