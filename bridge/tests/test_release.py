"""Release plan Task 5.2: the fnv-mcp source download, against a disposable Git repository."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location(
    'package_release', Path(__file__).resolve().parents[2] / 'tools/package_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

REPO = Path(__file__).resolve().parents[2]


def tracked():
    """The files git tracks in this checkout (relative POSIX paths), or None outside a git checkout (a zip)."""
    try:
        out = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z'], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [n for n in out.decode('utf-8').split('\0') if n]


def text_of(name):
    """A tracked file's text, or None for a binary or missing file."""
    try:
        data = (REPO / name).read_bytes()
    except OSError:
        return None
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', errors='replace')


def code_line(name, line):
    """The part of a source line that is code: Python and C comments dropped, C string escapes blanked."""
    stripped = line.strip()
    if stripped.startswith(('#', '*', '/*', '//')):
        return ''
    body = line.split('#', 1)[0] if name.endswith('.py') else line.split('//', 1)[0]
    return re.sub(r'\\[ntr]', ' ', body)


class RepositoryLeakTests(unittest.TestCase):
    """Detach from one PC (2026-11-26): nothing tracked names the machine it was written on, and nothing that only
    belongs on one machine is tracked. The machine values are read here at run time, so the test names none."""

    @classmethod
    def setUpClass(cls):
        cls.names = tracked()
        if cls.names is None:
            raise unittest.SkipTest('not a git checkout')

    @staticmethod
    def machine_values():
        values = set()
        user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
        if len(user) >= 4 and user.lower() not in {'user', 'admin', 'runner', 'runneradmin', 'test', 'root', 'home'}:
            values.add(user)
        home = os.environ.get('USERPROFILE') or str(Path.home())
        if len(Path(home).parts) > 2:
            values |= {home, home.replace('\\', '/')}
        parent = REPO.parent
        if len(parent.parts) > 1:           # the folder the clone sits in, unless it is a drive root
            values |= {str(parent), str(parent).replace('\\', '/')}
            if parent.name.lower() != REPO.name.lower():    # CI clones into <name>/<name>: the project's own name
                values.add(parent.name)
        try:
            email = subprocess.check_output(['git', '-C', str(REPO), 'config', 'user.email'],
                                            stderr=subprocess.DEVNULL).decode().strip()
        except (OSError, subprocess.CalledProcessError):
            email = ''
        if email and 'noreply' not in email and not email.endswith('.invalid'):
            values.add(email)
        return {v for v in values if len(v) >= 4 and v.lower() not in {'work', 'src', 'code', 'repos', 'projects'}}

    def test_no_tracked_file_names_this_machine(self):
        values = self.machine_values()
        if not values:
            self.skipTest('nothing machine-specific to look for here')
        pattern = re.compile('|'.join(re.escape(v) for v in sorted(values, key=len, reverse=True)), re.I)
        hits = []
        for name in self.names:
            for number, line in enumerate((text_of(name) or '').splitlines(), 1):
                if pattern.search(line):
                    hits.append('%s:%d' % (name, number))
        self.assertEqual(hits, [], 'tracked files name this machine (user, home, clone folder or e-mail)')

    def test_code_has_no_absolute_machine_path(self):
        """A drive path in code is a default that holds on one machine only: find it (fnv_mcp.locate) or make it a
        setting. Comments, C:/path/to placeholders and the C:\\Windows fallback for SystemRoot are allowed."""
        drive = re.compile(r'(?<![A-Za-z0-9_])[A-Za-z]:[\\/]+(?![\\/])(?!path[\\/]to\b|Windows\b)', re.I)
        code = [n for n in self.names if n.endswith(('.py', '.c', '.h', '.json')) and (
            n.startswith(('bridge/fnv_mcp/', 'bridge/tools/', 'bridge/engine_map/', 'bridge/native/', 'tools/'))
            or n in ('bridge/run_mcp.py', 'bridge/esm_index.py'))]
        hits = []
        for name in code:
            for number, line in enumerate((text_of(name) or '').splitlines(), 1):
                if drive.search(code_line(name, line)):
                    hits.append('%s:%d: %s' % (name, number, line.strip()[:100]))
        self.assertEqual(hits, [], 'hard-coded drive paths in code')

    def test_example_projects_are_generic(self):
        examples = [n for n in self.names if n.startswith('bridge/projects/')]
        self.assertTrue(examples)
        for name in examples:
            with self.subTest(name=name):
                self.assertTrue(Path(name).name.startswith('example-'), 'only example-*.json is tracked')
                data = json.loads(text_of(name))
                self.assertEqual(data.get('game_root'), 'auto')
                values = [data.get('state_dir'), data.get('spec')] + list((data.get('fnv') or {}).values())
                for value in values:
                    if isinstance(value, str):
                        self.assertFalse(re.match(r'^[A-Za-z]:|^[\\/]|^~', value), value)

    def test_nothing_machine_local_is_tracked(self):
        bad = re.compile(r'(^|/)(state|\.venv|evidence|photos|live-check|\.fnvdecomp)/'
                         r'|\.(exe|dll|pdb|obj|sqlite3?|jsonl|fos|zip|7z)$|(^|/)(api_key|\.env)$', re.I)
        self.assertEqual([n for n in self.names if bad.search(n)], [])

    def test_gitignore_keeps_local_files_out(self):
        probes = ['bridge/state/x.json', 'bridge/.venv/x.py', 'bridge/projects/my-fnv.json', 'bridge/x.exe',
                  'bridge/x.dll', 'bridge/x.pdb', 'bridge/x.sqlite', 'bridge/x.jsonl', 'bridge/x.fos', 'api_key',
                  '.env', 'bridge/build/x.obj']
        out = subprocess.run(['git', '-C', str(REPO), 'check-ignore', '--no-index', *probes],
                             capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(set(probes) - set(out)), [])
        kept = subprocess.run(['git', '-C', str(REPO), 'check-ignore', '--no-index',
                               'bridge/projects/example-fnv.json'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(kept, '')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'test@example.invalid')
        for name, data in {
            'README.md': b'# Test',
            'CHANGELOG.md': b'# Changelog\n\n## fnv-mcp 0.3.0 - unreleased\n',
            'LICENSE': b'Synthetic licence',
            'bridge/pyproject.toml': b'[project]\nversion="0.3.0"\n',
            'bridge/fnv_mcp/__init__.py': b'__version__="0.3.0"\n',
            'bridge/examples/fnv-tests/a.yaml': b'name: a\n',
            'bridge/examples/q/scripts/Q.gek': b'scn Q\n',
            'bridge/docs/BUG-LOG.md': b'internal',
            'bridge/docs/audit4/notes.md': b'internal',
            'bridge/native/runtime/companion.c': b'/* synthetic source */',
            'bridge/Probe.Mcp.exe': b'fake exe',
            'bridge/native/generated.dll': b'fake dll',
            'bridge/tests/fixtures/test.esp': b'fake plugin',
            'bridge/tests/test_build.py': b'fixture dependent',
            'bridge/projects/private.json': b'private profile',
            'bridge/projects/example-windows.json': b'{}',
            'bridge/state/raw.json': b'private evidence',
            'HANDOFF.md': b'coordination',
        }.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic source')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL)

    def test_archive_uses_committed_source_and_omits_private_and_binary_files(self):
        (self.repo / 'README.md').write_text('dirty working edit')
        (self.repo / 'bridge/untracked.py').write_text('not committed')
        output = self.root / 'one.zip'
        manifest = release.package(self.repo, 'HEAD', output)
        with zipfile.ZipFile(output) as z:
            self.assertIsNone(z.testzip())
            prefix = release.prefix('0.3.0')
            self.assertEqual(prefix, 'fnv-mcp-0.3.0/')
            self.assertEqual(z.read(prefix + 'README.md'), b'# Test')
            self.assertEqual(set(z.namelist()), {prefix + p for p in (
                'README.md', 'CHANGELOG.md', 'LICENSE', 'bridge/pyproject.toml', 'bridge/fnv_mcp/__init__.py',
                'bridge/native/runtime/companion.c', 'bridge/projects/example-windows.json',
                'bridge/examples/fnv-tests/a.yaml', 'bridge/examples/q/scripts/Q.gek')})
        self.assertEqual(manifest['name'], 'fnv-mcp')
        self.assertEqual(manifest, json.loads(output.with_suffix('.json').read_text()))
        self.assertEqual(manifest['sourceCommit'], self.git('rev-parse', 'HEAD').decode().strip())

    def test_reproducible_and_refuses_overwrite(self):
        first, second = self.root / 'one.zip', self.root / 'two.zip'
        release.package(self.repo, 'HEAD', first)
        release.package(self.repo, 'HEAD', second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(FileExistsError):
            release.package(self.repo, 'HEAD', first)

    def test_working_candidate_is_explicit_and_binds_uncommitted_source(self):
        (self.repo / 'bridge/new_feature.py').write_bytes(b'synthetic = True\n')
        output = self.root / 'candidate.zip'
        manifest = release.package(self.repo, 'HEAD', output, working_tree=True)
        self.assertTrue(manifest['candidateOnly'])
        self.assertFalse(manifest['releaseReady'])
        with zipfile.ZipFile(output) as archive:
            prefix = release.prefix('0.3.0')
            self.assertEqual(archive.read(prefix + 'bridge/new_feature.py'), b'synthetic = True\n')
            candidate = json.loads(archive.read(prefix + 'CANDIDATE.json'))
            self.assertFalse(candidate['releaseReady'])
            self.assertEqual(candidate['fileSha256'], manifest['fileSha256'])

    def test_mismatched_versions_refused_before_output(self):
        (self.repo / 'bridge/fnv_mcp/__init__.py').write_text('__version__="9.9.9"\n')
        self.git('add', 'bridge/fnv_mcp/__init__.py')
        self.git('commit', '-qm', 'Synthetic mismatch')
        output = self.root / 'bad.zip'
        with self.assertRaisesRegex(ValueError, 'version markers'):
            release.package(self.repo, 'HEAD', output)
        self.assertFalse(output.exists())

    def test_a_folder_output_gets_the_versioned_name(self):
        folder = self.root / 'out'
        folder.mkdir()
        manifest = release.package(self.repo, 'HEAD', folder)
        self.assertEqual(manifest['archive'], 'fnv-mcp-0.3.0.zip')
        self.assertTrue((folder / 'fnv-mcp-0.3.0.zip').is_file())

    def test_a_changelog_on_another_version_is_refused(self):
        (self.repo / 'CHANGELOG.md').write_text('# Changelog\n\n## fnv-mcp 9.9.9\n')
        self.git('add', 'CHANGELOG.md')
        self.git('commit', '-qm', 'Synthetic changelog')
        with self.assertRaisesRegex(ValueError, 'version markers'):
            release.package(self.repo, 'HEAD', self.root / 'bad.zip')

    def test_the_real_repository_packages(self):
        repo = Path(__file__).resolve().parents[2]
        manifest = release.package(repo, 'HEAD', self.root)
        self.assertEqual(manifest['name'], 'fnv-mcp')
        with zipfile.ZipFile(self.root / manifest['archive']) as z:
            names = z.namelist()
        self.assertFalse([n for n in names if n.lower().endswith(('.exe', '.dll', '.esp', '.nif'))])
        self.assertTrue(any(n.endswith('bridge/run_mcp.py') for n in names))
        self.assertTrue(any(n.endswith('.yaml') for n in names))

    def test_unsafe_paths_refused(self):
        for name in ('../outside.py', '/outside.py', 'bridge/../../outside.py',
                     'bridge\\outside.py', 'bridge/file:stream.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.included(name)
