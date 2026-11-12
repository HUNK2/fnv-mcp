"""W5 native modules against the fake xNVSE host: build a module with Zig, load it, install its hook request, see the
handler log and skip a call, call an export, see the frame callback, unload (original bytes back, writes restored)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_link  # noqa: E402
from test_engine_stops import PROLOGUE_LEN, StopHost, StopTestCase, build, hexint  # noqa: E402
from engine_host import needs_zig, zig_path  # noqa: E402,F401

SDK = Path(__file__).resolve().parents[1] / 'native' / 'sdk'
MODULE = r'''
#include "fnvmcp_module.h"
static const FnvmcpApi *api;
static volatile int frames;
static unsigned char saved;
static int on_hookme(FnvHookFrame *f, void *user) {
    (void)user;
    api->log("hookme arg %u", f->arg[0]);
    if (f->arg[0] == 42) { f->eax = 4242; return FNV_SKIP; }
    return FNV_CONTINUE;
}
static void on_frame(uint32_t n, void *user) { (void)n; (void)user; frames++; }
FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) {
    api = a;
    api->log("init v%u size %u", a->version, a->size);
    if (api->hook(HOOKME, on_hookme, 0, 0) != 0) return 2;
    api->on_frame(on_frame, 0);
    unsigned char marker = 0x5A;
    api->read(SCRATCH, &saved, 1);
    api->write(SCRATCH, &marker, 1);
    return 0;
}
FNVMCP_EXPORT int frames_seen(void) { return frames; }
FNVMCP_EXPORT int add(int a, int b) { return a + b; }
FNVMCP_EXPORT void fnvmcp_module_exit(void) { api->log("exit after %d frames", frames); }
'''


@needs_zig
class ModuleTests(StopTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = build(cls.tmp.name)
        cls.host = StopHost(receipt, Path(receipt['dll']).parent, cls.tmp.name, 300)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def build_module(self, name, scratch):
        src = Path(self.tmp.name) / (name + '.c')
        src.write_text(MODULE, encoding='utf-8')
        out = Path(self.tmp.name) / (name + '.dll')
        subprocess.run([zig_path(), 'cc', '-target', 'x86-windows-gnu', '-shared', '-O2',
                        '-I', str(SDK), '-DHOOKME=0x%xu' % self.host.t['hookme'], '-DSCRATCH=0x%xu' % scratch,
                        '-o', str(out), str(src)], check=True, capture_output=True, timeout=120)
        return out

    def test_load_hook_skip_call_unload(self):
        link = self.host.link
        scratch = self.host.t['buffer'] + 0x200     # a byte of the host's scratch buffer: written at init, put back
        before = link.one('rd', hexint(scratch), '1')['hex']
        dll = self.build_module('m1', scratch)
        loaded = link.one('mod', 'load', fnv_link.hexs(str(dll)))
        self.assertTrue(loaded.get('ok'), loaded)
        mod = loaded['module']
        self.assertEqual(int(mod['hooks'][0]['target'], 16), self.host.t['hookme'])
        self.assertFalse(mod['hooks'][0]['installed'])
        self.assertEqual(link.one('rd', hexint(scratch), '1')['hex'], '5a')
        exports = {e['name']: int(e['address'], 16) for e in loaded['exports']}
        self.assertIn('add', exports)
        # The bridge installs the request: a plain hook, then the native handler on it.
        hook = link.one('hook', hexint(self.host.t['hookme']), hexint(PROLOGUE_LEN), '-', hexint(0), hexint(0),
                        hexint(0), hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('mod'))
        attached = link.one('hooknative', hexint(hook['hook']['id']), hexint(mod['id']), '0')
        self.assertTrue(attached.get('ok'), attached)
        link.schedule(0, 'hookme 5')
        self.assertTrue(self.wait_line('hookme 1005'))
        link.schedule(0, 'hookme 42')
        self.assertTrue(self.wait_line('hookme 4242'))                  # FNV_SKIP returned the frame's eax
        added = link.one('call', hexint(exports['add']), 'i', '0', '0', hexint(2), hexint(3))
        self.assertEqual(int(added['eax'], 16), 5)
        time.sleep(0.3)
        frames = link.one('call', hexint(exports['frames_seen']), 'i', '0', '0')
        self.assertGreater(int(frames['eax'], 16), 0)
        texts = [line['text'] for line in link.one('mod', 'log', '0')['lines']]
        self.assertIn('init v1 size 44', texts)          # two words and nine pointers
        self.assertIn('hookme arg 5', texts)
        self.assertIn('hookme arg 42', texts)
        unloaded = link.one('mod', 'unload', hexint(mod['id']))
        self.assertTrue(unloaded.get('ok'), unloaded)
        self.assertEqual((unloaded['hooksRemoved'], unloaded['writesRestored']), (1, 1))
        self.assertEqual(link.one('rd', hexint(scratch), '1')['hex'], before)
        self.assertTrue(any(t.startswith('exit after') for t in
                            [line['text'] for line in link.one('mod', 'log', '0')['lines']]))
        link.schedule(0, 'hookme 42')
        self.assertTrue(self.wait_line('hookme 1042'))                  # the original function again
        self.assertEqual(link.one('mod', 'list')['modules'], [])

    def test_a_module_without_init_is_refused(self):
        src = Path(self.tmp.name) / 'bad.c'
        src.write_text('__declspec(dllexport) int other(void) { return 1; }\n', encoding='utf-8')
        out = Path(self.tmp.name) / 'bad.dll'
        subprocess.run([zig_path(), 'cc', '-target', 'x86-windows-gnu', '-shared', '-o', str(out),
                        str(src)], check=True, capture_output=True, timeout=120)
        self.assertEqual(self.host.link.one('mod', 'load', fnv_link.hexs(str(out))).get('error'), 'no_init_export')


if __name__ == '__main__':
    unittest.main()


@needs_zig
class CoverageTests(StopTestCase):
    """W6 line coverage: one-shot int3 sites that restore themselves on the first hit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = build(cls.tmp.name)
        cls.host = StopHost(receipt, Path(receipt['dll']).parent, cls.tmp.name, 300)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def test_sites_hit_once_and_restore_their_bytes(self):
        link, t = self.host.link, self.host.t
        sites = [t['bpTarget'], t['bpCall'], t['bpAfter'], t['bpRet'], t['faulter']]
        before = [link.one('rd', hexint(a), '1')['hex'] for a in sites]
        packed = b''.join(a.to_bytes(4, 'little') for a in sites).hex()
        self.assertEqual(link.one('cov', 'add', packed)['added'], 5)
        self.assertEqual(link.one('cov', 'arm')['armed'], 5)
        self.assertEqual(link.one('rd', hexint(t['bpTarget']), '1')['hex'], 'cc')
        link.schedule(0, 'bpcall 3')
        self.assertTrue(self.wait_line('bpcall 12'))                    # the function still computes the same
        read = link.one('cov', 'read', '0')
        hits = {int.from_bytes(bytes.fromhex(read['addresses'][i:i + 8]), 'little')
                for i in range(0, len(read['addresses']), 8)}
        self.assertEqual(hits, set(sites[:4]))                          # faulter never ran
        self.assertEqual(link.one('rd', hexint(t['bpTarget']), '1')['hex'], before[0])   # restored on the hit
        self.assertEqual(link.one('rd', hexint(t['faulter']), '1')['hex'], 'cc')
        link.one('cov', 'disarm')
        self.assertEqual([link.one('rd', hexint(a), '1')['hex'] for a in sites], before)
        link.schedule(0, 'bpcall 4')
        self.assertTrue(self.wait_line('bpcall 12') and self.wait_line('bpcall 12'))
        self.assertEqual(link.one('cov', 'reset')['ok'], True)
