"""Shared fakes for the live tools (PLAN-3 S4.4): one fake engine companion instead of a copy per test file.

FakeLink answers the companion ops from a small model (memory, expressions, console lines) and, for the ops it does
not model (breakpoints, hooks, coverage, modules), with the reply recorded from the real DLL in tests/wire/*.json.
test_wire_contract.py checks that every reply here has the real reply's keys and value kinds.

FakeLive is a LiveService over a FakeLink and a temp project with engine_control = "full"."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_game, fnv_link  # noqa: E402
from fnv_mcp.config import Project  # noqa: E402
from fnv_mcp.fnv_facts import FACTS  # noqa: E402

WIRE = Path(__file__).resolve().parent / 'wire'


def wire(key):
    """A fresh copy of the real reply recorded for op key (tests/wire/<key>.json)."""
    return copy.deepcopy(json.loads((WIRE / (key + '.json')).read_text(encoding='utf-8')))


class FakeLink(fnv_link.EngineLink):
    """The typed helpers (read, write, eval, console...) are the real EngineLink ones on top of this batch()."""

    idle_release_seconds = 0

    def __init__(self):
        super().__init__('fnvmcp-fake')
        self.memory = {}
        self.expressions = {}
        self.console_log = []
        self.hello = wire('ping')
        self.hello.update(pid=4242, started='1', frame=10)
        self.calls = []
        self.ops = []                 # every op sent: (name, args)
        self.hooks = {}               # id -> the hook record (wire shape)
        self.bps = {}

    def put(self, address, data):
        for i, b in enumerate(data):
            self.memory[address + i] = b

    def get(self, address, length):
        if any(address + i not in self.memory for i in range(length)):
            return None
        return bytes(self.memory[address + i] for i in range(length))

    def ping(self, timeout=3.0):
        return self.hello

    def connect(self, wait=0.0):
        return self

    def close(self):
        pass

    def batch(self, ops, timeout=None):
        results = []
        for op in ops:
            self.ops.append((op[0], [str(a) for a in op[1:]]))
            result = self._op(op[0], [str(a) for a in op[1:]])
            result.setdefault('frame', self.hello.get('frame'))
            results.append(result)
        return results

    def _op(self, name, args):
        if name == 'rd':
            data = self.get(int(args[0], 16), int(args[1], 16))
            return {'ok': True, 'hex': data.hex()} if data is not None else {'ok': False, 'error': 'unreadable'}
        if name == 'wr':
            address, data = int(args[0], 16), bytes.fromhex(args[1])
            old = self.get(address, len(data))
            if old is None:
                return {'ok': False, 'error': 'unreadable'}
            self.put(address, data)
            return {'ok': True, 'old': old.hex()}
        if name == 'ls':
            return {'ok': False, 'error': 'unknown_op'}
        if name == 'rs':
            return {'ok': False, 'error': 'unreadable'}
        if name in ('eval', 'run'):
            text = bytes.fromhex(args[0]).decode('cp1252')
            element = self.expressions.get(text)
            if element is None:
                return {'ok': False, 'error': 'compile_failed'}
            return {'ok': True, 'called': True, 'scriptErrors': 0, 'result': element}
        if name == 'con':
            line = bytes.fromhex(args[0]).decode('cp1252')
            flags = (self.get(FACTS['verbose_print_flag']['address'], 1),
                     self.get(FACTS['console_visible_flag']['address'], 1))
            self.console_log.append((line, flags))
            if line.startswith('SetNumericGameSetting'):
                value = float(line.split()[-1])
                self.expressions['GetNumericGameSetting "fJumpHeightMin"'] = {'type': 'number', 'value': value}
            return {'ok': True, 'accepted': True, 'scriptErrors': 0, 'captured': True, 'output': 'echo %s\r\n' % line}
        if name == 'call':
            self.calls.append(args)
            return {'ok': True, 'eax': '0x00000001', 'edx': '0x00000000', 'scriptErrors': 0}
        if name == 'events':
            return {'ok': True, 'seq': 0, 'events': []}
        if name == 'sched':
            return {'ok': True, 'slot': 0}
        if name == 'bp':
            return self._bp(args)
        if name == 'hook':
            record = wire('hook')
            hook = record['hook']
            hook.update(id=len(self.hooks) + 1, target='0x%08x' % int(args[0], 16),
                        label=bytes.fromhex(args[-1]).decode('cp1252') if args[-1] != '-' else '')
            self.hooks[hook['id']] = hook
            return {'ok': True, 'hook': hook}
        if name == 'hooks':
            return {'ok': True, 'hooks': list(self.hooks.values())}
        if name == 'unhook':
            hook_id = int(args[0], 16)
            if self.hooks.pop(hook_id, None) is None:
                return {'ok': False, 'error': 'unknown_hook'}
            return {'ok': True, 'id': hook_id}
        if name == 'cov':
            if args[:1] == ['read']:
                return {k: v for k, v in wire('cov_read').items() if k != 'frame'}
            return {'ok': True}
        if name == 'mod' and args[:1] == ['list']:
            return {'ok': True, 'modules': []}
        return {'ok': False, 'error': 'unknown_op'}

    def _bp(self, args):
        if args[:1] == ['set']:
            bp_id = int(args[1], 16)
            row = {k: v for k, v in wire('bp_list')['bps'][0].items()}
            row.update(id=bp_id, addr='0x%08x' % int(args[2], 16), kind=args[3], pred=args[4])
            self.bps[bp_id] = row
            return {'ok': True, 'id': bp_id, 'kind': args[3]}
        if args[:1] == ['list']:
            return {'bps': list(self.bps.values())}
        if args[:1] == ['clear']:
            target = int(args[1], 16)
            cleared = len(self.bps) if target == 0xFF else int(self.bps.pop(target, None) is not None)
            if target == 0xFF:
                self.bps.clear()
            return {'ok': True, 'cleared': cleared, 'toSites': 0}
        return {'ok': False, 'error': 'bad_arguments'}


def project(root, engine_control='full'):
    """A project in a temp folder with an MO2 dev profile (no game)."""
    root = Path(root)
    mo2 = root / 'mo2'
    (mo2 / 'profiles/Dev/saves').mkdir(parents=True, exist_ok=True)
    (mo2 / 'profiles/Dev/modlist.txt').write_text('# header\n+Other Mod\n', encoding='utf-8')
    (mo2 / 'profiles/Dev/plugins.txt').write_text('# header\nFalloutNV.esm\n', encoding='utf-8')
    (root / 'game').mkdir(exist_ok=True)
    return Project.model_validate({
        'project_id': 'livetest', 'game_root': str(root / 'game'), 'plugin': 'Fixture.esp',
        'state_dir': str(root / 'state'),
        'fnv': {'mo2_root': str(mo2), 'mo2_profile': 'Dev', 'engine_control': engine_control}})


class FakeLive:
    """A LiveService over a FakeLink, a journal in a temp state folder and engine_control = "full".
    Use as a context manager, or call close()."""

    def __init__(self, engine_control='full'):
        from fnv_mcp.fnv_live import LiveService
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = project(self.root, engine_control)
        self.link = FakeLink()
        self.live = LiveService(self.project)
        self.live._game = fnv_game.Game(self.project)
        self.live._game.link = self.link
        self.live._game.process = lambda fresh=False: (4242, 1)
        # what every running game has: the console print flags the console tools save and restore
        self.link.put(FACTS['verbose_print_flag']['address'], b'\x00')
        self.link.put(FACTS['console_visible_flag']['address'], b'\x00')

    def close(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
