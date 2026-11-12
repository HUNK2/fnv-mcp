"""PLAN-3 S4.4: the shared fakes answer with the real companion's reply shapes.

tests/wire/<op>.json holds one reply per op, recorded from the real DLL under the fake xNVSE host:

    .venv/Scripts/python.exe tests/test_wire_contract.py --record

The test sends every op again to a fresh host and checks that (a) the recorded file still has the real reply's keys
and value kinds (the companion did not drift from the record), and (b) fakes.FakeLink answers with the same keys and
kinds (the fake did not drift from the companion)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host, fakes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_host import StopHost, build, hexint, needs_zig  # noqa: E402
from geck_mcp import fnv_link  # noqa: E402

WIRE = Path(__file__).resolve().parent / 'wire'


def requests(t):
    """op key -> the op tuple sent to the host (t: the host's address header). Order matters: set before list."""
    buf = t['buffer']
    hook_args = (hexint(t['hookme']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0), hexint(1), '0', '0',
                 hexint(0), hexint(0), fnv_link.hexs('wire'))
    return [
        ('rd', ('rd', hexint(buf), '4')),
        ('wr', ('wr', hexint(buf + 0x180), 'aabbccdd')),
        ('eval', ('eval', fnv_link.hexs('form'), '0')),
        ('console', ('con', fnv_link.hexs('poll 0'), '0')),   # not 'con': a reserved file name on Windows
        ('bp_set', ('bp', 'set', hexint(1), hexint(t['bpTarget']), 'int3', 'ff', '0')),
        ('bp_list', ('bp', 'list')),
        ('bp_clear', ('bp', 'clear', hexint(1))),
        ('hook', ('hook',) + hook_args),
        ('hooks', ('hooks',)),
        ('cov_read', ('cov', 'read', '0')),
        ('mod_list', ('mod', 'list')),
    ]


def shape(value):
    """Key -> kind for a reply dict (numbers are one kind; lists and dicts are compared one level deep)."""
    def kind(v):
        if isinstance(v, bool):
            return 'bool'
        if isinstance(v, (int, float)):
            return 'number'
        if v is None:
            return 'null'
        return type(v).__name__
    return {k: kind(v) for k, v in value.items()}


def real_replies():
    tmp = tempfile.TemporaryDirectory()
    try:
        receipt = build(tmp.name)
        host = StopHost(receipt, Path(receipt['dll']).parent, tmp.name, 60)
        try:
            out = {}
            ping = host.link.ping()
            out['ping'] = {k: v for k, v in ping.items() if k != 'id'}
            hook_id = None
            for key, op in requests(host.t):
                reply = host.link.one(*op)
                out[key] = reply
                if key == 'hook' and reply.get('ok'):
                    hook_id = reply['hook']['id']
            if hook_id:
                out['unhook'] = host.link.one('unhook', hexint(hook_id))
            return out
        finally:
            host.close()
    finally:
        tmp.cleanup()


def record():
    WIRE.mkdir(exist_ok=True)
    for key, reply in real_replies().items():
        (WIRE / (key + '.json')).write_text(json.dumps(reply, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print('recorded', key)


@needs_zig
class WireContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = real_replies()

    def test_every_op_has_a_record(self):
        self.assertEqual(sorted(p.stem for p in WIRE.glob('*.json')), sorted(self.real))

    def test_records_match_the_real_companion(self):
        for key, reply in self.real.items():
            recorded = json.loads((WIRE / (key + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(shape(recorded), shape(reply), '%s: record out of date (run --record)' % key)

    def test_fake_replies_have_real_keys(self):
        import fakes
        fake = fakes.FakeLink()
        fake.put(0x01000000, b'\x00' * 0x400)
        fake.expressions['form'] = {'type': 'form', 'ptr': '0x01000000', 'formId': '00000014'}
        t = {'buffer': 0x01000000, 'bpTarget': 0x00401000, 'hookme': 0x00401100}
        for key, op in requests(t):
            reply = fake.one(*op)
            self.assertEqual(shape(reply), shape(self.real[key]), '%s: the fake answers another shape' % key)
        ping = {k: v for k, v in fake.ping().items() if k != 'id'}
        self.assertEqual(shape(ping), shape(self.real['ping']), 'ping: the fake answers another shape')


if __name__ == '__main__':
    if '--record' in sys.argv:
        record()
    else:
        unittest.main()
