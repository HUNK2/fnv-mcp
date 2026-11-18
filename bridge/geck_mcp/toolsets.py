"""Toolsets chosen at server start (TOOL-MERGE step 1): which families of tools the server registers.

game: the fnv_* tools for the running game, the offline engine knowledge and fnv_spec. naming: the function naming
tools (fnv_name_*, fnv_probe, fnv_atlas). core (opt-in, tool quality part 5): the everyday modding tools of the
game toolset only, for a client that loads the whole tool list up front. The project key `toolsets` (or
`--toolsets`) picks them; the default is game and naming. A client keeps the tool list it saw at connect, so a
toolset is fixed for a connection. (The editor and legacy toolsets, the geck_* tools, were removed on 2026-11-18.)"""

TOOLSETS = ('game', 'naming', 'core')
DEFAULT_TOOLSETS = ('game', 'naming')
# The core toolset: state and setup, the world and what is in it, records and forms, building and installing a
# plugin, testing, logs and errors, undo. fnv_guide find still names every other tool and how to turn it on.
CORE_TOOLS = frozenset({
    'fnv_status', 'fnv_setup_check', 'fnv_game', 'fnv_companion_install', 'fnv_spec', 'fnv_install',
    'fnv_player', 'fnv_ref', 'fnv_refs_near', 'fnv_console', 'fnv_place', 'fnv_world_edit',
    'fnv_photo', 'fnv_record', 'fnv_form', 'fnv_script_errors',
    'fnv_crash', 'fnv_warnings', 'fnv_logs', 'fnv_test', 'fnv_undo', 'fnv_journal', 'fnv_guide'})
# Registered whatever the toolsets: fnv_guide find names a hidden tool and the toolset that turns it on.
ALWAYS = frozenset({'fnv_guide'})
NAMING_TOOLS = frozenset({'fnv_probe', 'fnv_atlas'})


def toolset_of(name):
    """The toolset a tool belongs to."""
    if name.startswith('fnv_name_') or name in NAMING_TOOLS:
        return 'naming'
    return 'game'


def registered(name, active):
    """Whether a server with these active toolsets registers the tool."""
    return name in ALWAYS or toolset_of(name) in active or ('core' in active and name in CORE_TOOLS)


def hidden_note(toolset):
    """What a hidden tool's description says in fnv_guide find."""
    return ('In the %s toolset, off in this server: add "%s" to "toolsets" in the project file (or start the server '
            'with --toolsets) to use it.' % (toolset, toolset))
