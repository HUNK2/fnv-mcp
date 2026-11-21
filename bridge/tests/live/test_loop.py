"""Live loop suite (critique fix Task 5, 2026-11-21): the loop real use is made of, in the game.

4,228 real calls were mostly five tools: fnv_native, fnv_func, fnv_input, fnv_console, fnv_mem_read. Each test here
checks one step of that loop and one fact about its answer. Names carry a number so they run in order: the native
test restarts the game in the middle, and the tests after it need a game again."""
import os
import time
from pathlib import Path

from .base import LiveCase

START = os.environ.get('FNV_LIVE_START', 'coc:GoodspringsSource')

MODULE = r'''#include "fnvmcp_module.h"
#include "liveloop_value.h"

static const FnvmcpApi *api;

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) {
    api = a;
    api->log("liveloop init");
    return 0;
}

FNVMCP_EXPORT int answer(void) {
    api->log("liveloop answer %d", LIVELOOP_VALUE);
    return LIVELOOP_VALUE;
}
'''


def position(data):
    p = data.get('position') or data.get('pos')
    return (p['x'], p['y'], p['z']) if isinstance(p, dict) else tuple(p[:3])


class LoopTests(LiveCase):
    def _module(self, value):
        folder = Path(self.session.project.state_dir) / 'fnv' / 'live-loop'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'liveloop.c').write_text(MODULE, encoding='utf-8')
        (folder / 'liveloop_value.h').write_text('#define LIVELOOP_VALUE %d\n' % value, encoding='utf-8')
        return str(folder / 'liveloop.c')

    def _texts(self, lines):
        return [line['text'] for line in lines]

    def test_10_native_loop_survives_a_game_restart(self):
        self.call('fnv_native', action='unload', name='liveloop')      # a module left by an earlier run
        source = self._module(42)
        built = self.ok('fnv_native', action='build', source=source, name='liveloop')
        loaded = self.ok('fnv_native', action='load', name='liveloop')
        self.assertEqual(self._texts(loaded['log']), ['liveloop init'], 'load returned old log lines')
        called = self.ok('fnv_native', action='call', name='liveloop', export='answer')
        self.assertEqual(called['value'], 42)
        self.assertEqual(self._texts(called['log']), ['liveloop answer 42'], 'call returned old log lines')
        later = self.ok('fnv_native', action='log', since=called['logNext'] - 1)
        self.assertEqual(self._texts(later['lines']), ['liveloop answer 42'])

        # An edit of an included header gives a new build, and reload swaps it in.
        self._module(43)
        reloaded = self.ok('fnv_native', action='reload', name='liveloop', source=source)
        self.assertTrue(reloaded['rebuilt'])
        self.assertNotEqual(Path(self.ok('fnv_native', action='build', source=source, name='liveloop')['dll']).name,
                            Path(built['dll']).name, 'the edited header did not change the DLL name')
        self.assertEqual(self.ok('fnv_native', action='call', name='liveloop', export='answer')['value'], 43)

        # The game restarts: the module is gone with it, and the same name loads again (no rename, no ALREADY_LOADED).
        self.ok('fnv_game', action='restart', start=START)
        self.ok('fnv_status')
        self.assertEqual(self.code('fnv_native', action='call', name='liveloop', export='answer'), 'NOT_LOADED')
        again = self.ok('fnv_native', action='load', name='liveloop')
        self.assertEqual(again['id'], 1, 'module ids start at 1 in a new game process')
        self.assertEqual(self.ok('fnv_native', action='call', name='liveloop', export='answer')['value'], 43)
        listed = self.ok('fnv_native', action='list')
        row = next(r for r in listed['built'] if r['name'] == 'liveloop')
        self.assertGreaterEqual(row['builds'], 2)
        self.ok('fnv_native', action='unload', name='liveloop')
        self.assertEqual(self.code('fnv_native', action='unload', name='liveloop'), 'NOT_LOADED')

    def test_20_func_code_pages(self):
        first = self.ok('fnv_func', action='code', query='0x004572E0', max_lines=10)
        self.assertTrue(first['truncated'])
        self.assertEqual(first['next'], 10)
        self.assertIn('offset=10', first['truncatedNote'])
        rest = self.ok('fnv_func', action='code', query='0x004572E0', max_lines=400, offset=10)
        self.assertEqual(rest['firstLine'], first['firstLine'] + 10)

    def test_30_console(self):
        out = self.ok('fnv_console', line='GetPos Z', ref='player')
        self.assertTrue(out)

    def test_40_mem_read_player(self):
        out = self.ok('fnv_mem_read', target='player', path='m_pBaseProcess')
        self.assertTrue(out)

    def test_50_wait_for_frames(self):
        out = self.ok('fnv_wait_for', frames=10, timeout=30)
        self.assertTrue(out)

    def test_60_input_tap_and_holds(self):
        self.ok('fnv_input', action='tap', control='activate')
        before = position(self.ok('fnv_player'))
        self.ok('fnv_input', action='hold', control='forward', duration_ms=500)
        self.ok('fnv_wait_for', frames=5, timeout=30)
        moved = position(self.ok('fnv_player'))
        self.assertGreater(abs(moved[0] - before[0]) + abs(moved[1] - before[1]), 5, 'duration hold did not move')
        self.ok('fnv_input', action='hold', control='forward', frames=30)
        after = position(self.ok('fnv_player'))
        self.assertGreater(abs(after[0] - moved[0]) + abs(after[1] - moved[1]), 5, 'frames hold did not move')

    def test_70_photo_label_rects_inside_the_image(self):
        out = self.ok('fnv_photo', action='view', label=True, size=960)
        from PIL import Image
        width, height = Image.open(out['path']).size
        self.assertEqual(out.get('rectSpace'), 'image')
        for label in out.get('labels') or []:
            x0, y0, x1, y1 = label['rect']
            self.assertTrue(-1 <= x0 <= x1 <= width + 1 and -1 <= y0 <= y1 <= height + 1, (label, width, height))

    def test_80_logs_every_source(self):
        envelope = self.call('fnv_logs', sources=['nvse', 'console', 'crash', 'jip', 'mo2', 'error', 'havok'])
        self.assertTrue(envelope.get('ok'), envelope.get('error'))

    def test_85_ground_height(self):
        x, y, z = position(self.ok('fnv_player'))
        here = self.ok('fnv_raycast', ground_at={'x': x, 'y': y})
        self.assertTrue(here['found'], here)
        self.assertLessEqual(here['groundHeight'], z + 5, here)
        self.assertGreater(here['groundHeight'], z - 150, (here, z))
        there = self.ok('fnv_raycast', ground_at={'x': x + 2000, 'y': y + 2000})
        self.assertNotEqual(round(there.get('groundHeight') or 0, 1), round(here['groundHeight'], 1), (here, there))

    def test_90_status_names_this_checkout(self):
        data = self.ok('fnv_status')
        bridge = Path(__file__).resolve().parents[2]
        self.assertEqual(Path(data['server']['path']), bridge)
        self.assertTrue(data['running'])

    def test_95_time(self):
        start = time.monotonic()
        self.ok('fnv_wait_for', frames=30, timeout=30)
        self.assertLess(time.monotonic() - start, 30)
