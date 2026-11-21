"""Keep the display on while a game this server launched runs (critique fix 2026-11-21).

Started while the monitors are asleep, the game deadlocks in its Direct3D device reset (KNOWN-ISSUES, "The game
hangs at startup when the monitors are asleep"). A display-on request does not wake monitors that are already off,
but it stops them going off in the middle of live work. SetThreadExecutionState holds only while the thread that
asked lives, so one daemon thread asks and waits; release() ends the request."""
import ctypes
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_lock = threading.Lock()
_state = {'thread': None, 'stop': None, 'ok': None}


def _hold(stop, started):
    ok = False
    try:
        set_state = ctypes.windll.kernel32.SetThreadExecutionState
        set_state.restype = ctypes.c_uint32
        set_state.argtypes = [ctypes.c_uint32]
        ok = set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) != 0
    except (AttributeError, OSError):
        set_state = None
    _state['ok'] = ok
    started.set()
    stop.wait()
    if set_state is not None:
        try:
            set_state(ES_CONTINUOUS)
        except OSError:
            pass


def hold():
    """Ask Windows to keep the display and the system on until release(). True when the request was accepted."""
    with _lock:
        if _state['thread'] is not None and _state['thread'].is_alive():
            return bool(_state['ok'])
        stop, started = threading.Event(), threading.Event()
        thread = threading.Thread(target=_hold, args=(stop, started), name='fnvmcp-display-on', daemon=True)
        _state.update(thread=thread, stop=stop, ok=None)
        thread.start()
    started.wait(2)
    return bool(_state['ok'])


def release():
    """End the request hold() made (nothing when none is held)."""
    with _lock:
        thread, stop = _state['thread'], _state['stop']
        _state.update(thread=None, stop=None, ok=None)
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(2)


def held():
    thread = _state['thread']
    return thread is not None and thread.is_alive()
