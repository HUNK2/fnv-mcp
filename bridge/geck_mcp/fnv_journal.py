"""The act tools' journal (PLAN-3 S2.3, D3/D8): an append-only JSON-lines file shared by every server process.

- Every change is one line with a `seq` number (from the sidecar `journal.seq`, under the journal's OS lock), an
  id, a time and the game process it was made in (`pid`, `started`). The identity comes from the journal, never
  from the caller (R19); a change made with no known game is an `orphan` and is never undone automatically.
- Nothing is rewritten in place: an undo appends `{"kind": "_undone", "ids": [...]}`, which `entries()` folds
  into `undone: true` (R8: a reader holding the file can no longer make an undo go unrecorded).
- `entries()` reads only what was appended since the last call (it starts over when the file shrank).
- `compact(live)` runs at server start: entries of other game processes go to `journal-archive-YYYYMMDD.jsonl`
  (entries without a pid, from before the identity stamp, to `journal-archive-legacy.jsonl`).
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .fnv_state import GameIdentity, atomic_write, locked

UNDONE, CONFIRM = '_undone', '_confirm'
# An add waits this long for the journal lock: longer than an undo may hold it (fnv_live.UNDO_LOCK_TIMEOUT plus one
# companion request), so a change that already landed in the game always gets its entry (S2 review I3).
ADD_TIMEOUT = 60.0
EVENTS = (UNDONE, CONFIRM)
PROTECTED = ('id', 'seq', 'pid', 'started', 'orphan', 'undone', 'kind', 'state', 'time')   # a confirm never changes these
_ASK = object()


class Journal:
    def __init__(self, path, game=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seq_path = self.path.with_name(self.path.stem + '.seq')
        self.game = game            # callable -> the running game ({'pid', 'started'} or a GameIdentity), or None
        self._mutex = threading.RLock()
        self._records = []          # raw records read so far, in file order
        self._offset = 0
        self._fold = None           # (number of records folded, {id: entry}, [ids in order])
        self._local = threading.local()   # .ids: entries this thread added inside recording() (PLAN-3 S5.1)

    def recording(self):
        """Collect the ids of the entries this thread adds until the block ends (a tool call's own changes; other
        threads' entries are not included). Nested recordings also pass their ids to the outer one."""
        journal = self

        class _Recording:
            def __enter__(self):
                self.outer = getattr(journal._local, 'ids', None)
                self.ids = journal._local.ids = []
                return self.ids

            def __exit__(self, *exc):
                journal._local.ids = self.outer
                if self.outer is not None:
                    self.outer.extend(self.ids)
                return False
        return _Recording()

    # ------------------------------------------------------------------ locking
    def locked(self, timeout=10.0):
        """The journal's cross-process lock (undo holds it while it applies an entry, so no entry is undone twice)."""
        return locked(self.path, timeout)

    # ------------------------------------------------------------------ reading
    def _refresh(self):
        try:
            st = self.path.stat()
            size, ino = st.st_size, st.st_ino
        except FileNotFoundError:
            size, ino = 0, None
        if size < self._offset or ino != getattr(self, '_ino', ino):   # compacted or replaced: read it again
            self._records, self._offset, self._fold = [], 0, None
        self._ino = ino
        if size == self._offset:
            return
        with open(self.path, 'rb') as f:
            f.seek(self._offset)
            data = f.read(size - self._offset)
        end = data.rfind(b'\n') + 1          # only whole lines; a line being written is read next time
        for line in data[:end].splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line.decode('utf-8'))
            except ValueError:   # a line torn by an older, unlocked writer: skip it, keep the rest
                continue
            if isinstance(record, dict):
                self._records.append(record)
        self._offset += end

    def _folded(self):
        with self._mutex:
            self._refresh()
            done, by_id, order = self._fold or (0, {}, [])
            for record in self._records[done:]:
                kind = record.get('kind')
                if kind == UNDONE:
                    for i in record.get('ids') or []:
                        if i in by_id:
                            by_id[i]['undone'] = True
                            by_id[i].setdefault('undoneAt', record.get('time'))
                elif kind == CONFIRM:
                    e = by_id.get(record.get('id'))
                    if e is not None:
                        if e.get('state') == 'pending' and e.get('undone'):
                            # another server undid the intent before the change landed (S3 review I1)
                            e['undone'] = False
                            e.pop('undoneAt', None)
                        e['state'] = 'confirmed'
                        after = record.get('after')
                        if isinstance(after, dict):     # what the change turned out to be (S3.2)
                            e.update({k: v for k, v in after.items() if k not in PROTECTED})
                elif record.get('id'):
                    entry = dict(record)
                    entry.setdefault('undone', False)
                    entry.setdefault('state', 'confirmed')   # only an intent written before its change is pending
                    entry.setdefault('seq', 0)        # entries from before seq numbers
                    if entry['id'] not in by_id:
                        order.append(entry['id'])
                    by_id[entry['id']] = entry
            self._fold = (len(self._records), by_id, order)
            return by_id, order

    def entries(self, since_seq=None, process=_ASK, undoable=None):
        """Entries in journal order, with undo records folded in. since_seq: only entries after that number;
        process: only entries made in that game process (orphans never match; None: no game, so nothing matches;
        left out: every process); undoable: True for entries that
        can still be undone, False for the rest."""
        by_id, order = self._folded()
        filtering = process is not _ASK
        process = GameIdentity.parse(process) if filtering else None
        if filtering and process is None:
            return []
        out = []
        for i in order:
            e = by_id[i]
            if since_seq is not None and (e.get('seq') or 0) <= since_seq:
                continue
            if process is not None:
                ident = GameIdentity.parse(e)
                if not ident or not ident.same_game(process):
                    continue
            if undoable is not None and bool(e.get('undo') and not e['undone']) != undoable:
                continue
            out.append(dict(e))
        return out

    def get(self, entry_id):
        e = self._folded()[0].get(entry_id)
        return dict(e) if e else None

    def last_seq(self):
        """The highest seq number used: the sidecar or the journal itself, whichever is higher (an empty or stale
        sidecar never makes a seq repeat; S2 review I2)."""
        with self._mutex:
            try:
                side = int(self.seq_path.read_text(encoding='ascii').strip() or 0)
            except (OSError, ValueError):
                side = 0
            by_id, _ = self._folded()
            return max([side] + [e.get('seq') or 0 for e in by_id.values()])

    # ------------------------------------------------------------------ writing
    def _identity(self, given):
        if given is not _ASK:
            return GameIdentity.parse(given)
        if self.game is None:
            return None
        try:
            return GameIdentity.parse(self.game())
        except Exception:  # noqa: BLE001 - no game answer: the entry is an orphan
            return None

    def _append(self, record):
        """Append one line (under the lock). A last line left without its newline (a server killed mid-append) is
        closed first, so the new record is never glued to it and lost (S2 review I1)."""
        line = (json.dumps(record) + '\n').encode('utf-8')
        with open(self.path, 'a+b') as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b'\n':
                    line = b'\n' + line
            f.seek(0, os.SEEK_END)
            f.write(line)
            f.flush()

    def add(self, entry, identity=_ASK):
        """Append a change. Its pid/started come from `identity` (or the journal's game callable), never from the
        entry; with no known game it is stored as an orphan."""
        ident = self._identity(identity)
        body = {k: v for k, v in entry.items() if k not in ('pid', 'started', 'seq', 'orphan', 'undone')}
        stamp = ident.as_dict() if ident else {'orphan': True}
        body.pop('id', None)             # the journal's id, never the caller's (S2 review M1)
        with self.locked(ADD_TIMEOUT):
            seq = self.last_seq() + 1
            record = {'id': uuid.uuid4().hex[:12], 'seq': seq, 'time': time.time(), 'undone': False, **stamp, **body}
            atomic_write(self.seq_path, str(seq))
            self._append(record)
        ids = getattr(self._local, 'ids', None)
        if ids is not None:
            ids.append(record['id'])
        return record

    def mark_undone(self, ids):
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return
        with self.locked(ADD_TIMEOUT):
            self._append({'kind': UNDONE, 'ids': ids, 'time': time.time()})

    def confirm(self, entry_id, after=None):
        """The change of a pending entry (added with state 'pending' before the game was touched) landed; `after`
        updates the entry's fields (the old bytes actually replaced, the pose reached...). PLAN-3 S3.2."""
        with self.locked(ADD_TIMEOUT):
            self._append({'kind': CONFIRM, 'id': entry_id, 'after': after, 'time': time.time()})

    # ------------------------------------------------------------------ compaction
    def compact(self, live):
        """Move every entry not made in the game process `live` (and the undo records that only name such
        entries) to an archive file next to the journal; rewrite the journal with the rest. Returns {kept, archived}."""
        live = GameIdentity.parse(live)
        with self.locked():
            with self._mutex:
                self._records, self._offset, self._fold = [], 0, None
                self._refresh()
                records = list(self._records)
            if not records:
                return {'kept': 0, 'archived': 0}
            last = max([r.get('seq') or 0 for r in records] + [self.last_seq()])
            kept_ids, keep, dated, legacy = set(), [], [], []
            for r in records:
                if r.get('kind') in EVENTS:
                    continue
                ident = GameIdentity.parse(r) if r.get('pid') else None
                if ident and live and ident.same_game(live):
                    kept_ids.add(r.get('id'))
                    keep.append(r)
                elif r.get('pid') or r.get('orphan'):
                    dated.append(r)
                else:
                    legacy.append(r)
            events_keep, events_archive = [], []
            for r in records:
                if r.get('kind') == UNDONE:
                    ids = r.get('ids') or []
                    mine = [i for i in ids if i in kept_ids]
                    if mine:
                        events_keep.append(dict(r, ids=mine))
                    if len(mine) < len(ids):
                        events_archive.append(dict(r, ids=[i for i in ids if i not in kept_ids]))
                elif r.get('kind') == CONFIRM:
                    (events_keep if r.get('id') in kept_ids else events_archive).append(r)
            archived = len(dated) + len(legacy)
            if not archived:
                return {'kept': len(keep), 'archived': 0}
            if dated or events_archive:
                name = 'journal-archive-%s.jsonl' % time.strftime('%Y%m%d')
                with open(self.path.with_name(name), 'ab') as f:
                    f.write(''.join(json.dumps(r) + '\n' for r in dated + events_archive).encode('utf-8'))
            if legacy:
                with open(self.path.with_name('journal-archive-legacy.jsonl'), 'ab') as f:
                    f.write(''.join(json.dumps(r) + '\n' for r in legacy).encode('utf-8'))
            # entries in file order, then the undo records that still name kept entries
            body = [r for r in records if r.get('kind') not in EVENTS and r.get('id') in kept_ids]
            atomic_write(self.path, ''.join(json.dumps(r) + '\n' for r in body + events_keep))
            atomic_write(self.seq_path, str(last))
            with self._mutex:
                self._records, self._offset, self._fold = [], 0, None
            return {'kept': len(keep), 'archived': archived}

    def size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0
