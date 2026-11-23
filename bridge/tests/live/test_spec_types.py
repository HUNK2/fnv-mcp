"""Live proof of tool gaps 1 (2026-11-23): records of every new builder type, overrides of master records and objects
in an exterior cell, built by fnv_spec's builder, loaded by the game and read back.

The test builds its own plugin (FnvmcpSpecLive.esp, not the project's), so the project's spec and build stay as they
are. setUpClass: read where the player stands (an exterior cell of WastelandNV), build the plugin with two keys placed
there, quit the game, install the plugin as its own MO2 mod, launch at the same place. Each test reads records back:
in the game with fnv_form action=bytes (the engine's own SaveForm writer; fnv_form_live_diff for classes without one)
and from the load order with fnv_record. tearDownClass turns the mod off and relaunches, so later tests run on the
plain profile.

Run alone: tools/fnv_live_regress.py --suite spec (about 4 minutes: two game starts)."""
import json
import os
from pathlib import Path

from .base import LiveCase, Session

START = os.environ.get('FNV_LIVE_START', 'coc:GoodspringsSource')
PLUGIN = 'FnvmcpSpecLive.esp'
MOD = 'fnvmcp spec live'
MASTER = 'FalloutNV.esm'


def spec_for(grid, position, rex_part):
    """The test plugin: new records of each new type, overrides from each wave, two keys in the exterior cell."""
    x, y, z = position
    records = {
        # wave A: items and loot
        'LiveArmor': {'type': 'ARMO', 'template': '@ArmorLeather',
                      'set': {'name': 'Live Test Armor', 'value': 123, 'weight': 4.5, 'damage_threshold': 9}},
        'LiveAmmo': {'type': 'AMMO', 'template': '@Ammo10mm', 'set': {'name': 'Live Test Round', 'value': 3}},
        'LiveKey': {'type': 'KEYM', 'set': {'name': 'Live Test Key', 'value': 7, 'weight': 0.25,
                                            'model': 'Clutter\\Key01Clean.NIF'}},
        'LiveNote': {'type': 'NOTE', 'template': '@RecipesBrahminWellingtonNote', 'set': {'name': 'Live Test Note'}},
        'LiveBook': {'type': 'BOOK', 'template': '@BookSkillSurvival', 'set': {'name': 'Live Test Book', 'value': 77}},
        'LiveMod': {'type': 'IMOD', 'template': '@ModNV10mmPistolSilencer',
                    'set': {'name': 'Live Test Mod', 'value': 55}},
        'LiveLoot': {'type': 'LVLI', 'set': {'chance_none': 0, 'entries': [
            {'form': '@LiveKey', 'level': 1, 'count': 2}]}},
        'LiveNpcList': {'type': 'LVLN', 'template': '@VarVHDNCRRanger9Veteran', 'set': {'chance_none': 25}},
        'LiveCreatureList': {'type': 'LVLC', 'template': '@VEncTier4SupermutantMelee50', 'set': {'chance_none': 10}},
        'LiveRecipe': {'type': 'RCPE', 'template': '@Recipe127mm', 'set': {
            'name': 'Live Test Recipe', 'ingredients': [{'form': '@Ammo10mm', 'count': 2}],
            'outputs': [{'form': '@LiveAmmo', 'count': 1}]}},
        'ArmorLeather': {'type': 'ARMO', 'override': True, 'set': {'value': 201}},
        # wave B: doors, terminals, perks
        'LiveDoor': {'type': 'DOOR', 'template': '@HVBunkerOuterDoor', 'set': {'name': 'Live Test Door'}},
        'LiveTerminal': {'type': 'TERM', 'template': '@CommuneTerm', 'set': {
            'name': 'Live Test Terminal', 'welcome_text': 'Welcome, tester.', 'difficulty': 'easy',
            'flags': ['unlocked'], 'menu': [{'text': 'Read the log', 'result': 'All quiet.'}]}},
        'LivePerk': {'type': 'PERK', 'set': {
            'name': 'Live Test Perk', 'description': 'Weapons do 10% more damage.', 'ranks': 1, 'playable': 1,
            'min_level': 2, 'effects': [{'entry_point': 'Calculate Weapon Damage', 'function': 'multiply',
                                         'value': 1.1}]}},
        'HVBunkerOuterDoor': {'type': 'DOOR', 'override': True, 'set': {'name': 'Bunker Door (live test)'}},
        # wave C: races, idles, body parts (overrides; a new idle from a master one)
        'Caucasian': {'type': 'RACE', 'override': True, 'set': {'description': 'Live test race text.'}},
        'LiveIdle': {'type': 'IDLE', 'template': '@NightStalkerHitTorso', 'set': {'replay_delay': 3}},
        'RexBodyPartData': {'type': 'BPTD', 'override': True, 'set': {
            'parts': [{'part': rex_part, 'damage_mult': 3}]}},
    }
    objects = [{'ref_id': 'key_here', 'base': '@LiveKey', 'position': [x + 120, y, z + 40]},
               {'ref_id': 'key_kept', 'base': '@LiveKey', 'position': [x - 120, y, z + 40], 'persistent': True}]
    return {'schema': 1, 'project_id': 'fnvmcp-spec-live', 'plugin': PLUGIN, 'masters': [MASTER],
            'records': records, 'worlds': {'WastelandNV': {'cells': {'LiveCamp': {'grid': list(grid),
                                                                                  'objects': objects}}}}}


def merged(fields):
    """fnv_form bytes fields -> {name: value}: decoded layouts by field name, text subrecords by tag."""
    out = {}
    for f in fields:
        value = f.get('value')
        if isinstance(value, dict):
            for k, v in value.items():
                out.setdefault(k, v)
        elif value is not None:
            out.setdefault(f['tag'], value)
    return out


class SpecTypesLive(LiveCase):
    built = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls._set_up()
        except BaseException:
            cls._clean_up()           # unittest skips tearDownClass when setUpClass fails: the mod must not stay on
            raise

    @classmethod
    def _set_up(cls):
        s = cls.session
        from fnv_mcp.authoring import Authoring
        from fnv_mcp.esp import layouts, worlds
        if not (s.call('fnv_status').get('data') or {}).get('running'):
            must(s.call('fnv_game', {'action': 'launch', 'start': START}), 'launch')
        player = must(s.call('fnv_player'), 'fnv_player')
        if player['cell']['formId'][:2] != '00':         # a game left in another plugin's cell: start where we test
            must(s.call('fnv_game', {'action': 'restart', 'start': START}), 'restart at ' + START)
            player = must(s.call('fnv_player'), 'fnv_player')
        p = player['position']
        authoring = Authoring(s.project.model_copy(update={'plugin': PLUGIN}),
                              spec_path=Path(s.project.state_dir) / 'fnv' / 'spec-live' / 'spec.json',
                              build_dir=Path(s.project.state_dir) / 'fnv' / 'spec-live' / 'build')
        sources = authoring.master_records([MASTER])
        nv = sources.file(MASTER)
        world = sources.winning([MASTER], MASTER, 0x0DA726, 'WRLD')[1]
        assert world.editor_id == 'WastelandNV', world.editor_id
        layout = worlds.world_layout(nv.path, 0x0DA726)
        cell = int(player['cell']['formId'], 16)
        grid = next((g for g, (raw, _b, _s) in layout['cells'].items() if raw == cell), None)
        assert grid is not None, 'the player is not in an exterior cell of WastelandNV: %s' % player['cell']
        rex = sources.winning([MASTER], MASTER, 0x17626D, 'BPTD')[1]
        rex_part = layouts.read_lists('BPTD', rex.subrecords, None)['parts'][0]['part']
        spec = spec_for(grid, (p['x'], p['y'], p['z']), rex_part)
        authoring.spec_path.parent.mkdir(parents=True, exist_ok=True)
        authoring.spec_path.write_text(json.dumps(spec, indent=1), encoding='utf-8')
        receipt, _data = authoring.build(write=True)
        assert receipt['written'], receipt['validation']
        cls.built = {'receipt': receipt, 'grid': grid, 'position': p, 'rex_part': rex_part,
                     'plugin_path': str(authoring.built_path)}
        if (s.call('fnv_status').get('data') or {}).get('running'):
            must(s.call('fnv_game', {'action': 'quit'}), 'quit')
        cls.built['install'] = must(s.call('fnv_install', {'plugin_path': str(authoring.built_path),
                                                           'mod_name': MOD}), 'fnv_install')
        must(s.call('fnv_game', {'action': 'launch', 'start': START}), 'launch with the plugin')
        must(s.call('fnv_status'), 'fnv_status')

    @classmethod
    def tearDownClass(cls):
        cls._clean_up()

    @classmethod
    def _clean_up(cls):
        s = cls.session or Session.get()
        from fnv_mcp import fnv_link
        s.call('fnv_game', {'action': 'quit'})
        fnv_link.disable_mod(s.project.fnv.mo2_root, s.project.fnv.mo2_profile, MOD)
        s.call('fnv_game', {'action': 'launch', 'start': START})
        s.call('fnv_status')

    # helpers
    def local(self, edid):
        """The new record's Plugin.esp:LOCALID, from the build receipt."""
        return '%s:%06X' % (PLUGIN, int(self.built['receipt']['formIds'][edid], 16) & 0xFFFFFF)

    def live_fields(self, form):
        data = self.ok('fnv_form', action='bytes', form=form)
        self.assertTrue(data['written'], data)
        return merged(data['fields']), data

    # wave A
    def test_10_items_read_back_in_the_game(self):
        armor, data = self.live_fields(self.local('LiveArmor'))
        self.assertEqual(data['type'], 'ARMO')
        self.assertEqual((armor['FULL'], armor['value'], armor['weight'], armor['damage_threshold']),
                         ('Live Test Armor', 123, 4.5, 9))
        self.assertEqual(armor['biped_flags'], ['upper_body'], 'the template\'s fields are kept')
        for edid, rtype, name, value in [('LiveAmmo', 'AMMO', 'Live Test Round', 3),
                                         ('LiveBook', 'BOOK', 'Live Test Book', 77),
                                         ('LiveMod', 'IMOD', 'Live Test Mod', 55)]:
            with self.subTest(edid=edid):
                got = self.ok('fnv_form', action='bytes', form=self.local(edid))
                if not got['written']:
                    diff = self.ok('fnv_form_live_diff', form=self.local(edid))
                    self.assertEqual(diff['type'], rtype)
                    self.assertEqual(diff['changed'], [], 'the form in memory is the record as built')
                    continue
                fields = merged(got['fields'])
                self.assertEqual((got['type'], fields['FULL'], fields['value']), (rtype, name, value))
        note, data = self.live_fields(self.local('LiveNote'))
        self.assertEqual((data['type'], note['FULL']), ('NOTE', 'Live Test Note'))

    def test_11_key_from_nothing_is_in_the_game(self):
        # KEYM has no SaveForm writer and fnv_form_live_diff has no layout for its fields (live run 2026-11-23: it
        # compared none). So: the record from the load order, the form in the game by type and EditorID, and its
        # name in the inventory.
        shown = self.ok('fnv_record', action='show', record='@LiveKey')
        self.assertEqual(shown['winner'], PLUGIN)
        fields = merged(shown['fields'])
        self.assertEqual((fields['FULL'], fields['value'], fields['weight']), ('Live Test Key', 7, 0.25))
        diff = self.ok('fnv_form_live_diff', form=self.local('LiveKey'))
        self.assertEqual((diff['type'], diff['editorId'], diff['changed']), ('KEYM', 'LiveKey', []))
        key = diff['form']
        self.ok('fnv_console', line='player.AddItem %s 1' % key)
        rows = self.ok('fnv_inventory')['items']
        self.assertEqual([r.get('name') for r in rows if r.get('formId') == key], ['Live Test Key'])
        self.ok('fnv_console', line='player.RemoveItem %s 1' % key)

    def test_12_override_keeps_the_master_formid(self):
        armor, data = self.live_fields('00020423')
        self.assertEqual(data['formIdInRecord'][-6:], '020423')
        self.assertEqual((armor['FULL'], armor['value']), ('Leather Armor', 201))
        self.assertEqual(self.built['receipt']['overrides']['ArmorLeather']['from'], MASTER)

    def test_13_items_work_in_the_inventory(self):
        armor = self.ok('fnv_form', action='bytes', form=self.local('LiveArmor'))['form']
        before = self.ok('fnv_console', line='player.GetItemCount %s' % armor)
        self.ok('fnv_console', line='player.AddItem %s 1' % armor)
        rows = self.ok('fnv_inventory')['items']
        row = next((r for r in rows if r.get('formId') == armor), None)
        self.assertIsNotNone(row, 'the new armour is not in the inventory')
        self.assertEqual(row.get('name'), 'Live Test Armor')
        self.ok('fnv_console', line='player.RemoveItem %s 1' % armor)
        self.assertIsNotNone(before)

    def test_14_leveled_lists_roll_their_entries(self):
        key = self.ok('fnv_form', action='bytes', form=self.local('LiveKey'))['form']
        roll = self.ok('fnv_leveled_roll', form=self.local('LiveLoot'), level=1, rolls=50, seed=7)
        self.assertEqual(roll['chanceNone'], 0)
        self.assertEqual([(o['form'], o['count']) for o in roll['outcomes']], [(key, 2)])
        self.assertEqual(roll['nothing']['times'], 0)
        for edid, chance in [('LiveNpcList', 25), ('LiveCreatureList', 10)]:
            with self.subTest(edid=edid):
                self.assertEqual(self.ok('fnv_leveled_roll', form=self.local(edid), rolls=20,
                                         seed=7)['chanceNone'], chance)

    def test_15_recipe_lists_read_back(self):
        recipe, data = self.live_fields(self.local('LiveRecipe'))
        self.assertEqual((data['type'], recipe['FULL']), ('RCPE', 'Live Test Recipe'))
        shown = self.ok('fnv_record', action='show', record='@LiveRecipe')
        self.assertEqual([i['count'] for i in shown['lists']['ingredients']], [2])
        self.assertEqual(len(shown['lists']['outputs']), 1)

    # wave B
    def test_20_door_terminal_perk_read_back(self):
        door, data = self.live_fields(self.local('LiveDoor'))
        self.assertEqual((data['type'], door['FULL']), ('DOOR', 'Live Test Door'))
        term, data = self.live_fields(self.local('LiveTerminal'))
        self.assertEqual((data['type'], term['FULL'], term['difficulty']), ('TERM', 'Live Test Terminal', 'easy'))
        # SaveForm writes no DESC (live run 2026-11-23): the welcome text is read from the load order.
        shown = merged(self.ok('fnv_record', action='show', record='@LiveTerminal')['fields'])
        self.assertEqual(shown['DESC'], 'Welcome, tester.')
        texts = [f.get('value') for f in data['fields'] if f['tag'] == 'ITXT']
        self.assertEqual(texts, ['Read the log'], 'the menu replaces the template\'s menu')
        perk, data = self.live_fields(self.local('LivePerk'))
        self.assertEqual((data['type'], perk['FULL']), ('PERK', 'Live Test Perk'))
        bunker, _ = self.live_fields('001735C4')
        self.assertEqual(bunker['FULL'], 'Bunker Door (live test)')

    def test_21_perk_is_given(self):
        perk = self.ok('fnv_form', action='bytes', form=self.local('LivePerk'))['form']
        self.ok('fnv_console', line='player.AddPerk %s' % perk)
        has = self.ok('fnv_console', line='player.HasPerk %s' % perk)
        self.ok('fnv_console', line='player.RemovePerk %s' % perk)
        self.assertIn('1', json.dumps(has))

    # wave C
    def test_30_race_idle_body_parts_read_back(self):
        race, data = self.live_fields('00000019')
        self.assertEqual((data['type'], race['FULL']), ('RACE', 'Caucasian'))
        shown = self.ok('fnv_record', action='show', record='00000019')
        self.assertEqual((shown['winner'], merged(shown['fields'])['DESC']), (PLUGIN, 'Live test race text.'))
        idle, data = self.live_fields(self.local('LiveIdle'))
        self.assertEqual((data['type'], idle['replay_delay']), ('IDLE', 3))
        shown = self.ok('fnv_record', action='show', record='@RexBodyPartData')
        part = next(p for p in shown['lists']['parts'] if p['part'] == self.built['rex_part'])
        self.assertEqual(part['damage_mult'], 3.0)
        got = self.ok('fnv_form', action='bytes', form='0017626D')
        if got['written']:
            mults = [f['value']['damage_mult'] for f in got['fields'] if f['tag'] == 'BPND']
            self.assertIn(3.0, mults)

    # the load order index sees every new record by its names
    def test_00_the_load_order_lists_the_plugin(self):
        order = self.ok('fnv_loadorder')
        row = next((p for p in order['plugins'] if p['name'].lower() == PLUGIN.lower()), None)
        profile = Path(self.session.project.fnv.mo2_root) / 'profiles' / self.session.project.fnv.mo2_profile
        lines = {n: [l for l in (profile / n).read_text(encoding='utf-8').splitlines() if 'pec' in l]
                 for n in ('plugins.txt', 'loadorder.txt', 'modlist.txt')}
        self.assertTrue(row and row['active'], 'source %s, row %s, install %s, profile %s' % (
            order['source'], row, self.built.get('install'), lines))

    def test_40_records_by_name_in_the_load_order(self):
        for edid in ('LiveArmor', 'LiveKey', 'LiveTerminal', 'LivePerk', 'LiveLoot'):
            with self.subTest(edid=edid):
                shown = self.ok('fnv_record', action='show', record='@' + edid)
                self.assertEqual((shown['editorId'], shown['winner']), (edid, PLUGIN))

    # exterior cell
    def test_50_objects_in_the_exterior_cell(self):
        key = self.ok('fnv_form', action='bytes', form=self.local('LiveKey'))['form']
        refs = self.ok('fnv_refs_near', radius=1500, base=key)['refs']
        self.assertEqual(len(refs), 2, refs)
        self.assertTrue(all(r['name'] == 'Live Test Key' for r in refs))
        ids = sorted(r['formId'][-6:] for r in refs)
        built = sorted(r['formId'][-6:] for r in self.built['receipt']['references']['LiveCamp'])
        self.assertEqual(ids, built)


def must(envelope, what):
    if not envelope.get('ok'):
        raise AssertionError('%s failed: %s' % (what, json.dumps(envelope.get('error'))[:500]))
    return envelope.get('data')


