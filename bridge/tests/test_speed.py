"""Speed regressions. Each test counts work done (SQLite steps, file reads, tokenizing), never seconds, so a slow
or busy machine cannot fail it and a fast one cannot hide a slow path."""
import os
import re
import struct
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_engine, fnv_game, fnv_link, fnv_records, fnv_tools  # noqa: E402
from geck_mcp.fnv_data import DataTools  # noqa: E402
from geck_mcp.fnv_tools import FnvService  # noqa: E402
from test_fnv import MISC_C, FnvTestBase, write  # noqa: E402


def sub(tag, data):
    return tag.encode() + struct.pack('<H', len(data)) + data


def record(tag, form_id, *subs):
    body = b''.join(subs)
    return tag.encode() + struct.pack('<IIIIHH', len(body), 0, form_id, 0, 15, 0) + body


def write_plugin(path, records, masters=()):
    head = b''.join(sub('MAST', m.encode() + b'\0') + sub('DATA', b'\0' * 8) for m in masters)
    Path(path).write_bytes(b'TES4' + struct.pack('<IIIIHH', len(head), 0, 0, 0, 15, 0) + head + b''.join(records))


def edid(name):
    return sub('EDID', name.encode() + b'\0')


def sqlite_steps(db, fn):
    """SQLite virtual machine steps `fn` makes this connection run, to the nearest 100."""
    ticks = [0]

    def tick():
        ticks[0] += 1
    db.set_progress_handler(tick, 100)
    try:
        fn()
    finally:
        db.set_progress_handler(None, 0)
    return ticks[0] * 100


class RecordLookupSpeed(unittest.TestCase):
    """fnv_conflicts and fnv_record action=users looked up each record version through the per-file index: one walk
    over every record of the plugin (465,016 in FalloutNV.esm) per lookup. 43 s for ten conflicts on a real load
    order."""

    BIG = 3000       # records in the master
    FEW = 40         # of them a second plugin touches

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # per-thread connections close on GC
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = DataTools(SimpleNamespace(state_dir=str(self.root / 'state')))
        self.addCleanup(lambda: self.data.index.db.close())
        self.tools = fnv_records.RecordsTools(self.data)
        self.tools._cond = {}        # no engine map: every condition parameter is a candidate

    def load(self, *plugins):
        """Index the plugins as the active load order, in the order given."""
        entries = []
        for index, path in enumerate(plugins):
            entries.append({'index': index, 'name': path.name, 'path': str(path), 'active': True,
                            'fileId': self.data.index.ensure(path)})
        self.data._active = lambda: (entries, None)
        return entries

    def one_pass(self):
        """What reading every record row once costs: the unit the tests compare against."""
        db = self.data.index.db
        return sqlite_steps(db, lambda: db.execute('select sum(offset), sum(flags) from records').fetchall())

    def test_conflicts_do_not_walk_the_master_once_per_conflict(self):
        big, patch = self.root / 'Big.esm', self.root / 'Patch.esp'
        write_plugin(big, [record('STAT', 0x800 + i, edid('Rock%d' % i)) for i in range(self.BIG)])
        write_plugin(patch, [record('STAT', 0x800 + i, edid('Stone%d' % i)) for i in range(self.FEW)],
                     masters=['Big.esm'])
        self.load(big, patch)
        out = {}
        used = sqlite_steps(self.data.index.db, lambda: out.update(self.tools.conflicts(limit=self.FEW)))
        self.assertEqual(out['count'], self.FEW)
        self.assertEqual(out['conflicts'][0]['winner'], 'Patch.esp')
        self.assertEqual(out['conflicts'][0]['overrides'][0]['changes'], ['EDID'])
        passes = used / self.one_pass()
        self.assertLess(passes, self.FEW / 2, 'conflicts read the record table %.0f times over for %d conflicts'
                        % (passes, self.FEW))

    def test_conflicts_of_one_plugin_do_not_group_the_whole_load_order(self):
        # fnv_author check and fnv_conflicts plugin=X grouped every record of the load order (370 ms) to keep the few
        # groups the plugin is in
        big, patch, other = self.root / 'Big.esm', self.root / 'Patch.esp', self.root / 'Other.esp'
        write_plugin(big, [record('STAT', 0x800 + i, edid('Rock%d' % i)) for i in range(self.BIG)])
        write_plugin(patch, [record('STAT', 0x800 + i, edid('Stone%d' % i)) for i in range(self.FEW)],
                     masters=['Big.esm'])
        write_plugin(other, [record('STAT', 0x800 + i, edid('Pebble%d' % i)) for i in range(self.BIG // 2)],
                     masters=['Big.esm'])
        self.load(big, patch, other)
        out = {}
        used = sqlite_steps(self.data.index.db, lambda: out.update(self.tools.conflicts(plugin='Patch.esp',
                                                                                       limit=self.FEW)))
        self.assertEqual(out['count'], self.FEW)
        self.assertEqual(out['plugin'], 'Patch.esp')
        self.assertEqual(out['conflicts'][0]['plugins'], ['Big.esm', 'Patch.esp', 'Other.esp'])
        self.assertTrue(all('Patch.esp' in c['plugins'] for c in out['conflicts']))
        self.assertLess(used, self.one_pass() / 2, 'conflicts of one plugin read the whole record table')

    def test_conflicts_of_one_plugin_keep_the_type_filter(self):
        big, patch = self.root / 'Big.esm', self.root / 'Patch.esp'
        write_plugin(big, [record('STAT', 0x800, edid('Rock')), record('MISC', 0x801, edid('Can'))])
        write_plugin(patch, [record('STAT', 0x800, edid('Stone')), record('MISC', 0x801, edid('Tin'))],
                     masters=['Big.esm'])
        self.load(big, patch)
        out = self.tools.conflicts(plugin='Patch.esp', record_type='misc')
        self.assertEqual([c['type'] for c in out['conflicts']], ['MISC'])

    def test_users_do_not_walk_the_master_once_per_dialogue_line(self):
        target = 0x800
        lines = [record('INFO', 0x900 + i, sub('QSTI', struct.pack('<I', target)), sub('NAM1', b'Line %d\0' % i))
                 for i in range(self.FEW)]
        filler = [record('STAT', 0x2000 + i, edid('Rock%d' % i)) for i in range(self.BIG)]
        big = self.root / 'Big.esm'
        write_plugin(big, [record('NPC_', target, edid('Sunny'))] + lines + filler)
        self.load(big)
        self.tools.record_users('Sunny', limit=1)            # builds the reference index: not what is measured
        out = {}
        used = sqlite_steps(self.data.index.db,
                            lambda: out.update(self.tools.record_users('Sunny', limit=self.FEW)))
        self.assertEqual(out['count'], self.FEW)
        self.assertEqual(sorted(u['text'] for u in out['users'])[0], 'Line 0')
        passes = used / self.one_pass()
        self.assertLess(passes, self.FEW / 2, 'users read the record table %.0f times over for %d dialogue lines'
                        % (passes, self.FEW))


@unittest.skipUnless(sys.platform == 'win32', 'Windows processes')
class GameProcessSpeed(unittest.TestCase):
    """"Is the game running" started tasklist.exe (about 70 ms) on nearly every tool call and every pipe connect."""

    def test_finding_the_game_starts_no_program(self):
        me = Path(fnv_game.image_path(os.getpid())).name         # this test process stands in for the game
        refuse = AssertionError('a program was started to list processes')
        with mock.patch.object(fnv_game, 'GAME_EXE', me), \
                mock.patch.object(fnv_game.subprocess, 'run', side_effect=refuse), \
                mock.patch.object(fnv_game.subprocess, 'Popen', side_effect=refuse):
            found = fnv_game.game_processes()
        self.assertIn(os.getpid(), [pid for pid, _ in found])
        started = dict(found)[os.getpid()]
        self.assertEqual(started, fnv_game.process_started(os.getpid()))

    def test_a_program_that_is_not_running_is_not_found(self):
        with mock.patch.object(fnv_game, 'GAME_EXE', 'fnvmcp-no-such-%s.exe' % uuid.uuid4().hex[:8]):
            self.assertEqual(fnv_game.game_processes(), [])


class Clock:
    """Stands in for the time module inside one module under test: a sleep moves its clock on, at once."""

    def __init__(self):
        self.now, self.slept = 1000.0, 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept += seconds


@unittest.skipUnless(sys.platform == 'win32', 'named pipes')
class NoGameSpeed(unittest.TestCase):
    """With the game closed every live tool waited half a second for the companion's pipe before saying so."""

    def link(self):
        return fnv_link.EngineLink('fnvmcp-test-none-%s' % uuid.uuid4().hex[:8])

    def connect(self, link):
        """(error code, seconds slept) of one connect attempt to a pipe nobody serves."""
        clock = Clock()
        with mock.patch.object(fnv_link, 'time', clock):
            with self.assertRaises(fnv_link.LinkError) as caught:
                link.connect(wait=0.5)
        return caught.exception.code, clock.slept

    def test_no_wait_for_a_pipe_when_no_game_process_exists(self):
        link = self.link()
        link.server_exists = lambda: False
        self.assertEqual(self.connect(link), ('COMPANION_UNREACHABLE', 0))

    def test_a_running_game_still_gets_its_wait(self):
        # the companion closes and recreates its pipe between clients: with a game running, a missing pipe is retried
        link = self.link()
        link.server_exists = lambda: True
        code, slept = self.connect(link)
        self.assertEqual(code, 'COMPANION_UNREACHABLE')
        self.assertGreater(slept, 0)

    def test_a_link_without_the_check_waits_as_before(self):
        code, slept = self.connect(self.link())
        self.assertEqual(code, 'COMPANION_UNREACHABLE')
        self.assertGreater(slept, 0)

    def test_a_failing_check_counts_as_a_running_game(self):
        link = self.link()

        def broken():
            raise OSError('no process list')
        link.server_exists = broken
        code, slept = self.connect(link)
        self.assertEqual(code, 'COMPANION_UNREACHABLE')
        self.assertGreater(slept, 0)

    def test_the_game_object_installs_the_check(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=tmp.name, mo2_profile='Dev', engine_pipe=None),
                                  game_root=tmp.name, state_dir=tmp.name)
        check = getattr(fnv_game.Game(project).link, 'server_exists', None)
        self.assertIsNotNone(check, 'the game object gives its link no way to ask whether a game runs')
        with mock.patch.object(fnv_game, 'game_processes', lambda: []):
            self.assertIs(check(), False)
        with mock.patch.object(fnv_game, 'game_processes', lambda: [(4242, 1)]):
            self.assertIs(check(), True)


class CountingRe:
    """Stands in for the re module inside fnv_engine: records every text a compiled pattern searches."""

    def __init__(self):
        self.searched = []

    def __getattr__(self, name):
        return getattr(re, name)

    def compile(self, pattern, flags=0):
        real, searched = re.compile(pattern, flags), self.searched

        class Pattern:
            def search(self, text, *args):
                searched.append(text)
                return real.search(text, *args)

            def __getattr__(self, name):
                return getattr(real, name)
        return Pattern()


class EngineSearchSpeed(FnvTestBase):
    """fnv_func on the real map: a name search split all 68,657 function names into words again on every call
    (0.57 s), and a code search ran a case-insensitive regex over all 118 MB of decomp source (0.5 s), twice for
    action=strings."""

    def test_a_second_name_search_splits_only_the_query(self):
        engine = fnv_engine.EngineMap(self.map_path)
        first = engine.find_by_name('limb damage')
        with mock.patch.object(fnv_engine, '_words', wraps=fnv_engine._words) as words:
            second = engine.find_by_name('limb damage')
            other = engine.find_by_name('apply')
        self.assertEqual(second, first)
        self.assertEqual(first[1][0]['address'], '0x00401100')
        self.assertEqual({r['address'] for r in other[1]}, {'0x00401000', '0x00401100'})
        self.assertEqual(words.call_count, 2, 'the names of the functions were split into words again')

    def test_the_first_name_search_splits_only_the_query_too(self):
        # the key of a name is its letters and digits in lowercase: no word splitting is needed to build it
        engine = fnv_engine.EngineMap(self.map_path)
        with mock.patch.object(fnv_engine, '_words', wraps=fnv_engine._words) as words:
            total, results = engine.find_by_name('limb damage')
        self.assertEqual((total, results[0]['address']), (1, '0x00401100'))
        self.assertEqual(words.call_count, 1)

    def test_a_name_key_is_the_words_of_the_names_run_together(self):
        # what find_by_name matched against before the keys were kept: every word of the three names, joined
        engine = fnv_engine.EngineMap(self.map_path)
        odd = {'name': 'TESObjectREFR::~Get3D_Node2 İKK', 'decompName': 'FUN_00401200', 'status': 'decomp',
               'proof': {'name': 'NiAVObject::UpdateWorldData'}}
        engine.functions[0x401200].update(odd)
        keys = engine.name_keys()
        for address, f in engine.functions.items():
            names = (f['name'], f.get('decompName') or '', (f.get('proof') or {}).get('name') or '')
            self.assertEqual(keys[address], ''.join(w for n in names for w in fnv_engine._words(n)), f['name'])
        self.assertEqual(keys[0x401200], 'tesobjectrefrget3dnode2ikkfun00401200niavobjectupdateworlddata')

    def test_a_file_without_the_text_is_not_searched_with_the_regex(self):
        engine = fnv_engine.EngineMap(self.map_path)
        counting = CountingRe()
        with mock.patch.object(fnv_engine, 're', counting):
            total, results = engine.find_in_code('fcombatplayerLIMBdamagemult')
        self.assertEqual([r['address'] for r in results], ['0x00401100'])
        self.assertEqual(results[0]['matches'], [{'line': 21, 'text':
                                                  'm_fHealth -= fCombatPlayerLimbDamageMult; /* limb damage */'}])
        self.assertTrue(counting.searched, 'the file with the text is still searched line by line')
        in_misc = [t for t in counting.searched if 'FUN_00401200' in t]      # only src/misc.c holds this name
        self.assertEqual(in_misc, [], 'src/misc.c does not hold the text, yet the regex ran over it')

    def test_a_regex_search_skips_files_without_its_fixed_part(self):
        engine = fnv_engine.EngineMap(self.map_path)
        counting = CountingRe()
        with mock.patch.object(fnv_engine, 're', counting):
            total, results = engine.find_in_code(r'\bfCombat\w+Mult\b', regex=True)
        self.assertEqual((total, [r['address'] for r in results]), (1, ['0x00401100']))
        self.assertEqual([t for t in counting.searched if 'FUN_00401200' in t], [])

    def test_strings_reads_each_file_once(self):
        import io
        service = FnvService(self.project)
        service.engine()
        opened = []

        def counting(real):
            def opener(file, *args, **kwargs):
                opened.append(Path(str(file)).name)
                return real(file, *args, **kwargs)
            return opener
        with mock.patch.object(io, 'open', counting(io.open)), mock.patch.object(os, 'open', counting(os.open)):
            out = service.string_xrefs('limb damage')       # in a comment, never inside a quoted string
        self.assertTrue(out['ok'], out)
        self.assertEqual([r['address'] for r in out['data']['results']], ['0x00401100'])
        self.assertEqual(opened.count('misc.c'), 1, 'the decomp files were read %d times' % opened.count('misc.c'))

    def test_strings_still_prefers_the_quoted_form(self):
        write(self.decomp, 'src/misc.c', MISC_C.replace('return a + 1;', 'Log("limb damage");\n    return a + 1;'))
        self.addCleanup(write, self.decomp, 'src/misc.c', MISC_C)
        out = FnvService(self.project).string_xrefs('limb damage')
        self.assertEqual([r['address'] for r in out['data']['results']], ['0x00401200'])
        self.assertEqual(out['data']['total'], 1)


class WarmUp(FnvTestBase):
    """The first engine lookup of a session loaded the 42 MB engine map and built the name keys: 1.1 s on that call.
    The server now does both on a background thread just after it starts."""

    def setUp(self):
        fnv_engine._cache.clear()
        self.addCleanup(fnv_engine._cache.clear)

    def test_the_map_and_its_name_keys_are_ready_before_the_first_search(self):
        thread = fnv_tools.warm_up(self.project, delay=0)
        self.assertIsNotNone(thread, 'nothing was started')
        thread.join(30)
        self.assertFalse(thread.is_alive())
        with mock.patch.object(fnv_engine, 'EngineMap', side_effect=AssertionError('the map was loaded again')), \
                mock.patch.object(fnv_engine, '_words', wraps=fnv_engine._words) as words:
            out = FnvService(self.project).func_find('limb damage')
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['data']['results'][0]['address'], '0x00401100')
        self.assertEqual(words.call_count, 1, 'the name keys were not built by the warm-up')

    def test_it_waits_for_the_first_requests(self):
        thread = fnv_tools.warm_up(self.project, delay=30)
        self.addCleanup(thread.cancel)
        self.assertTrue(thread.daemon, 'a waiting warm-up must not keep the server process alive')
        self.assertNotIn('map', fnv_engine._cache)

    def test_no_engine_map_file_starts_nothing(self):
        fnv = self.project.fnv.model_copy(update={'engine_map': str(Path(self.tmp.name) / 'none' / 'engine_map.json')})
        self.assertIsNone(fnv_tools.warm_up(self.project.model_copy(update={'fnv': fnv}), delay=0))

    def test_a_server_with_only_the_editor_tools_starts_nothing(self):
        self.assertIsNone(fnv_tools.warm_up(self.project.model_copy(update={'toolsets': ['editor']}), delay=0))

    def test_a_map_that_does_not_load_is_not_an_error(self):
        broken = Path(self.tmp.name) / 'broken' / 'engine_map.json'
        broken.parent.mkdir(exist_ok=True)
        broken.write_text('{"schema": 99}', encoding='utf-8')
        fnv = self.project.fnv.model_copy(update={'engine_map': str(broken)})
        thread = fnv_tools.warm_up(self.project.model_copy(update={'fnv': fnv}), delay=0)
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('map', fnv_engine._cache)

    def test_the_server_starts_it(self):
        from geck_mcp import server as server_module
        with mock.patch.object(server_module.Project, 'load', return_value=self.project), \
                mock.patch.object(server_module, 'Service', return_value=mock.Mock()), \
                mock.patch.object(server_module, 'make_server', return_value=mock.Mock()), \
                mock.patch.object(fnv_tools, 'warm_up') as warm_up:
            server_module.main(['--project', 'x.json'])
        warm_up.assert_called_once_with(self.project)

    def test_two_first_uses_load_the_map_once(self):
        built = []
        real = fnv_engine.EngineMap

        def slow(path, decomp_root=None):
            built.append(path)
            time.sleep(0.2)                      # long enough for the second caller to arrive
            return real(path, decomp_root)
        with mock.patch.object(fnv_engine, 'EngineMap', slow):
            threads = [threading.Thread(target=fnv_engine.load, args=fnv_engine.default_paths(self.project))
                       for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(len(built), 1, 'the map was parsed %d times' % len(built))


class RequiredText(unittest.TestCase):
    """The text every match of a search must hold (case ignored): files without it are skipped unread by the regex."""

    def test_plain_text_is_its_own_requirement(self):
        self.assertEqual(fnv_engine.required_text('Actor::Kill', regex=False), b'actor::kill')
        self.assertEqual(fnv_engine.required_text('a.b*c', regex=False), b'a.b*c')

    def test_the_longest_fixed_run_of_a_regex(self):
        self.assertEqual(fnv_engine.required_text(r'\bfCombat\w+Mult\b', regex=True), b'fcombat')
        self.assertEqual(fnv_engine.required_text(r'\bg_011f6ebc\b', regex=True), b'g_011f6ebc')
        self.assertEqual(fnv_engine.required_text(r'colou?r', regex=True), b'colo')
        self.assertEqual(fnv_engine.required_text('[Aa]ctor', regex=True), b'ctor')

    def test_no_requirement_when_nothing_is_certain(self):
        # alternatives, groups and repeats promise nothing; one or two letters are in nearly every file
        for pattern in ('abc|def', '(abc)+', 'a.c', r'\w+', 'ab', '(?:Actor)?', 'x{3}'):
            self.assertIsNone(fnv_engine.required_text(pattern, regex=True), pattern)
        self.assertIsNone(fnv_engine.required_text('if', regex=False))

    def test_text_outside_ascii_gives_none(self):
        # re.I pairs some ASCII letters with letters outside ASCII (s with the long s): bytes cannot be compared
        self.assertIsNone(fnv_engine.required_text('café', regex=False))
        self.assertIsNone(fnv_engine.required_text('ſtring', regex=True))

    def test_line_breaks_split_the_requirement(self):
        self.assertEqual(fnv_engine.required_text('first\nsecond one', regex=False), b'second one')
        self.assertEqual(fnv_engine.required_text('abc\r\nde', regex=False), b'abc')


class LoadOrderSpeed(unittest.TestCase):
    """Every fnv_record show (and so every quest of fnv_save_inspect, 200 lookups for 100 quests) worked the load order
    out again: each plugin looked for in every enabled mod folder and its header read, about 160 file checks and 11
    ms a time; 1.5 s for one save. The load order is kept until a profile file, a mod folder or a plugin changes."""

    MODS = ['Top', 'Middle', 'Bottom']

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.mo2, self.data_dir = root / 'MO2', root / 'Game' / 'Data'
        self.profile = self.mo2 / 'profiles' / 'Dev'
        for folder in [self.profile, self.data_dir] + [self.mo2 / 'mods' / m for m in self.MODS]:
            folder.mkdir(parents=True)
        self.project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=str(self.mo2), mo2_profile='Dev'),
                                       game_root=str(root / 'Game'), state_dir=str(root / 'state'))
        write_plugin(self.data_dir / 'FalloutNV.esm', [])
        write_plugin(self.mo2 / 'mods' / 'Bottom' / 'Mod.esp', [], masters=['FalloutNV.esm'])
        write_plugin(self.mo2 / 'mods' / 'Middle' / 'Other.esp', [], masters=['FalloutNV.esm'])
        (self.profile / 'modlist.txt').write_text('\n'.join('+' + m for m in self.MODS) + '\n', encoding='utf-8')
        self.set_plugins(['FalloutNV.esm', 'Mod.esp', 'Other.esp'])

    def set_plugins(self, active):
        (self.profile / 'plugins.txt').write_text(''.join('*%s\n' % p for p in active), encoding='utf-8')
        (self.profile / 'loadorder.txt').write_text('FalloutNV.esm\nMod.esp\nOther.esp\n', encoding='utf-8')

    def entry(self, entries, name):
        return next(e for e in entries if e['name'] == name)

    def counted(self, fn):
        """(result, plugin headers read, file checks made) of one call."""
        from geck_mcp import fnv_data
        real_exists, checks = Path.exists, [0]

        def exists(path, *args, **kwargs):
            checks[0] += 1
            return real_exists(path, *args, **kwargs)
        with mock.patch.object(fnv_data, 'plugin_masters', wraps=fnv_data.plugin_masters) as masters, \
                mock.patch.object(Path, 'exists', exists):
            out = fn()
        return out, masters.call_count, checks[0]

    def test_a_second_load_order_reads_no_plugin_and_looks_in_no_mod_folder(self):
        from geck_mcp.fnv_data import load_order
        first, read_first, checks_first = self.counted(lambda: load_order(self.project))
        second, read_second, checks_second = self.counted(lambda: load_order(self.project))
        self.assertEqual(second, first)
        self.assertEqual(read_first, 3)
        self.assertEqual(read_second, 0, 'plugin headers were read again')
        self.assertLess(checks_second, len(first), 'every plugin was looked for again (%d file checks)' % checks_second)

    def test_a_plugin_put_in_a_higher_mod_wins_at_once(self):
        from geck_mcp.fnv_data import load_order
        load_order(self.project)
        write_plugin(self.mo2 / 'mods' / 'Top' / 'Mod.esp', [], masters=['FalloutNV.esm', 'Other.esp'])
        mod = self.entry(load_order(self.project), 'Mod.esp')
        self.assertEqual(mod['mod'], 'Top')
        self.assertEqual(mod['masters'], ['FalloutNV.esm', 'Other.esp'])

    def test_a_plugin_removed_from_its_mod_is_missed_at_once(self):
        from geck_mcp.fnv_data import load_order
        load_order(self.project)
        (self.mo2 / 'mods' / 'Middle' / 'Other.esp').unlink()
        self.assertIsNone(self.entry(load_order(self.project), 'Other.esp')['path'])

    def test_a_rewritten_plugin_gives_its_new_masters(self):
        from geck_mcp.fnv_data import load_order
        load_order(self.project)
        write_plugin(self.mo2 / 'mods' / 'Bottom' / 'Mod.esp', [], masters=['FalloutNV.esm', 'Other.esp'])
        self.assertEqual(self.entry(load_order(self.project), 'Mod.esp')['masters'], ['FalloutNV.esm', 'Other.esp'])

    def test_a_changed_plugin_list_is_read_again(self):
        from geck_mcp.fnv_data import load_order
        self.assertTrue(self.entry(load_order(self.project), 'Other.esp')['active'])
        self.set_plugins(['FalloutNV.esm', 'Mod.esp'])
        self.assertFalse(self.entry(load_order(self.project), 'Other.esp')['active'])

    def test_a_changed_mod_list_is_read_again(self):
        from geck_mcp.fnv_data import load_order
        self.assertEqual(self.entry(load_order(self.project), 'Other.esp')['mod'], 'Middle')
        (self.profile / 'modlist.txt').write_text('+Top\n-Middle\n+Bottom\n', encoding='utf-8')
        self.assertIsNone(self.entry(load_order(self.project), 'Other.esp')['path'])

    def test_a_plugin_added_in_the_same_clock_tick_is_seen(self):
        """A folder's time stays put when a file arrives in the same tick of the file clock: folders are told apart
        by the plugin names in them."""
        from geck_mcp.fnv_data import load_order
        top = self.mo2 / 'mods' / 'Top'
        load_order(self.project)
        st = top.stat()
        write_plugin(top / 'Mod.esp', [], masters=['FalloutNV.esm', 'Other.esp'])
        os.utime(top, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.entry(load_order(self.project), 'Mod.esp')['mod'], 'Top')

    def test_a_mod_turned_off_in_the_same_clock_tick_is_seen(self):
        """'+Middle' to '-Middle' keeps the file's size, and a write in the same tick of the file clock (about 16 ms
        on Windows, as on the CI runner) keeps its time: profile files are told apart by what they say."""
        from geck_mcp.fnv_data import load_order
        modlist = self.profile / 'modlist.txt'
        self.assertEqual(self.entry(load_order(self.project), 'Other.esp')['mod'], 'Middle')
        st = modlist.stat()
        modlist.write_text('+Top\n-Middle\n+Bottom\n', encoding='utf-8')
        os.utime(modlist, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(modlist.stat().st_size, st.st_size)
        self.assertIsNone(self.entry(load_order(self.project), 'Other.esp')['path'])

    def test_a_plugin_rewritten_in_the_same_clock_tick_gives_its_new_masters(self):
        """A plugin rewritten at the same size in the same tick of the file clock keeps its size and time: plugins are
        told apart by their header record, where the masters are."""
        from geck_mcp.fnv_data import load_order
        mod = self.mo2 / 'mods' / 'Bottom' / 'Mod.esp'
        write_plugin(mod, [], masters=['FalloutNV.esm', 'Other.esp'])
        self.assertEqual(self.entry(load_order(self.project), 'Mod.esp')['masters'], ['FalloutNV.esm', 'Other.esp'])
        st = mod.stat()
        write_plugin(mod, [], masters=['FalloutNV.esm', 'Thing.esp'])
        os.utime(mod, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(mod.stat().st_size, st.st_size)
        self.assertEqual(self.entry(load_order(self.project), 'Mod.esp')['masters'], ['FalloutNV.esm', 'Thing.esp'])

    def test_the_game_list_is_its_own_load_order(self):
        from geck_mcp.fnv_data import load_order
        load_order(self.project)
        live = self.entry(load_order(self.project, {'FalloutNV.esm': 0, 'Other.esp': 1}), 'Other.esp')
        self.assertEqual(live['index'], 1)
        self.assertEqual(self.entry(load_order(self.project), 'Other.esp')['index'], 2)

    def test_a_caller_that_changes_the_entries_changes_no_later_answer(self):
        from geck_mcp.fnv_data import load_order
        entries = load_order(self.project)
        entries[0]['fileId'] = 7
        entries[0]['masters'].append('Junk.esm')
        entries.pop()
        again = load_order(self.project)
        self.assertEqual(len(again), 3)
        self.assertNotIn('fileId', again[0])
        self.assertEqual(again[0]['masters'], [])


class ArchiveNameSpeed(unittest.TestCase):
    """Every fnv_asset where / nif / dds, fnv_asset_check and fnv_author check made a new asset index, which read the
    name table of every BSA of the load order again: about 120 ms a call. Names are kept per archive until the
    archive file changes."""

    def setUp(self):
        from test_validate import make_bsa
        self.make_bsa = make_bsa
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.bsa = Path(tmp.name) / 'Test - Meshes.bsa'
        make_bsa(self.bsa, {'meshes\\a\\b.nif': b'1', 'textures\\t.dds': b'2'})

    def opened(self, fn):
        """(result, BSA files opened) of one call."""
        import builtins
        real_open, opened = builtins.open, []

        def counting(path, *args, **kwargs):
            if str(path).lower().endswith('.bsa'):
                opened.append(str(path))
            return real_open(path, *args, **kwargs)
        with mock.patch.object(builtins, 'open', counting):
            out = fn()
        return out, opened

    def test_a_second_index_reads_no_archive(self):
        from geck_mcp.esp.assets import AssetIndex
        AssetIndex(None, [], [self.bsa])
        index, opened = self.opened(lambda: AssetIndex(None, [], [self.bsa]))
        self.assertEqual(opened, [])
        self.assertIsNotNone(index.find('meshes/a/b.nif'))
        self.assertIn('textures\\t.dds', index.archived)
        self.assertEqual(index.archives, ['Test - Meshes.bsa'])

    def test_a_changed_archive_is_read_again(self):
        from geck_mcp.esp.assets import AssetIndex
        AssetIndex(None, [], [self.bsa])
        st = self.bsa.stat()
        self.make_bsa(self.bsa, {'meshes\\a\\b.nif': b'1', 'meshes\\new.nif': b'22'})
        # This archive has the old one's size; a real rebuild takes longer than a tick of the file clock (about 16 ms
        # on Windows), which two test writes do not (seen on the CI runner): its time is set one second later.
        os.utime(self.bsa, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        index = AssetIndex(None, [], [self.bsa])
        self.assertIsNotNone(index.find('meshes\\new.nif'))
        self.assertIsNone(index.find('textures\\t.dds'))


class AssetUsersSpeed(unittest.TestCase):
    """fnv_asset users kept the file paths each plugin names in memory only: the first call of every server parsed
    every plugin of the load order in full, 7 s on a real one. The paths now live in the record index, per plugin
    file, and go with the plugin's other rows when the file changes."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # per-thread connections close on GC
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.mo2, data_dir = root / 'MO2', root / 'Game' / 'Data'
        profile = self.mo2 / 'profiles' / 'Dev'
        for folder in (profile, data_dir, self.mo2 / 'mods' / 'Mod'):
            folder.mkdir(parents=True)
        self.project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=str(self.mo2), mo2_profile='Dev'),
                                       game_root=str(root / 'Game'), state_dir=str(root / 'state'))
        write_plugin(data_dir / 'FalloutNV.esm', [
            record('STAT', 0x800, edid('Rock'), sub('MODL', b'Clutter\\Rock.nif\0')),
            record('STAT', 0x801, edid('Crate'), sub('MODL', b'clutter\\crate.nif\0'))])
        self.mod = self.mo2 / 'mods' / 'Mod' / 'Mod.esp'
        self.write_mod(b'clutter\\rock.nif')
        (profile / 'modlist.txt').write_text('+Mod\n', encoding='utf-8')
        (profile / 'plugins.txt').write_text('*FalloutNV.esm\n*Mod.esp\n', encoding='utf-8')
        (profile / 'loadorder.txt').write_text('FalloutNV.esm\nMod.esp\n', encoding='utf-8')

    def write_mod(self, model):
        write_plugin(self.mod, [record('MISC', 0x01000800, edid('Pebble'), sub('MODL', model + b'\0'),
                                       sub('ICON', b'interface\\pebble.dds\0'))], masters=['FalloutNV.esm'])

    def server(self):
        """The asset tools of a newly started server: nothing kept in memory, the same state folder."""
        from geck_mcp.fnv_create import AssetTools
        data = DataTools(self.project)
        self.addCleanup(lambda: data.index.db.close())
        return AssetTools(SimpleNamespace(project=self.project), fnv_records.RecordsTools(data))

    def users(self, tools, path):
        """(users as (plugin, form id, field), plugins parsed in full) of one call."""
        from geck_mcp.esp import codec
        with mock.patch.object(codec, 'parse', wraps=codec.parse) as parse:
            out = tools.users(path)
        return sorted((u['plugin'], u['formId'], u['field']) for u in out['users']), parse.call_count

    def test_a_new_server_reads_no_plugin(self):
        first, parsed = self.users(self.server(), 'meshes/clutter/rock.nif')
        self.assertEqual(first, [('FalloutNV.esm', '00000800', 'MODL'), ('Mod.esp', '01000800', 'MODL')])
        self.assertEqual(parsed, 2)
        again, parsed = self.users(self.server(), 'Meshes\\Clutter\\Rock.nif')
        self.assertEqual(again, first)
        self.assertEqual(parsed, 0, 'a new server parsed the plugins again')

    def test_a_changed_plugin_is_read_again(self):
        self.users(self.server(), 'meshes/clutter/rock.nif')
        time.sleep(0.01)
        self.write_mod(b'clutter\\pebble.nif')
        os.utime(self.mod, None)
        tools = self.server()
        rock, parsed = self.users(tools, 'meshes/clutter/rock.nif')
        self.assertEqual(rock, [('FalloutNV.esm', '00000800', 'MODL')])
        self.assertEqual(parsed, 1, 'only the changed plugin is read')
        self.assertEqual(self.users(tools, 'meshes/clutter/pebble.nif')[0], [('Mod.esp', '01000800', 'MODL')])
        self.assertEqual(self.users(tools, 'textures/interface/pebble.dds')[0], [('Mod.esp', '01000800', 'ICON')])


class LivePluginListSpeed(unittest.TestCase):
    """With the game running, every record lookup also asked the game for its plugin list: a ping and two requests,
    about two frames, 200 times for one save. A game's plugins are fixed once it has loaded them, so the list is
    asked once per game process."""

    def setUp(self):
        self.proc = (100, 5000)
        self.lists = [[{'name': 'FalloutNV.esm', 'index': '00'}, {'name': 'Mod.esp', 'index': '01'}]]
        self.asked = 0
        test = self

        class Game:
            def process(self, fresh=False):
                return test.proc

            def ping(self):
                return {'frame': 1}

            def loaded_plugins(self):
                test.asked += 1
                return test.lists.pop(0) if len(test.lists) > 1 else test.lists[0]
        self.data = DataTools(SimpleNamespace(state_dir=tempfile.gettempdir()), SimpleNamespace(game=Game()))

    def test_one_game_is_asked_once(self):
        first = self.data._live_plugins()
        self.assertEqual(self.data._live_plugins(), first)
        self.assertEqual(first, {'FalloutNV.esm': 0, 'Mod.esp': 1})
        self.assertEqual(self.asked, 1)

    def test_a_new_game_is_asked_again(self):
        self.data._live_plugins()
        self.proc = (101, 6000)
        self.data._live_plugins()
        self.assertEqual(self.asked, 2)

    def test_no_answer_yet_is_asked_again(self):
        self.lists = [None, [{'name': 'FalloutNV.esm', 'index': '00'}]]
        self.assertIsNone(self.data._live_plugins())
        self.assertEqual(self.data._live_plugins(), {'FalloutNV.esm': 0})
        self.assertEqual(self.asked, 2)

    def test_no_game_gives_none_without_asking(self):
        self.data._live_plugins()
        self.proc = None
        self.assertIsNone(self.data._live_plugins())
        self.assertEqual(self.asked, 1)


if __name__ == '__main__':
    unittest.main()
