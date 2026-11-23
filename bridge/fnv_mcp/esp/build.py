"""Spec -> plugin bytes. Pure and deterministic: same spec + FormID map + index = same bytes.

Spec (JSON):
{
  "schema": 1, "project_id": "coolworld", "plugin": "CoolWorld.esp",
  "author": "2Shay", "description": "...", "masters": ["FalloutNV.esm"],
  "records": {"<EditorID>": {"type": "STAT"|"LIGH", ...fields}},
  "cells":   {"<EditorID>": {"name": ..., "lighting": {...},
              "objects": [{"ref_id": "salvatore", "base": "@EditorID"|"XXXXXXXX",
                           "position": [x,y,z], "rotation": [deg,deg,deg], "scale": 1.0}]}},
  "release": {}   # reserved for Milestone 3
}
  "worlds":  {"<world EditorID>": {"cells": {"<label>": {"grid": [x, y], "objects": [...]}}}}
Exterior cells are the master's, overridden to hold the objects (esp/worlds.py). Standard library only.
"""
import hashlib
import json
import re

from . import codec, records
from .codec import Group, Plugin
from .formids import FormIdError, FormIdMap, MasterIndex, Resolver
from .records import SpecError
from . import dialogue, generic, worlds as world_files

SCHEMA = 1
GENERATED_MARK = '[geck-bridge generated: %s]'
# Top-level group order used by GECK/FalloutNV.esm (read from the master, 2026-09-29).
TOP_ORDER = ['GMST', 'TXST', 'MICN', 'GLOB', 'CLAS', 'FACT', 'HDPT', 'HAIR', 'EYES', 'RACE', 'SOUN',
             'ASPC', 'MGEF', 'SCPT', 'LTEX', 'ENCH', 'SPEL', 'ACTI', 'TACT', 'TERM', 'ARMO', 'BOOK',
             'CONT', 'DOOR', 'INGR', 'LIGH', 'MISC', 'STAT', 'SCOL', 'MSTT', 'PWAT', 'GRAS', 'TREE',
             'FURN', 'WEAP', 'AMMO', 'NPC_', 'CREA', 'LVLC', 'LVLN', 'KEYM', 'ALCH', 'IDLM', 'NOTE',
             'COBJ', 'PROJ', 'LVLI', 'WTHR', 'CLMT', 'REGN', 'NAVI', 'CELL', 'WRLD', 'DIAL', 'QUST',
             'IDLE', 'PACK', 'CSTY', 'LSCR', 'ANIO', 'WATR', 'EFSH', 'EXPL', 'DEBR', 'IMGS', 'IMAD',
             'FLST', 'PERK', 'BPTD', 'ADDN', 'AVIF', 'RADS', 'CAMS', 'CPTH', 'VTYP', 'IPCT', 'IPDS',
             'ARMA', 'ECZN', 'MESG', 'RGDL', 'DOBJ', 'LGTM', 'MUSC', 'IMOD', 'REPU', 'RCPE', 'RCCT',
             'CHIP', 'CSNO', 'LSCT', 'MSET', 'ALOC', 'CHAL', 'AMEF', 'CCRD', 'CMNY', 'CDCK', 'DEHY',
             'HUNG', 'SLPD']
REF_ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}\Z')
TOP_KEYS = {'schema', 'project_id', 'plugin', 'author', 'description', 'masters', 'records',
            'cells', 'worlds', 'release'}


def load_spec(text):
    try:
        spec = json.loads(text)
    except ValueError as e:
        raise SpecError('spec', 'invalid JSON: %s' % e)
    return check_spec(spec)


def check_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError('spec', 'expected a JSON object')
    unknown = set(spec) - TOP_KEYS
    if unknown:
        raise SpecError('spec', 'unknown top-level field(s): %s' % ', '.join(sorted(unknown)))
    if spec.get('schema') != SCHEMA:
        raise SpecError('schema', 'must be %d' % SCHEMA)
    if not isinstance(spec.get('project_id'), str) or not re.match(r'^[a-z0-9][a-z0-9_-]{0,63}\Z', spec['project_id']):
        raise SpecError('project_id', 'lowercase letters, digits, - and _ only')
    if spec['project_id'] in records.DEVICE_NAMES:
        raise SpecError('project_id', '%s is a Windows device name' % spec['project_id'])
    plugin = spec.get('plugin')
    if not isinstance(plugin, str) or not re.match(r'^[A-Za-z0-9 _.-]{1,60}\.esp\Z', plugin):
        raise SpecError('plugin', 'must be a simple .esp filename')
    if plugin.split('.', 1)[0].strip().lower() in records.DEVICE_NAMES:
        raise SpecError('plugin', '%s is a Windows device name' % plugin)
    masters = spec.get('masters')
    # bug hunt 2026-11-18: a non-string master (a number, null) raised AttributeError on .lower() before this check
    if not isinstance(masters, list) or not masters or not all(isinstance(m, str) for m in masters) \
            or len(set(m.lower() for m in masters)) != len(masters):
        raise SpecError('masters', 'must be a non-empty list without duplicates')
    for field in ('records', 'cells'):
        if not isinstance(spec.get(field, {}), dict):
            raise SpecError(field, 'expected an object keyed by EditorID')
    seen = {}
    if not isinstance(spec.get('worlds', {}), dict):
        raise SpecError('worlds', 'expected an object keyed by the world\'s EditorID')
    labels = [('records', k) for k in spec.get('records', {})] + [('cells', k) for k in spec.get('cells', {})]
    for world, wspec in spec.get('worlds', {}).items():
        records.check_edid(world, 'worlds.%s' % world)
        if not isinstance(wspec, dict) or set(wspec) - {'world', 'cells'} or \
                not isinstance(wspec.get('cells', {}), dict):
            raise SpecError('worlds.%s' % world, 'give "cells" (an object keyed by a label) and optionally "world" '
                            '(the world\'s "@EditorID" when the key is a label)')
        labels += [('worlds.%s.cells' % world, k) for k in wspec.get('cells', {})]
    for field, edid in labels:
        records.check_edid(edid, '%s.%s' % (field, edid))
        if edid.lower() in seen:
            raise SpecError('%s.%s' % (field, edid), 'EditorID also used in %s' % seen[edid.lower()])
        seen[edid.lower()] = field
    for edid, rec in spec.get('records', {}).items():
        kinds = set(records.BUILDERS) | generic.GENERIC_TYPES
        # bug hunt 2026-11-18: a list or object as "type" is unhashable and raised TypeError on the set test
        if not isinstance(rec, dict) or not isinstance(rec.get('type'), str) or rec.get('type') not in kinds:
            raise SpecError('records.%s.type' % edid, 'must be one of %s' % ', '.join(sorted(kinds)))
        override = rec.get('override')
        if override not in (None, True, False) and not (
                isinstance(override, str) and (override.startswith('@') or re.match(r'^[0-9A-Fa-f]{8}\Z', override))):
            raise SpecError('records.%s.override' % edid, 'true (the key is the master record\'s EditorID), '
                            '"@EditorID" or an 8-digit FormID')
        if 'set' in rec and (rec.get('type') not in generic.layouts.TYPES or not isinstance(rec['set'], dict)):
            raise SpecError('records.%s.set' % edid, 'named fields exist for %s; for %s use "fields"'
                            % (', '.join(generic.layouts.TYPES), rec.get('type')))
        if override and 'template' in rec:
            raise SpecError('records.%s.template' % edid, 'an override starts from the master record: no template')
        if 'infos' in rec:
            if rec.get('type') != 'DIAL':
                raise SpecError('records.%s.infos' % edid, 'lines (infos) belong to a topic (DIAL)')
            dialogue.check_lines(edid, rec, 'records.%s' % edid)
    for world, wspec in spec.get('worlds', {}).items():
        for cell_id, cell in wspec.get('cells', {}).items():
            where = 'worlds.%s.cells.%s' % (world, cell_id)
            if not isinstance(cell, dict) or set(cell) - {'grid', 'objects'}:
                raise SpecError(where, 'give "grid" [x, y] and "objects"')
            grid = cell.get('grid')
            if not isinstance(grid, list) or len(grid) != 2 or not all(
                    isinstance(v, int) and not isinstance(v, bool) and -128 <= v <= 127 for v in grid):
                raise SpecError(where + '.grid', 'the cell\'s grid as [x, y], two integers')
            check_objects(cell.get('objects', []), where)
    for cell_id, cell in spec.get('cells', {}).items():
        if not isinstance(cell, dict):
            raise SpecError('cells.%s' % cell_id, 'expected an object')
        check_objects(cell.get('objects', []), 'cells.%s' % cell_id)
    return spec


def check_objects(objects, owner):
    """The objects of one cell: each has a base; ref_ids are well formed and unique in the cell."""
    if not isinstance(objects, list):
        raise SpecError('%s.objects' % owner, 'expected a list')
    refs = set()
    for i, obj in enumerate(objects):
        where = '%s.objects[%d]' % (owner, i)
        if not isinstance(obj, dict) or 'base' not in obj:
            raise SpecError(where, 'each object needs a "base"')
        ref_id = obj.get('ref_id')
        if ref_id is not None:
            if not isinstance(ref_id, str) or not REF_ID_RE.match(ref_id):
                raise SpecError(where + '.ref_id', 'letters, digits, . _ - only (max 64)')
            if ref_id in refs:
                raise SpecError(where + '.ref_id', 'duplicate ref_id %r in this cell' % ref_id)
            refs.add(ref_id)


def ref_key(cell_id, index, obj):
    return 'REFR:%s:%s' % (cell_id, obj['ref_id'] if obj.get('ref_id') else '#%d' % index)


def allocation_keys(spec):
    """Every FormID key in deterministic spec order: records, cells, references, then dialogue lines. Overridden
    master records keep the master's FormID and get none."""
    records_ = spec.get('records', {})
    keys = [k for k, r in records_.items() if not r.get('override')] + list(spec.get('cells', {}))
    for cell_id, cell in spec.get('cells', {}).items():
        keys += [ref_key(cell_id, i, obj) for i, obj in enumerate(cell.get('objects', []))]
    for topic, rec in records_.items():
        keys += [dialogue.info_key(topic, line) for line in rec.get('infos', [])]
    for wspec in spec.get('worlds', {}).values():
        for cell_id, cell in wspec.get('cells', {}).items():
            keys += [ref_key(cell_id, i, obj) for i, obj in enumerate(cell.get('objects', []))]
    return keys


def interior_block(local_id):
    """Interior cell block / sub-block: last and penultimate decimal digits of the object index."""
    return local_id % 10, (local_id // 10) % 10


def build(spec, formids, index=None, base_dir=None, script_cache=None, templates=None, master_records=None):
    """Return (plugin_bytes, receipt). `formids` is a FormIdMap (updated in place, not saved).

    base_dir: folder that script "source" paths are relative to (the spec's folder).
    script_cache: {EditorID: compiled entry} from scripts.load_cache(); uncompiled otherwise.
    templates: template folders in lookup order (generic.template_dirs); default: the code root's.
    master_records: masters.MasterRecords, the master files that templates and overrides are read from.
    """
    spec = check_spec(spec)
    index = index or MasterIndex()
    masters = spec['masters']
    self_byte = len(masters) << 24
    warnings = []
    local = formids.sync(allocation_keys(spec))
    full = {k: self_byte | v for k, v in local.items()}
    own = {k: v for k, v in full.items() if not k.startswith(('REFR:', 'INFO:'))}
    for edid in own:
        if spec.get('records', {}).get(edid, {}).get('override'):
            continue
        clash = [h for h in index.lookup(edid) if index.masters[h[0]] in masters]
        if clash:
            raise SpecError('records.%s' % edid, 'EditorID already exists in %s as %s %08X; pick a new name '
                            '(overriding master records is not supported yet)'
                            % (index.masters[clash[0][0]], clash[0][1], clash[0][2]))
    own_types = {k: v['type'] for k, v in spec.get('records', {}).items() if not v.get('override')}
    own_types.update({k: 'CELL' for k in spec.get('cells', {})})
    resolver = Resolver(masters, own, index, own_types)

    # Base records, grouped by type in GECK's top-group order.
    by_type = {}
    uncompiled = []
    lines_of = {}
    overrides = {}
    for edid, rec_spec in spec.get('records', {}).items():
        rtype = rec_spec['type']
        if rtype == 'DIAL' and rec_spec.get('override') is True and generic.dump_exists(edid, dirs=templates):
            rec = override_topic(edid, rec_spec, resolver, masters, templates)
            overrides[edid] = {'formId': '%08X' % rec.form_id, 'from': 'template ' + edid}
        elif rec_spec.get('override'):
            rec, source = generic.build_override(
                edid, {k: v for k, v in rec_spec.items() if k != 'infos'}, resolver, masters, master_records)
            overrides[edid] = {'formId': '%08X' % rec.form_id, 'from': source}
        elif rtype == 'SCPT':
            rec = generic.build_script(full[edid], edid, rec_spec, base_dir=base_dir, cache=script_cache)
            if not rec.compiled:
                uncompiled.append(edid)
        elif rtype in records.BUILDERS and 'template' not in rec_spec and 'fields' not in rec_spec:
            rec = records.BUILDERS[rtype](full[edid], edid, rec_spec)
        else:
            generic_spec = {k: v for k, v in rec_spec.items() if k != 'infos'}
            rec = generic.build_generic(full[edid], edid, generic_spec, resolver, masters=masters, templates=templates,
                                        master_records=master_records)
        if rtype == 'DIAL':
            lines = build_lines(edid, rec_spec, rec, full, resolver, script_cache)
            uncompiled += ['%s line %s' % (edid, line_key) for line_key, line in lines if not line.compiled]
            lines_of[rec.form_id] = [line for _, line in lines]
        by_type.setdefault(rtype, []).append(rec)

    # Interior cells: CELL -> block -> sub-block -> [CELL, children(6) -> temporary(9) -> REFR*]
    blocks = {}
    references = {}
    for cell_id, cell_spec in spec.get('cells', {}).items():
        cell_form = full[cell_id]
        cell = records.cell_interior(cell_form, cell_id, {k: v for k, v in cell_spec.items()})
        refs = place_objects(cell_id, cell_spec.get('objects', []), 'cells.%s' % cell_id, full, resolver, warnings,
                             references)
        persistent = [r for r in refs if r.flags & 0x400]
        temporary = [r for r in refs if not r.flags & 0x400]
        children = []
        if refs:
            inner = []
            if persistent:
                inner.append(Group(struct_label(cell_form), 8, children=persistent))
            if temporary:
                inner.append(Group(struct_label(cell_form), 9, children=temporary))
            children = [Group(struct_label(cell_form), 6, children=inner)]
        block, sub_block = interior_block(cell_form & 0xFFFFFF)
        blocks.setdefault(block, {}).setdefault(sub_block, []).append((cell_form, [cell] + children))

    groups = []
    for rtype in TOP_ORDER:
        if rtype == 'DIAL' and rtype in by_type:
            # Each topic is followed by its topic-children group (type 7, labelled with the topic's FormID).
            children = []
            for topic in sorted(by_type[rtype], key=lambda r: r.form_id):
                children.append(topic)
                if lines_of.get(topic.form_id):
                    children.append(Group(struct_label(topic.form_id), 7, children=lines_of[topic.form_id]))
            groups.append(Group(b'DIAL', 0, children=children))
        elif rtype in by_type:
            groups.append(Group(rtype.encode('ascii'), 0,
                                children=sorted(by_type[rtype], key=lambda r: r.form_id)))
        elif rtype == 'CELL' and blocks:
            block_groups = []
            for block in sorted(blocks):
                subs = []
                for sub_block in sorted(blocks[block]):
                    items = []
                    for _form, content in sorted(blocks[block][sub_block], key=lambda x: x[0]):
                        items += content
                    subs.append(Group(int_label(sub_block), 3, children=items))
                block_groups.append(Group(int_label(block), 2, children=subs))
            groups.append(Group(b'CELL', 0, children=block_groups))

    if spec.get('worlds'):
        wrld = build_worlds(spec, full, resolver, masters, master_records, warnings, references)
        at = next((i for i, g in enumerate(groups) if TOP_ORDER.index(g.label.decode('ascii')) >
                   TOP_ORDER.index('WRLD')), len(groups))
        groups.insert(at, wrld)
    count = sum(1 for g in groups for _ in walk(g))
    description = spec.get('description') or ''
    mark = GENERATED_MARK % spec['project_id']
    header = records.tes4(masters, count, formids.next_object_id,
                          author=spec.get('author', 'DEFAULT'),
                          description=(description + '\n' + mark).strip())
    data = codec.write(Plugin(header, groups))
    type_counts = {}
    for rec, _ in codec.parse(data).records():
        type_counts[rec.type] = type_counts.get(rec.type, 0) + 1
    receipt = {
        'plugin': spec['plugin'], 'projectId': spec['project_id'],
        'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
        'specSha256': hashlib.sha256(canonical(spec)).hexdigest(),
        'masters': masters, 'indexedMasters': index.masters,
        'recordCounts': type_counts, 'hedrCount': count,
        'nextObjectId': '%06X' % formids.next_object_id,
        'formIds': {k: '%08X' % v for k, v in full.items()}, 'overrides': overrides,
        'references': references, 'warnings': warnings, 'uncompiledScripts': uncompiled,
    }
    return data, receipt


def place_objects(cell_id, objects, owner, full, resolver, warnings, references):
    """The references of one cell (sorted by FormID); their receipt rows go to references[cell_id]."""
    refs = []
    references[cell_id] = []
    for i, obj in enumerate(objects):
        where = '%s.objects[%d]' % (owner, i)
        key = ref_key(cell_id, i, obj)
        if not obj.get('ref_id'):
            warnings.append('%s has no ref_id; its FormID depends on list position' % where)
        try:
            base, base_type = resolver.resolve_typed(obj['base'])
        except FormIdError as e:
            raise SpecError(where + '.base', str(e))
        kind = records.placement_type(base_type, where + '.base')
        if base_type is None:
            warnings.append('%s base type unknown (raw FormID); placed as REFR' % where)
        refs.append(records.refr(full[key], base, obj, where, record_type=kind))
        references[cell_id].append({'refId': obj.get('ref_id'), 'formId': '%08X' % full[key],
                                    'base': '%08X' % base, 'baseSpec': obj['base'],
                                    'baseType': base_type, 'recordType': kind,
                                    'editorId': obj.get('editor_id')})
    refs.sort(key=lambda r: r.form_id)
    return refs


def _copy(master_records, masters, owner, local, rtype, where, drop=()):
    try:
        _source, rec = master_records.winning(masters, owner, local, rtype)
    except Exception as e:   # MasterError
        raise SpecError(where, str(e))
    rec.subrecords = [s for s in rec.subrecords if s.tag not in drop]
    return rec


def build_worlds(spec, full, resolver, masters, master_records, warnings, references):
    """The WRLD top group: per world, its override, the persistent cell (when an object is persistent) and the
    exterior cells at the spec's grids, each with its references."""
    worlds = []
    for label, wspec in sorted(spec['worlds'].items()):
        where = 'worlds.%s' % label
        if master_records is None:
            raise SpecError(where, 'exterior cells need the master files (the game Data folder)')
        try:
            world_form = resolver.resolve(wspec.get('world', '@' + label), expect_types={'WRLD'})
        except FormIdError as e:
            raise SpecError(where, str(e))
        if world_form >> 24 >= len(masters):
            raise SpecError(where, '%s is not a master world' % label)
        owner = masters[world_form >> 24]
        try:
            f = master_records.file(owner)
            own = len(f.masters)
            layout = world_files.world_layout(f.path, (own << 24) | (world_form & 0xFFFFFF))
        except Exception as e:   # MasterError, WorldError
            raise SpecError(where, str(e))
        top = world_form & 0xFF000000

        world = _copy(master_records, masters, owner, world_form & 0xFFFFFF, 'WRLD', where, drop=('OFST',))
        persistent_refs, blocks = [], {}
        for cell_id, cell_spec in sorted(wspec.get('cells', {}).items()):
            cw = '%s.cells.%s' % (where, cell_id)
            grid = tuple(cell_spec['grid'])
            if grid not in layout['cells']:
                raise SpecError(cw + '.grid', 'no exterior cell at %d, %d in %s' % (grid[0], grid[1], label))
            raw, block, sub_block = layout['cells'][grid]
            refs = place_objects(cell_id, cell_spec.get('objects', []), cw, full, resolver, warnings, references)
            persistent_refs += [r for r in refs if r.flags & 0x400]
            temporary = [r for r in refs if not r.flags & 0x400]
            cell_form = top | (raw & 0xFFFFFF)
            entry = blocks.setdefault(block, {}).setdefault(sub_block, {})
            if cell_form not in entry:
                entry[cell_form] = [_copy(master_records, masters, owner, raw & 0xFFFFFF, 'CELL', cw), []]
            entry[cell_form][1] += temporary
        children = []
        if persistent_refs:
            if layout['persistent'] is None:
                raise SpecError(where, '%s has no persistent cell for persistent objects' % label)
            pform = top | (layout['persistent'] & 0xFFFFFF)
            children += [_copy(master_records, masters, owner, layout['persistent'] & 0xFFFFFF, 'CELL', where),
                         Group(struct_label(pform), 6, children=[
                             Group(struct_label(pform), 8, children=sorted(persistent_refs, key=lambda r: r.form_id))])]
        for block in sorted(blocks):
            subs = []
            for sub_block in sorted(blocks[block]):
                items = []
                for cell_form, (cell, temporary) in sorted(blocks[block][sub_block].items()):
                    items.append(cell)
                    if temporary:
                        items.append(Group(struct_label(cell_form), 6, children=[
                            Group(struct_label(cell_form), 9, children=sorted(temporary, key=lambda r: r.form_id))]))
                subs.append(Group(sub_block, 5, children=items))
            children.append(Group(block, 4, children=subs))
        worlds += [world, Group(struct_label(world.form_id), 1, children=children)]
    return Group(b'WRLD', 0, children=worlds)


def override_topic(edid, rec_spec, resolver, masters, templates):
    """A master topic copied from its template dump (same EditorID) with the master's FormID, so new lines can
    be added to it (GREETING and the like)."""
    unknown = set(rec_spec) - {'type', 'override', 'fields', 'infos', 'set'}
    if unknown:
        raise SpecError('records.%s' % edid, 'unknown field(s) for an override: %s' % ', '.join(sorted(unknown)))
    try:
        form = resolver.resolve('@' + edid, expect_types={'DIAL'})
    except FormIdError as e:
        raise SpecError('records.%s.override' % edid, str(e))
    if form >> 24 >= len(masters):
        raise SpecError('records.%s.override' % edid, '%s is not a master record' % edid)
    rec = generic.build_generic(form, edid, {'type': 'DIAL', 'template': edid, 'fields': rec_spec.get('fields', [])},
                                resolver, masters=masters, templates=templates)
    return rec


def build_lines(topic, rec_spec, topic_rec, full, resolver, script_cache):
    """INFO records of a topic, in spec order; each line's quest is added to the topic's quests (QSTI)."""
    def resolve(value, where):
        try:
            return resolver.resolve(value)
        except FormIdError as e:
            raise SpecError(where, str(e))

    def fields(subs, ops, where):
        generic.apply_fields(subs, ops, resolver, where)
    quests = {s.data for s in topic_rec.subrecords if s.tag == 'QSTI'}
    out = []
    for i, line in enumerate(rec_spec.get('infos', [])):
        where = 'records.%s.infos[%d]' % (topic, i)
        form = full[dialogue.info_key(topic, line)]
        rec = dialogue.build_line(form, topic, line, resolve, where, cache=script_cache, apply_fields=fields)
        out.append((line['key'], rec))
        qsti = rec.get('QSTI')
        if qsti not in quests:
            quests.add(qsti)
            subs = topic_rec.subrecords
            # A quest's INFC/INFX (info connections) follow its QSTI: add the new quest after the last of them.
            at = max([n for n, s in enumerate(subs) if s.tag in ('EDID', 'QSTI', 'INFC', 'INFX')]) + 1
            subs.insert(at, codec.Subrecord('QSTI', qsti))
    return out


def walk(group):
    """Yield every group and record inside (and including) a group."""
    yield group
    for child in group.children:
        if isinstance(child, Group):
            yield from walk(child)
        else:
            yield child


def struct_label(form_id):
    return form_id.to_bytes(4, 'little')


def int_label(value):
    return value.to_bytes(4, 'little', signed=True)


def canonical(spec):
    return json.dumps(spec, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def is_generated(plugin_bytes, project_id=None):
    """True if the plugin carries our generated marker (for the given project, if any)."""
    header = codec.parse(plugin_bytes).header
    snam = header.get('SNAM')
    if not snam:
        return False
    text = codec.cstring(snam)
    return (GENERATED_MARK % project_id) in text if project_id else '[geck-bridge generated:' in text


def adopt_existing(formids, spec, plugin_bytes):
    """Seed a FormID map from a hand-built plugin so a first rebuild keeps its IDs.

    Base records and cells match by EditorID; references match by position within
    each cell, in ascending FormID order of the existing plugin.
    """
    spec = check_spec(spec)
    plugin = codec.parse(plugin_bytes)
    by_edid, refs_by_cell, cell_of = {}, {}, {}
    for rec, path in plugin.records():
        if rec.editor_id:
            by_edid[rec.editor_id.lower()] = rec
        if rec.type in ('REFR', 'ACHR', 'ACRE'):
            owner = [g for g in path if g.group_type in (8, 9)]
            if owner:
                refs_by_cell.setdefault(int.from_bytes(owner[-1].label, 'little'), []).append(rec)
    adopted = []
    for edid in list(spec.get('records', {})) + list(spec.get('cells', {})):
        rec = by_edid.get(edid.lower())
        if rec is not None:
            formids.seed(edid, rec.form_id & 0xFFFFFF)
            adopted.append(edid)
            cell_of[rec.form_id] = edid
    for cell_form, refs in refs_by_cell.items():
        cell_id = cell_of.get(cell_form)
        if cell_id is None:
            continue
        objects = spec['cells'].get(cell_id, {}).get('objects', [])
        for i, (obj, rec) in enumerate(zip(objects, sorted(refs, key=lambda r: r.form_id))):
            key = ref_key(cell_id, i, obj)
            formids.seed(key, rec.form_id & 0xFFFFFF)
            adopted.append(key)
    return adopted
