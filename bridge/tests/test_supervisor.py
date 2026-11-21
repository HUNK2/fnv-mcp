"""The stdio supervisor (fnv_mcp/supervisor.py): code reloads reach the client without a reconnect.

A small fake server reads its tool name from name.txt; the tests change that file and check that the client is
told (handshake era: notifications/tools/list_changed; 2026-07-28 era: a subscriptions/listen event) and then
lists the new tool. Also: a crashing server fails only the call in flight, and a broken edit keeps the old server.
"""
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]

FAKE_SERVER = textwrap.dedent('''
    import os
    from pathlib import Path
    from mcp.server import MCPServer
    name = (Path(__file__).parent / 'name.txt').read_text().strip()
    server = MCPServer('fake')

    @server.tool(name=name)
    def named() -> str:
        return name

    @server.tool(name='crash')
    def crash() -> str:
        os._exit(3)

    server.run(transport='stdio')
''')

LAUNCHER = textwrap.dedent('''
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location('geck_supervisor', {supervisor!r})
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.Supervisor([sys.executable, {server!r}], [{watch!r}], poll=0.1, settle=0.2).run()
''')


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / 'srv'
        self.dir.mkdir()
        (self.dir / 'server.py').write_text(FAKE_SERVER)
        (self.dir / 'name.txt').write_text('first_tool')
        launcher = Path(self.tmp.name) / 'launch.py'
        launcher.write_text(LAUNCHER.format(supervisor=str(ROOT / 'fnv_mcp' / 'supervisor.py'),
                                            server=str(self.dir / 'server.py'), watch=str(self.dir)))
        self.params = StdioServerParameters(command=sys.executable, args=[str(launcher)], cwd=str(ROOT))

    def tearDown(self):
        for _ in range(20):                       # child processes may hold the folder for a moment on Windows
            try:
                self.tmp.cleanup()
                return
            except OSError:
                time.sleep(0.25)

    async def names(self, client):
        listing = await client.list_tools()
        tools = listing.tools if hasattr(listing, 'tools') else listing
        return {tool.name for tool in tools}

    async def test_handshake_client_is_told_and_lists_new_tool(self):
        changed = anyio.Event()
        seen = []

        async def handler(message):
            if getattr(getattr(message, 'root', message), 'method', None) == 'notifications/tools/list_changed':
                seen.append(message)
                if len(seen) >= 2:            # the first comes after initialized (a respawned client refetches)
                    changed.set()

        async with Client(self.params, mode='legacy', message_handler=handler) as client:
            self.assertIn('first_tool', await self.names(client))
            (self.dir / 'name.txt').write_text('second_tool')
            with anyio.fail_after(30):
                await changed.wait()
            names = await self.names(client)
            self.assertIn('second_tool', names)
            self.assertNotIn('first_tool', names)
            result = await client.call_tool('second_tool', {})
            self.assertFalse(result.is_error, result)

    async def test_modern_client_gets_listen_event(self):
        async with Client(self.params) as client:
            self.assertIn('first_tool', await self.names(client))
            async with client.listen(tools_list_changed=True) as sub:
                (self.dir / 'name.txt').write_text('third_tool')
                with anyio.fail_after(30):
                    async for _event in sub:
                        break
            self.assertIn('third_tool', await self.names(client))
            result = await client.call_tool('third_tool', {})
            self.assertFalse(result.is_error, result)

    async def test_crash_fails_only_the_call_in_flight(self):
        async with Client(self.params, mode='legacy') as client:
            with self.assertRaises(Exception) as caught:
                await client.call_tool('crash', {})
            self.assertIn('exited during this call', str(caught.exception))
            result = await client.call_tool('first_tool', {})
            self.assertFalse(result.is_error, result)

    async def test_broken_edit_keeps_old_server(self):
        async with Client(self.params, mode='legacy') as client:
            source = (self.dir / 'server.py').read_text()
            (self.dir / 'server.py').write_text(source + '\ndef broken(:\n')
            await anyio.sleep(2.0)            # the reload is tried and refused
            result = await client.call_tool('first_tool', {})
            self.assertFalse(result.is_error, result)
            (self.dir / 'server.py').write_text(source)
            (self.dir / 'name.txt').write_text('fixed_tool')
            with anyio.fail_after(30):
                while 'fixed_tool' not in await self.names(client):
                    await anyio.sleep(0.2)


# A JSON-lines child whose tools/call runs WORK seconds inside busy.track (as an act call does in the real server)
# in a non-daemon thread, like a shielded call that keeps running after the client cancels it. On end of input it
# waits for its work, then exits 0.
BUSY_CHILD = textwrap.dedent('''
    import json, os, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from fnv_mcp import busy
    here = Path(__file__).parent
    WORK = float(os.environ.get('WORK', '3'))
    workers = []
    def work(rid):
        with busy.track('long_act'):
            time.sleep(WORK)
            (here / 'done.txt').write_text(str(time.time()))
        sys.stdout.write(json.dumps({{'jsonrpc': '2.0', 'id': rid, 'result': {{'content': []}}}}) + '\\n')
        sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line)
        if m.get('method') == 'initialize':
            sys.stdout.write(json.dumps({{'jsonrpc': '2.0', 'id': m['id'],
                                          'result': {{'capabilities': {{'tools': {{}}}}}}}}) + '\\n')
            sys.stdout.flush()
        elif m.get('method') == 'tools/call':
            t = threading.Thread(target=work, args=(m['id'],))
            t.start()
            workers.append(t)
    for t in workers:
        t.join()
''')


class BusyReloadTests(unittest.TestCase):                    # R4
    def setUp(self):
        import importlib.util
        import io
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / 'child.py').write_text(BUSY_CHILD.format(root=str(ROOT)))
        self.watched = self.dir / 'watched'
        self.watched.mkdir()
        (self.watched / 'code.py').write_text('v = 1\n')
        spec = importlib.util.spec_from_file_location('geck_supervisor_busy', ROOT / 'fnv_mcp' / 'supervisor.py')
        self.sup_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sup_mod)
        self.sup_mod.LOG_FILE = self.dir / 'supervisor.log'

        class Out(io.RawIOBase):
            def write(self, b):
                return len(b)
        self.Out = Out

    def tearDown(self):
        self.sup.closing = True
        for child in {self.sup.child, getattr(self, 'old', None)} - {None}:
            if child.proc.poll() is None:
                child.proc.kill()
            child.proc.wait(10)
        time.sleep(0.2)
        self.tmp.cleanup()

    def start(self, work, drain_limit=None):
        import json
        import os
        import threading
        os.environ['WORK'] = str(work)
        self.addCleanup(os.environ.pop, 'WORK', None)
        self.sup = self.sup_mod.Supervisor([sys.executable, str(self.dir / 'child.py')], [str(self.watched)],
                                           poll=0.1, settle=0.2, out=self.Out(), inp=None)
        self.sup.close_grace = 1.0                # shorter than the work: a reload that does not wait kills it
        if drain_limit is not None:
            self.sup.drain_limit = drain_limit
        threading.Thread(target=self.sup.watch_loop, daemon=True).start()
        self.send = lambda m: self.sup.from_client(json.dumps(m).encode())
        self.send({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.send({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'long_act'}})
        self.old = self.sup.child
        time.sleep(0.5)
        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 1}})
        self.assertEqual(self.sup.in_flight, {})
        time.sleep(0.5)
        (self.watched / 'code.py').write_text('v = 2\n')          # a code edit while the cancelled call still runs

    def wait_reload(self, timeout):
        end = time.monotonic() + timeout
        while self.sup.reloads == 0 and time.monotonic() < end:
            time.sleep(0.05)
        return time.time()

    def test_cancelled_call_survives_reload(self):
        self.start(work=4)
        self.wait_reload(15)
        self.assertEqual(self.sup.reloads, 1)
        self.assertEqual(self.old.proc.wait(15), 0)               # it ended by itself, not killed
        self.assertTrue((self.dir / 'done.txt').exists(), 'the cancelled call was cut short')

    def test_reload_waits_for_busy_then_proceeds(self):
        self.start(work=3)
        reloaded_at = self.wait_reload(15)
        done_at = float((self.dir / 'done.txt').read_text())
        self.assertGreaterEqual(reloaded_at, done_at - 0.05, 'reloaded while the call was still running')
        self.assertLess(reloaded_at - done_at, 1.0, 'the reload did not follow the end of the call')

    def test_old_busy_wait_does_not_count_against_a_later_one(self):
        # An edit made while a cancelled call ran, then reverted: the reload is no longer pending. A later busy wait
        # must start its own drain clock, not inherit the first one (which would kill the server at once).
        import json
        self.start(work=2, drain_limit=3.0)
        time.sleep(0.8)                                           # the watch loop has seen the busy child
        self.assertIsNotNone(self.sup.busy_since)
        (self.watched / 'code.py').write_text('v = 1\n')         # reverted: same code as the running child
        time.sleep(4.0)                                           # call 1 ends; the first wait is long past 3 s
        self.assertEqual(self.sup.reloads, 0)
        self.sup.from_client(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                         'params': {'name': 'long_act'}}).encode())
        time.sleep(0.5)
        self.sup.from_client(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
                                         'params': {'requestId': 2}}).encode())
        (self.watched / 'code.py').write_text('v = 3\n')
        self.wait_reload(15)
        self.assertEqual(self.sup.reloads, 1)
        self.assertEqual(self.old.proc.wait(15), 0, 'the busy server was killed')
        self.assertNotIn('killed a busy server', (self.dir / 'supervisor.log').read_text(encoding='utf-8')
                         if (self.dir / 'supervisor.log').exists() else '')

    def test_drain_limit_logs_and_reloads(self):
        started = time.monotonic()
        self.start(work=120, drain_limit=2.0)
        self.wait_reload(15)
        self.assertEqual(self.sup.reloads, 1)
        self.assertGreater(time.monotonic() - started, 2.0)
        self.assertIn('killed a busy server', (self.dir / 'supervisor.log').read_text(encoding='utf-8'))


class BusyFileTests(unittest.TestCase):
    def test_track_and_count(self):
        import os
        sys.path.insert(0, str(ROOT))
        from fnv_mcp import busy
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'busy.json'
            os.environ['GECK_MCP_BUSY_FILE'] = str(path)
            try:
                self.assertEqual(busy.count(path), 0)
                with busy.track('a'):
                    with busy.track('b'):
                        self.assertEqual(busy.count(path), 2)
                    self.assertEqual(busy.count(path), 1)
                self.assertEqual(busy.count(path), 0)
                path.write_text('{not json')
                self.assertEqual(busy.count(path), 0)
            finally:
                os.environ.pop('GECK_MCP_BUSY_FILE')
            with busy.track('unset'):                 # no variable: does nothing
                pass

    @unittest.skipUnless(sys.platform == 'win32', 'Windows sharing rules')
    def test_update_survives_a_reader_holding_the_file(self):
        # The supervisor reads the file while the server writes it; on Windows os.replace onto a file another
        # handle has open fails (no FILE_SHARE_DELETE). The update must retry, not be lost.
        import os
        import threading
        sys.path.insert(0, str(ROOT))
        from fnv_mcp import busy
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'busy.json'
            os.environ['GECK_MCP_BUSY_FILE'] = str(path)
            try:
                with busy.track('a'):
                    reader = open(path, encoding='utf-8')
                    threading.Timer(0.2, reader.close).start()
                    with busy.track('b'):
                        time.sleep(0.4)
                        self.assertEqual(busy.count(path), 2)
                self.assertEqual(busy.count(path), 0)
            finally:
                os.environ.pop('GECK_MCP_BUSY_FILE')


if __name__ == '__main__':
    unittest.main()
