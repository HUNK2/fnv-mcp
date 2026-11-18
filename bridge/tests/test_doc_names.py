"""The docs name only tools that exist, link only files that exist, and keep build history in docs/history/.

Every `fnv_*` or `geck_*` name in the docs must be a current tool, an old name in the rename table
(geck_mcp/tool_examples.RENAMED_TOOLS), a Python module of this repository, or one of NOT_TOOLS. Files under
bridge/docs/history/ are the record of how the tools were built and are not checked: they keep the names and dates
of their time. CHANGELOG.md is a record too and is not checked."""
import re
import sys
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
REPO = BRIDGE.parent
sys.path.insert(0, str(BRIDGE))

from geck_mcp.fnv_guide import TOOL_NAME, tool_table  # noqa: E402
from geck_mcp.tool_examples import RENAMED_TOOLS  # noqa: E402

HISTORY = BRIDGE / 'docs' / 'history'
# fnv_* and geck_* words in the docs that are not tools.
NOT_TOOLS = {
    'fnv_decomp',            # the decompilation project
    'geck_mcp',              # the Python package
    'fnv_engine_addresses',  # a header file the engine map writes
    'fnv_door',              # the made-up example tool in FNV-TOOL-CONVENTIONS.md
    'fnv_orig_',             # the prefix fnv_code gives the original function in edited code
}
# Words that belong to the build history (plan and wave names, bug tags), not to a page for a new reader.
HISTORY_TERMS = re.compile(r'\bPlan [0-9]\b|\bPLAN-[0-9]|\b(?:W|S|G|T|X|N|Y|M|L|H)[0-9]{1,2}\b|[Mm]ilestone|'
                           r'\bround-4\b')
LINK = re.compile(r'\]\(([^)#\s]+)(?:#[^)]*)?\)')


def doc_files():
    """The Markdown a reader meets: bridge/docs (without history), the READMEs, CLAUDE.md, AGENTS.md,
    CONTRIBUTING.md."""
    files = [p for p in sorted((BRIDGE / 'docs').rglob('*.md')) if HISTORY not in p.parents]
    for path in (BRIDGE / 'README.md', REPO / 'README.md', REPO / 'CLAUDE.md', REPO / 'AGENTS.md',
                 REPO / 'CONTRIBUTING.md'):
        if path.exists():
            files.append(path)
    return files


def module_names():
    names = set()
    for folder in (BRIDGE / 'geck_mcp', BRIDGE / 'tools', BRIDGE / 'engine_map', BRIDGE, REPO / 'tools'):
        names.update(p.stem for p in folder.glob('*.py'))
    return names


class DocNameTests(unittest.TestCase):
    def test_every_tool_name_in_the_docs_is_known(self):
        tools = set(tool_table())
        known = tools | set(RENAMED_TOOLS) | module_names() | NOT_TOOLS
        unknown = {}
        for path in doc_files():
            names = set(TOOL_NAME.findall(path.read_text(encoding='utf-8'))) - known
            # a name ending in _ is a prefix (`fnv_name_*`): known when a current tool starts with it
            names = {n for n in names if not (n.endswith('_') and any(t.startswith(n) for t in tools))}
            if names:
                unknown[str(path.relative_to(REPO))] = sorted(names)
        self.assertEqual(unknown, {}, 'names that are not tools, old names or modules')

    def test_the_check_catches_a_wrong_name(self):
        known = set(tool_table()) | set(RENAMED_TOOLS)
        self.assertIn('fnv_not_a_tool', set(TOOL_NAME.findall('call `fnv_not_a_tool`')) - known)
        self.assertNotIn('fnv_place', set(TOOL_NAME.findall('call `fnv_place`')) - known)

    def test_relative_links_resolve(self):
        broken = []
        for path in doc_files():
            for target in LINK.findall(path.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('mailto:'):
                    continue
                if not (path.parent / target).exists():
                    broken.append('%s -> %s' % (path.relative_to(REPO), target))
        self.assertEqual(broken, [])

    def test_no_build_history_outside_history(self):
        found = []
        for path in doc_files():
            if path.name == 'CLAUDE.md':
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                match = HISTORY_TERMS.search(line)
                if match:
                    found.append('%s:%d %s' % (path.relative_to(REPO), number, match.group(0)))
        self.assertEqual(found, [], 'move dated material to docs/history/ or say it in plain words')

    def test_history_has_an_index(self):
        self.assertTrue((HISTORY / 'README.md').exists())
        index = (HISTORY / 'README.md').read_text(encoding='utf-8')
        for path in HISTORY.glob('*.md'):
            if path.name != 'README.md':
                self.assertIn(path.name, index, 'docs/history/README.md does not list %s' % path.name)

    def test_docs_index_names_every_page(self):
        index = (BRIDGE / 'docs' / 'README.md').read_text(encoding='utf-8')
        for path in (BRIDGE / 'docs').glob('*.md'):
            if path.name != 'README.md':
                self.assertIn(path.name, index, 'docs/README.md does not link %s' % path.name)


if __name__ == '__main__':
    unittest.main()
