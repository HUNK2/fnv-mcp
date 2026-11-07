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
    'removeitem': {'target': 'player', 'setup': ['AddItem Caps001 10'], 'trigger': ['RemoveItem Caps001 3'],
                   'observe': 'GetItemCount Caps001', 'expect': ['delta', -3]},
    'additem': {'target': 'player', 'setup': [], 'trigger': ['AddItem Caps001 4'], 'observe': 'GetItemCount Caps001',
                'expect': ['delta', 4]},
}

# Answer key, written from the names before any run: the panels each name's function must block when skipped
# (`primary`) and the ones it may also block because the name covers them (`allowed`).
BATTERY_KEY = {
    'TESObjectREFR::SetScale': (['setscale'], ['setscale']),
    'TESObjectREFR::Disable': (['disable'], ['disable']),
    'TESObjectREFR::Enable': (['enable'], ['enable']),
    'TESObjectREFR::SetAngleOnReference': (['setangle'], ['setangle']),
    'TESForm::SetDestroyed': (['setdestroyed'], ['setdestroyed']),
    'Actor::SetGhost': (['setghost'], ['setghost']),
    'Actor::SetActorValue': (['npc_setav'], ['npc_setav', 'player_setav', 'creature_setav']),
    'Creature::SetActorValue': (['creature_setav'], ['creature_setav']),
    'Creature::DamageActorValue': (['creature_damageav'], ['creature_damageav']),
    'PlayerCharacter::SetActorValue': (['player_setav'], ['player_setav']),
    'PlayerCharacter::DamageActorValue': (['player_damageav'], ['player_damageav']),
    'PlayerCharacter::RewardXP': (['rewardxp'], ['rewardxp']),
    'TESObjectREFR::RemoveItem': (['removeitem'], ['removeitem']),
}


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

    def trial(self, spec, ptr, function=None, intervention=None):
        """One repetition: setup, read, (hook), trigger, read, (unhook), teardown. Returns the readings."""
        self._lines(spec.get('setup'), ptr)
        time.sleep(0.15)
        before = self._read(spec['observe'], ptr)
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
                baseline[p] = None if not ptr else self.trial(spec, ptr)['effect']
            for a in cands:
                blocked = []
                for p in panels:
                    spec = PANELS[p]
                    ptr = targets.get(spec['target'])
                    if not ptr or not baseline[p]:
                        continue
                    t = self.trial(spec, ptr, hx(a), {'action': 'skip'})
                    if t['effect'] is False:
                        # confirm with reps: control works and the skip blocks, every time
                        ok = True
                        for _ in range(reps - 1):
                            c = self.trial(spec, ptr)
                            t2 = self.trial(spec, ptr, hx(a), {'action': 'skip'})
                            if not c['effect'] or t2['effect'] is not False:
                                ok = False
                                break
                        if ok:
                            blocked.append(p)
                results[a] = blocked
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
        path = self.live.project.state_dir / ('experiment_battery_%s.json' % time.strftime('%Y%m%d-%H%M%S'))
        path.write_text(json.dumps(out, indent=1, default=str), encoding='utf-8')
        out['file'] = str(path)
        return out
