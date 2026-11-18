"""Brief answers (tool quality part 5) for the tools whose usual answer is over 10,000 characters: fnv_threads list,
the hang report (fnv_threads hang) and fnv_scene_graph. Each function takes the full answer and returns a shorter
one with the same verdict; the full answer is never changed (brief=false returns it as it was)."""

THREAD_KEYS = ('threadId', 'main', 'start', 'now', 'gameFunction')
HANG_KEYS = ('pid', 'state', 'verdict', 'mainThreadId', 'busyThreads', 'threadCount', 'ping')
FAULT_KEYS = ('threadId', 'mainThread', 'code', 'address', 'access', 'accessAddress', 'request', 'consoleLine',
              'context', 'pathsOnStack')
STACK_FRAMES = 8


def threads(answer):
    """fnv_threads list: each thread's start routine and what it runs now, without the six-frame stack."""
    out = {k: v for k, v in answer.items() if k != 'threads'}
    out['threads'] = [{k: r[k] for k in THREAD_KEYS if r.get(k) is not None} for r in answer.get('threads') or []]
    out['brief'] = 'Each thread without its stack and start address; brief=false for both.'
    return out


def frame_name(frame):
    """One frame of the hang report as text: function (source), module+offset, or the address."""
    if not frame:
        return None
    if frame.get('function'):
        return '%s (%s)' % (frame['function'], frame['source']) if frame.get('source') else frame['function']
    if frame.get('module'):
        return '%s+%s' % (frame['module'], frame.get('offset'))
    return frame.get('address')


def _stack(frames):
    return [frame_name(f) for f in (frames or [])[:STACK_FRAMES]]


def hang(answer):
    """The hang report: per thread where it is stuck and one stack of names; held faults without frame lists."""
    out = {k: answer[k] for k in HANG_KEYS if k in answer}
    rows = []
    for t in answer.get('threads') or []:
        samples = t.get('samples') or []
        top = t.get('topGameFunction')
        # the first sample whose frames contain the function the thread was in most often
        pick = next((s for s in samples if any(f.get('function') == top for f in s.get('frames') or [])),
                    samples[0] if samples else {})
        rows.append({'threadId': t.get('threadId'), 'main': t.get('main'), 'topGameFunction': top,
                     'sameInSamples': t.get('sameInSamples'), 'cpuPercent': t.get('cpuPercent'),
                     'stack': _stack(pick.get('frames'))})
    out['threads'] = rows
    out['heldFaults'] = [dict({k: f.get(k) for k in FAULT_KEYS}, function=frame_name(f.get('function')),
                              stack=_stack(f.get('frames'))) for f in answer.get('heldFaults') or []]
    out['brief'] = ('One stack per thread, %d frames, as names; brief=false for every sample, the stack scan, '
                    'locals and addresses.' % STACK_FRAMES)
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
        notes.append('sequences: ' + ', '.join('%s%s' % (s.get('name'), '*' if s.get('active') else '')
                                               for s in sequences))
    elif n.get('controllers'):
        notes.append('controllers: ' + ', '.join(str(c.get('class')) for c in n['controllers']))
    if n.get('extraData'):
        notes.append('extra: ' + ', '.join('%s=%s' % (e.get('name') or e.get('class'), e['value']) if 'value' in e
                                           else str(e.get('name') or e.get('class')) for e in n['extraData']))
    if notes:
        parts.append(' (%s)' % '; '.join(notes))
    return ''.join(parts)


def scene(answer):
    """fnv_scene_graph: the node tree as indented lines (class, name, address, triangles, hidden, animation
    sequences with * for active, extra data) and a count per class; no transforms, flags or per-node textures."""
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
