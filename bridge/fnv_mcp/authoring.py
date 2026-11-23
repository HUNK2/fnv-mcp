"""Spec-driven plugin authoring for a configured project (Milestone 2): the builder behind fnv_spec.

Installing a build is fnv_install (the MO2 dev profile). Paths:
  <state_dir>/../build/<Plugin>.esp and <Plugin>.receipt.json   build outputs (default_build_dir())
  <state_dir>/formids/<project_id>.json                     stable FormID map
  <state_dir>/index/<master>.json.gz                        master indexes (esm_index.py); falls back to
                                                            this repo's state/index
  <state_dir>/templates/<master>/<EditorID>.json            record templates (esm_research.py dump);
                                                            falls back to this repo's state/templates
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .esp import build as builder, codec, diff, generic, scripts
from .esp.assets import AssetIndex, AssetError, nif_textures, bsa_extract
from .esp.formids import FormIdMap, MasterIndex
from .esp import index as esp_index
from .esp.masters import MasterRecords
from .esp.records import SpecError
from .esp.validate import validate
from .esp import codec as _codec  # noqa: F401 - registers the cp1252-bytes error handler (R16)


CODE_STATE = Path(__file__).resolve().parent.parent / 'state'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def default_build_dir(state_dir):
    """The build folder shared by the MCP tools and the CLIs: next to the project's state folder."""
    return Path(state_dir).parent / 'build'


def index_files(state_dir, masters):
    """Master index per master: <state_dir>/index first, then this repo's state/index."""
    files = []
    for m in masters:
        for folder in (Path(state_dir) / 'index', CODE_STATE / 'index'):
            path = folder / (m + '.json.gz')
            if path.exists():
                files.append(path)
                break
    return files


class AuthoringError(ValueError):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code, self.evidence = code, evidence


class Authoring:
    def __init__(self, project, spec_path=None, build_dir=None):
        self.project = project
        self.spec_path = Path(spec_path) if spec_path else getattr(project, 'spec', None)
        if not self.spec_path:
            raise AuthoringError('NO_SPEC', 'Project %s has no "spec" configured' % project.project_id)
        self.build_dir = Path(build_dir) if build_dir else default_build_dir(project.state_dir)
        self.data_dir = project.game_root / 'Data'

    # -- inputs -----------------------------------------------------------------------
    def load_spec(self, override=None):
        spec = builder.check_spec(override) if override is not None else builder.load_spec(self.spec_path.read_text(encoding="utf-8"))
        if spec['plugin'] != self.project.plugin:
            raise SpecError('plugin', 'spec builds %s but the project is configured for %s'
                            % (spec['plugin'], self.project.plugin))
        return spec

    def index(self, masters):
        """The EditorID index of the masters. A master with no index file whose file is found gets one, written to
        <state_dir>/index (about 6 s for FalloutNV.esm, once)."""
        have = {Path(p).name[:-len('.json.gz')].lower() for p in index_files(self.project.state_dir, masters)}
        sources = self.master_records(masters)
        for m in masters:
            if m.lower() not in have and sources is not None and sources.has(m):
                esp_index.write_index(sources.paths[m.lower()], Path(self.project.state_dir) / 'index')
        return MasterIndex(index_files(self.project.state_dir, masters))

    @property
    def templates(self):
        return generic.template_dirs(self.project.state_dir)

    def master_records(self, masters):
        """The master files templates and overrides are read from: each master in a dev profile mod folder (highest
        priority first) or the game Data folder. None without a Data folder (then only template dumps work)."""
        if not self.data_dir.is_dir():
            return None
        paths = []
        roots = self.mod_roots() + [self.data_dir]
        for m in masters:
            hit = next((root / m for root in roots if (root / m).is_file()), None)
            if hit:
                paths.append(hit)
        return MasterRecords(paths) if paths else None

    def formids(self, spec):
        return FormIdMap(self.project.state_dir / 'formids' / (spec['project_id'] + '.json'), spec['plugin'])

    @property
    def built_path(self):
        return self.build_dir / self.project.plugin

    @property
    def receipt_path(self):
        return self.build_dir / (Path(self.project.plugin).stem + '.receipt.json')

    def receipt(self):
        if not self.receipt_path.exists():
            raise AuthoringError('NOT_BUILT', 'No build receipt yet; build first')
        return json.loads(self.receipt_path.read_text(encoding="utf-8"))

    def mod_roots(self):
        """Loose-file roots of the MO2 dev profile (fnv.mo2_root / mo2_profile): the enabled mods, highest priority
        first, and the plugin's own mod folder (mods/<plugin name>, where fnv_install puts it). A mesh a mod ships
        loose lives there, not in Data (seen 2026-11-11: a crate mesh in its mod folder failed the build's check)."""
        fnv = getattr(self.project, 'fnv', None)
        if not fnv or not fnv.mo2_root or not fnv.mo2_profile:
            return []           # the direct install: loose files are in Data, which the build checks anyway
        mods = Path(fnv.mo2_root) / 'mods'
        roots = []
        modlist = Path(fnv.mo2_root) / 'profiles' / fnv.mo2_profile / 'modlist.txt'
        if modlist.exists():
            for line in modlist.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith('+') and (mods / line[1:].strip()).is_dir():
                    roots.append(mods / line[1:].strip())
        own = mods / Path(self.project.plugin).stem
        if own.is_dir() and own not in roots:
            roots.insert(0, own)
        return roots

    # -- build ---------------------------------------------------------------------------
    def asset_manifest(self, data, assets):
        """Content identities for referenced loose assets; archive identities remain explicit."""
        wanted, models = set(), set()
        for rec, _ in codec.parse(data).records():
            for sub in rec.subrecords:
                if sub.tag in ('MODL', 'NIFZ'):
                    for value in sub.data.split(b'\0'):
                        if value:
                            models.add('meshes\\' + value.decode('cp1252', 'cp1252-bytes'))
                elif sub.tag in ('ICON', 'MICO') and sub.data:
                    wanted.add('textures\\' + codec.cstring(sub.data))
        wanted |= models
        result = {}
        if assets is None:
            return {'scope': 'asset checks unavailable', 'files': {}, 'archives': []}
        archived = {}
        for name in assets.archives:
            archived.update(bsa_extract(self.data_dir / name, {m for m in models if (assets.find(m) or {}).get('where') == 'bsa'}))
        for model in sorted(models):
            hit = assets.find(model)
            if hit:
                try:
                    payload = Path(hit['path']).read_bytes() if hit['where'] == 'loose' else archived.get(model.lower())
                    if payload is not None:
                        wanted.update(nif_textures(payload))
                except AssetError:
                    pass  # validate() records this as a failure; never used to authorize installation
        for name in assets.archives:
            archived.update(bsa_extract(self.data_dir / name, {m for m in wanted if (assets.find(m) or {}).get('where') == 'bsa'}))
        for relative in sorted(wanted):
            hit = assets.find(relative)
            result[relative.lower()] = ({'where': 'missing'} if not hit else
                                       {'where': 'loose', 'sha256': sha(Path(hit['path']).read_bytes())}
                                       if hit['where'] == 'loose' else {'where': 'bsa',
                                           'sha256': sha(archived[relative.lower()]) if relative.lower() in archived else None})
        archives = [{'name': name, 'size': (self.data_dir / name).stat().st_size,
                     'modifiedNs': (self.data_dir / name).stat().st_mtime_ns} for name in assets.archives]
        return {'scope': 'MODL/NIFZ models, model texture references, ICON/MICO; content hashes and archive stat identity',
                'files': result, 'archives': archives}

    def compile_context(self, spec):
        """Conservative invalidation: any source, template, index or compiler change invalidates reuse."""
        inputs = {'spec': spec, 'sources': {}, 'files': {}, 'formids': self.formids(spec).assigned}
        for edid, rec in spec.get('records', {}).items():
            if rec.get('type') == 'SCPT':
                inputs['sources'][edid] = sha(generic.script_text(self.spec_path.parent, rec, edid, edid))
            if rec.get('template') and generic.dump_exists(rec['template'], spec['masters'][0], self.templates):
                template = generic.load_template(rec['template'], spec['masters'][0], self.templates)
                inputs['files']['template:' + edid] = sha(json.dumps(template, sort_keys=True).encode())
        sources = self.master_records(spec['masters'])
        for name, path in sorted((sources.paths if sources else {}).items()):
            st = path.stat()        # a template or override read from a master changes when the master does
            inputs['files']['master:' + name] = [st.st_size, st.st_mtime_ns]
        for path in index_files(self.project.state_dir, spec['masters']):
            inputs['files'][str(path.resolve())] = sha(path.read_bytes())
        return sha(json.dumps(inputs, sort_keys=True).encode())

    def build(self, write=False, spec_override=None, adopt=None, check_assets=True, compare=None):
        """Build (and validate) the plugin. Without write, nothing is saved (a dry run)."""
        spec = self.load_spec(spec_override)
        formids = self.formids(spec)
        if adopt is not None:
            if formids.assigned or formids.retired:
                raise SpecError('adopt', 'FormID map already exists; adoption is only for the first build')
            builder.adopt_existing(formids, spec, Path(adopt).read_bytes())
        index = self.index(spec['masters'])
        context = self.compile_context(spec)
        cache = {edid: entry for edid, entry in scripts.load_cache(self.project.state_dir, spec['project_id']).items()
                 if entry.get('provenance', {}).get('context') == context}
        data, receipt = builder.build(spec, formids, index, base_dir=self.spec_path.parent,
                                      script_cache=cache,
                                      templates=self.templates, master_records=self.master_records(spec['masters']))
        assets = AssetIndex(self.data_dir, self.mod_roots()) if check_assets and self.data_dir.is_dir() else None
        receipt['validation'] = validate(data, index, assets, spec['masters'])
        receipt['assets'] = self.asset_manifest(data, assets)
        receipt['compileContext'] = context
        missing = [m for m in spec['masters'] if m not in index.masters]
        if missing:
            receipt['validation']['warnings'].append('no index for %s: master references unchecked '
                                                     '(run esm_index.py)' % ', '.join(missing))
        if assets is None:
            receipt['validation']['warnings'].append('asset checks skipped (game Data folder not available)')
        if compare is not None:
            receipt['comparedWith'] = str(compare)
            receipt['differences'] = diff.diff(Path(compare).read_bytes(), data)
        receipt['written'] = False
        if write and receipt['validation']['ok']:
            self.build_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(self.built_path, data)
            receipt['written'] = True
            formids.save()
            receipt['compileContext'] = self.compile_context(spec)
            atomic_write(self.receipt_path, (json.dumps(receipt, indent=1) + '\n').encode())
        return receipt, data

    # -- read-only helpers -------------------------------------------------------------------
    def installed_path(self):
        """Where fnv_install put the plugin: its MO2 mod folder (mods/<plugin name>) when the project has a dev
        profile and that copy exists, else the game Data folder."""
        fnv = getattr(self.project, 'fnv', None)
        if fnv and fnv.mo2_root:
            own = Path(fnv.mo2_root) / 'mods' / Path(self.project.plugin).stem / self.project.plugin
            if own.exists():
                return own
        return self.data_dir / self.project.plugin

    def inspect(self, which='build'):
        path = self.installed_path() if which == 'installed' else self.built_path
        if not path.exists():
            raise AuthoringError('NOT_FOUND', '%s plugin not found: %s' % (which, path))
        data = path.read_bytes()
        plugin = codec.parse(data)
        types, cells = {}, {}
        for rec, groups in plugin.records():
            types[rec.type] = types.get(rec.type, 0) + 1
            if rec.type == 'CELL':
                cells['%08X' % rec.form_id] = {'editorId': rec.editor_id, 'references': 0}
            if rec.type in ('REFR', 'ACHR', 'ACRE'):
                owner = [g for g in groups if g.group_type in (8, 9, 10)]
                if owner:
                    key = '%08X' % int.from_bytes(owner[-1].label, 'little')
                    cells.setdefault(key, {'editorId': None, 'references': 0})['references'] += 1
        header = plugin.header
        return {'which': which, 'path': str(path), 'sha256': sha(data), 'size': len(data),
                'masters': [codec.cstring(s.data) for s in header.subrecords if s.tag == 'MAST'],
                'author': codec.cstring(header.get('CNAM')) if header.get('CNAM') else None,
                'generated': builder.is_generated(data, self.load_spec()['project_id']),
                'recordCounts': types, 'cells': cells,
                'matchesBuild': self.receipt_path.exists() and sha(data) == self.receipt()['sha256']}

    def lookup(self, query, record_type=None, limit=25):
        spec = self.load_spec()
        index = self.index(spec['masters'])
        own = []
        for edid, rec in spec.get('records', {}).items():
            if query.lower() in edid.lower() and (record_type is None or rec['type'] == record_type):
                own.append({'editorId': edid, 'type': rec['type'], 'source': spec['plugin'], 'reference': '@' + edid})
        rows = index.search(query, record_type, limit)
        for row in rows:
            row['reference'] = '@' + row['editorId']
        return {'query': query, 'type': record_type, 'project': own, 'masters': rows,
                'indexedMasters': index.masters}


def atomic_write(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
