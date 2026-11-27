"""Gemini audit area 1 (native engine companion): regression tests for the fixes that show on the wire.

BUG-011 a `wr` into code goes through the `code patch` path (coverage released first).
BUG-008 a hook that already has a native handler refuses a second attach.
BUG-009 a module unload frees its hooks for reuse (removed_at is set).
BUG-022 `bp log` with since = 0xFFFFFFFF answers an empty page instead of wrapping to slot 0.
BUG-020 the compile cache tells a script from an expression of the same text (source check)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_link  # noqa: E402
from engine_host import needs_zig, zig_path  # noqa: E402
from test_engine_stops import PROLOGUE_LEN, StopHost, StopTestCase, build, hexint  # noqa: E402
from test_engine_races import SEAM, poke  # noqa: E402

SDK = Path(__file__).resolve().parents[1] / 'native' / 'sdk'
ENGINE = Path(__file__).resolve().parents[1] / 'native' / 'engine'
MODULE = r'''
#include "fnvmcp_module.h"
static const FnvmcpApi *api;
static int on_hookme(FnvHookFrame *f, void *user) { (void)f; (void)user; return FNV_CONTINUE; }
FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) {
    api = a;
    return api->hook(HOOKME, on_hookme, 0, 0) == 0 ? 0 : 2;
}
FNVMCP_EXPORT void fnvmcp_module_exit(void) {}
'''


@needs_zig
class Area1Tests(StopTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name, defines=SEAM)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp.name, 300)
        self.addCleanup(self.host.close)
        self.t, self.link = self.host.t, self.host.link

    def build_module(self, name):
        src = Path(self.tmp.name) / (name + '.c')
        src.write_text(MODULE, encoding='utf-8')
        out = Path(self.tmp.name) / (name + '.dll')
        subprocess.run([zig_path(), 'cc', '-target', 'x86-windows-gnu', '-shared', '-O2', '-I', str(SDK),
                        '-DHOOKME=0x%xu' % self.t['hookme'], '-o', str(out), str(src)],
                       check=True, capture_output=True, timeout=120)
        return out

    def install(self, label):
        return self.link.one('hook', hexint(self.t['hookme']), hexint(PROLOGUE_LEN), '-', hexint(0), hexint(0),
                             hexint(0), hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs(label))

    def test_bug011_a_write_into_armed_coverage_code_keeps_the_real_old_bytes(self):
        link, target = self.link, self.t['bpTarget']
        original = link.one('rd', hexint(target), '1')['hex']
        self.assertEqual(link.one('cov', 'add', target.to_bytes(4, 'little').hex())['added'], 1)
        self.assertEqual(link.one('cov', 'arm')['armed'], 1)
        self.assertEqual(link.one('rd', hexint(target), '1')['hex'], 'cc')
        reply = link.one('wr', hexint(target), original)
        self.assertTrue(reply.get('ok'), reply)
        self.assertEqual(reply['old'], original, 'the coverage 0xCC must not be saved as the old byte')
        self.assertEqual(link.one('rd', hexint(target), '1')['hex'], original)
        link.one('cov', 'disarm')
        self.assertEqual(link.one('rd', hexint(target), '1')['hex'], original)
        link.one('cov', 'reset')

    def test_bug008_a_second_native_attach_is_refused(self):
        link = self.link
        mod = link.one('mod', 'load', fnv_link.hexs(str(self.build_module('a1m1'))))
        self.assertTrue(mod.get('ok'), mod)
        hook = self.install('a1')
        first = link.one('hooknative', hexint(hook['hook']['id']), hexint(mod['module']['id']), '0')
        self.assertTrue(first.get('ok'), first)
        second = link.one('hooknative', hexint(hook['hook']['id']), hexint(mod['module']['id']), '0')
        self.assertEqual(second.get('error'), 'already_native')
        link.one('mod', 'unload', hexint(mod['module']['id']))

    def test_bug009_a_module_unload_frees_its_hook_for_reuse(self):
        link = self.link
        poke(self.host.process, self.t['hookReuseMs'], 0)
        mod = link.one('mod', 'load', fnv_link.hexs(str(self.build_module('a1m2'))))
        hook = self.install('a2')
        self.assertTrue(link.one('hooknative', hexint(hook['hook']['id']), hexint(mod['module']['id']), '0').get('ok'))
        unloaded = link.one('mod', 'unload', hexint(mod['module']['id']))
        self.assertEqual(unloaded['hooksRemoved'], 1)
        again = self.install('a2b')
        self.assertTrue(again.get('ok'), again)
        self.assertEqual(again['hook']['hook'], hook['hook']['hook'], 'the removed hook is reused, not leaked')
        link.one('unhook', hexint(again['hook']['id']))

    def test_bug022_a_log_read_from_the_last_sequence_number_is_empty(self):
        reply = self.link.one('bp', 'log', hexint(0), hexint(0xFFFFFFFF))
        self.assertTrue(reply.get('ok'), reply)
        self.assertEqual(reply['entries'], [])


class SourceChecks(unittest.TestCase):
    """Fixes that need a race or an unwind to show: the source keeps the shape that closes them."""

    def read(self, name):
        return (ENGINE / name).read_text(encoding='utf-8')

    def test_bug001_enter_prunes_stale_shadow_frames(self):
        source = self.read('fnvmcp_engine.c')
        self.assertIn('static void shadow_prune(Shadow *s, uint32_t ret_slot)', source)
        self.assertEqual(source.count('shadow_prune(s, (uint32_t)(uintptr_t)&fr[10]);'), 1)
        self.assertEqual(source.count('shadow_prune(sh, (uint32_t)(uintptr_t)&fr[10]);'), 1)

    def test_bug020_compile_cache_is_keyed_on_the_kind(self):
        source = self.read('fnvmcp_engine.c')
        self.assertIn('compiled[i].expression == expression && !strcmp(compiled[i].text, text)', source)

    def test_bug002_the_breakpoint_record_is_filled_before_the_int3(self):
        source = self.read('w2_bp.c')
        record = source.index('b->log_len = log_len;')
        plant = source.index('site = isite_add(addr, (int)id, 0, SITE_BP);')
        self.assertLess(record, plant)

    def test_bug003_skip_stub_slots_in_use_are_not_rewritten(self):
        self.assertIn('hooks[i]->skip_stub == cand', self.read('fnvmcp_engine.c'))


if __name__ == '__main__':
    unittest.main()
