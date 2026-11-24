"""Live proof of tool gap 7 (2026-11-24): input as a person gives it.

- A frame-exact hold of the forward control is seen by the engine's GetControlState (0x00A24660, which reads the key
  bytes, as scripts' IsControlPressed does) while the hold runs; before this gap only the forced state was set.
- A tap of always_run toggles it: the player covers a different distance in the same frames of forward after the tap.
- fnv_window background keeps the pause menu shut when the window procedure gets WM_ACTIVATE inactive (the message a
  focus loss sends, posted here so the test does not depend on which window Windows lets come to the front), and the
  frames go on; without it the same message opens the pause menu. foreground unloads the module.

Run alone: tools/fnv_live_regress.py --suite input."""
import asyncio
import ctypes
import math
import time
from ctypes import wintypes

from .base import LiveCase

GET_CONTROL_STATE = '0x00A24660'
OS_INPUT_GLOBALS = '0x011F35CC'
WM_ACTIVATE = 6


def game_window():
    from fnv_mcp.fnv_game import game_processes
    pids = {p[0] for p in game_processes()}
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


class InputLive(LiveCase):
    def unpause(self):
        state = self.ok('fnv_window', action='unpause')['state']
        self.assertFalse(state['pauseMenuOpen'], state)

    def frame(self):
        return self.ok('fnv_status')['companion']['frame']

    def control_state(self, control):
        globals_ptr = self.ok('fnv_mem_read', target=OS_INPUT_GLOBALS, as_type='u32')['value']
        out = self.ok('fnv_engine_call', function=GET_CONTROL_STATE, this='0x%08X' % int(globals_ptr),
                      args=[{'type': 'int', 'value': control}, {'type': 'int', 'value': 0}], returns='int',
                      convention='thiscall', allow_unproven=True)
        return int(out['result'])

    def player_xy(self):
        p = self.ok('fnv_ref', ref='player')['position']
        return (p['x'], p['y']) if isinstance(p, dict) else (p[0], p[1])

    def walk(self, frames):
        """Distance covered holding forward for frames, from the same spot each time: a coc to GoodspringsSource
        (live run 2026-11-24: after the physics suite the player stood against a rock and covered 0 units)."""
        self.ok('fnv_console', line='coc GoodspringsSource')
        self.ok('fnv_wait_for', seconds=4, clock='real')
        self.unpause()
        before = self.player_xy()
        self.ok('fnv_input', action='hold', control='forward', frames=frames)
        after = self.player_xy()
        return math.hypot(after[0] - before[0], after[1] - before[1])

    def test_10_a_frame_hold_is_seen_by_get_control_state(self):
        self.unpause()
        self.assertEqual(self.control_state(0), 0, 'forward reads held before any input')
        session = self.session
        hold = asyncio.run_coroutine_threadsafe(session.client.call_tool(
            'fnv_input', {'action': 'hold', 'control': 'forward', 'frames': 240}), session.loop)
        seen = []
        deadline = time.time() + 6
        while time.time() < deadline and not hold.done():
            seen.append(self.control_state(0))
            if seen[-1] > 0:
                break
        envelope = hold.result(120).structured_content
        self.assertTrue(envelope.get('ok'), envelope.get('error'))
        self.assertIsNotNone(envelope['data'].get('boundKey'), envelope['data'])
        self.assertTrue(any(s > 0 for s in seen), 'GetControlState never saw the held forward control: %s' % seen)
        self.assertEqual(self.control_state(0), 0, 'forward still reads held after the hold ended')

    def test_20_an_always_run_tap_toggles_it(self):
        first = self.walk(40)
        self.ok('fnv_input', action='tap', control='always_run', frames=2)
        try:
            second = self.walk(40)
        finally:
            self.ok('fnv_input', action='tap', control='always_run', frames=2)     # back as it was
        self.assertGreater(min(first, second), 20, 'the player did not walk: %.0f, %.0f units' % (first, second))
        ratio = max(first, second) / min(first, second)
        self.assertGreater(ratio, 1.3, 'the always_run tap changed nothing: %.0f then %.0f units in 40 frames'
                           % (first, second))

    def test_30_background_keeps_the_pause_menu_shut(self):
        hwnd = game_window()
        self.assertTrue(hwnd, 'no game window')
        user32 = ctypes.windll.user32
        self.ok('fnv_window', action='foreground')
        self.unpause()
        user32.PostMessageW(hwnd, WM_ACTIVATE, 0, 0)            # WA_INACTIVE: what a focus loss sends
        self.ok('fnv_wait_for', seconds=1, clock='real')
        state = self.ok('fnv_window', action='status')['state']
        self.assertTrue(state['pauseMenuOpen'], 'without background the message opened no pause menu: %s' % state)
        on = self.ok('fnv_window', action='background')
        self.assertIn('loaded', on['noPause'], on)
        self.assertFalse(on['state']['pauseMenuOpen'], on)       # background closes an open one
        self.assertTrue(on['state']['background'], on)
        try:
            before = self.frame()
            user32.PostMessageW(hwnd, WM_ACTIVATE, 0, 0)
            self.ok('fnv_wait_for', seconds=1, clock='real')
            state = self.ok('fnv_window', action='status')['state']
            self.assertFalse(state['pauseMenuOpen'], 'the pause menu opened with background on: %s' % state)
            self.assertGreater(self.frame() - before, 10, 'the frames stopped with background on')
        finally:
            off = self.ok('fnv_window', action='foreground')
            self.assertIn('unloaded', off.get('noPause', ''), off)
            self.assertFalse(off['state']['background'], off)
