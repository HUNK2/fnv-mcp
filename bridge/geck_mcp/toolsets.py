"""Toolsets chosen at server start (TOOL-MERGE step 1): which families of tools the server registers.

game: the fnv_* tools for the running game, the offline engine knowledge and fnv_spec. naming: the function naming
tools (fnv_name_*, fnv_probe, fnv_atlas). The project key `toolsets` (or `--toolsets`) picks them; the default is
both. A client keeps the tool list it saw at connect, so a toolset is fixed for a connection. (The editor and legacy
toolsets, the geck_* tools, were removed on 2026-11-18.)"""

TOOLSETS = ('game', 'naming')
DEFAULT_TOOLSETS = ('game', 'naming')
# Registered whatever the toolsets: fnv_guide find names a hidden tool and the toolset that turns it on.
ALWAYS = frozenset({'fnv_guide'})
NAMING_TOOLS = frozenset({'fnv_probe', 'fnv_atlas'})


def toolset_of(name):
    """The toolset a tool belongs to."""
    if name.startswith('fnv_name_') or name in NAMING_TOOLS:
        return 'naming'
    return 'game'


def hidden_note(toolset):
    """What a hidden tool's description says in fnv_guide find."""
    return ('In the %s toolset, off in this server: add "%s" to "toolsets" in the project file (or start the server '
            'with --toolsets) to use it.' % (toolset, toolset))
