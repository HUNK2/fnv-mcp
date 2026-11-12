"""Experiments (NAMING-PLAN.md component F, proof rule 4.2 "Experiment"): change one function's behaviour, do an
action, and check a prediction written down before the run.

A hypothesis:
  {"function": "0x00567490",
   "intervention": {"action": "skip"} | {"action": "result", "value": 0} | {"action": "arg", "arg": 1, "value": 2},
   "target": "player" | "npc" | "creature" | a ref,
   "setup": ["SetScale 1.0"],              console lines on the target before each repetition (no hook active)
   "trigger": ["SetScale 1.3"],            console lines on the target with the hook active
   "observe": "GetScale",                  an expression on the target, read before and after the trigger
   "expect": ["equals", 1.3] | ["delta", -3],   what the trigger does normally
   "predict": "blocked" | "normal",        what the intervention does to it (default: blocked)
   "teardown": [...], "reps": 3}
Each repetition runs once without the hook (control) and once with it (treatment). Confirmed: in every repetition
the control shows the normal effect and the treatment shows the predicted one. Refuted: the control works and the
treatment does the opposite of the prediction. Otherwise inconclusive. The raw readings are kept.

`blind_battery` re-identifies functions by experiment: every candidate is skipped during every panel of a fixed
battery, the panels it blocks are its fingerprint, and only then are names compared with `BATTERY_KEY`.
"""
import json
import time

from .fnv_link import LinkError
from .fnv_naming import hx

# Console-driven effects, each on its own kind of target. Caps001 is the caps item (vanilla EditorID).
PANELS = {
    'setscale': {'target': 'npc', 'setup': ['SetScale 1.0'], 'trigger': ['SetScale 1.3'], 'observe': 'GetScale',
                 'expect': ['equals', 1.3], 'teardown': ['SetScale 1.0']},
    'disable': {'target': 'npc', 'setup': ['Enable'], 'trigger': ['Disable'], 'observe': 'GetDisabled',
                'expect': ['equals', 1], 'teardown': ['Enable']},
    'enable': {'target': 'npc', 'setup': ['Disable'], 'trigger': ['Enable'], 'observe': 'GetDisabled',
               'expect': ['equals', 0], 'teardown': ['Enable']},
    'setangle': {'target': 'npc', 'setup': ['SetAngle Z 0'], 'trigger': ['SetAngle Z 90'], 'observe': 'GetAngle Z',
                 'expect': ['equals', 90]},
    'setanglex': {'target': 'npc', 'setup': ['SetAngle X 0'], 'trigger': ['SetAngle X 20'], 'observe': 'GetAngle X',
                  'expect': ['equals', 20], 'teardown': ['SetAngle X 0']},
    'setangley': {'target': 'npc', 'setup': ['SetAngle Y 0'], 'trigger': ['SetAngle Y 20'], 'observe': 'GetAngle Y',
                  'expect': ['equals', 20], 'teardown': ['SetAngle Y 0']},
    'setdestroyed': {'target': 'npc', 'setup': ['SetDestroyed 0'], 'trigger': ['SetDestroyed 1'],
                     'observe': 'GetDestroyed', 'expect': ['equals', 1], 'teardown': ['SetDestroyed 0']},
    'setghost': {'target': 'npc', 'setup': ['SetGhost 0'], 'trigger': ['SetGhost 1'], 'observe': 'GetIsGhost',
                 'expect': ['equals', 1], 'teardown': ['SetGhost 0']},
    'npc_setav': {'target': 'npc', 'setup': ['SetAV Luck 5'], 'trigger': ['SetAV Luck 7'], 'observe': 'GetAV Luck',
                  'expect': ['equals', 7], 'teardown': ['SetAV Luck 5']},
    'npc_damageav': {'target': 'npc', 'setup': ['RestoreAV Luck 100'], 'trigger': ['DamageAV Luck 1'],
                     'observe': 'GetAV Luck', 'expect': ['delta', -1], 'teardown': ['RestoreAV Luck 100']},
    'creature_setav': {'target': 'creature', 'setup': ['SetAV Aggression 0'], 'trigger': ['SetAV Aggression 1'],
                       'observe': 'GetAV Aggression', 'expect': ['equals', 1], 'teardown': ['SetAV Aggression 0']},
    'creature_damageav': {'target': 'creature', 'setup': ['RestoreAV Health 1000'], 'trigger': ['DamageAV Health 5'],
                          'observe': 'GetAV Health', 'expect': ['delta', -5], 'teardown': ['RestoreAV Health 1000']},
    'player_setav': {'target': 'player', 'setup': ['SetAV Luck 5'], 'trigger': ['SetAV Luck 7'], 'observe': 'GetAV Luck',
                     'expect': ['equals', 7], 'teardown': ['SetAV Luck 5']},
    'player_damageav': {'target': 'player', 'setup': ['RestoreAV Luck 100'], 'trigger': ['DamageAV Luck 1'],
                        'observe': 'GetAV Luck', 'expect': ['delta', -1], 'teardown': ['RestoreAV Luck 100']},
    'rewardxp': {'target': 'player', 'setup': [], 'trigger': ['RewardXP 25'], 'observe': 'GetAV XP',
                 'expect': ['delta', 25]},
    'removeitem': {'target': 'player', 'setup': ['AddItem f 10'], 'trigger': ['RemoveItem f 3'],
                   'observe': 'GetItemCount f', 'expect': ['delta', -3]},
    'additem': {'target': 'player', 'setup': [], 'trigger': ['AddItem f 4'], 'observe': 'GetItemCount f',
                'expect': ['delta', 4]},
    'forceav': {'target': 'npc', 'setup': ['SetAV Luck 5'], 'trigger': ['ForceAV Luck 9'], 'observe': 'GetAV Luck',
                'expect': ['equals', 9], 'teardown': ['SetAV Luck 5']},
    'modav': {'target': 'npc', 'setup': ['SetAV Luck 5'], 'trigger': ['ModAV Luck 2'], 'observe': 'GetAV Luck',
              'expect': ['delta', 2], 'teardown': ['SetAV Luck 5']},
    'restoreav': {'target': 'npc', 'setup': ['RestoreAV Luck 100', 'DamageAV Luck 2'], 'trigger': ['RestoreAV Luck 2'],
                  'observe': 'GetAV Luck', 'expect': ['delta', 2], 'teardown': ['RestoreAV Luck 100']},
    'setposx': {'target': 'npc', 'setup': ['SetPos X 100'], 'trigger': ['SetPos X 132'], 'observe': 'GetPos X',
                'expect': ['equals', 132]},
    'setrestrained': {'target': 'npc', 'setup': ['SetRestrained 0'], 'trigger': ['SetRestrained 1'],
                      'observe': 'GetRestrained', 'expect': ['equals', 1], 'teardown': ['SetRestrained 1']},
    'setunconscious': {'target': 'npc', 'setup': ['SetUnconscious 0'], 'trigger': ['SetUnconscious 1'],
                       'observe': 'GetUnconscious', 'expect': ['equals', 1], 'teardown': ['SetUnconscious 0']},
    'setalert': {'target': 'npc', 'setup': ['SetAlert 0'], 'trigger': ['SetAlert 1'], 'observe': 'GetIsAlerted',
                 'expect': ['equals', 1], 'teardown': ['SetAlert 0']},
    'setteammate': {'target': 'npc', 'setup': ['SetPlayerTeammate 0'], 'trigger': ['SetPlayerTeammate 1'],
                    'observe': 'GetPlayerTeammate', 'expect': ['equals', 1], 'teardown': ['SetPlayerTeammate 0']},
    'setlevel': {'target': 'npc', 'setup': ['SetLevel 3'], 'trigger': ['SetLevel 7'], 'observe': 'GetLevel',
                 'expect': ['equals', 7]},
    'setignorefriendlyhits': {'target': 'npc', 'setup': ['SetIgnoreFriendlyHits 0'], 'trigger': ['SetIgnoreFriendlyHits 1'],
                              'observe': 'GetIgnoreFriendlyHits', 'expect': ['equals', 1],
                              'teardown': ['SetIgnoreFriendlyHits 0']},
    'setforcerun': {'target': 'npc', 'setup': ['SetForceRun 0'], 'trigger': ['SetForceRun 1'], 'observe': 'GetForceRun',
                    'expect': ['equals', 1], 'teardown': ['SetForceRun 0']},
    'setforcesneak': {'target': 'npc', 'setup': ['SetForceSneak 0'], 'trigger': ['SetForceSneak 1'],
                      'observe': 'GetForceSneak', 'expect': ['equals', 1], 'teardown': ['SetForceSneak 0']},
    'addtofaction': {'target': 'npc', 'setup': ['RemoveFromFaction 1B2A4'], 'trigger': ['AddToFaction 1B2A4 0'],
                     'observe': 'GetInFaction 1B2A4', 'expect': ['equals', 1], 'teardown': ['RemoveFromFaction 1B2A4']},
    'removefromfaction': {'target': 'npc', 'setup': ['AddToFaction 1B2A4 0'], 'trigger': ['RemoveFromFaction 1B2A4'],
                          'observe': 'GetInFaction 1B2A4', 'expect': ['equals', 0]},
    'setfactionrank': {'target': 'npc', 'setup': ['AddToFaction 1B2A4 0'], 'trigger': ['SetFactionRank 1B2A4 1'],
                       'observe': 'GetFactionRank 1B2A4', 'expect': ['equals', 1],
                       'teardown': ['RemoveFromFaction 1B2A4']},
    'lock': {'target': 'door', 'setup': ['Unlock'], 'trigger': ['Lock 10'], 'observe': 'GetLocked',
             'expect': ['equals', 1], 'teardown': ['Unlock']},
    'setopenstate': {'target': 'door', 'setup': ['SetOpenState 0'], 'trigger': ['SetOpenState 1'],
                     'observe': 'GetOpenState', 'expect': ['equals', 1], 'teardown': ['SetOpenState 0']},
}

# Answer key, written from the names before any run: the panels each name's function must block when skipped
# (`primary`) and the ones it may also block because the name covers them (`allowed`).
BATTERY_KEY = {
    'TESObjectREFR::SetScale': (['setscale'], ['setscale']),
    'TESObjectREFR::Disable': (['disable'], ['disable']),
    'TESObjectREFR::Enable': (['enable'], ['enable']),
    'TESObjectREFR::SetAngleOnReference': (['setangle', 'setanglex', 'setangley'], ['setangle', 'setanglex', 'setangley']),
    'TESForm::SetDestroyed': (['setdestroyed'], ['setdestroyed']),
    'Actor::SetGhost': (['setghost'], ['setghost']),
    'Actor::SetActorValue': (['npc_setav'], ['npc_setav', 'player_setav', 'creature_setav']),
    'Creature::SetActorValue': (['creature_setav'], ['creature_setav']),
    'Creature::DamageActorValue': (['creature_damageav'], ['creature_damageav']),
    'PlayerCharacter::SetActorValue': (['player_setav'], ['player_setav']),
    'PlayerCharacter::DamageActorValue': (['player_damageav'], ['player_damageav']),
    'PlayerCharacter::RewardXP': (['rewardxp'], ['rewardxp']),
    'TESObjectREFR::RemoveItem': (['removeitem'], ['removeitem']),
    'Actor::SetAlert': (['setalert'], ['setalert']),
    'Actor::SetFactionRank': (['setfactionrank'], ['setfactionrank', 'addtofaction', 'removefromfaction']),
    'Actor::SetPlayerTeammate': (['setteammate'], ['setteammate']),
    'ExtraDataList::SetGhost': (['setghost'], ['setghost']),
    'ExtraDataList::SetScale': (['setscale'], ['setscale']),
    'MobileObject::SetPosition': (['setposx'], ['setposx']),
    'TESActorBaseData::SetLevel': (['setlevel'], ['setlevel']),
    'TESObjectREFR::SetAngleOnReferenceZ': (['setangle'], ['setangle']),
    'REFR_LOCK::SetLocked': (['lock'], ['lock']),
    'TESObjectREFR::SetAngleOnReferenceX': (['setanglex'], ['setanglex']),
    'TESObjectREFR::SetAngleOnReferenceY': (['setangley'], ['setangley']),
    'Actor::AddFaction': (['addtofaction'], ['addtofaction', 'setfactionrank']),
    'Script::SetAngleBase': (['setangle', 'setanglex', 'setangley'], ['setangle', 'setanglex', 'setangley']),
    'Script::SetPosBase': (['setposx'], ['setposx']),
    'Script::SetScaleBase': (['setscale'], ['setscale']),
    'Actor::SetUnConscious': (['setunconscious'], ['setunconscious']),
    'TESObjectREFR::AddLock': (['lock'], ['lock']),
}
# The console's own command handlers (proven from the game's command table): each blocks its command's panel.
for _cmd, _panels in {
        'SetScale': ['setscale'], 'Disable': ['disable'], 'Enable': ['enable'],
        'SetAngle': ['setangle', 'setanglex', 'setangley'],
        'SetDestroyed': ['setdestroyed'], 'SetGhost': ['setghost'],
        'SetActorValue': ['npc_setav', 'creature_setav', 'player_setav'],
        'DamageActorValue': ['npc_damageav', 'creature_damageav', 'player_damageav'], 'RewardXP': ['rewardxp'],
        'RemoveItem': ['removeitem'], 'AddItem': ['additem'], 'ForceActorValue': ['forceav'],
        'ModActorValue': ['modav'], 'RestoreActorValue': ['restoreav'], 'SetPos': ['setposx'],
        'SetRestrained': ['setrestrained'], 'SetUnconscious': ['setunconscious'], 'SetAlert': ['setalert'],
        'SetPlayerTeammate': ['setteammate'], 'SetLevel': ['setlevel'], 'SetIgnoreFriendlyHits': ['setignorefriendlyhits'],
        'AddToFaction': ['addtofaction'], 'RemoveFromFaction': ['removefromfaction'], 'SetFactionRank': ['setfactionrank'],
        'SetForceRun': ['setforcerun'], 'SetForceSneak': ['setforcesneak'], 'Lock': ['lock'],
        'SetOpenState': ['setopenstate']}.items():
    BATTERY_KEY['Cmd_%s_Execute' % _cmd] = (_panels, _panels)


def ret_pop(engine, image, address):
    """Bytes the function pops on return, read from its own `ret n` instructions in the clean exe (a skip that
    pops a different count would unbalance the caller's stack). Raises when its returns disagree."""
    from .fnv_probe import disassembler
    size = int(engine.functions[address].get('size') or 0)
    code = image.read(address, size) if size else None
    if not code:
        raise ValueError('No code for %s' % hx(address))
    pops = set()
    for _at, _sz, mnemonic, op_str in disassembler().disasm_lite(code, address):
        if mnemonic in ('ret', 'retn'):
            pops.add(int(op_str, 0) if op_str else 0)
    if len(pops) != 1:
        raise ValueError('%s returns with %s: cannot tell how many argument bytes a skip must pop'
                         % (hx(address), sorted(pops) or 'no ret (tail jumps only)'))
    return pops.pop()


class ExperimentTools:
    def __init__(self, live, control, naming):
        self.live, self.control, self.naming = live, control, naming

    @property
    def link(self):
        return self.live.link

    # ------------------------------------------------------------------ targets
    def targets(self):
        """A living, restrained NPC and creature near the player (restrained so they stay put), and the player."""
        out = {'player': self.live.resolve('player')['ptr']}
        for r in self.live.refs_near(radius=20000, form_type=200, limit=200)['refs']:
            if r.get('dead') or r.get('disabled'):
                continue
            ptr = self.live.resolve(r['formId'])['ptr']
            cls = (self.live.rtti([ptr]).get(ptr) or {}).get('class')
            key = {'Character': 'npc', 'Creature': 'creature'}.get(cls)
            if key and key not in out:
                for line in ('SetRestrained 1', 'StopCombat'):
                    self.link.console(line, ref=ptr)
                out[key] = ptr
                out[key + 'FormId'] = r['formId']
        doors = self.live.refs_near(radius=20000, form_type=28, limit=10)['refs']
        if doors:
            out['door'] = self.live.resolve(doors[0]['formId'])['ptr']
            out['doorFormId'] = doors[0]['formId']
        return out

    # ------------------------------------------------------------------ one hypothesis
    def _read(self, expr, ptr):
        from .fnv_link import value_of
        r = self.link.eval(expr, ref=ptr)
        v = value_of(r) if r.get('ok') else None
        return float(v) if isinstance(v, (int, float)) else v

    def _lines(self, lines, ptr):
        for line in lines or []:
            self.link.console(line, ref=ptr)

    def _effect(self, spec, before, after):
        kind, value = spec['expect']
        if before is None or after is None:
            return None
        if kind == 'equals':
            return abs(after - value) <= 1e-3 * max(1.0, abs(value))
        return abs((after - before) - value) <= 1e-3 * max(1.0, abs(value))

    # one hook per candidate for a whole battery: installed once, its skip switched on only around the trigger
    # (each install allocates stub memory the companion never frees, so calls already inside can finish)
    def _arm(self, function):
        from .fnv_control import ACT_PREFIX
        pop = ret_pop(self.live.engine(), self.live.deep._image(required=True), int(function, 16))
        self.control.hook_set(function, action='skip', value=0, pop_bytes=pop, allow_unproven=True)
        try:
            # The skip is live from the moment the hook exists: any failure before it is switched off removes it.
            h = self.control.deep.hook(ACT_PREFIX + '%08X' % int(function, 16))
            hook = {'id': h['id'], 'pop': pop}
            self._switch(hook, False)
        except BaseException:
            self._disarm(function)
            raise
        return hook

    def _switch(self, hook, on):
        from .fnv_link import word
        r = self.link.one('hookact', word(hook['id']), word(1 if on else 0), word(0), word(hook['pop'] if on else 0),
                          word(0), '-')
        if not r.get('ok'):
            raise LinkError('HOOK_SWITCH_FAILED', 'hookact failed: %s' % r.get('error'))

    def _disarm(self, function):
        try:
            self.control.hook_set(function, action='remove', allow_unproven=True)
        except Exception:  # noqa: BLE001 - already gone
            pass

    def trial(self, spec, ptr, function=None, intervention=None, toggle=None):
        """One repetition: setup, read, (hook), trigger, read, (unhook), teardown. Returns the readings. With
        `toggle` (a hook from _arm) the installed hook's skip is switched on just for the trigger."""
        self._lines(spec.get('setup'), ptr)
        time.sleep(0.15)
        before = self._read(spec['observe'], ptr)
        if toggle is not None:
            self._switch(toggle, True)
            try:
                self._lines(spec['trigger'], ptr)
                time.sleep(0.25)
            finally:
                self._switch(toggle, False)
            after = self._read(spec['observe'], ptr)
            self._lines(spec.get('teardown'), ptr)
            return {'before': before, 'after': after, 'effect': self._effect(spec, before, after)}
        hooked = False
        if function is not None:
            iv = dict(intervention or {'action': 'skip'})
            action = iv.get('action', 'skip')
            value = iv.get('value')
            pop = None
            if action == 'skip':
                value = 0 if value is None else value
                pop = ret_pop(self.live.engine(), self.live.deep._image(required=True), int(function, 16))
            self.control.hook_set(function, action=action, value=value, value_type=iv.get('value_type'),
                                  arg=iv.get('arg'), pop_bytes=pop, allow_unproven=True)
            hooked = True
        try:
            self._lines(spec['trigger'], ptr)
            time.sleep(0.25)
        finally:
            if hooked:
                try:
                    hits = self.control.hook_set(function, action='remove', allow_unproven=True)
                except Exception:  # noqa: BLE001 - report what we can
                    hits = {}
        after = self._read(spec['observe'], ptr)
        self._lines(spec.get('teardown'), ptr)
        out = {'before': before, 'after': after, 'effect': self._effect(spec, before, after)}
        if hooked:
            out['hookCalls'] = len((hits or {}).get('calls') or []) if isinstance(hits, dict) else None
        return out

    def test(self, hypothesis):
        """Run a hypothesis (see the module doc): control and treatment in each repetition, then a verdict."""
        self.live.in_game()
        engine = self.live.engine()
        h = dict(hypothesis)
        address = engine.resolve(h['function'])
        if h.get('panel'):
            spec = dict(PANELS[h['panel']])
        else:
            spec = {k: h[k] for k in ('setup', 'trigger', 'observe', 'expect', 'teardown') if k in h}
            spec['target'] = h.get('target', 'player')
        predict = h.get('predict', 'blocked')
        reps = max(3, int(h.get('reps', 3)))
        targets = self.targets()
        ptr = targets.get(spec['target']) if spec['target'] in ('player', 'npc', 'creature') else \
            self.live.resolve(spec['target'])['ptr']
        if not ptr:
            raise ValueError('No %s target near the player' % spec['target'])
        unpause = self.live.game.keep_unpaused(600)
        rows = []
        try:
            for _ in range(reps):
                control = self.trial(spec, ptr)
                treatment = self.trial(spec, ptr, hx(address), h.get('intervention'))
                rows.append({'control': control, 'treatment': treatment})
        except LinkError as e:
            return self._record(address, h, spec, rows, 'crashed', {'error': str(e), 'crash': self.live.game.crash_summary()
                                                                    if hasattr(self.live.game, 'crash_summary') else None})
        finally:
            self.live.game.stop_task(unpause)
        control_ok = all(r['control']['effect'] for r in rows)
        want = (predict == 'normal')
        treated = [r['treatment']['effect'] for r in rows]
        if control_ok and all(t is want for t in treated):
            verdict = 'confirmed'
        elif control_ok and all(t is (not want) for t in treated):
            verdict = 'refuted'
        else:
            verdict = 'inconclusive'
        return self._record(address, h, spec, rows, verdict, {'controlShowsNormalEffect': control_ok})

    def _record(self, address, h, spec, rows, verdict, extra):
        out = {'function': self.live.engine().summary(address), 'hypothesis': h, 'panel': spec, 'reps': rows,
               'verdict': verdict, 'rule': 'PROOF 4.2 experiment: >=3 repetitions, control without the change', **extra}
        db = self.naming.db()
        try:
            db.execute('CREATE TABLE IF NOT EXISTS experiments (at REAL, address INTEGER, verdict TEXT, hypothesis TEXT, '
                       'result TEXT)')
            db.execute('INSERT INTO experiments VALUES (?,?,?,?,?)', (time.time(), address, verdict, json.dumps(h),
                                                                       json.dumps(out, default=str)))
            db.commit()
        finally:
            db.close()
        return out

    # ------------------------------------------------------------------ blind re-identification
    def blind_battery(self, functions=None, panels=None, reps=3):
        """Skip each candidate during each panel. A candidate's fingerprint = the panels whose effect it blocked in
        every repetition while the control worked. Names are looked up only to score, after all runs."""
        self.live.in_game()
        engine = self.live.engine()
        names = functions or sorted(BATTERY_KEY)
        cands = [engine.resolve(n) for n in names]
        import random
        random.Random(5).shuffle(cands)  # run order does not follow the key
        panels = panels or list(PANELS)
        targets = self.targets()
        unpause = self.live.game.keep_unpaused(3600)
        results = {}
        try:
            baseline = {}
            for p in panels:
                spec = PANELS[p]
                ptr = targets.get(spec['target'])
                if not ptr:
                    baseline[p] = None
                    continue
                ok = [bool(self.trial(spec, ptr)['effect']) for _ in range(3)]
                baseline[p] = sum(ok) >= 2
            usable = [p for p in panels if targets.get(PANELS[p]['target']) and baseline[p]]
            errors = {}
            for a in cands:
                try:
                    hook = self._arm(hx(a))
                    hits = {p: 0 for p in usable}
                    for r in range(reps):
                        # round 1: every panel; later rounds confirm, with a control, only what stayed blocked
                        for p in (usable if r == 0 else [q for q in usable if hits[q] == r]):
                            spec, ptr = PANELS[p], targets[PANELS[p]['target']]
                            if r and not self.trial(spec, ptr)['effect']:
                                continue
                            if self.trial(spec, ptr, toggle=hook)['effect'] is False:
                                hits[p] += 1
                    results[a] = [p for p in usable if hits[p] == reps]
                except LinkError:
                    raise
                except Exception as e:  # noqa: BLE001 - one candidate's failure keeps the other results
                    errors[hx(a)] = '%s: %s' % (type(e).__name__, str(e)[:200])
                finally:
                    self._disarm(hx(a))
        except LinkError as e:
            return {'error': 'the game stopped answering: %s' % e, 'partial': {hx(a): b for a, b in results.items()}}
        finally:
            self.live.game.stop_task(unpause)
        # scoring: names come in only now
        detail, correct, wrong, abstained = [], 0, 0, 0
        for a in cands:
            name = engine.functions[a]['name']
            primary, allowed = BATTERY_KEY.get(name, ([], []))
            blocked = results.get(a, [])
            if not blocked:
                verdict = 'abstained'
                abstained += 1
            elif set(blocked) <= set(allowed) and set(blocked) & set(primary):
                verdict = 'correct'
                correct += 1
            else:
                verdict = 'wrong'
                wrong += 1
            detail.append({'address': hx(a), 'name': name, 'blocked': blocked, 'expected': primary, 'verdict': verdict})
        out = {'method': 'experiment battery (skip each candidate during each panel, %d repetitions, control each time)'
                         % reps, 'panels': panels, 'baseline': baseline, 'cases': len(cands), 'claims': correct + wrong,
               'correct': correct, 'wrong': wrong, 'abstained': abstained,
               'precision': round(correct / (correct + wrong), 4) if correct + wrong else None, 'detail': detail}
        if errors:
            out['errors'] = errors          # candidates whose run failed (counted as abstained)
        path = self.live.project.state_dir / ('experiment_battery_%s.json' % time.strftime('%Y%m%d-%H%M%S'))
        path.write_text(json.dumps(out, indent=1, default=str), encoding='utf-8')
        out['file'] = str(path)
        return out
