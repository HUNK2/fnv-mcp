"""Brief answers (tool quality part 5) for the tools whose usual answer is over 10,000 characters: fnv_threads list,
the hang report (fnv_threads hang) and fnv_scene_graph. Each function takes the full answer and returns a shorter
one with the same verdict; the full answer is never changed (brief=false returns it as it was)."""
import re

THREAD_KEYS = ('threadId', 'main', 'start', 'now', 'gameFunction')
HANG_KEYS = ('pid', 'state', 'verdict', 'mainThreadId', 'busyThreads', 'threadCount', 'ping')
FAULT_KEYS = ('threadId', 'mainThread', 'code', 'address', 'access', 'accessAddress', 'request', 'consoleLine',
              'context', 'pathsOnStack')
STACK_FRAMES = 8


_GLUED = re.compile(r'^(?=[0-9A-F]{0,5}\d)[0-9A-F]{6}(?=[A-Za-z_~]\w*::)')
INVALID_LOCK = 0xFFFFFFFF


def clean_name(name, address=None):
    """A function name without a hex address glued in front ("E7CCD0Renderer::PresentFrame" is
    "Renderer::PresentFrame"). With the function's address the prefix is exact; without it, six hex digits with a
    digit among them, before a Class:: name."""
    if not isinstance(name, str):
        return name
    if isinstance(address, str):
        try:
            address = int(address, 16)
        except ValueError:
            address = None
    if address is not None:
        for prefix in ('%08X' % address, '%X' % address):
            if name.startswith(prefix) and len(name) > len(prefix) and not name[len(prefix)].isdigit():
                return name[len(prefix):]
    return _GLUED.sub('', name)


def _lock(row):
    """A lock row; an owner or count of 0xFFFFFFFF (-1) is not a lock state, so no thread id and no count."""
    if row.get('ownerThread') == INVALID_LOCK or row.get('count') == INVALID_LOCK:
        return dict(row, ownerThread=None, count=None,
                    note='not a valid lock state (owner and count read 0xFFFFFFFF); the lock is not in use or is not '
                         'laid out as read')
    return row


def threads(answer):
    """fnv_threads list: each game thread's start routine and what it runs now, without the six-frame stack. Threads
    with no game function (graphics drivers, system pools) are one row per start module: count and thread ids."""
    out = {k: v for k, v in answer.items() if k not in ('threads', 'locks')}
    rows, groups = [], {}
    for r in answer.get('threads') or []:
        if r.get('gameFunction') is None and not r.get('main'):
            start = r.get('start') or 'unknown'
            module = start.split('!', 1)[0] if '!' in start else start
            groups.setdefault(module, []).append(r.get('threadId'))
            continue
        row = {k: r[k] for k in THREAD_KEYS if r.get(k) is not None}
        for k in ('start', 'now', 'gameFunction'):
            if k in row:
                row[k] = clean_name(row[k])
        rows.append(row)
    out['threads'] = rows
    out['threadsWithoutGameFunction'] = [{'startModule': m, 'count': len(ids), 'threadIds': ids}
                                         for m, ids in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    if 'locks' in answer:
        out['locks'] = [_lock(x) for x in answer['locks'] or []]
    out['brief'] = ('Each game thread without its stack and start address; threads with no game function are grouped '
                    'by start module; brief=false for every thread.')
    return out


def frame_name(frame):
    """One frame of the hang report as text: function (source), module+offset, or the address."""
    if not frame:
        return None
    if frame.get('function'):
        fn = clean_name(frame['function'])
        return '%s (%s)' % (fn, frame['source']) if frame.get('source') else fn
    if frame.get('module'):
        return '%s+%s' % (frame['module'], frame.get('offset'))
    return frame.get('address')


def _stack(frames):
    """The first STACK_FRAMES frames from the first game function down (the driver and ntdll frames above it come
    to a count), or the top frames when no frame is a game function."""
    frames = frames or []
    first = next((i for i, f in enumerate(frames) if f.get('function')), 0)
    return [frame_name(f) for f in frames[first:first + STACK_FRAMES]]


def _skipped(frames):
    return next((i for i, f in enumerate(frames or []) if f.get('function')), 0)


def hang(answer):
    """The hang report: per thread where it is stuck and one stack of names; held faults without frame lists."""
    out = {k: answer[k] for k in HANG_KEYS if k in answer}
    if isinstance(out.get('verdict'), str):
        out['verdict'] = re.sub(r'(?=[0-9A-F]{0,5}\d)[0-9A-F]{6}(?=[A-Za-z_~]\w*::)', '', out['verdict'])
    rows = []
    for t in answer.get('threads') or []:
        samples = t.get('samples') or []
        top = t.get('topGameFunction')
        # the first sample whose frames contain the function the thread was in most often
        pick = next((s for s in samples if any(f.get('function') == top for f in s.get('frames') or [])),
                    samples[0] if samples else {})
        row = {'threadId': t.get('threadId'), 'main': t.get('main'), 'topGameFunction': clean_name(top),
               'sameInSamples': t.get('sameInSamples'), 'cpuPercent': t.get('cpuPercent'),
               'stack': _stack(pick.get('frames'))}
        skipped = _skipped(pick.get('frames'))
        if skipped:
            row['framesAboveGameFunction'] = skipped
        rows.append(row)
    out['threads'] = rows
    out['heldFaults'] = [dict({k: f.get(k) for k in FAULT_KEYS}, function=frame_name(f.get('function')),
                              stack=_stack(f.get('frames'))) for f in answer.get('heldFaults') or []]
    out['brief'] = ('One stack per thread, %d frames as names, starting at the first game function; brief=false for '
                    'every sample, the stack scan, locals and addresses.' % STACK_FRAMES)
    return out


def _node_line(n, level):
    parts = ['%s%s "%s" %s' % ('  ' * level, n.get('class'), n.get('name') or '', n.get('node'))]
    g = n.get('geometryInfo') or {}
    if 'triangles' in g:
        parts.append(' %s tris' % g['triangles'] + (', skinned' if g.get('skinned') else ''))
    notes = []
    if n.get('hidden'):
        notes.append('hidden')
    sequences = [s for c in n.get('controllers') or [] for s in c.get('sequences') or []]
    if sequences:
        active = [s for s in sequences if s.get('active')]
        notes.append('%d sequences, active: %s' % (len(sequences), ', '.join(str(s.get('name')) for s in active)
                                                   if active else 'none'))
    elif n.get('controllers'):
        notes.append('controllers: ' + ', '.join(str(c.get('class')) for c in n['controllers']))
    if n.get('extraData'):
        notes.append('extra: ' + ', '.join('%s=%s' % (e.get('name') or e.get('class'), ' '.join(str(e['value']).split())) if 'value' in e
                                           else str(e.get('name') or e.get('class')) for e in n['extraData']))
    if notes:
        parts.append(' (%s)' % '; '.join(notes))
    return ''.join(parts)


def scene(answer):
    """fnv_scene_graph: the node tree as indented lines (class, name, address, triangles, hidden, animation
    sequence count and the active ones, extra data) and a count per class; no transforms, flags or per-node textures."""
    if 'nodes' not in answer:
        return answer
    nodes = answer['nodes']
    children, known = {}, {n.get('node') for n in nodes}
    for n in nodes:
        children.setdefault(n.get('parent') if n.get('parent') in known else None, []).append(n)
    tree, stack = [], [(n, 0) for n in reversed(children.get(None, []))]
    while stack:
        n, level = stack.pop()
        tree.append(_node_line(n, level))
        stack.extend((c, level + 1) for c in reversed(children.get(n.get('node'), [])))
    classes = {}
    for n in nodes:
        classes[n.get('class')] = classes.get(n.get('class'), 0) + 1
    out = {k: v for k, v in answer.items() if k not in ('nodes', 'uses')}
    out['tree'] = tree
    out['classes'] = classes
    out['brief'] = ('The tree as lines; brief=false for each node\'s local and world transform, flags, geometry, '
                    'shader, textures, controllers and extra data.')
    return out
