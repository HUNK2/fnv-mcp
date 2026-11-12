"""The stdio supervisor (geck_mcp/supervisor.py): code reloads reach the client without a reconnect.

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
        launcher.write_text(LAUNCHER.format(supervisor=str(ROOT / 'geck_mcp' / 'supervisor.py'),
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


if __name__ == '__main__':
    unittest.main()
