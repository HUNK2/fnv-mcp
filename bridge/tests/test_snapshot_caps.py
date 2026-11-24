"""W9: fnv_snapshot took any number of items and kept every console output line."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fnv_mcp import fnv_scenario


class SnapshotCapTests(unittest.TestCase):
    def live(self, tmp, lines=0):
        return SimpleNamespace(project=SimpleNamespace(state_dir=Path(tmp)), identity=lambda: {}, game=None,
                               console_refused=lambda line: None,
                               console=lambda line: {'output': ['line %d' % i for i in range(lines)]})

    def test_w9_too_many_items_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [{'console': 'x'}] * (fnv_scenario.SNAPSHOT_MAX_ITEMS + 1)
            with self.assertRaises(ValueError):
                fnv_scenario.snapshot(self.live(tmp), items, 'big')
            self.assertFalse((Path(tmp) / 'fnv/snapshots').exists())

    def test_w9_console_output_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = fnv_scenario.snapshot(self.live(tmp, lines=5000), [{'console': 'x'}], 'c')
            kept = next(iter(shot['values'].values()))
            self.assertEqual(len(kept), 400)


if __name__ == '__main__':
    unittest.main()
