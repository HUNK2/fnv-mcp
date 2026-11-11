"""Game time for the live tools (Plan 2, W1): the clock pause, the frame gate, fixed frame steps and speed
(fnv_time_control), and fnv_wait_for, which blocks inside the game until a frame count, a predicate or the loaded
state is met. Design: D-time.md sections 2 and 3. Companion ops: docs/FNV-COMPANION-V2.md sections 2 and 3.

Addresses come from D-time.md section 1. Each timer function is looked up in the engine map and must be there with
the signature this module expects; otherwise the tool refuses (ENGINE_MAP_MISMATCH) instead of calling other code.
"""
import struct
import threading
import time

from . import fnv_link
from .fnv_conv import ConvError, dispatch, verdict
from .fnv_pred import compile_predicate

TIME_GLOBAL = 0x011F6394       # TimeGlobal: +0 disable counter, +4 fixed step (ms, 0 = off), +0x0C frame delta
FRAME_COUNTER = 0x011A2FE0     # +1 per frame
MULTIPLIER = 0x011AC3A0        # time multiplier now; the target is the next float
MULTIPLIER_TARGET = 0x011AC3A4
LOADING_FLAG = 0x011DA0C0      # 0 when no load runs
MENU_MODE = 0x011DEA2B         # 0 when no menu mode is set
PRED_ID = 63                   # predicate slot of fnv_wait_for; it is cleared after each wait
# One wait or gate action at a time: they share predicate slots 62 and 63 and the gate itself, and the MCP server
# can run tool calls in parallel.
GATE_LOCK = threading.RLock()
FRAME_SLOT = 62                # predicate slot that reads the companion's own frame counter
HOLD_DEFAULT_S = 120           # the gate's dead-man time (0x1D4C0 ms)
STEP_MAX = 10000               # 0x2710 frames
FPS_MAX = 1000
SPEED_MIN, SPEED_MAX = 0.05, 10.0
STATUS_WINDOW = 0.5            # seconds between the two frame readings of a real frame rate
ASSUMED_FPS = 60.0
LOADED_TEXT = '[0x011DA0C0]:u8 == 0 and [0x011DEA2B]:u8 == 0 and [0x011F6394+0x0C]:f32 > 0'
LOADED_ROUND_FRAMES = 600
FUNCTIONS = {
    # key: (address, text the signature must contain, text the name must contain or None)
    'disable': (0x00AA50A0, '(void)', None),        # BSTimer_Disable (the map has it unnamed)
    'enable': (0x00AA5040, '(void)', 'Enable'),     # BSTimer_Enable
    'clamp': (0x0086D4C0, '(unsigned int)', None),  # BSTimer_ClampAt(fps)
}


def w(value):
    return fnv_link.word(value)


def engine_functions(engine):
    """The three timer functions by address, each checked against the engine map."""
    out = {}
    for key, (address, signature, name) in FUNCTIONS.items():
        record = engine.functions.get(address) or {}
        text = record.get('signature') or ''
        if not record or '__thiscall' not in text or signature not in text or \
                (name and name not in (record.get('name') or '')):
            raise ConvError('ENGINE_MAP_MISMATCH', 'The engine map does not have the timer function %s at 0x%08X '
                            'with signature %s. Next: call fnv_setup_check; the engine map may be from another build.'
                            % (key, address, signature), {'address': '0x%08X' % address, 'found': text or None})
        out[key] = address
    return out


def _bytes(result, what):
    if not result.get('ok'):
        raise ConvError('MEMORY_UNREADABLE', 'Cannot read %s (%s). Next: call fnv_status.' % (what, result.get('error')),
                        result)
    return bytes.fromhex(result['hex'])


def _u32(result, what):
    return struct.unpack('<I', _bytes(result, what))[0]


def _f32(result, what):
    return struct.unpack('<f', _bytes(result, what))[0]


def frame_ops():
    """Two ops that read the companion's frame counter (the one predicates call `frame`), not the game's."""
    return [('pred', 'set', w(FRAME_SLOT), compile_predicate('frame')['hex']), ('pred', 'eval', w(FRAME_SLOT))]


def _frame(result):
    if 'top' not in result:
        raise ConvError('FRAME_UNREADABLE', 'The engine companion did not report its frame counter (%s). Next: call '
                        'fnv_status.' % result.get('error'), result)
    return int(result['top'])


def _refused(what, result):
    return ConvError('COMPANION_REFUSED', '%s was refused by the engine companion (%s). Next: call fnv_status; if the '
                     'companion is old, call fnv_companion_install.' % (what, result.get('error') or 'no reason given'),
                     result)


class TimeControl:
    """The clock pause, the frame gate, fixed frame steps and speed. The pause flag is this tool's own: it does not
    resume a pause that focus loss or another user holds."""

    def __init__(self, live, sleep=time.sleep, clock=time.monotonic):
        self.live = live
        self.sleep = sleep
        self.clock = clock
        self.pid = None
        self.configured = False
        self.paused_by_us = False

    # ------------------------------------------------------------ plumbing
    def batch(self, ops, timeout=None):
        return self.live.link.batch(ops, timeout=timeout)

    def require_game(self):
        """The game's pid. A new process resets this tool's flags and the gate configuration."""
        proc = self.live.game.process()
        if not proc:
            raise ConvError('NOT_RUNNING', 'Time control needs the game running.')
        if proc[0] != self.pid:
            self.pid, self.configured, self.paused_by_us = proc[0], False, False
        return proc[0]

    def functions(self):
        return engine_functions(self.live.engine())

    def ensure_gate(self):
        """gatecfg once per game process: the gate then pauses the clock while it is closed."""
        if self.configured:
            return
        fns = self.functions()
        result = self.batch([('gatecfg', w(fns['disable']), w(fns['enable']), w(TIME_GLOBAL))])[0]
        if not result.get('ok'):
            raise ConvError('GATE_UNSUPPORTED', 'The engine companion has no frame gate (%s). Next: call '
                            'fnv_companion_install, then fnv_launch.' % (result.get('error') or 'gatecfg refused'), result)
        self.configured = True

    def snapshot(self, measure=False):
        """One request: the gate, frame counter, clock counter, fixed step and multipliers. With measure, the real
        frame rate from a second reading after STATUS_WINDOW seconds (None while the gate is held)."""
        results = self.batch([('gate',), ('rd', w(FRAME_COUNTER), '4'), ('rd', w(TIME_GLOBAL), '4'),
                              ('rd', w(TIME_GLOBAL + 4), '4'), ('rd', w(MULTIPLIER), '4'),
                              ('rd', w(MULTIPLIER_TARGET), '4')])
        gate = results[0]
        if 'held' not in gate:
            raise ConvError('GATE_UNSUPPORTED', 'The engine companion does not report the frame gate (%s). Next: call '
                            'fnv_companion_install, then fnv_launch.' % (gate.get('error') or 'gate refused'), gate)
        held = bool(gate.get('held'))
        frame_a = _u32(results[1], 'the frame counter')
        real_fps = None
        if measure and not held:
            t_a = self.clock()
            self.sleep(STATUS_WINDOW)
            frame_b = _u32(self.batch([('rd', w(FRAME_COUNTER), '4')])[0], 'the frame counter')
            window = self.clock() - t_a
            if window > 0:
                real_fps = round((frame_b - frame_a) / window, 2)
        step_ms = _f32(results[3], 'the fixed step')   # TimeGlobal+4 is a float (ms per frame; 0 = off)
        step_ms = round(step_ms, 4) if step_ms and step_ms > 0 else None
        return {'gate': gate, 'held': held, 'frame': frame_a,
                'clockCounter': _u32(results[2], 'the clock counter'),
                'fixedStepMs': step_ms, 'fixedFps': round(1000.0 / step_ms, 3) if step_ms else None,
                'multiplier': _f32(results[4], 'the multiplier'),
                'targetMultiplier': _f32(results[5], 'the target multiplier'), 'realFps': real_fps}

    # ------------------------------------------------------------ actions
    def pause(self):
        self.require_game()
        if self.paused_by_us:
            return verdict('The clock is already paused by this tool', 'Nothing changed.', changed=False,
                           pausedByUs=True)
        fns = self.functions()
        before, call, after = self.batch([('rd', w(TIME_GLOBAL), '4'),
                                          ('call', w(fns['disable']), 'v', w(TIME_GLOBAL), '0'),
                                          ('rd', w(TIME_GLOBAL), '4')])
        if not call.get('ok'):
            raise _refused('The clock pause', call)
        self.paused_by_us = True
        counter_before, counter_after = _u32(before, 'the clock counter'), _u32(after, 'the clock counter')
        return verdict('The game clock is paused',
                       'The world stops and keeps rendering, so the camera and photo tools see a still scene. '
                       'Clock counter %d -> %d.' % (counter_before, counter_after),
                       changed=True, pausedByUs=True, clockCounterBefore=counter_before,
                       clockCounter=counter_after,
                       next='Call fnv_time_control resume to start the clock again.')

    def resume(self):
        self.require_game()
        if not self.paused_by_us:
            return verdict('The clock is not paused by this tool', 'Nothing changed. A focus loss or another user may '
                           'hold the counter; this tool leaves it alone.', changed=False, pausedByUs=False)
        fns = self.functions()
        call, after = self.batch([('call', w(fns['enable']), 'v', w(TIME_GLOBAL), '0'),
                                  ('rd', w(TIME_GLOBAL), '4')])
        if not call.get('ok'):
            raise _refused('The clock resume', call)
        self.paused_by_us = False
        counter = _u32(after, 'the clock counter')
        reason = 'Clock counter now %d.' % counter + (' Something else still holds the clock (focus loss or another '
                                                    'user), so it stays stopped.' if counter else '')
        return verdict('The game clock runs again', reason, changed=True, pausedByUs=False, clockCounter=counter)

    def hold(self, timeout_seconds=None):
        self.require_game()
        self.ensure_gate()
        seconds = timeout_seconds or HOLD_DEFAULT_S
        before, after = self.batch([('gate',), ('hold', 'on', w(seconds * 1000))])
        if after.get('held') is not True:
            raise _refused('Closing the frame gate', after)
        return verdict('The frame gate is closed',
                       'Nothing runs between frames until release. Requests are still served. If nothing calls or '
                       'pings for %d s, the gate opens by itself (the dead-man switch).' % seconds,
                       changed=not before.get('held'), held=True, timeoutSeconds=seconds,
                       next='Call fnv_time_control release to let the game run.')

    def release(self):
        self.require_game()
        before, after = self.batch([('gate',), ('hold', 'off')])
        return verdict('The frame gate is open' if after.get('held') is False else 'The frame gate state is unknown',
                       'The game runs again.' if before.get('held') else 'The gate was already open; nothing changed.',
                       changed=bool(before.get('held')), held=bool(after.get('held')))

    def step(self, frames, fps=None):
        if not 1 <= frames <= STEP_MAX:
            raise ValueError('frames must be from 1 to %d' % STEP_MAX)
        if fps is not None and not 0 <= fps <= FPS_MAX:
            raise ValueError('fps must be from 0 (off) to %d' % FPS_MAX)
        self.require_game()
        self.ensure_gate()
        fns = self.functions()
        ops = frame_ops()
        if fps:
            ops.append(('call', w(fns['clamp']), 'v', w(TIME_GLOBAL), '0', w(fps)))
        ops += [('step', w(frames)), ('gate',)]
        results = self.batch(ops)
        before = _frame(results[1])
        if fps and not results[2].get('ok'):
            raise _refused('Setting the fixed frame step', results[2])
        step, gate = results[-2], results[-1]
        if step.get('error') == 'stopped':
            raise ConvError('STOPPED', 'A debugger stop parked the main thread after %s of %d frames (frame %s). '
                            'Next: fnv_debug status, then continue.' % (step.get('stepped', 0), frames,
                                                                        step.get('frame')), step)
        if step.get('stepped') != frames:
            raise ConvError('STEP_REFUSED', 'The engine companion stepped %s frames, not %d (%s). Next: call fnv_status.'
                            % (step.get('stepped'), frames, step.get('error')), step)
        after = step.get('frame')
        return verdict('Stepped %d frames to frame %d' % (frames, after),
                       'The game is held at that frame: nothing runs between frames until release.',
                       changed=True, frames=frames, frameBefore=before, frameAfter=after, advanced=after - before,
                       fixedFps=fps or None, held=bool(gate.get('held')),
                       next='Call fnv_time_control release to let it run, or step again.')

    def fixed(self, fps):
        if not 0 <= fps <= FPS_MAX:
            raise ValueError('fps must be from 0 (off) to %d' % FPS_MAX)
        self.require_game()
        fns = self.functions()
        call, step_now = self.batch([('call', w(fns['clamp']), 'v', w(TIME_GLOBAL), '0', w(fps)),
                                     ('rd', w(TIME_GLOBAL + 4), '4')])
        if not call.get('ok'):
            raise _refused('Setting the fixed frame step', call)
        step_ms = _f32(step_now, 'the fixed step')   # a float: ms per frame
        step_ms = round(step_ms, 4) if step_ms and step_ms > 0 else None
        if fps:
            return verdict('Fixed frame step: %d fps' % fps,
                           'Every frame takes %s ms of game time, so physics and timers repeat exactly.'
                           % (step_ms or '?'), changed=True, fixedFps=fps, fixedStepMs=step_ms)
        return verdict('The fixed frame step is off', 'Frames take real time again.', changed=True,
                       fixedFps=None, fixedStepMs=step_ms)

    def speed(self, x):
        if not SPEED_MIN <= x <= SPEED_MAX:
            raise ValueError('x must be from %g to %g' % (SPEED_MIN, SPEED_MAX))
        self.require_game()
        packed = struct.pack('<f', x).hex()
        before = self.batch([('rd', w(MULTIPLIER), '4'), ('rd', w(MULTIPLIER_TARGET), '4'),
                             ('wr', w(MULTIPLIER), packed), ('wr', w(MULTIPLIER_TARGET), packed)])
        previous = _f32(before[0], 'the multiplier')
        for result, what in ((before[2], 'the multiplier'), (before[3], 'the target multiplier')):
            if not result.get('ok'):
                raise ConvError('MEMORY_UNWRITABLE', 'Cannot write %s (%s). Next: call fnv_status.'
                                % (what, result.get('error')), result)
        return verdict('Time multiplier set to %g' % x,
                       'Both multiplier floats are written, so the change does not ramp. Above about 3x, physics '
                       'drops sub-steps.', changed=True, multiplier=x, previous=round(previous, 6))

    def status(self):
        self.require_game()
        snap = self.snapshot(measure=True)
        state = 'held' if snap['held'] else 'running'
        reasons = ['clock counter %d%s' % (snap['clockCounter'],
                                           ', paused by this tool' if self.paused_by_us else '')]
        reasons.append('fixed step %s fps' % ('%g' % snap['fixedFps'] if snap['fixedFps'] else 'off'))
        reasons.append('multiplier %g' % snap['multiplier'])
        if snap['realFps'] is not None:
            reasons.append('%g real fps' % snap['realFps'])
        elif snap['held']:
            reasons.append('no real fps: the gate is held')
        return verdict('The game is %s at frame %d' % (state, snap['frame']), ', '.join(reasons) + '.',
                       held=snap['held'], pausedByUs=self.paused_by_us, clockCounter=snap['clockCounter'],
                       fixedStepMs=snap['fixedStepMs'] or None, fixedFps=snap['fixedFps'],
                       multiplier=snap['multiplier'], targetMultiplier=snap['targetMultiplier'],
                       frame=snap['frame'], realFps=snap['realFps'], gate=snap['gate'], configured=self.configured,
                       next='Call fnv_time_control release to let the game run.' if snap['held'] else None)

    def run(self, *args, **kwargs):
        with GATE_LOCK:
            return self._run(*args, **kwargs)

    def _run(self, action, frames=None, fps=None, x=None, timeout_seconds=None):
        def need(value, message):
            if value is None:
                raise ValueError(message)
            return value
        return dispatch(action, {
            'pause': self.pause, 'resume': self.resume,
            'hold': lambda: self.hold(timeout_seconds),
            'release': self.release,
            'step': lambda: self.step(need(frames, 'step needs frames=N (1 to %d)' % STEP_MAX), fps),
            'fixed': lambda: self.fixed(need(fps, 'fixed needs fps=F, or fps=0 to turn the fixed step off')),
            'speed': lambda: self.speed(need(x, 'speed needs x=F (0.05 to 10)')),
            'status': self.status})


class WaitFor:
    """fnv_wait_for: one request holds the game's predicate and the frame wait. `until` runs frames even while the
    gate is open, so the wait works either way; when the gate was open before, it is opened again at the stop."""

    def __init__(self, control):
        self.control = control

    def run(self, *args, **kwargs):
        with GATE_LOCK:
            return self._run(*args, **kwargs)

    def _run(self, frames=None, seconds=None, clock='game', predicate=None, loaded=False, timeout_seconds=60,
            max_frames=3600):
        given = [name for name, on in (('frames', frames is not None), ('seconds', seconds is not None),
                                       ('predicate', predicate is not None), ('loaded', bool(loaded))) if on]
        if len(given) != 1:
            raise ValueError('Give exactly one of frames, seconds, predicate or loaded (got %s)'
                             % (', '.join(given) or 'none'))
        if clock not in ('game', 'real'):
            raise ValueError('clock must be game or real')
        if frames is not None and not 1 <= frames <= STEP_MAX:
            raise ValueError('frames must be from 1 to %d' % STEP_MAX)
        if not 1 <= max_frames <= STEP_MAX:
            raise ValueError('max_frames must be from 1 to %d' % STEP_MAX)
        self.control.require_game()
        self.control.ensure_gate()
        timeout = float(timeout_seconds)
        if loaded:
            return self.loaded(timeout)
        if frames is not None:
            snap = self.control.snapshot()
            frame0 = self.base_frame(timeout)
            text, limit = 'frame >= %d' % (frame0 + frames), frames + 2
            extra = {'framesRequested': frames}
        elif seconds is not None:
            snap = self.control.snapshot(measure=True)
            fps, source = self.rate(snap)
            multiplier = snap['multiplier'] if clock == 'game' else 1.0
            if multiplier <= 0:
                raise ConvError('BAD_TIMESCALE', 'The time multiplier is %g, so game seconds do not pass. Next: call '
                                'fnv_time_control speed with x=1.' % multiplier, {'multiplier': multiplier})
            estimate = max(1, int(round(seconds * fps / multiplier)))
            frame0 = self.base_frame(timeout)
            text, limit = 'frame >= %d' % (frame0 + estimate), estimate + 2
            extra = {'secondsRequested': seconds, 'clock': clock, 'estimatedFrames': estimate,
                     'rateFps': round(fps, 3), 'rateSource': source}
        else:
            snap = self.control.snapshot()
            text, limit = predicate, max_frames
            extra = {}
        res = self.until(text, limit, snap['held'], timeout)
        if not res['met']:
            raise ConvError('WAIT_TIMEOUT', 'The condition was still false after %d frames (%g s). Next: call '
                            'fnv_time_control status, then raise max_frames or timeout_seconds.'
                            % (res['frames'], timeout), {'predicate': text, 'frame': res['frame']})
        return verdict('Condition met at frame %d after %d frames' % (res['frame'], res['frames']),
                       'The predicate was true at the start of that frame. The gate is %s again.'
                       % ('held' if snap['held'] else 'open'),
                       met=True, frames=res['frames'], frame=res['frame'], predicate=text,
                       predicateOps=res['predicateOps'], heldBefore=snap['held'], **extra)

    def base_frame(self, timeout):
        """Close the gate and read the frame in one request. While the gate is closed nothing runs, so the base is
        exact; read while the game runs, it can be a few frames stale and the wait would end early."""
        results = self.control.batch([('hold', 'on', w(int(max(timeout, 1.0) * 1000)))] + frame_ops())
        if results[0].get('held') is not True:
            raise _refused('Closing the frame gate', results[0])
        return _frame(results[2])

    @staticmethod
    def rate(snap):
        """(frames per real second, where the number came from)."""
        if snap['fixedFps']:
            return snap['fixedFps'], 'fixed step'
        if snap['realFps']:
            return snap['realFps'], 'measured'
        return ASSUMED_FPS, 'assumed %d (the gate is held, so frames could not be measured)' % ASSUMED_FPS

    def until(self, text, limit, held_before, timeout, eval_cell=False):
        """One request: the predicate into slot PRED_ID, until it holds or limit frames pass, then (optionally) the
        player's parent cell, then the gate reopened when it was open before; the slot is cleared."""
        compiled = compile_predicate(text)
        ops = [('pred', 'set', w(PRED_ID), compiled['hex']), ('until', w(PRED_ID), w(limit))]
        if eval_cell:
            ops.append(('eval', fnv_link.hexs('player.GetParentCell'), w(0)))
        if not held_before:
            ops.append(('hold', 'off'))
        ops.append(('pred', 'clear', w(PRED_ID)))
        results = self.control.batch(ops, timeout=timeout + 5)
        if not results[0].get('ok'):
            raise ConvError('PREDICATE_REFUSED', 'The engine companion refused the predicate (%s). Next: call '
                            'fnv_status.' % results[0].get('error'), results[0])
        until = results[1]
        if until.get('error') == 'stopped':
            raise ConvError('STOPPED', 'A debugger stop parked the main thread at frame %s, %s frames into the wait: no '
                            'frame runs while it is stopped. Next: fnv_debug status, then continue (or clear the '
                            'break).' % (until.get('frame'), until.get('frames', 0)), until)
        if 'met' not in until:
            raise ConvError('WAIT_FAILED', 'The engine companion did not run the wait (%s). Next: call fnv_status.'
                            % until.get('error'), until)
        cell = fnv_link.value_of(results[2]) if eval_cell else None
        return {'met': bool(until['met']), 'frames': until.get('frames'), 'frame': until.get('frame'), 'cell': cell,
                'predicateOps': compiled['ops']}

    def loaded(self, timeout):
        """The game is loaded and simulating: the loading flag and menu mode are clear, the frame delta is above 0,
        the frame counter advanced by 2 and the player has a parent cell. The 3D check is not made yet."""
        snap = self.control.snapshot()
        base = self.base_frame(timeout)
        deadline = self.control.clock() + timeout
        rounds = 0
        while True:
            rounds += 1
            remaining = max(1.0, deadline - self.control.clock())
            res = self.until(LOADED_TEXT + ' and frame >= %d' % (base + 2), LOADED_ROUND_FRAMES, snap['held'],
                             remaining, eval_cell=True)
            cell = res['cell']
            if res['met'] and cell not in (None, '', '00000000', 0):
                return verdict('The game is loaded and simulating at frame %d' % res['frame'],
                               'Loading flag and menu mode are clear, the frame delta is above 0, two frames ran, and '
                               'the player has a parent cell (%s).' % cell,
                               met=True, frame=res['frame'], frames=res['frames'], cell=cell, rounds=rounds,
                               heldBefore=snap['held'])
            if self.control.clock() >= deadline:
                raise ConvError('WAIT_TIMEOUT', 'The game was not loaded and simulating after %g s (%d rounds). Next: '
                                'call fnv_status to see the phase.' % (timeout, rounds),
                                {'frame': res['frame'], 'cell': cell})
            base = res['frame'] if res['frame'] is not None else base
