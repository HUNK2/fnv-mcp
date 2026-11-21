"""The input injector (fnv_inject) against the fake companion: one install per process, the engine map check before any
install, the job ops and their two's complement deltas, the refusals with their next steps, and the frame wait."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w1_support import POLL_FN, FORCE_FN, W1Live  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_inject import OS_GLOBALS_PTR, Injector, scan_to_job  # noqa: E402


class InjectorTests(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.inj = Injector.of(self.live)
        self.link = self.live.link

    def test_the_first_job_installs_the_injector_once(self):
        self.inj.control(3, 4, wait=False)
        self.assertEqual(len(self.link.inpcfg_calls), 1)
        poll, stolen, globals_ptr, force = [int(x, 16) for x in self.link.inpcfg_calls[0]]
        self.assertEqual((poll, globals_ptr, force), (POLL_FN, OS_GLOBALS_PTR, FORCE_FN))
        self.assertEqual(stolen, 6)               # push ebp; mov ebp,esp; sub esp,0x10
        self.inj.key(30, 2, wait=False)
        self.assertEqual(len(self.link.inpcfg_calls), 1)

    def test_a_companion_with_an_injector_already_installed_is_taken_as_it_is(self):
        self.link.installed = True
        self.inj.key(30, 2, wait=False)
        self.assertEqual(self.link.inpcfg_calls, [])

    def test_an_engine_map_mismatch_refuses_before_any_install(self):
        self.live.functions[POLL_FN]['signature'] = 'void __cdecl InputPoller(void)'
        with self.assertRaises(ConvError) as cm:
            self.inj.key(30, 2, wait=False)
        self.assertEqual(cm.exception.code, 'ENGINE_MAP_MISMATCH')
        self.assertIn('Next:', str(cm.exception))
        self.assertEqual(self.link.inpcfg_calls, [])

    def test_a_relative_call_in_the_prologue_is_refused_not_relocated(self):
        self.link.put(POLL_FN, bytes([0xE8, 0, 0, 0, 0]) + b'\x90' * 20)
        with self.assertRaises(ConvError) as cm:
            self.inj.key(30, 2, wait=False)
        self.assertEqual(cm.exception.code, 'PROLOGUE_RELOCATION')
        self.assertEqual(self.link.inpcfg_calls, [])

    def test_each_job_kind_sends_its_op(self):
        self.inj.control(5, 7, wait=False)
        self.inj.key(30, 2, wait=False)
        self.inj.button(2, 4, wait=False)
        jobs = self.link.inp_jobs
        self.assertEqual([(j['kind'], j['args']) for j in jobs],
                         [('control', ['5', '7']), ('key', ['1e', '2']), ('button', ['2', '4'])])

    def test_mouse_deltas_are_sent_in_two_complement(self):
        self.inj.mouse(-1, 2, 3, wait=False)
        self.assertEqual(self.link.inp_jobs[-1]['args'], ['ffffffff', '2', '3'])

    def test_a_scan_code_of_256_and_up_is_a_mouse_button(self):
        scan_to_job(self.inj, 258, 4, wait=False)
        scan_to_job(self.inj, 30, 2, wait=False)
        self.assertEqual([j['kind'] for j in self.link.inp_jobs], ['button', 'key'])

    def test_out_of_range_values_are_refused_before_any_call(self):
        with self.assertRaises(ValueError):
            self.inj.control(13, 1, wait=False)
        with self.assertRaises(ValueError):
            self.inj.key(256, 1, wait=False)
        with self.assertRaises(ValueError):
            self.inj.button(8, 1, wait=False)
        with self.assertRaises(ValueError):
            self.inj.key(30, 0, wait=False)
        with self.assertRaises(ValueError):
            self.inj.key(30, 10001, wait=False)
        self.assertEqual(self.link.inpcfg_calls, [])
        self.assertEqual(self.link.inp_jobs, [])

    def test_a_refused_job_says_why_and_what_next(self):
        self.inj.ensure()
        self.link.refuse_inp = True
        with self.assertRaises(ConvError) as cm:
            self.inj.key(30, 2, wait=False)
        self.assertEqual(cm.exception.code, 'INPUT_REFUSED')
        self.assertIn('Next:', str(cm.exception))

    def test_waiting_runs_the_frames_of_the_job(self):
        before = self.link.frame
        out = self.inj.key(30, 5)
        self.assertTrue(out['waited'])
        self.assertEqual(out['frames'], 5)
        self.assertGreaterEqual(self.link.frame - before, 5)
        self.assertIn('for 5 frames', out['verdict'])

    def test_no_game_means_not_running(self):
        self.live.game.proc = None
        with self.assertRaises(ConvError) as cm:
            self.inj.key(30, 2, wait=False)
        self.assertEqual(cm.exception.code, 'NOT_RUNNING')

    def test_status_lists_the_jobs_and_clear_drops_them(self):
        self.inj.key(30, 2, wait=False)
        status = self.inj.status()
        self.assertTrue(status['installed'])
        self.assertEqual(len(status['jobs']), 1)
        self.inj.clear()
        self.assertEqual(self.link.inp_jobs, [])


if __name__ == '__main__':
    unittest.main()
