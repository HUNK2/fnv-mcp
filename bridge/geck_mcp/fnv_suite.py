"""Plan 2 W7: fnv_bisect and fnv_selftest.

fnv_bisect level=mods|plugins steps=[...] (the fnv_do steps of a scenario that fails): find the one MO2 mod or plugin
that makes it fail. Round zero checks that the scenario fails with everything on and passes with every candidate
off; then each round turns off half of the remaining suspects (fnv_profile_set, game closed), runs the scenario
(the runner launches the game), and keeps the half that holds the culprit. The profile is put back at the end.

fnv_selftest: quick checks of what each tool family relies on, against the running game: the companion and its wire,
engine-map identity, the singletons the tools read (player, TES, scene graph camera, HUD, OSGlobals, memory manager),
the prologues the hooks plan, the symbol database, the decomp checkout, and a cheap call into each family. Each row
is ok, degraded (a fallback is in use) or broken, with the fact that failed.
"""
import struct
import time
from pathlib import Path

from .fnv_conv import ConvError, verdict

PROTECTED_MODS = ('fnvmcp engine companion', 'xnvse data', 'jip ln nvse plugin')
BASE_PLUGINS = ('falloutnv.esm', 'deadmoney.esm', 'honesthearts.esm', 'oldworldblues.esm', 'lonesomeroad.esm',
                'gunrunnersarsenal.esm', 'classicpack.esm', 'mercenarypack.esm', 'tribalpack.esm', 'caravanpack.esm')


class BisectTools:
    def __init__(self, live, records, runner_factory, quit_game=None):
        self.live = live
        self.records = records
        self.runner_factory = runner_factory        # () -> ScenarioRunner
        self.quit_game = quit_game or (lambda: self.live.game.quit())

    def _candidates(self, level, keep):
        profile = self.live.game.profile_dir
        keep = {k.lower() for k in keep or []}
        if level == 'mods':
            lines = (profile / 'modlist.txt').read_text(encoding='utf-8').splitlines()
            mods = [l[1:] for l in lines if l.startswith('+')]
            return [m for m in mods if m.lower() not in PROTECTED_MODS and m.lower() not in keep
                    and not m.endswith('_separator')]
        lines = (profile / 'plugins.txt').read_text(encoding='utf-8', errors='replace').splitlines()
        active = [l[1:] for l in lines if l.startswith('*')]
        return [p for p in active if p.lower() not in BASE_PLUGINS and p.lower() not in keep]

    def _set(self, level, on, off):
        if self.live.game.process():
            self.quit_game()
            for _ in range(60):
                if not self.live.game.process():
                    break
                time.sleep(0.5)
        changes = {**{x: True for x in on}, **{x: False for x in off}}
        if level == 'mods':
            self.records.profile_set(changes, None, self.live.game, False)
        else:
            self.records.profile_set(None, changes, self.live.game, False)

    def _passes(self, scenario):
        report = self.runner_factory().run(data=dict(scenario))
        return bool(report.get('passed')), report.get('summary') or report.get('failures')

    def bisect(self, level, steps, start='continue', keep=None, max_rounds=10):
        if level not in ('mods', 'plugins'):
            raise ConvError('BAD_ARGUMENTS', 'level is mods or plugins.')
        if not steps:
            raise ConvError('BAD_ARGUMENTS', 'steps (the failing scenario, as for fnv_do) are needed.')
        scenario = {'name': 'bisect', 'start': start, 'steps': list(steps), 'relaunch_on_crash': True}
        everything = self._candidates(level, keep)
        if len(everything) < 1:
            raise ConvError('NO_CANDIDATES', 'No %s to bisect (protected ones are kept on).' % level)
        rounds = []
        try:
            self._set(level, everything, [])
            ok, why = self._passes(scenario)
            rounds.append({'round': 0, 'off': [], 'passed': ok})
            if ok:
                return verdict('The scenario passes with every %s on: nothing to bisect' % level[:-1], str(why),
                               rounds=rounds)
            self._set(level, [], everything)
            ok, why = self._passes(scenario)
            rounds.append({'round': 0, 'off': list(everything), 'passed': ok})
            if not ok:
                return verdict('The scenario fails even with every candidate off: the cause is not one of them',
                               str(why), rounds=rounds, candidates=everything)
            suspects = list(everything)
            n = 1
            while len(suspects) > 1 and n <= max_rounds:
                half = suspects[:len(suspects) // 2]
                rest = [x for x in everything if x not in half]
                self._set(level, half, rest)         # only the first half of the suspects on
                ok, why = self._passes(scenario)
                rounds.append({'round': n, 'on': half, 'passed': ok})
                suspects = half if not ok else suspects[len(suspects) // 2:]
                n += 1
            culprit = suspects[0] if len(suspects) == 1 else None
            return verdict('The culprit is %s (%d rounds)' % (culprit, n - 1) if culprit else
                           'Narrowed to %d suspects in %d rounds' % (len(suspects), n - 1),
                           'With only the culprit\'s half on, the scenario fails; with the other half on, it passes.',
                           culprit=culprit, suspects=suspects, rounds=rounds, candidates=everything)
        finally:
            try:
                self._set(level, everything, [])        # the profile as it was
            except Exception:  # noqa: BLE001 - reported by the caller's next fnv_profile_set
                pass


class SelfTest:
    def __init__(self, live, tools):
        self.live = live
        self.tools = tools          # dict of tool objects by family

    def _row(self, family, check, fn):
        try:
            state, detail = fn()
        except Exception as e:  # noqa: BLE001 - a broken check is a result
            state, detail = 'broken', str(e)[:200]
        return {'family': family, 'check': check, 'state': state, 'detail': detail}

    def run(self):
        live = self.live
        rows = []

        def ping():
            p = live.link.ping()
            return ('ok' if p.get('wire') == 2 else 'degraded'), 'companion %s wire %s frame %s' % (
                p.get('companion'), p.get('wire'), p.get('frame'))
        rows.append(self._row('foundation', 'companion', ping))

        def engine_map():
            ident = live.engine().identity()
            return ('ok' if ident.get('exeImageCheck') else 'degraded'), 'decomp %s' % str(ident.get('decompCommit'))[:10]
        rows.append(self._row('foundation', 'engine map', engine_map))

        def symdb():
            db = getattr(live.engine(), 'symdb', None)
            return ('ok', 'symbol database loaded') if db else ('degraded', 'no symbol database: no lines or locals')
        rows.append(self._row('foundation', 'symbol database', symdb))

        singletons = {'player': 0x011DEA3C, 'TES': 0x011DEA10, 'scene graph': 0x011DEB7C, 'HUD': 0x011D96C0,
                      'OSGlobals': 0x011DEA0C}
        for name, address in singletons.items():
            def single(address=address, name=name):
                v = live.u32s([address])[0]
                return ('ok', '0x%08X' % v) if v else ('broken', '%s pointer at 0x%08X is null' % (name, address))
            rows.append(self._row('eyes and hands', name, single))

        def camera():
            cam = live.u32s([live.u32s([0x011DEB7C])[0] + 0xAC])[0]
            cls = (live.rtti([cam]).get(cam) or {}).get('class')
            return ('ok' if cls == 'NiCamera' else 'broken'), 'scene graph +0xAC is %s' % cls
        rows.append(self._row('eyes', 'camera', camera))

        prologues = {'TESObjectREFR::Activate': (0x00573170, '558bec'), 'TES::RunScripts': (0x00455490, '558bec'),
                     'MemoryManager::Allocate': (0x00AA3E40, None), 'Warning': (0x005B5E40, None)}
        for name, (address, expect) in prologues.items():
            def prologue(address=address, expect=expect):
                raw = live.reads([(address, 6)])[0]
                if raw is None:
                    return 'broken', 'unreadable'
                if raw[0] == 0xE9:
                    return 'degraded', 'starts with another module\'s jump (hooks chain behind it)'
                if expect and not raw.hex().startswith(expect):
                    return 'broken', 'prologue %s, expected %s...' % (raw.hex(), expect)
                return 'ok', raw.hex()
            rows.append(self._row('hooks', name, prologue))

        def memory():
            out = self.tools['memory'].pools()
            return ('ok' if out.get('pools') else 'broken'), out.get('verdict')
        rows.append(self._row('memory', 'pools', memory))

        def formula():
            out = self.tools['systems'].formula('xp_for_level', [2])
            return ('ok' if out.get('result') == 200 else 'broken'), out.get('verdict')
        rows.append(self._row('systems', 'formula call', formula))

        def camera_tool():
            out = self.tools['eyes'].camera_tool('get')
            return 'ok', 'fov %s' % out.get('fov')
        rows.append(self._row('eyes', 'fnv_camera get', camera_tool))

        def hud():
            out = self.tools['eyes'].hud_read()
            return 'ok', 'mode %s' % (out.get('mode') or {}).get('name')
        rows.append(self._row('eyes', 'fnv_hud read', hud))

        def profile():
            out = self.tools['profile'].sample(0.3, 50, 'main', 'selftest', 3)
            return ('ok' if out.get('samples') else 'broken'), '%s samples' % out.get('samples')
        rows.append(self._row('profile', 'sample', profile))

        def native():
            out = live.link.one('mod', 'list')
            return ('ok' if out.get('ok') else 'broken'), '%d modules' % len(out.get('modules', []))
        rows.append(self._row('engine code', 'module loader', native))

        def code_ops():
            out = live.link.one('code', 'free', '10')        # not a block: the op answers unknown_block
            return ('ok' if out.get('error') == 'unknown_block' else 'broken'), 'code ops answer'
        rows.append(self._row('engine code', 'code placement', code_ops))

        def coverage():
            out = live.link.one('cov', 'read', '0')
            return ('ok' if out.get('ok') else 'broken'), 'coverage ops answer'
        rows.append(self._row('coverage', 'cov', coverage))

        def decomp():
            root = getattr(live.project.fnv, 'decomp_root', None)
            ok = root and (Path(root) / 'build' / 'units.json').is_file()
            return ('ok', str(root)) if ok else ('degraded', 'no decomp checkout: fnv_code cannot build')
        rows.append(self._row('engine code', 'decomp checkout', decomp))

        counts = {s: sum(1 for r in rows if r['state'] == s) for s in ('ok', 'degraded', 'broken')}
        return verdict('%d ok, %d degraded, %d broken' % (counts['ok'], counts['degraded'], counts['broken']),
                       'Run after a game, xNVSE, plugin or decomp update.', checks=rows, **counts)
