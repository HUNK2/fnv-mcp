"""fnv_crawl (Plan 2, W7): unattended checks of a whole plugin.

cells plugin      every cell the plugin adds or edits: go there, let it settle, then collect script errors, engine
                  warnings (the live capture), files not found (the file log), references with no 3D, load doors that
                  lead nowhere, the frame rate, and a photo; one row per cell, worst first.
dialogue npc      every response written for the NPC (INFO records whose conditions name it): its topic and quest,
                  whether the engine would allow it now (TESTopicInfo::CheckCondition through fnv_dialogue_tree) and
                  which condition fails, and the voice and lip files the engine will ask for with whether they exist,
                  loose or in an archive. The file name rule was read from the game's own archives:
                  sound\\voice\\<plugin>\\<voice type>\\<quest EditorID, 10 chars>_<topic EditorID, 15 chars>_
                  <info form id as in its plugin>_<response number>.ogg, with a .lip beside it. (Calling the engine's
                  VFDialogue::BuildVoiceFilePath 0x00616FA0 directly faulted: it needs a this pointer the decomp does
                  not show.)
"""
import struct
import time

from .fnv_conv import ConvError, verdict
from .fnv_link import word

BUILD_VOICE_PATH = 0x00616FA0
QUEST_CHARS, TOPIC_CHARS = 10, 15
MARKER_BASES = 0x800            # form ids below this are the engine's own markers (no 3D in the game)


class CrawlTools:
    def __init__(self, live, agent=None, data=None, records=None, social=None, assets=None, eyes=None,
                 warnings=None, sleep=time.sleep):
        self.live = live
        self.agent = agent
        self.data = data
        self.records = records
        self.social = social
        self.assets = assets
        self.eyes = eyes
        self.warnings = warnings        # callable: live -> Warnings with the live capture
        self.sleep = sleep

    # ================================================================ cells
    def _plugin_cells(self, plugin):
        from .esp import codec
        from .fnv_data import load_order
        entries = load_order(self.live.project, self.data._live_plugins())
        target = next((e for e in entries if e['name'].lower() == str(plugin).lower()), None)
        if not target or not target.get('path'):
            raise ConvError('PLUGIN_NOT_FOUND', '%s is not in the active load order.' % plugin)
        data = open(target['path'], 'rb').read()
        cells = []
        for rec, labels in codec.iter_records(data, types=['CELL']):
            flags = rec.get('DATA')
            interior = bool(flags and flags[0] & 1)
            grid = rec.get('XCLC')
            cells.append({'formId': rec.form_id, 'editorId': rec.editor_id, 'interior': interior,
                          'grid': struct.unpack_from('<ii', grid, 0) if grid and len(grid) >= 8 else None,
                          'world': next((l for l in labels if isinstance(l, int) and l > 0xFF), None)})
        return target['name'], cells

    def _go(self, cell):
        if cell['interior'] and cell['editorId']:
            return self.agent.move('player', None, cell['editorId'], None, None, 120)
        if cell['grid'] and cell['world']:
            world = self.live.form_edid(cell['world']) if hasattr(self.live, 'form_edid') else None
            if not world:
                raise ConvError('NO_WORLD', 'Cannot name the worldspace of exterior cell %08X.' % cell['formId'])
            out = self.live.console('cow %s %d %d' % (world, cell['grid'][0], cell['grid'][1]))
            self.live.wait_loaded(120) if hasattr(self.live, 'wait_loaded') else self.sleep(8)
            return out
        raise ConvError('CANNOT_VISIT', 'Cell %08X has no EditorID or grid.' % cell['formId'])

    def _settle(self, frames):
        try:
            from .fnv_inject import shared_time
            _, waiter = shared_time(self.live)
            waiter.run(loaded=True, timeout_seconds=60)
            waiter.run(frames=int(frames), timeout_seconds=60)
        except Exception:  # noqa: BLE001 - fall back to wall time
            self.sleep(frames / 60.0)

    def _fps(self, seconds=1.0):
        a = self.live.link.ping()
        self.sleep(seconds)
        b = self.live.link.ping()
        return round((int(b.get('frame', 0)) - int(a.get('frame', 0))) / seconds, 1)

    def _refs_report(self):
        from .fnv_eyes import cell_refs
        refs = cell_refs(self.live)
        no3d, bad_doors = [], []
        ptrs = [r if isinstance(r, int) else r.get('ptr') for r in refs]
        ptrs = [p for p in ptrs if p]
        if not ptrs:
            return no3d, bad_doors, 0
        flags = self.live.u32s([p + 8 for p in ptrs])
        bases = self.live.u32s([p + 0x20 for p in ptrs])
        loaded = self.live.u32s([p + 0x64 for p in ptrs])
        roots = self.live.u32s([l + 0x14 if l else 0 for l in loaded])
        base_ids = self.live.u32s([b + 0x0C if b else 0 for b in bases])
        base_types = self.live.reads([(b + 4, 1) if b else (0x10, 1) for b in bases])
        ids = self.live.u32s([p + 0x0C for p in ptrs])
        for p, f, root, bid, btype, rid in zip(ptrs, flags, roots, base_ids, base_types, ids):
            disabled = bool(f & 0x800)
            deleted = bool(f & 0x20)
            kind = btype[0] if btype else 0
            if disabled or deleted or bid is None or bid < MARKER_BASES:
                continue
            if not root and kind in (28, 31, 32, 34, 39, 21, 27, 24, 40, 30):
                no3d.append({'ref': '%08X' % rid, 'base': '%08X' % bid, 'type': kind})
        doors = [(p, rid) for p, btype, rid in zip(ptrs, base_types, ids) if btype and btype[0] == 28]
        for p, rid in doors[:100]:
            try:
                info = self.live.ref('%08X' % rid)
                if not any(x.get('name') == 'Teleport' for x in info.get('extraData', [])):
                    continue
                linked = self.live.values(['GetLinkedDoor'], p)[0]
            except Exception:  # noqa: BLE001
                continue
            if not linked or str(linked) in ('0', '00000000'):
                bad_doors.append({'door': '%08X' % rid})
        return no3d, bad_doors, len(ptrs)

    def cells(self, plugin, max_cells=20, settle_frames=90, photos=True):
        self.live.in_game()
        name, cells = self._plugin_cells(plugin)
        if not cells:
            return verdict('%s adds or edits no cells' % name, None, plugin=name, cells=[])
        capture = None
        if self.warnings:
            capture = self.warnings(self.live).capture
            try:
                capture.start()
            except Exception:  # noqa: BLE001 - reported per cell as unknown
                capture = None
        rows = []
        started = time.time()
        for cell in cells[:int(max_cells)]:
            row = {'cell': cell['editorId'] or '%08X' % cell['formId'], 'interior': cell['interior']}
            try:
                events_since = (self.live.events(0) or {}).get('seq', 0)
                warn_since = None
                if capture:
                    h = capture.hook()
                    warn_since = int(self.live.u32s([int(h['seqAddress'], 16)])[0] or 0) if h else None
                if self.assets:
                    try:
                        self.assets.file_log('stop')
                    except Exception:  # noqa: BLE001
                        pass
                    self.assets.file_log('start')
                self._go(cell)
                self._settle(settle_frames)
                evts = (self.live.events(events_since) or {}).get('events', [])
                row['scriptErrors'] = [e.get('text') for e in evts if e.get('type') == 'script_error'][:20]
                if capture and warn_since is not None:
                    got = capture.rows(warn_since)
                    row['warnings'] = [w['text'] for w in (got[0] if got else [])][:30]
                if self.assets:
                    missing = self.assets.file_log('read', missing=True, limit=100)
                    row['missingFiles'] = sorted({e['path'] for e in missing.get('entries', [])})[:40]
                    self.assets.file_log('stop')
                row['refsWithout3D'], row['doorsToNowhere'], row['refs'] = self._refs_report()
                row['fps'] = self._fps(1.0)
                if photos and self.eyes:
                    try:
                        row['photo'] = self.eyes.photo('view', size=640).get('path')
                    except Exception as e:  # noqa: BLE001
                        row['photo'] = 'not taken: %s' % str(e)[:80]
            except Exception as e:  # noqa: BLE001 - one bad cell does not stop the crawl
                row['error'] = str(e)[:300]
            row['problems'] = (10 * len(row.get('scriptErrors') or []) + 3 * len(row.get('warnings') or []) +
                               5 * len(row.get('missingFiles') or []) + 2 * len(row.get('refsWithout3D') or []) +
                               5 * len(row.get('doorsToNowhere') or []) + (50 if row.get('error') else 0))
            rows.append(row)
        rows.sort(key=lambda r: -r['problems'])
        bad = [r for r in rows if r['problems']]
        return verdict('%s: %d cells crawled, %d with problems' % (name, len(rows), len(bad)),
                       'Each cell: visited, settled %d frames, then checked. Worst first.' % int(settle_frames),
                       plugin=name, cells=rows, seconds=round(time.time() - started, 1),
                       notCrawled=max(0, len(cells) - int(max_cells)))

    # ================================================================ dialogue
    def _info_topic(self, info_form):
        """The DIAL that owns an INFO: in the plugin file, a topic record is followed by the group of its INFOs, so
        the owner is the last DIAL before the INFO in the same file."""
        entries, _ = self.data._active()
        owner, local = self.data._resolve(entries, info_form)
        db = self.data.index.db
        row = db.execute('select file, offset from records where owner = ? and local = ? and type = ? '
                         'order by offset desc limit 1', (owner, local, 'INFO')).fetchone()
        if not row:
            return None
        dial = db.execute('select owner, local, edid from records where file = ? and type = ? and offset < ? '
                          'order by offset desc limit 1', (row[0], 'DIAL', row[1])).fetchone()
        if not dial:
            return None
        index = next((e['index'] for e in entries if e['name'].lower() == str(dial[0]).lower()), None)
        if index is None:
            return None
        return {'formId': '%02X%06X' % (index, dial[1]), 'editorId': dial[2]}

    def _quest_of(self, info_form):
        try:
            shown = self.data.record_show(info_form, all_versions=False)
        except Exception:  # noqa: BLE001
            return None
        for sub in (shown.get('subrecords') or shown.get('winner', {}).get('subrecords') or []):
            if isinstance(sub, dict) and sub.get('tag') == 'QSTI':
                value = sub.get('formId') or sub.get('value')
                if isinstance(value, dict):
                    value = value.get('formId')
                return str(value) if value else None
        return None

    def _edid(self, form_id):
        entries, _ = self.data._active()
        owner, local = self.data._resolve(entries, form_id)
        row = self.data.index.db.execute('select edid from records where owner = ? and local = ? and edid is not null '
                                         'limit 1', (owner, local)).fetchone()
        return row[0] if row else None

    def _voice_type(self, base):
        try:
            shown = self.data.record_show(base, all_versions=False)
        except Exception:  # noqa: BLE001
            return None
        for sub in shown.get('fields') or []:
            if isinstance(sub, dict) and sub.get('tag') == 'VTCK':
                form = sub.get('form') or {}
                return form.get('editorId') or (self._edid(form['formId']) if form.get('formId') else None)
        return None

    def _voice_files(self, voice, quest, topic_edid, info):
        """Expected voice files of an INFO: one per response (TRDT), by the archive naming rule."""
        if not (voice and quest and topic_edid):
            return []
        quest_edid = self._edid(quest)
        entries, _ = self.data._active()
        owner, local = self.data._resolve(entries, info)
        entry = next((e for e in entries if e['name'].lower() == owner), None)
        if not quest_edid or not entry:
            return []
        own_prefix = len(entry.get('masters') or [])
        responses = 1
        try:
            shown = self.data.record_show(info, all_versions=False)
            responses = max(1, sum(1 for x in shown.get('fields') or [] if isinstance(x, dict) and x.get('tag') == 'TRDT'))
        except Exception:  # noqa: BLE001
            pass
        stem = 'sound\\voice\\%s\\%s\\%s_%s_%02x%06x' % (owner, voice.lower(), quest_edid[:QUEST_CHARS].lower(),
                                                       topic_edid[:TOPIC_CHARS].lower(), own_prefix, local)
        return ['%s_%d' % (stem, n) for n in range(1, responses + 1)]

    def _voice(self, speaker_base, topic, quest, info, buffer):
        if not all((speaker_base, topic, quest, info)):
            return None
        ptrs = {k: self.live.resolve(v)['ptr'] for k, v in (('speaker', speaker_base), ('topic', topic),
                                                           ('quest', quest), ('info', info))}
        self.live.link.one('wr', word(buffer), '00')
        args = [{'type': 'ptr', 'value': '0x%08X' % ptrs['speaker']}, {'type': 'ptr', 'value': '0x%08X' % ptrs['topic']},
                {'type': 'ptr', 'value': '0x%08X' % ptrs['quest']}, {'type': 'ptr', 'value': '0x%08X' % ptrs['info']},
                {'type': 'ptr', 'value': '0x%08X' % buffer}]
        out = self.live.engine_call('0x%08X' % BUILD_VOICE_PATH, '0x00000000', args, 'bool', 'thiscall', True)
        raw = self.live.reads([(buffer, 260)])[0]
        path = raw.split(b'\0', 1)[0].decode('latin-1') if raw else ''
        return path if out.get('result') and path else None

    def dialogue(self, npc, limit=60):
        self.live.in_game()
        r = self.live.resolve(npc)
        base = self.live.form_ids([self.live.u32s([r['ptr'] + 0x20])[0]])[0].get('formId')
        users = self.records.record_users(base, ['INFO'], 500, 0)
        infos = [u for u in users.get('users', []) if u.get('type') == 'INFO'][:int(limit)]
        voice = self._voice_type(base)
        by_topic, rows = {}, []
        for u in infos:
            topic = self._info_topic(u['formId'])
            row = {'info': u['formId'], 'text': u.get('text'), 'topic': topic and (topic['editorId'] or topic['formId']),
                   '_topic': topic and topic['formId'], 'quest': None}
            if topic:
                by_topic.setdefault(topic['formId'], []).append(row)
            rows.append(row)
        for topic_id, topic_rows in by_topic.items():
            try:
                tree = self.social.dialogue_tree(npc, topic_id, False, 300, 'speaker')
            except Exception as e:  # noqa: BLE001
                for row in topic_rows:
                    row['conditions'] = 'not evaluated: %s' % str(e)[:100]
                continue
            verdicts = {}
            for resp in tree.get('responses', []):
                fid = str(resp.get('info') or '').upper()
                failing = [c.get('text') or c.get('function') for c in resp.get('conditions', [])
                           if c.get('passed') is False]
                verdicts[fid] = {'quest': resp.get('quest'), 'engineSays': resp.get('engineSays'),
                                 'failing': failing[:3]}
            for row in topic_rows:
                v = verdicts.get(row['info'].upper())
                if v:
                    row.update(v)
        for row in rows:
            row.pop('_topic', None)
            files = self._voice_files(voice, row.get('quest'), row.get('topic'), row['info'])
            if not files:
                continue
            row['voiceFiles'] = [f + '.ogg' for f in files]
            if self.assets:
                row['voiceFound'] = all(bool(self.assets.where(f + '.ogg').get('winner')) for f in files)
                row['lipFound'] = all(bool(self.assets.where(f + '.lip').get('winner')) for f in files)
        missing_voice = [r for r in rows if r.get('voiceFound') is False]
        unknown = [r for r in rows if 'voiceFound' not in r]
        return verdict('%s: %d lines; %d with no voice file' % (r['formId'], len(rows), len(missing_voice)),
                       'Lines are the INFO records whose conditions name the NPC; voice and lip paths follow the '
                       'naming rule of the archives of the game, checked against the loose files and archives.',
                       npc=r['formId'],
                       base=base, voiceType=voice, lines=rows, missingVoice=len(missing_voice),
                       voiceNotChecked=len(unknown))

    def run(self, action, **a):
        if action == 'cells':
            if not a.get('plugin'):
                raise ConvError('BAD_ARGUMENTS', 'cells needs plugin.')
            return self.cells(a['plugin'], a.get('max_cells') or 20, a.get('settle_frames') or 90,
                              a.get('photos', True))
        if action == 'dialogue':
            if not a.get('npc'):
                raise ConvError('BAD_ARGUMENTS', 'dialogue needs npc (a reference).')
            return self.dialogue(a['npc'], a.get('limit') or 60)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: cells, dialogue.' % action)
