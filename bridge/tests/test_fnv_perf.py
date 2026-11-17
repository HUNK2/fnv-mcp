"""Plan 2 W6 against fakes: pool block maths, the profile report and its diff, coverage results, animation group
decoding and formula calls."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_coverage import CoverageTools  # noqa: E402
from geck_mcp.fnv_inspect import FORMULAS, SystemsTools, _decode_group  # noqa: E402
from geck_mcp.fnv_perf import ProfileTools, block_offsets, block_start, sample_threads  # noqa: E402


class BlockTests(unittest.TestCase):
    def test_blocks_restart_at_every_page(self):
        # Seen live: Character blocks of 0x1E0, eight per 4 KB page from the page start.
        self.assertEqual(block_start(0x4D12D7A0, 0x1E0), 0x4D12D780)
        self.assertEqual(block_start(0x4D12DD30, 0x1E0), 0x4D12DD20)
        self.assertIsNone(block_start(0x4D12DF10, 0x1E0))      # past the 8th block: page slack
        offs = list(block_offsets(0x4D120000, 0x1E0, 0x2000))
        self.assertEqual(offs[:9], [0, 0x1E0, 0x3C0, 0x5A0, 0x780, 0x960, 0xB40, 0xD20, 0x1000])


def namer(address, return_address=False):
    table = {0x401000: ('Leaf::Work', 0x401000, 'src/a.cpp:10'), 0x402000: ('Caller::Loop', 0x402000, 'src/b.cpp:5'),
             0x77000000: ('ntdll.dll!0x0', None, None)}
    for base, (fn, start, line) in table.items():
        if base <= address < base + 0x1000:
            return {'function': fn, 'start': start, 'line': line}
    return {'function': '0x%08X' % address, 'start': None, 'line': None}


class SamplingClockTests(unittest.TestCase):
    """fnv_threads list takes one sample (rate 1, 1 ms) and then slept out the rest of a 1 s period: 1 s per call."""

    def run_sampler(self, rate, seconds):
        now, slept = [0.0], []

        def sleep(s):
            slept.append(s)
            now[0] += s
        sample_threads(None, [], rate, seconds, sleep=sleep, clock=lambda: now[0])
        return now[0], slept

    def test_no_sleep_runs_past_the_end(self):
        took, slept = self.run_sampler(1, 0.001)
        self.assertLessEqual(took, 0.001 + 1e-9, slept)

    def test_samples_keep_their_rate_inside_the_window(self):
        took, slept = self.run_sampler(10, 0.35)
        self.assertEqual([round(s, 6) for s in slept], [0.1, 0.1, 0.1, 0.05])
        self.assertAlmostEqual(took, 0.35)


class ProfileReportTests(unittest.TestCase):
    def test_self_inclusive_and_game_function_tables(self):
        stacks = {7: [[0x401010, 0x402020]] * 3 + [[0x77000010, 0x401010, 0x402020]]}
        report = ProfileTools(None)._report(stacks, namer, 7, 10)
        t = report['threads'][0]
        self.assertEqual(t['samples'], 4)
        self.assertEqual(t['selfFunctions'][0], {'function': 'Leaf::Work', 'samples': 3, 'percent': 75.0,
                                                 'calledFrom': [{'function': 'Caller::Loop', 'samples': 3}]})
        self.assertEqual(t['gameFunctions'][0]['function'], 'Leaf::Work')       # the ntdll wait counts for its caller
        self.assertEqual(t['gameFunctions'][0]['percent'], 100.0)
        self.assertEqual({r['function']: r['percent'] for r in t['inclusive']}['Caller::Loop'], 100.0)
        self.assertEqual(report['_folded']['thread_7;Caller::Loop;Leaf::Work'], 3)

    def test_diff_finds_profiles_by_name_and_reports_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = ProfileTools(SimpleNamespace(project=SimpleNamespace(state_dir=tmp)))
            tools.folder.mkdir(parents=True)
            for name, pct, fps in (('slow', 80.0, 34.0), ('fixed', 0.0, 57.0)):
                (tools.folder / ('%s-20260101-000000.json' % name)).write_text(json.dumps(
                    {'fps': fps, 'threads': [{'samples': 100, 'gameFunctions': [{'function': 'Hot', 'percent': pct}]}]}))
            out = tools.diff('slow', 'fixed')
            self.assertEqual(out['fps'], {'before': 34.0, 'after': 57.0})
            self.assertEqual(out['changes'][0], {'function': 'Hot', 'before': 80.0, 'after': 0.0, 'change': -80.0})


class CoverageTests(unittest.TestCase):
    def test_stop_reports_lines_run_and_never_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = struct.pack('<II', 0x401000, 0x401008).hex()
            ops = []

            def one(*op):
                ops.append(op)
                if op[:2] == ('cov', 'read'):
                    return {'ok': True, 'addresses': hits, 'hits': 2, 'next': 2}
                return {'ok': True}
            engine = SimpleNamespace(summary=lambda a, with_location=False: {'name': 'F'})
            live = SimpleNamespace(project=SimpleNamespace(state_dir=tmp), link=SimpleNamespace(one=one),
                                   ready=lambda: None, engine=lambda: engine)
            cov = CoverageTools(live)
            cov.active = {'name': 'run1', 'sites': {0x401000: ('src/a.cpp', 10, 0x401000),
                                                    0x401004: ('src/a.cpp', 11, 0x401000),
                                                    0x401008: ('src/a.cpp', 12, 0x401000)}}
            out = cov.stop()
            self.assertEqual(out['files'][0]['runLines'], [10, 12])
            self.assertEqual(out['files'][0]['neverRun'], [11])
            self.assertIn(('cov', 'disarm'), ops)
            self.assertEqual(json.loads(Path(out['path']).read_text())['run'], ['src/a.cpp:10', 'src/a.cpp:12'])


class InspectTests(unittest.TestCase):
    def test_group_id_decoding(self):
        g = _decode_group(0x8000 | (3 << 12) | (5 << 8) | 0x2A)
        self.assertEqual((g['type'], g['weapon'], g['move'], g['powerArmour']), (0x2A, 5, 3, True))

    def test_catalog_formula_is_called_with_typed_arguments(self):
        calls = []

        def engine_call(function, this, args, returns, convention, allow):
            calls.append((function, args, returns, convention, allow))
            return {'result': 200}
        tools = SystemsTools(SimpleNamespace(engine_call=engine_call, ready=lambda: None))
        out = tools.formula('xp_for_level', [2])
        self.assertEqual(out['result'], 200)
        self.assertEqual(calls[0], ('0x%08X' % FORMULAS['xp_for_level']['address'],
                                    [{'type': 'int', 'value': 2}], 'int', 'cdecl', True))
        sweep = tools.sweep('xp_for_level', [1], 0, 1, 3, 2)
        self.assertEqual([r['input'] for r in sweep['rows']], [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
