"""Critique fix Task 2 (2026-11-21): fnv_native follows the game's own module list, returns only the log lines a
call wrote, and rebuilds when an included file changes.

FakeCompanion copies the companion's rules in native/engine/w5_mod.c: module ids count up from 1 per game process,
`mod log <since> [max]` returns the oldest lines after since (max 200 by default) from a 1,024-line ring, with
`next` and `last`."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_native import NativeTools, source_digest  # noqa: E402

RING = 1024


def signed(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


class FakeCompanion:
    def __init__(self):
        self.calls = []
        self.restart()

    def restart(self):
        self.modules, self.next_id, self.seq, self.ring = [], 1, 0, {}

    def write_log(self, n, text='old'):
        for _ in range(n):
            self.seq += 1
            self.ring[(self.seq - 1) % RING] = (self.seq, '%s %d' % (text, self.seq))

    def _log(self, since, maximum=200):
        last = self.seq
        first = max(1, last - RING + 1)
        since = signed(int(since, 16))
        lines, nxt = [], since
        seq = max(since + 1, first)
        while seq <= last and len(lines) < maximum:
            stored, text = self.ring[(seq - 1) % RING]
            if stored == seq:
                lines.append({'seq': seq, 'module': 1, 'frame': 0, 'text': text})
                nxt = seq
            seq += 1
        return {'ok': True, 'lines': lines, 'next': nxt if lines else (last if last > since else since), 'last': last}

    def one(self, *op, timeout=None):
        self.calls.append(op)
        if op[:2] == ('mod', 'list'):
            return {'ok': True, 'modules': [dict(m) for m in self.modules]}
        if op[:2] == ('mod', 'log'):
            return self._log(op[2], int(op[3]) if len(op) > 3 else 200)
        if op[:2] == ('mod', 'load'):
            path = bytes.fromhex(op[2]).decode()
            mid = self.next_id
            self.next_id += 1
            base = 0x10000000 + 0x100000 * mid
            mod = {'id': mid, 'name': Path(path).name, 'base': '0x%08x' % base, 'hooks': []}
            self.modules.append(mod)
            self.write_log(1, 'init of %d' % mid)
            return {'ok': True, 'module': dict(mod), 'exports': [{'name': 'answer', 'address': '0x%08X' % (base + 0x1010)}]}
        if op[:2] == ('mod', 'unload'):
            mid = int(op[2], 16)
            if not any(m['id'] == mid for m in self.modules):
                return {'ok': False, 'error': 'bad_id'}
            self.modules = [m for m in self.modules if m['id'] != mid]
            return {'ok': True, 'id': mid, 'hooksRemoved': 0, 'writesRestored': 0}
        if op[0] == 'call':
            self.write_log(self.call_logs, 'call')
            return {'ok': True, 'eax': '0x0000002a'}
        return {'ok': True}

    call_logs = 2


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = FakeCompanion()
        live = SimpleNamespace(link=self.game, ready=lambda: None, changing=lambda *a: None,
                               journal=SimpleNamespace(add=lambda e: None),
                               project=SimpleNamespace(state_dir=self.tmp.name))
        self.t = NativeTools(live)
        modules = Path(self.tmp.name) / 'fnv' / 'modules'
        modules.mkdir(parents=True)
        (modules / 'm-0123456789ab.dll').write_bytes(b'MZ')

    # ------------------------------------------------------------------ module tracking
    def test_load_after_game_restart_loads_again(self):
        self.t.load('m')
        self.game.restart()
        out = self.t.load('m')
        self.assertEqual(out['id'], 1)

    def test_unload_after_game_restart_says_not_loaded(self):
        self.t.load('m')
        self.game.restart()
        with self.assertRaises(ConvError) as cm:
            self.t.unload('m')
        self.assertEqual(cm.exception.code, 'NOT_LOADED')

    def test_call_after_game_restart_calls_nothing(self):
        self.t.load('m')
        self.game.restart()
        with self.assertRaises(ConvError) as cm:
            self.t.call('m', 'answer')
        self.assertEqual(cm.exception.code, 'NOT_LOADED')
        self.assertFalse(any(op[0] == 'call' for op in self.game.calls))

    def test_load_twice_in_one_game_is_still_already_loaded(self):
        self.t.load('m')
        with self.assertRaises(ConvError) as cm:
            self.t.load('m')
        self.assertEqual(cm.exception.code, 'ALREADY_LOADED')

    # ------------------------------------------------------------------ log
    def test_call_returns_only_lines_the_call_logged(self):
        self.game.write_log(299)
        self.t.load('m')                # its init logs line 300
        out = self.t.call('m', 'answer')
        self.assertEqual([line['seq'] for line in out['log']], [301, 302])
        self.assertEqual(out['logNext'], 302)

    def test_call_that_logs_nothing_returns_no_lines(self):
        self.game.write_log(300)
        self.t.load('m')
        self.game.call_logs = 0
        try:
            out = self.t.call('m', 'answer')
        finally:
            self.game.call_logs = 2
        self.assertEqual(out['log'], [])

    def test_call_that_logs_a_lot_says_how_much_was_cut(self):
        self.t.load('m')
        self.game.call_logs = 100
        try:
            out = self.t.call('m', 'answer')
        finally:
            self.game.call_logs = 2
        self.assertEqual(len(out['log']), 40)
        self.assertEqual(out['logCut'], 60)
        self.assertEqual(out['logNext'], out['log'][-1]['seq'])

    def test_load_returns_only_its_init_lines(self):
        self.game.write_log(250)
        out = self.t.load('m')
        self.assertEqual([line['seq'] for line in out['log']], [251])

    def test_log_default_is_the_newest_lines(self):
        self.game.write_log(450)
        out = self.t.log()
        self.assertEqual(len(out['lines']), 200)
        self.assertEqual(out['lines'][-1]['seq'], 450)

    def test_log_since_reads_past_200(self):
        self.game.write_log(450)
        out = self.t.log(since=200)
        self.assertEqual([line['seq'] for line in out['lines']], list(range(201, 451)))
        self.assertEqual(out['next'], 450)

    def test_log_after_the_ring_wrapped(self):
        self.game.write_log(1500)
        out = self.t.log(since=10)
        self.assertEqual(out['lines'][0]['seq'], 477)
        self.assertEqual(out['dropped'], 466)

    def test_build_of_unchanged_source_reuses_the_dll(self):
        # Live 2026-11-21: building the source of the loaded module again failed, "failed to write output ...
        # Permission denied": the loaded DLL is the same file. The same digest is the same build.
        from geck_mcp.fnv_native import source_digest
        source = Path(self.tmp.name) / 'r.c'
        source.write_text('int x;', encoding='utf-8')
        self.t.zig = 'C:/no/such/zig.exe'
        sdk = Path(self.tmp.name) / 'fnv' / 'sdk'
        sdk.mkdir(parents=True, exist_ok=True)
        (sdk / 'fnvmcp_module.h').write_text('// api', encoding='utf-8')
        dll = Path(self.tmp.name) / 'fnv' / 'modules' / ('r-%s.dll' % source_digest('int x;', source, sdk))
        dll.write_bytes(b'MZ')
        out = self.t.build(str(source), 'r')
        self.assertTrue(out['reused'])
        self.assertEqual(Path(out['dll']), dll)

    def test_list_shows_the_newest_build_per_name(self):
        modules = Path(self.tmp.name) / 'fnv' / 'modules'
        for i, name in enumerate(('a-000000000001', 'a-000000000002', 'a-000000000003', 'b-000000000001')):
            (modules / (name + '.dll')).write_bytes(b'MZ')
        rows = {row['name']: row for row in self.t.list()['built']}
        self.assertEqual(set(rows), {'a', 'b', 'm'})
        self.assertEqual(rows['a']['builds'], 3)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sdk = self.tmp / 'sdk'
        self.sdk.mkdir()
        (self.sdk / 'fnvmcp_module.h').write_text('// api', encoding='utf-8')

    def test_changed_included_file_gives_a_new_dll_name(self):
        main = self.tmp / 'm.c'
        main.write_text('#include "helper.h"\nint x;', encoding='utf-8')
        (self.tmp / 'helper.h').write_text('#define A 1', encoding='utf-8')
        before = source_digest(main.read_text(encoding='utf-8'), main, self.sdk)
        (self.tmp / 'helper.h').write_text('#define A 2', encoding='utf-8')
        self.assertNotEqual(before, source_digest(main.read_text(encoding='utf-8'), main, self.sdk))

    def test_include_cycle_ends(self):
        main = self.tmp / 'm.c'
        main.write_text('#include "a.h"', encoding='utf-8')
        (self.tmp / 'a.h').write_text('#include "b.h"', encoding='utf-8')
        (self.tmp / 'b.h').write_text('#include "a.h"', encoding='utf-8')
        self.assertEqual(len(source_digest(main.read_text(encoding='utf-8'), main, self.sdk)), 12)

    def test_missing_include_is_left_to_the_compiler(self):
        text = '#include "nothere.h"'
        self.assertEqual(len(source_digest(text, None, self.sdk)), 12)

    def test_sdk_header_change_still_changes_the_name(self):
        text = '#include "fnvmcp_module.h"'
        before = source_digest(text, None, self.sdk)
        (self.sdk / 'fnvmcp_module.h').write_text('// api 2', encoding='utf-8')
        self.assertNotEqual(before, source_digest(text, None, self.sdk))


if __name__ == '__main__':
    unittest.main()
