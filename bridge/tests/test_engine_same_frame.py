"""Engine companion: read requests the bridge marks are served in the same game frame.

The companion takes one request per frame. A tool that reads the game in 200 small requests then waits 200 frames
(3.3 s at 60 fps) for work that takes the game 30 ms. A request whose id starts with "r" is marked by the bridge as a
read; after serving one the main thread waits a moment for the next request and serves it in the same frame.

Over the real DLL and a fake xNVSE host (it pumps the main-loop message every 5 ms or slower). Skipped when no Zig
is found. Numbers on the wire are hex."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_link  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host
from engine_host import needs_zig, zig_path  # noqa: E402,F401


def hexint(value):
    return fnv_link.word(value)


# A wait and a frame budget no test can run into by accident: the chain ends only where the test ends it.
LONG_WAIT_US = 100_000
LONG_FRAME_US = 1_000_000


@needs_zig
class SameFrameCompanionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = fnv_link.build(cls.tmp.name, zig_path(), with_fake_host=True)
        folder = Path(receipt['dll']).parent
        cls.pipe = 'fnvmcp-frame-' + uuid.uuid4().hex[:8]
        env = dict(os.environ, FNVMCP_PIPE=cls.pipe, FNVMCP_CONSOLE_LOG=str(Path(cls.tmp.name) / 'console.log'),
                   LOCALAPPDATA=str(Path(cls.tmp.name) / 'localappdata'))
        cls.host = subprocess.Popen([str(folder / 'fnvmcp_fake_host.exe'), receipt['dll'], '300'], stdout=subprocess.PIPE,
                                    env=env, text=True)
        cls.t = {k: int(v, 16) for k, v in json.loads(cls.host.stdout.readline()).items()}
        cls.link = fnv_link.EngineLink(cls.pipe).connect(wait=10)
        cls.link.one('gatecfg', hexint(cls.t['timerDisable']), hexint(cls.t['timerEnable']), hexint(cls.t['timerGlobal']))

    @classmethod
    def tearDownClass(cls):
        cls.link.close()
        cls.host.kill()
        cls.host.wait()
        cls.host.stdout.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.linger(LONG_WAIT_US, LONG_FRAME_US)

    def tearDown(self):
        if self.link.ping()['held']:
            self.link.one('hold', 'off')

    def linger(self, wait_us, frame_us):
        result = self.link.one('linger', hexint(wait_us), hexint(frame_us))
        self.assertEqual(result, {'ok': True, 'waitUs': wait_us, 'frameUs': frame_us, 'frame': result.get('frame')})

    def send(self, *ops, marked):
        """One request line under our own id (the bridge's own marking is tested apart); the decoded reply."""
        request_id = ('r' if marked else 'a') + uuid.uuid4().hex[:11]
        reply = self.link._exchange(request_id + '\t' + '\t'.join(ops), 15.0)
        self.assertEqual(reply['id'], request_id)
        return reply

    def read(self, marked):
        return self.send('rd %s 4' % hexint(self.t['buffer']), marked=marked)

    # ------------------------------------------------------------------ the speed-up
    def test_marked_reads_are_served_in_one_frame(self):
        replies = [self.read(marked=True) for _ in range(40)]
        self.assertEqual(len({r['frame'] for r in replies}), 1, [r['frame'] for r in replies])
        self.assertTrue(all(r['results'][0]['ok'] for r in replies))

    def test_an_unmarked_request_is_served_in_the_frame_of_the_reads_before_it(self):
        first = self.read(marked=True)
        last = self.read(marked=False)
        self.assertEqual(last['frame'], first['frame'])

    # ------------------------------------------------------------------ what stays as it was
    def test_unmarked_requests_each_wait_for_their_own_frame(self):
        frames = [self.read(marked=False)['frame'] for _ in range(8)]
        self.assertEqual(sorted(set(frames)), frames, frames)       # strictly rising: no two share a frame

    def test_a_frame_passes_after_every_unmarked_request(self):
        """A request that may change the game is the last one of its frame: what follows sees the next frame."""
        frames = []
        for _ in range(4):
            frames.append(self.read(marked=True)['frame'])
            frames.append(self.read(marked=False)['frame'])
        self.assertEqual(frames[0::2], frames[1::2], frames)        # each unmarked one joins the read before it
        self.assertEqual(sorted(set(frames[1::2])), frames[1::2], frames)   # and ends that frame

    def test_the_frame_budget_ends_a_chain(self):
        """A client that never stops reading cannot stop the frames: past the budget the frame goes on."""
        self.linger(LONG_WAIT_US, 1)          # 1 microsecond: used up by the first request
        frames = [self.read(marked=True)['frame'] for _ in range(6)]
        self.assertEqual(sorted(set(frames)), frames, frames)

    def test_frames_keep_running_under_a_stream_of_marked_reads(self):
        self.linger(2000, 8000)               # the defaults
        start = self.read(marked=True)['frame']
        deadline = time.monotonic() + 1.0
        count = 0
        while time.monotonic() < deadline:
            last = self.read(marked=True)['frame']
            count += 1
        # 8 ms of requests, then the frame and the host's 5 to 16 ms pause: about 40 frames a second here. Without
        # the budget this stream is one endless frame; 10 leaves room for a slow test machine.
        self.assertGreaterEqual(last - start, 10, 'frames %d..%d in one second of %d reads' % (start, last, count))
        self.assertGreater(count, 2 * (last - start), 'reads did not share frames: %d reads in %d frames' % (count, last - start))

    def test_zero_turns_it_off(self):
        self.linger(0, 0)
        frames = [self.read(marked=True)['frame'] for _ in range(6)]
        self.assertEqual(sorted(set(frames)), frames, frames)

    def test_a_marked_step_still_runs_its_frames(self):
        """step suspends its request for N frames: the wait for a next request must not swallow or delay it."""
        reply = self.send('info', 'step 3', 'info', marked=True)
        served = reply['results'][0]['frame']
        self.assertEqual((reply['results'][1]['stepped'], reply['results'][1]['frame']), (3, served + 3))
        self.assertEqual(reply['results'][2]['frame'], served + 3)
        self.assertTrue(self.link.ping()['held'])                   # a step ends held, as before

    def test_a_marked_hold_closes_the_gate(self):
        reply = self.send('hold on', marked=True)
        self.assertTrue(reply['results'][0]['held'])
        self.assertTrue(self.link.ping()['held'])
        frames = {self.read(marked=True)['frame'] for _ in range(5)} | {self.read(marked=False)['frame'] for _ in range(5)}
        self.assertEqual(len(frames), 1)                             # held: no frame runs, whatever the mark
        self.link.one('hold', 'off')
        before = self.link.info()['frame']
        time.sleep(0.2)
        self.assertGreater(self.link.info()['frame'], before)        # and the game runs again

    # ------------------------------------------------------------------ the setting
    def test_info_reports_the_setting(self):
        self.linger(1500, 6000)
        self.assertEqual(self.link.info().get('linger'), {'waitUs': 1500, 'frameUs': 6000})

    def test_bad_settings_are_refused(self):
        for args in (('zz', '10'), ('10',), (hexint(100_001), '10'), ('10', hexint(1_000_001)), ('1', '2', '3')):
            self.assertEqual(self.link.one('linger', *args).get('error'), 'bad_arguments', args)
        self.assertEqual(self.link.info().get('linger'), {'waitUs': LONG_WAIT_US, 'frameUs': LONG_FRAME_US})

    def test_linger_alone_reads_the_setting(self):
        result = self.link.one('linger')
        self.assertEqual((result['ok'], result['waitUs'], result['frameUs']), (True, LONG_WAIT_US, LONG_FRAME_US))


@needs_zig
class SameFrameDefaultsTests(unittest.TestCase):
    """A fresh game and a client that leaves: the defaults, and nothing a client set outlives it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = fnv_link.build(cls.tmp.name, zig_path(), with_fake_host=True)
        folder = Path(receipt['dll']).parent
        cls.pipe = 'fnvmcp-frame-' + uuid.uuid4().hex[:8]
        env = dict(os.environ, FNVMCP_PIPE=cls.pipe, FNVMCP_CONSOLE_LOG=str(Path(cls.tmp.name) / 'console.log'),
                   LOCALAPPDATA=str(Path(cls.tmp.name) / 'localappdata'))
        cls.host = subprocess.Popen([str(folder / 'fnvmcp_fake_host.exe'), receipt['dll'], '300'], stdout=subprocess.PIPE,
                                    env=env, text=True)
        cls.host.stdout.readline()

    @classmethod
    def tearDownClass(cls):
        cls.host.kill()
        cls.host.wait()
        cls.host.stdout.close()
        cls.tmp.cleanup()

    def test_defaults_and_reset_when_the_client_leaves(self):
        link = fnv_link.EngineLink(self.pipe).connect(wait=10)
        try:
            self.assertEqual(link.info().get('linger'), {'waitUs': 2000, 'frameUs': 8000})
            self.assertTrue(link.one('linger', hexint(50_000), hexint(500_000))['ok'])
            self.assertEqual(link.info().get('linger'), {'waitUs': 50_000, 'frameUs': 500_000})
        finally:
            link.close()
        link = fnv_link.EngineLink(self.pipe).connect(wait=10)
        try:
            deadline = time.monotonic() + 5.0           # the main thread resets it in its next frame
            while link.info().get('linger') != {'waitUs': 2000, 'frameUs': 8000} and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(link.info().get('linger'), {'waitUs': 2000, 'frameUs': 8000})
        finally:
            link.close()


if __name__ == '__main__':
    unittest.main()
