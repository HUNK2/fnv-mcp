"""fnv_guide: which tool for which problem. Guides (Markdown in fnv_mcp/guides/), a search over the tool
descriptions, and a search over saved engine research notes and the recorded engine facts."""
import math
import os
import re
from pathlib import Path

from .fnv_conv import ConvError, dispatch, page, verdict

GUIDES_DIR = Path(__file__).resolve().parent / 'guides'
STOP = set('a an the to of for in on is it my i do does did how what which why when and or with that this tool tools '
           'use find get can be are was me from into at by its so fnv please want need'.split())
TOOL_NAME = re.compile(r'\b(?:fnv|geck)_[a-z0-9_]+\b')
UNDOUBLE = set('bdgmnpt')          # running -> run, stopped -> stop; call, miss, buzz keep their pair


def stem(w):
    """A small suffix rule (no dependency): plural and -ing/-ed endings go, then a final e, so place, places,
    placed and placing all give "plac", and building gives "build" (a whole word: it never matches "build" inside
    another word)."""
    if len(w) > 4 and w.endswith('ies'):
        w = w[:-3] + 'y'
    elif len(w) > 4 and w.endswith('sses'):
        w = w[:-2]
    elif (len(w) > 3 and w.endswith('es') and w[-3] in 'sxz') or w.endswith(('ches', 'shes')):
        w = w[:-2]
    elif len(w) > 3 and w.endswith('s') and not w.endswith(('ss', 'us', 'is')):
        w = w[:-1]
    for suffix in ('ing', 'ed'):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            w = w[:-len(suffix)]
            if len(w) > 3 and w[-1] == w[-2] and w[-1] in UNDOUBLE:
                w = w[:-1]
            break
    if len(w) > 3 and w.endswith('e'):
        w = w[:-1]
    return w


def words(text):
    """Lower-case whole words of a text, stop words dropped, each stemmed (fnv_guide find, part 2)."""
    return [stem(w) for w in re.findall(r'[a-z0-9]+', (text or '').lower()) if w not in STOP]


stems = words          # the older name (guides and the engine search use it)


def load_guides(folder=GUIDES_DIR):
    """{name: {name, title, summary, words, text}}. A guide starts with '# Title', then 'Summary: ...' and
    'Words: a, b, c' lines."""
    guides = {}
    for path in sorted(Path(folder).glob('*.md')):
        text = path.read_text(encoding='utf-8')
        title = re.search(r'^# (.+)$', text, re.M)
        summary = re.search(r'^Summary: (.+)$', text, re.M)
        words = re.search(r'^Words: (.+)$', text, re.M)
        guides[path.stem] = {'name': path.stem, 'title': title.group(1).strip() if title else path.stem,
                             'summary': summary.group(1).strip() if summary else '',
                             'words': words.group(1).strip() if words else '', 'text': text}
    return guides


def tool_table(toolsets=None):
    """{name: {kind, toolset, description}} for every offline and live fnv_* tool. A tool whose toolset is not in
    `toolsets` (default: all) is not registered, so its description says how to turn it on (TOOL-MERGE step 1)."""
    from .fnv_live_tools import FNV_LIVE_TOOLS
    from .fnv_spec import FNV_SPEC_TOOLS
    from .fnv_tools import FNV_TOOLS
    from .toolsets import DEFAULT_TOOLSETS, hidden_note, registered, toolset_of
    active = set(DEFAULT_TOOLSETS if toolsets is None else toolsets)
    table = {n: {'kind': 'read', 'description': d} for n, d in FNV_TOOLS.items()}
    table.update({n: {'kind': k, 'description': d} for n, (k, d) in FNV_LIVE_TOOLS.items()})
    table.update({n: {'kind': k, 'description': d} for n, (k, d) in FNV_SPEC_TOOLS.items()})
    for name, row in table.items():
        row['toolset'] = toolset_of(name)
        row['active'] = registered(name, active)
        if not row['active']:
            row['description'] = '%s. %s' % (row['description'], hidden_note(row['toolset']))
    return table


ACTION_PHRASE = re.compile(r'^([a-z_]+): (.+)$')


def tool_documents(table, use_when, schemas=None):
    """{tool: {name, desc, phrases, actions: {action: {name, phrases}}}}, every field a set of words (phrases a list
    of sets). An action comes from the tool's schema (its `action` enum), ACTION_PARAMS and the use_when phrases."""
    from .action_params import ACTION_PARAMS
    docs = {}
    for name, row in table.items():
        tool_phrases, action_phrases = [], {}
        for phrase in use_when.get(name, ()):
            m = ACTION_PHRASE.match(phrase)
            if m:
                action_phrases.setdefault(m.group(1), []).append(set(words(m.group(2))))
            else:
                tool_phrases.append(set(words(phrase)))
        enum = (((schemas or {}).get(name) or {}).get('properties') or {}).get('action', {}).get('enum') or []
        actions = list(enum) + [a for a in ACTION_PARAMS.get(name, {}).get('actions', {}) if a not in enum]
        actions += [a for a in action_phrases if a not in actions]
        docs[name] = {'name': set(words(name.replace('_', ' '))), 'desc': set(words(row['description'])),
                      'phrases': tool_phrases,
                      'actions': {a: {'name': set(words(a.replace('_', ' '))), 'phrases': action_phrases.get(a, [])}
                                  for a in actions}}
    return docs


def _all_words(doc):
    out = set(doc['name']) | doc['desc']
    for p in doc['phrases']:
        out |= p
    for a in doc['actions'].values():
        out |= a['name']
        for p in a['phrases']:
            out |= p
    return out


def score_tool(query, doc, idf, action=None):
    """Score of one tool (and one of its actions, when given) for the query words: per word its weight (idf) times
    the best field it is in (name 3, a task phrase 3, the action's name 2.5, the description 1), plus the words that
    one phrase shares with the query again (a phrase that says the whole task beats words found here and there)."""
    phrases = list(doc['phrases'])
    action_name = set()
    if action is not None:
        phrases += doc['actions'][action]['phrases']
        action_name = doc['actions'][action]['name']
    phrase_words = set().union(*phrases) if phrases else set()
    total = 0.0
    for q in query:
        w = idf.get(q, 0)
        field = max(3 * (q in doc['name']), 3 * (q in phrase_words), 2.5 * (q in action_name), 1 * (q in doc['desc']))
        total += w * field
    if phrases:
        total += 2 * max(sum(idf.get(q, 0) for q in query if q in p) for p in phrases)
    return total


def example_call(name, action=None, schema=None):
    """One line: fnv_place(action="snap", ref=...). Required parameters other than action get `...`."""
    args = ['action="%s"' % action] if action else []
    args += ['%s=...' % p for p in ((schema or {}).get('required') or []) if p != 'action']
    return '%s(%s)' % (name, ', '.join(args))


def score_guide(query, guide):
    title = set(stems(guide['title'] + ' ' + guide['name'].replace('-', ' ')))
    words = set(stems(guide['words']))
    summary = set(stems(guide['summary']))
    body = stems(guide['text'])
    total = 0
    for q in set(query):
        total += 4 * (q in title) + 4 * (q in words) + 2 * (q in summary) + min(body.count(q), 5) * 0.2
    return total


class GuideTools:
    def __init__(self, research_dir=None, guides_dir=GUIDES_DIR, toolsets=None, schemas=None):
        self.research_dir = research_dir or os.environ.get('FNV_RESEARCH_DIR')
        self.guides_dir = guides_dir
        self.toolsets = toolsets          # the server's active toolsets (None: the default ones)
        self.schemas = schemas            # {tool: input schema}: actions and required parameters for examples

    # ------------------------------------------------------------ list, topic, find
    def list(self):
        guides = load_guides(self.guides_dir)
        rows = [{'name': g['name'], 'title': g['title'], 'summary': g['summary']} for g in guides.values()]
        return verdict('%d guides' % len(rows), 'Call fnv_guide topic with a name or words that describe the problem.',
                       rows=rows)

    def topic(self, words):
        if not words or not words.strip():
            raise ValueError('topic needs words: a guide name or a description of the problem')
        guides = load_guides(self.guides_dir)
        key = words.strip().lower().replace(' ', '-')
        ranked = []
        if key in guides:
            ranked = [(100, guides[key])]
        else:
            query = stems(words)
            ranked = sorted(((score_guide(query, g), g) for g in guides.values()), key=lambda x: -x[0])
            ranked = [x for x in ranked if x[0] >= 1]
        if not ranked:
            return verdict('No guide matches %r' % words, 'Call fnv_guide find to search the tools, or list for all guides.',
                           guides=[g['name'] for g in guides.values()])
        best = ranked[0][1]
        return verdict('Guide: %s (%s)' % (best['title'], best['name']),
                       'Best match for %r.' % words if key not in guides else 'Exact name.',
                       otherCandidates=[{'name': g['name'], 'title': g['title'], 'score': round(s, 1)}
                                        for s, g in ranked[1:4]], guide=best['text'])

    def find(self, text, limit=20, offset=0, brief=False):
        """Tools, their actions and the guides for a task said in plain words. Words match whole (after a small
        stemming rule), weighted by how rare they are among the tools; each tool's use_when task phrases count like
        its name. A tool row gives the best action and a one-line example call; active=false when the tool is in a
        toolset this server does not register."""
        if not text or not text.strip():
            raise ValueError('find needs words, for example "script does not run" or "crash log"')
        from .fnv_live_tools import USE_WHEN
        query = list(dict.fromkeys(words(text)))
        table = tool_table(self.toolsets)
        docs = tool_documents(table, USE_WHEN, self.schemas)
        df = {}
        for doc in docs.values():
            for w in _all_words(doc):
                df[w] = df.get(w, 0) + 1
        idf = {q: math.log(1 + len(docs) / df[q]) for q in query if q in df}
        rows = []
        for name, doc in docs.items():
            best, best_action = score_tool(query, doc, idf), None
            for action in doc['actions']:
                score = score_tool(query, doc, idf, action)
                if score > best + 1e-9:
                    best, best_action = score, action
            if best <= 0:
                continue
            t = table[name]
            rows.append({'kind': 'tool', 'name': name, 'tool': name, 'action': best_action,
                         'example': example_call(name, best_action, (self.schemas or {}).get(name)),
                         'toolKind': t['kind'], 'toolset': t['toolset'], 'active': t['active'],
                         'score': round(best, 2), 'description': t['description']})
        if query:
            for g in load_guides(self.guides_dir).values():
                score = score_guide(query, g)
                if score >= 1:
                    rows.append({'kind': 'guide', 'name': g['name'], 'score': round(score, 1),
                                 'description': g['summary']})
        rows.sort(key=lambda r: (-r['score'], r['name']))
        head = ('%d matches for %r, best: %s' % (len(rows), text, rows[0]['name'])) if rows else 'No match for %r' % text
        return verdict(head, None if rows else 'Try other words, or call fnv_guide list.',
                       **page(rows, limit, offset, brief=brief, brief_fields=['kind', 'name', 'action', 'example']))

    # ------------------------------------------------------------ engine research and recorded facts
    def sections(self, folder):
        """Every heading section of every top-level Markdown file: (file, line, heading path, lines)."""
        out = []
        for path in sorted(Path(folder).glob('*.md')):
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            stack, start, begin = [], None, 0
            for i, line in enumerate(lines + ['# end']):
                match = re.match(r'^(#{1,6})\s+(.*\S)', line)
                if not match:
                    continue
                if start is not None:
                    out.append((path.name, start + 1, ' > '.join(h for _, h in stack), lines[begin:i]))
                level = len(match.group(1))
                stack = [s for s in stack if s[0] < level] + [(level, match.group(2).strip())]
                start, begin = i, i + 1
        return out

    @staticmethod
    def excerpt(lines, query):
        best, best_hits = '', 0
        for line in lines:
            low = line.lower()
            hits = sum(1 for q in set(query) if q in low)
            if hits > best_hits:
                best, best_hits = line.strip(), hits
        if not best:
            best = next((l.strip() for l in lines if l.strip()), '')
        return best if len(best) <= 240 else best[:237] + '...'

    def engine(self, words, limit=20, offset=0, brief=False):
        if not words or not words.strip():
            raise ValueError('engine needs words, for example "barter price" or "fast travel"')
        query = stems(words)
        if not query:
            raise ValueError('engine needs words that say something: "%s" has only filler words. Next: name the thing, '
                             'for example "barter price" or "fast travel"' % words.strip()[:60])
        facts = []
        from .fnv_facts import FACTS
        for name, fact in FACTS.items():
            haystack = ' '.join([name.replace('.', ' '), str(fact.get('evidence', ''))]).lower()
            hits = sum(1 for q in set(query) if q in haystack)
            if hits:
                shown = {k: (hex(v) if isinstance(v, int) and k in ('address', 'offset') else v) for k, v in fact.items()}
                facts.append({'name': name, 'hits': hits, **shown})
        facts.sort(key=lambda f: -f['hits'])
        folder = self.research_dir
        notes = []
        # With no folder of the user's own notes, the guides that ship with the server are searched (release audit L8)
        searched = folder if folder and Path(folder).is_dir() else (self.guides_dir if not folder else None)
        if searched and Path(searched).is_dir():
            n = len(set(query))
            need = n if n <= 2 else max(2, (n + 1) // 2)
            source = 'research' if searched == folder else 'guide'
            for file, line, heading, body in self.sections(searched):
                text = (heading + '\n' + '\n'.join(body)).lower()
                head = heading.lower()
                present = [q for q in set(query) if q in text]
                if len(present) >= need:
                    score = len(present) * 10 + 5 * sum(1 for q in present if q in head) + min(sum(text.count(q) for q in present), 20) / 10
                    notes.append({'file': file, 'line': line, 'heading': heading, 'score': round(score, 1),
                                  'source': source, 'excerpt': self.excerpt(body, query)})
            notes.sort(key=lambda n: -n['score'])
        reason, nxt = None, None
        if not folder:
            reason = ('No research folder is set: the shipped guides and the recorded facts were searched. The decomp '
                      'itself is searched with fnv_func.')
            nxt = ('For how the engine does it, call fnv_func with the same words (mode="code" searches the '
                   'decomp source). To search notes of your own too, set fnv.research_dir in the project file, or '
                   'the environment variable FNV_RESEARCH_DIR.')
        elif not Path(folder).is_dir():
            reason, nxt = 'The research folder %s does not exist.' % folder, 'Fix fnv.research_dir or FNV_RESEARCH_DIR.'
        head = '%d %s sections and %d recorded facts match %r' % (
            len(notes), 'research' if folder else 'guide', len(facts), words)
        if not notes and not facts and not nxt:
            nxt = 'Try other words, or read the decomp with fnv_func.'
        out = verdict(head, reason, **page(notes, limit, offset, brief=brief, brief_fields=['file', 'line', 'heading']))
        out['facts'] = facts[:10]
        out['researchDir'] = str(folder) if folder else None
        if nxt:
            out['next'] = nxt
        return out

    def run(self, action, words=None, limit=20, offset=0, brief=False):
        return dispatch(action, {
            'list': self.list, 'topic': lambda: self.topic(words),
            'find': lambda: self.find(words, limit, offset, brief),
            'engine': lambda: self.engine(words, limit, offset, brief)})


def guide_names(folder=GUIDES_DIR):
    return [p.stem for p in sorted(Path(folder).glob('*.md'))]


def guide_text(name, folder=GUIDES_DIR):
    path = Path(folder) / (name + '.md')
    if not re.match(r'^[a-z0-9-]+$', name) or not path.exists():
        raise ConvError('NOT_FOUND', 'No guide %r. Guides: %s.' % (name, ', '.join(guide_names(folder))))
    return path.read_text(encoding='utf-8')
