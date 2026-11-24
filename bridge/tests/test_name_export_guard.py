"""Z3: fnv_name_export replaced any fnvsym-trace file, including fnvsym's own Frida trace."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fnv_mcp import fnv_naming
from fnv_mcp.fnv_probe import ProbeError


def tools(state):
    t = fnv_naming.NamingTools.__new__(fnv_naming.NamingTools)
    t.live = SimpleNamespace(project=SimpleNamespace(state_dir=Path(state)))
    return t


class ExportGuardTests(unittest.TestCase):
    def test_z3_only_our_own_export_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'traces'
            folder.mkdir()
            frida = folder / 'fnv.json'
            frida.write_text(json.dumps({'format': 'fnvsym-trace/1', 'functions': {}}), encoding='utf-8')
            ours = folder / 'ours.json'
            ours.write_text(json.dumps({'format': 'fnvsym-trace/2', 'tool': 'fnv-mcp (geck-mcp-nv bridge)'}),
                            encoding='utf-8')
            fake_v2 = folder / 'other.json'
            fake_v2.write_text(json.dumps({'format': 'fnvsym-trace/2', 'tool': 'fnvsym frida'}), encoding='utf-8')
            t = tools(tmp)
            for path in (frida, fake_v2):
                with self.assertRaises(ProbeError, msg=path.name) as caught:
                    t._export_path(str(path))
                self.assertEqual(caught.exception.code, 'BAD_PATH')
            self.assertEqual(t._export_path(str(ours)), ours.resolve())
            self.assertEqual(t._export_path(str(folder / 'new.json')), (folder / 'new.json').resolve())


if __name__ == '__main__':
    unittest.main()
