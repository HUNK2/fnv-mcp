"""Tool gaps 1c: objects placed in exterior cells. The plugin overrides the world (without its offset table), the
master's exterior cell at the grid, and the persistent cell when an object is persistent, in the groups GECK writes:
WRLD > world children > [persistent CELL > children > persistent] > block > sub-block > CELL > children > temporary."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from fnv_mcp.esp import build as builder, codec
from fnv_mcp.esp.codec import Group, Plugin, Record, Subrecord
from fnv_mcp.esp.formids import FormIdMap, MasterIndex
from fnv_mcp.esp.masters import MasterRecords
from fnv_mcp.esp.records import SpecError

from test_spec_masters import write_index, z

WORLD, PCELL, CELL, CELL2, CRATE = 0x000DA726, 0x000DA727, 0x000DA800, 0x000DA801, 0x00018EE1


def sub(tag, data):
    return Subrecord(tag, data)


def cell(form_id, grid=None, name=None):
    subs = [sub('EDID', z(name))] if name else []
    subs += [sub('DATA', b'\x02')]
    if grid is not None:
        subs.append(sub('XCLC', struct.pack('<iiI', grid[0], grid[1], 0)))
    return Record('CELL', form_id, flags=0, _subrecords=subs)


def hh(y, x):
    return struct.pack('<hh', y, x)


def write_world_master(folder):
    header = Record('TES4', 0, flags=1, _subrecords=[sub('HEDR', struct.pack('<fII', 1.34, 9, 0x800)),
                                                      sub('CNAM', z('tests'))])
    world = Record('WRLD', WORLD, _subrecords=[sub('EDID', z('TestWasteland')), sub('FULL', z('Test Wasteland')),
                                               sub('OFST', b'\x01' * 64)])
    crate = Record('STAT', CRATE, _subrecords=[sub('EDID', z('TestCrate')), sub('OBND', b'\0' * 12),
                                               sub('MODL', z('Clutter\\Crate.nif'))])
    other_ref = Record('REFR', 0x000DA900, _subrecords=[sub('NAME', struct.pack('<I', CRATE)),
                                                        sub('DATA', b'\0' * 24)])
    children = Group(struct.pack('<I', WORLD), 1, children=[
        cell(PCELL),
        Group(hh(0, -1), 4, children=[
            Group(hh(0, -3), 5, children=[
                cell(CELL, (-18, 2), 'TestGoodsprings'),
                Group(struct.pack('<I', CELL), 6, children=[
                    Group(struct.pack('<I', CELL), 9, children=[other_ref])]),
                cell(CELL2, (-17, 2))])])])
    plugin = Plugin(header, [Group(b'STAT', 0, children=[crate]), Group(b'WRLD', 0, children=[world, children])])
    path = Path(folder) / 'FalloutNV.esm'
    path.write_bytes(codec.write(plugin))
    return path


def spec_of(cells, world='TestWasteland'):
    return {'schema': 1, 'project_id': 'worldtest', 'plugin': 'WorldTest.esp', 'masters': ['FalloutNV.esm'],
            'records': {}, 'worlds': {world: {'cells': cells}}}


class ExteriorCells(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nv = write_world_master(self.root)
        self.index = MasterIndex([write_index(self.root, self.nv)])
        self.sources = MasterRecords([self.nv])

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, cells, world='TestWasteland'):
        spec = spec_of(cells, world)
        builder.check_spec(spec)
        data, receipt = builder.build(spec, FormIdMap(self.root / 'f.json', spec['plugin']), self.index,
                                      master_records=self.sources)
        return codec.parse(data), receipt

    def test_objects_in_an_exterior_cell(self):
        plugin, receipt = self.build({'CampSite': {'grid': [-18, 2], 'objects': [
            {'ref_id': 'crate', 'base': '@TestCrate', 'position': [100, 200, 30]},
            {'ref_id': 'marker', 'base': '@TestCrate', 'position': [0, 0, 0], 'persistent': True}]}})
        wrld = plugin.top_group('WRLD')
        self.assertIsNotNone(wrld)
        world, children = wrld.children
        self.assertEqual((world.type, world.form_id), ('WRLD', WORLD))
        self.assertNotIn('OFST', [s.tag for s in world.subrecords])
        self.assertEqual(world.get('FULL'), z('Test Wasteland'))
        self.assertEqual((children.group_type, children.label), (1, struct.pack('<I', WORLD)))
        pcell, pchildren, block = children.children
        self.assertEqual((pcell.type, pcell.form_id), ('CELL', PCELL))
        self.assertEqual(pchildren.group_type, 6)
        persistent = pchildren.children[0]
        self.assertEqual(persistent.group_type, 8)
        self.assertEqual([r.flags & 0x400 for r in persistent.children], [0x400])
        self.assertEqual((block.group_type, block.label), (4, hh(0, -1)))
        subblock = block.children[0]
        self.assertEqual((subblock.group_type, subblock.label), (5, hh(0, -3)))
        ext, ext_children = subblock.children
        self.assertEqual(ext.form_id, CELL)
        self.assertEqual(ext.editor_id, 'TestGoodsprings')
        temporary = ext_children.children[0]
        self.assertEqual(temporary.group_type, 9)
        refr = temporary.children[0]
        self.assertEqual(struct.unpack('<I', refr.get('NAME'))[0], CRATE)
        self.assertEqual(struct.unpack('<6f', refr.get('DATA'))[:3], (100.0, 200.0, 30.0))
        self.assertEqual(refr.form_id >> 24, 1)
        self.assertEqual(len(receipt['references']['CampSite']), 2)
        self.assertNotIn('CampSite', receipt['formIds'])                  # the cell keeps the master's FormID
        self.assertEqual(receipt['hedrCount'], sum(1 for _ in plugin.records()) - 1 + 8)  # records + 8 groups

    def test_two_cells_share_the_world_override(self):
        plugin, _ = self.build({'A': {'grid': [-18, 2], 'objects': [{'ref_id': 'a', 'base': '@TestCrate'}]},
                                'B': {'grid': [-17, 2], 'objects': [{'ref_id': 'b', 'base': '@TestCrate'}]}})
        world, children = plugin.top_group('WRLD').children
        self.assertEqual(len(children.children), 1)                          # no persistent cell: one block
        cells = [c for c in children.children[0].children[0].children if isinstance(c, Record)]
        self.assertEqual(sorted(c.form_id for c in cells), [CELL, CELL2])

    def test_errors(self):
        cases = [({'X': {'grid': [50, 50], 'objects': []}}, 'TestWasteland', 'no exterior cell at 50, 50'),
                 ({'X': {'grid': [-18], 'objects': []}}, 'TestWasteland', 'grid'),
                 ({'X': {'grid': [-18, 2], 'objects': [{'base': '@TestCrate'}]}}, 'NoSuchWorld', 'NoSuchWorld'),
                 ({'X': {'grid': [-18, 2], 'objects': [{'nobase': 1}]}}, 'TestWasteland', 'base')]
        for cells, world, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaises(SpecError) as caught:
                self.build(cells, world)
            self.assertIn(fragment, str(caught.exception))

    def test_cell_labels_share_the_spec_namespace(self):
        spec = spec_of({'Dup': {'grid': [-18, 2], 'objects': []}})
        spec['cells'] = {'Dup': {'objects': []}}
        with self.assertRaises(SpecError) as caught:
            builder.check_spec(json.loads(json.dumps(spec)))
        self.assertIn('also used', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
