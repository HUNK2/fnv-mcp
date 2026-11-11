"""Engine companion, wire version 2: sampler, event sinks, guarded call and input injector, over the real DLL and the
fake xNVSE host. Skipped unless GECK_MCP_ZIG is set (the companion is built with Zig). Numbers on the wire are hex."""
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_link  # noqa: E402

EXE_BASE_ALIAS = 0  # the fake host image is not the game image; addresses come from its JSON line


def hexint(value):
    return fnv_link.word(value)


def i32(value):
    return struct.pack('<i', value).hex()


def u32(value):
    return struct.pack('<I', value).hex()


def f32(value):
    return struct.pack('<f', value).hex()


def hx(text):
    return int(text, 16)


@unittest.skipUnless(os.environ.get('GECK_MCP_ZIG'), 'set GECK_MCP_ZIG to build the engine companion')
class IoCompanionTests(unittest.TestCase):
    """The real DLL over its pipe. The fake host pumps the main-loop message every 5 ms; its input poll runs only
    when a test asks for it ("poll <n>"), so the tests count polls exactly."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = fnv_link.build(cls.tmp.name, os.environ['GECK_MCP_ZIG'], with_fake_host=True)
        folder = Path(receipt['dll']).parent
        cls.pipe = 'fnvmcp-io-' + uuid.uuid4().hex[:8]
        env = dict(os.environ, FNVMCP_PIPE=cls.pipe, FNVMCP_CONSOLE_LOG=str(Path(cls.tmp.name) / 'console.log'))
        cls.host = subprocess.Popen([str(folder / 'fnvmcp_fake_host.exe'), receipt['dll'], '300'], stdout=subprocess.PIPE,
                                    env=env, text=True)
        cls.t = {k: hx(v) for k, v in __import__('json').loads(cls.host.stdout.readline()).items()}
        cls.link = fnv_link.EngineLink(cls.pipe).connect(wait=10)
        cls.link.one('gatecfg', hexint(cls.t['timerDisable']), hexint(cls.t['timerEnable']), hexint(cls.t['timerGlobal']))

    @classmethod
    def tearDownClass(cls):
        cls.link.close()
        cls.host.kill()
        cls.host.wait()
        cls.host.stdout.close()
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def op(self, *args):
        return self.link.one(*args)

    def poll(self, count=1):
        self.link.console('poll %d' % count)

    def globals_byte(self, offset):
        return self.link.read(self.t['osGlobals'] + offset, 1)[0]

    def globals_u32(self, offset):
        return self.link.u32(self.t['osGlobals'] + offset)

    def inp_jobs(self):
        return self.op('inp')['jobs']

    def sink_records(self, since=0, ref=None, max_count=200):
        args = ['sink', 'read', hexint(since), hexint(max_count)]
        if ref is not None:
            args.append(hexint(ref))
        return self.op(*args)

    # ------------------------------------------------------------------ sampler
    def test_sampler_records_values_floats_and_errors(self):
        self.assertEqual(self.op('samp', 'set', '0', '1', '50', 'ff', '8')['ok'], True)  # push frame
        time.sleep(0.2)
        read = self.op('samp', 'read', '0', '0')
        self.assertTrue(read['samples'])
        for sample in read['samples']:
            self.assertEqual(sample['values'], [sample['frame']])
        self.assertIsNone(read['stopFrame'])
        # a float value keeps its float form
        self.op('samp', 'set', '1', '1', '02' + f32(1.5), 'ff', '4')
        time.sleep(0.1)
        floats = self.op('samp', 'read', '1', '0')
        self.assertEqual(floats['samples'][0]['values'], [1.5])
        # a load from a bad address records nothing and counts an error
        self.op('samp', 'set', '2', '1', '10' + '00' + u32(4) + '01' + i32(0) + '04', 'ff', '4')
        time.sleep(0.1)
        listed = {s['id']: s for s in self.op('samp', 'list')['samplers']}
        self.assertGreater(listed[2]['errors'], 0)
        self.assertEqual(listed[2]['samples'], 0)
        self.assertEqual(self.op('samp', 'clear', 'ff')['ok'], True)
        self.assertEqual(self.op('samp', 'read', '0', '0')['error'], 'bad_id')

    def test_sampler_refuses_bad_arguments(self):
        self.assertEqual(self.op('samp', 'set', '0', '1', 'ff', 'ff', '4')['error'], 'bad_program')
        self.assertEqual(self.op('samp', 'set', '16', '1', '50', 'ff', '4')['error'], 'bad_arguments')
        self.assertEqual(self.op('samp', 'set', '0', '1', '50', '40', '4')['error'], 'bad_id')
        self.assertEqual(self.op('samp', 'set', '0', '1', '50', 'ff', '0')['error'], 'bad_arguments')
        self.assertEqual(self.op('samp', 'clear', 'ff')['ok'], True)

    def test_sampler_ring_wraps_and_reports_dropped(self):
        self.op('samp', 'set', '3', '1', '50', 'ff', '4')
        time.sleep(0.3)
        read = self.op('samp', 'read', '3', '0')
        self.assertLessEqual(len(read['samples']), 4)
        self.assertGreater(read['dropped'], 0)
        values = [s['values'][0] for s in read['samples']]
        self.assertEqual(values, list(range(values[0], values[0] + len(values))))
        self.assertEqual(read['next'], read['samples'][-1]['seq'])
        self.op('samp', 'clear', '3')

    def test_sampler_stops_on_its_predicate(self):
        frame = self.link.info()['frame']
        self.op('pred', 'set', '0a', '50' + '01' + i32(frame + 3) + '25')  # frame >= frame + 3 (ids are hex)
        self.op('samp', 'set', '4', '1', '50', '0a', '8')
        time.sleep(0.3)
        read = self.op('samp', 'read', '4', '0')
        self.assertTrue(read['stopped'])
        self.assertGreaterEqual(read['stopFrame'], frame + 3)
        time.sleep(0.1)
        again = self.op('samp', 'read', '4', hexint(read['next']))
        self.assertEqual(again['samples'], [])
        self.op('samp', 'clear', '4')

    # ------------------------------------------------------------------ event sinks
    def test_sink_msg_records_per_form_messages_with_ref_filter(self):
        slot = self.op('sink', 'add', 'msg', '28')['slot']  # 40 decimal: a per-form type the companion used to drop
        self.assertEqual(self.op('sink', 'add', 'msg', '28')['slot'], slot)  # the same type gives the same slot
        self.link.console('raise 40 4')  # data is a pointer to the fake form (a game object with a vtable)
        self.link.console('raise 41 4')  # not subscribed
        read = self.sink_records()
        msgs = [r for r in read['records'] if r['source'] == 'msg:40']
        self.assertTrue(msgs)
        self.assertEqual(msgs[-1]['ref'], '0x0000abcd')
        self.assertFalse([r for r in read['records'] if r['source'] == 'msg:41'])
        matched = self.sink_records(ref=0xABCD)['records']
        self.assertTrue(any(r['source'] == 'msg:40' for r in matched))
        self.assertTrue(all(r['ref'] == '0x0000abcd' for r in matched))
        self.assertEqual(self.sink_records(ref=0x1234)['records'], [])
        self.assertEqual(self.op('sink', 'remove', hexint(slot))['removed'], 1)

    def test_sink_nvse_event_records_and_removes(self):
        self.assertEqual(self.op('sink', 'add', 'nvse', 'missing'.encode().hex())['error'], 'event_refused')
        slot = self.op('sink', 'add', 'nvse', 'onhit'.encode().hex())['slot']
        self.link.console('raise_event onhit')
        read = self.sink_records()
        hits = [r for r in read['records'] if r['source'] == 'onhit']
        self.assertTrue(hits)
        self.assertEqual(hits[-1]['ref'], '0x0000abcd')
        self.assertEqual(hits[-1]['words'], ['0x%08x' % n for n in range(1, 7)])
        self.assertEqual(self.op('sink', 'remove', hexint(slot))['removed'], 1)
        before = len([r for r in self.sink_records()['records'] if r['source'] == 'onhit'])
        self.link.console('raise_event onhit')
        after = len([r for r in self.sink_records()['records'] if r['source'] == 'onhit'])
        self.assertEqual(before, after)

    # ------------------------------------------------------------------ guarded call
    def test_guarded_call_returns_faults_and_the_companion_keeps_serving(self):
        fault = self.op('call', hexint(self.t['faulter']), 'ig', '0', '0', hexint(0))
        self.assertFalse(fault['ok'])
        self.assertEqual(fault['fault']['code'], '0xc0000005')
        self.assertEqual(fault['fault']['access'], 'read')
        self.assertEqual(fault['fault']['address'], '0x00000000')
        self.assertEqual(fault['fault']['target'], '0x%08x' % self.t['faulter'])
        self.assertTrue(fault['stateSuspect'])
        bad = self.op('call', '0', 'ig', '0', '0')
        self.assertFalse(bad['ok'])
        self.assertEqual(bad['fault']['code'], '0xc0000005')
        self.assertEqual(bad['fault']['target'], '0x00000000')
        illegal = self.op('call', hexint(self.t['ud2']), 'vg', '0', '0')
        self.assertEqual(illegal['fault']['code'], '0xc000001d')
        good = self.op('call', hexint(self.t['add3']), 'ig', '0', '0', hexint(1), hexint(2), hexint(3))
        self.assertEqual((good['ok'], good['eax']), (True, '0x00000006'))
        self.assertEqual(self.link.ping()['companion'], 2)

    def test_unguarded_call_is_unchanged(self):
        ok = self.op('call', hexint(self.t['add3']), 'i', '0', '0', hexint(1), hexint(2), hexint(3))
        self.assertEqual((ok['ok'], ok['eax']), (True, '0x00000006'))
        self.assertEqual(self.op('call', '0', 'i', '0', '0')['error'], 'bad_arguments')  # plain targets below 0x10000 are refused
        self.assertEqual(self.op('call', hexint(self.t['add3']), 'x', '0', '0')['error'], 'bad_arguments')
        self.assertEqual(self.op('call', hexint(self.t['add3']), 'gi', '0', '0')['error'], 'bad_arguments')

    # ------------------------------------------------------------------ input injector
    def install_injector(self):
        if not self.link.info().get('ok'):
            self.fail('info failed')
        reply = self.op('inpcfg', hexint(self.t['poll']), hexint(5), hexint(self.t['osPtr']), hexint(self.t['force']))
        if reply.get('error') == 'already_installed':
            return
        self.assertEqual(reply['installed'], True)

    def test_injector_installs_and_the_poll_still_runs(self):
        self.install_injector()
        self.assertEqual(self.op('inpcfg', hexint(self.t['poll']), hexint(5), hexint(self.t['osPtr']),
                                 hexint(self.t['force']))['error'], 'already_installed')
        status = self.inp_jobs
        self.assertEqual(self.op('inp')['installed'], True)
        before = self.link.u32(self.t['polls'])
        self.poll(2)
        self.assertEqual(self.link.u32(self.t['polls']), before + 2)
        self.assertEqual(self.op('inp', 'key', '1c', '1')['ok'], True)
        self.assertEqual(self.op('inp', 'clear')['ok'], True)
        self.assertEqual(status(), [])

    def test_injector_key_applies_for_exactly_its_polls(self):
        self.install_injector()
        self.op('inp', 'key', '1c', '3')
        self.poll(1)
        self.assertEqual(self.globals_byte(0x18F8 + 0x1C), 0x80)
        self.assertEqual(self.inp_jobs()[0]['framesLeft'], 2)
        self.poll(1)
        self.assertEqual(self.globals_byte(0x18F8 + 0x1C), 0x80)
        self.assertEqual(self.inp_jobs()[0]['framesLeft'], 1)
        self.poll(1)  # the third poll is the last one: the key is released and the job is gone
        self.assertEqual(self.globals_byte(0x18F8 + 0x1C), 0)
        self.assertEqual(self.inp_jobs(), [])
        self.poll(2)
        self.assertEqual(self.globals_byte(0x18F8 + 0x1C), 0)

    def test_injector_mouse_and_button_jobs(self):
        self.install_injector()
        self.link.write(self.t['osGlobals'] + 0x1B24, bytes(12))
        self.op('inp', 'mouse', '5', 'ffffffff', '2')
        self.poll(1)
        self.assertEqual((self.globals_u32(0x1B24), self.globals_u32(0x1B28)), (5, 0xFFFFFFFF))
        self.poll(1)
        self.assertEqual((self.globals_u32(0x1B24), self.globals_u32(0x1B28)), (10, 0xFFFFFFFE))
        self.poll(1)  # the job has ended: no more deltas
        self.assertEqual((self.globals_u32(0x1B24), self.globals_u32(0x1B28)), (10, 0xFFFFFFFE))
        self.op('inp', 'btn', '2', '2')
        self.poll(1)
        self.assertEqual(self.globals_byte(0x1B30 + 2), 0x80)
        self.poll(1)
        self.assertEqual(self.globals_byte(0x1B30 + 2), 0)
        self.assertEqual(self.inp_jobs(), [])

    def test_injector_control_jobs_call_force_and_clear_releases(self):
        self.install_injector()
        forces = self.link.u32(self.t['forces'])
        self.op('inp', 'ctl', '5', '2')
        self.poll(1)
        self.assertEqual(self.link.u32(self.t['forces']), forces + 1)
        self.assertEqual(self.link.u32(self.t['lastForce']), 5)
        self.poll(1)
        self.assertEqual(self.link.u32(self.t['forces']), forces + 2)
        self.poll(1)
        self.assertEqual(self.link.u32(self.t['forces']), forces + 2)
        self.op('inp', 'key', '1e', '100')
        self.poll(1)
        self.assertEqual(self.globals_byte(0x18F8 + 0x1E), 0x80)
        cleared = self.op('inp', 'clear')
        self.assertEqual((cleared['ok'], cleared['cleared']), (True, 1))
        self.assertEqual(self.globals_byte(0x18F8 + 0x1E), 0)
        self.assertEqual(self.inp_jobs(), [])

    def test_injector_refuses_bad_jobs(self):
        self.install_injector()
        self.assertEqual(self.op('inp', 'key', '100', '1')['error'], 'bad_arguments')
        self.assertEqual(self.op('inp', 'key', '1c', '0')['error'], 'bad_arguments')
        self.assertEqual(self.op('inp', 'btn', '8', '1')['error'], 'bad_arguments')
        self.assertEqual(self.op('inp', 'ctl', '13', '1')['error'], 'bad_arguments')
        self.assertEqual(self.op('inp', 'fly', '1', '1')['error'], 'bad_arguments')

    def test_injector_releases_on_the_dead_man_switch(self):
        self.install_injector()
        self.op('inp', 'key', '1d', '1000')
        self.poll(1)
        self.assertEqual(self.globals_byte(0x18F8 + 0x1D), 0x80)
        self.op('hold', 'on', hexint(300))
        time.sleep(0.8)  # no request and no ping: the dead-man switch opens the gate and ends the jobs
        self.assertFalse(self.link.ping()['held'])
        self.assertEqual(self.inp_jobs(), [])
        self.assertEqual(self.globals_byte(0x18F8 + 0x1D), 0)
        types = [e['type'] for e in self.link.events(0)['events']]
        self.assertIn('deadman', types)


if __name__ == '__main__':
    unittest.main()
