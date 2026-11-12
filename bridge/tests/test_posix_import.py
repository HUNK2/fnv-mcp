"""PLAN-3 S7: the server imports without the Windows-only libraries (macOS/Linux, the upstream Wine path).

The CI macOS and Linux jobs failed on every fnv-mcp push: fnv_procmem imported msvcrt at load, and nearly every fnv
module imports fnv_procmem, so server.py could not be imported off Windows. This test hides msvcrt and
ctypes.WinDLL/windll in a child process (and a stub fcntl) and imports every geck_mcp module.
"""
import subprocess
import sys
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]

CHILD = r'''
import sys, ctypes, importlib, pkgutil, traceback
# the standard library and third-party packages use msvcrt on Windows themselves: load them first
import subprocess, asyncio, multiprocessing.connection, getpass, mcp, mcp.server, anyio  # noqa: E401,F401
sys.modules['msvcrt'] = None            # then import msvcrt -> ImportError, as on posix
import types
sys.modules['fcntl'] = types.ModuleType('fcntl')
for name in ('WinDLL', 'windll', 'OleDLL', 'oledll'):
    if hasattr(ctypes, name):
        delattr(ctypes, name)
sys.path.insert(0, sys.argv[1])
import geck_mcp
bad = []
for mod in pkgutil.walk_packages(geck_mcp.__path__, 'geck_mcp.'):
    try:
        importlib.import_module(mod.name)
    except Exception as e:  # noqa: BLE001
        bad.append('%s: %s: %s' % (mod.name, type(e).__name__, e))
print('\n'.join(bad))
sys.exit(1 if bad else 0)
'''


class PosixImportTests(unittest.TestCase):
    def test_every_module_imports_without_windows_libraries(self):
        out = subprocess.run([sys.executable, '-c', CHILD, str(BRIDGE)], capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, 'modules that need Windows at import:\n' + out.stdout + out.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
