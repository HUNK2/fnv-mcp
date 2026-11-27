"""Descriptions for tool parameters that the signatures leave undescribed.

The server fills them into each tool's input schema when it lists the tools (BridgeServer.list_tools), so a client
sees what every argument does without reading the tool's prose. A parameter that already has a description in its
signature (Field(description=...)) keeps it and needs no entry here. tests/test_param_docs.py checks that every
parameter of every tool is described, and that every entry names a real tool and parameter.

PARAM_FIXES corrects a signature's description that is wrong: {tool: {parameter: (old text, new text)}}. The new
text replaces the old one only while the signature still has exactly the old text."""

PARAM_DOCS: dict[str, dict[str, str]] = {'fnv_actor_control': {'action': 'What the actor does: evaluate_package, start_combat, stop_combat, add_package, '
                                 'remove_package, play_idle, move_to, kill, resurrect, enable, disable, equip, '
                                 'unequip, add_item, remove_item, cast, look_at, say_to, set_av, force_av or '
                                 'set_position.',
                       'target': 'What the action works on: the combat target, package, idle, move_to destination, '
                                 'item, spell or the actor value name (set_av, force_av).'},
 'fnv_actor_values': {'names': 'Actor value names to read, such as Health, ActionPoints or Strength. Omitted reads all '
                               '77. An unknown name is refused.'},
 'fnv_anim': {'action': 'What to do: state (current/queued groups), play (start an animation), groups (list groups '
                        'with KF files), idle_tree (why an idle would or would not be picked), biped (equipment '
                        'slots), face (expression values), events (record animation actions), reload (rebuild 3D).',
              'group': 'For action=play: name of the animation group to play on the reference.',
              'idle': 'For action=play: the IDLE form to play on the reference.',
              'kf': 'For action=play: a .kf file under meshes (any file).',
              'rebuild': "For action=biped: re-run the engine's equipment build (changes the game).",
              'seconds': 'For action=events: how many seconds to record animation actions (equip, attack, reload...). '
                         'Default 3.'},
 'fnv_asset': {'action': 'What to do: reload (re-read a file in the game), watch, file_log, queue, where, models, '
                         'archives, nif/dds (offline checks), users, make_* (make a file), extract, preview.',
               'path': 'The asset file; for watch the mod folder; for make_* the source (.glb, keys .json, image).',
               'out': 'For make_*: the Data path to write (meshes/mymod/x.nif) in the mod folder.',
               'options': 'For make_*: mod, scale, up, texture, normal, specular, format, skeleton... (guide "assets").',
               'mode': 'For action=file_log: start, read (default) or stop the file-open log.',
               'missing': 'For action=file_log: if true, list only files the engine failed to find.',
               'ext': 'For action=file_log: only show files with this extension.',
               'folder': 'For action=file_log: only show files under this folder.',
               'limit': 'Maximum number of rows returned. Default 200.',
               'wait_idle': 'For action=queue: wait until the asset IO queue is idle before returning.',
               'menu': 'For reload of a menu .xml: the menu name (HUDMainMenu...) to rebuild.'},
 'fnv_asset_check': {'include_nif_textures': 'If true (default), also open the loose meshes the plugin names and '
                                             'report textures named inside them that are missing.',
                     'limit': 'Maximum number of missing files listed, 1 to 1000. Default 200.'},
 'fnv_atlas': {'action': 'What to do: actions (list scripted actions), run (measure actions), runs (stored runs), '
                         'function (fits for one function), action (functions an action runs), import_frida (load a '
                         'Frida trace file).',
               'actions': 'For action=run: names of the scripted actions to measure. Omitted means the default set '
                          'from the action library.',
               'reps': 'For action=run: how many times each measurement is repeated. Default 2.',
               'doses': 'For action=run: how many times the action is done in each segment, for example [1, 2, 4]. '
                        "Omitted uses each action's own default.",
               'name': 'For action=action: the name of the game action whose functions to list.',
               'function': 'For action=function: the function (name or address) whose recorded fits and callers to '
                           'show.',
               'kind': 'For action=action: keep only fits of this kind, per_unit (calls per action) or with_action '
                       '(only while acting).',
               'run': 'For action=action: the run number to read. Omitted means the latest run that measured the '
                      'action.',
               'statuses': 'For action=action: keep only functions with these naming statuses, for example '
                           "['unnamed'].",
               'exclusive': 'For action=action: if true, keep only functions that no other action runs.',
               'include_console_machinery': 'For action=action: if true, also include functions that only the '
                                            'console-command driver runs; normally left out.',
               'path': 'For action=import_frida: path of the fnvsym Frida trace file to load as its own run.',
               'notes': 'For action=run: free-text note stored with the run.',
               'limit': 'For action=action: maximum functions returned. Default 60.'},
 'fnv_audio': {'action': 'What to do: log (sounds created meanwhile), play, playing (sounds held now), stop, music, '
                         'radio, subtitles, hear (would a sound play at a position), voice_check (voice/lip files of '
                         "an NPC's lines).",
               'seconds': 'For action=log and subtitles: how many seconds to watch. Default 3.',
               'sound': 'Sound form (SOUN) name or id. Used by play, stop and hear.',
               'sound_id': 'For action=stop: id of the playing sound instance to stop, as shown by playing.',
               'file': 'For action=music: path of a music track to force.',
               'stop': 'For action=music: if true, fade the music out.',
               'fade_ms': 'For action=music with stop: fade-out time in milliseconds. Default 1000.',
               'enable': 'For action=radio: turn the radio on or off. For action=subtitles: turn subtitles on. Omitted '
                         'leaves the setting alone.',
               'position': 'For action=hear: world position [x, y, z] in game units where the sound would be played.'},
 'fnv_author': {'action': "export (this session's forms and placements as a spec part), check (missing files, "
                          'conflicts, warnings of a plugin), scaffold (a starting project) or package (release folder '
                          'and zip).',
                'plugin': 'For check and package: the plugin name in the load order, or its file path.',
                'module': 'For package: the fnv_plugin_build module to ship in Data/NVSE/Plugins.',
                'build': 'For package: build the spec (if it makes the plugin) and the module first.',
                'version': 'For package: the version (default 1.0).',
                'kind': 'For scaffold: the kind of starting project.',
                'name': 'For scaffold: the project name, letters, digits and _ starting with a letter; it becomes the '
                        'plugin name and the EditorID prefix.',
                'cell': 'For export: the cell EditorID the placed objects are filed under in the spec part (your '
                        "plugin's own interior cell). Default LiveSessionCell."},
 'fnv_bisect': {'level': 'What to turn off to find the culprit: mods (MO2 mods), plugins, or records of one plugin '
                         '(needs plugin).',
                'steps': 'The failing scenario to run each round, in the same format as fnv_test steps.',
                'keep': 'Mods, plugins or record form ids that are never turned off. The companion, xNVSE data, JIP '
                        'and base game files are always kept.',
                'max_rounds': 'Maximum number of halving rounds before stopping. Default 10.',
                'plugin': 'For level=records: the plugin whose records are the suspects. Each round writes a variant '
                          'of it without half of them.'},
 'fnv_blame': {'action': 'What to do: field (watch writes to an address), ref (hook engine functions that touch a '
                         'reference), report (group hits so far by culprit), form (change flags set on a form '
                         'meanwhile).',
               'address': 'For action=field: the address to watch, as 0x... or a reference plus 0x offset.',
               'ref': 'For action=ref and form: the reference to watch.',
               'what': 'For action=ref: comma-separated hook presets to arm, or all. Omitted uses all presets.',
               'length': 'For action=field: number of bytes watched at the address, 1 to 64. Default 4.',
               'access': 'For action=field: break on writes only, or on reads and writes.',
               'scope': 'For action=field: watch the main thread only, or all threads.',
               'seconds': 'How long to watch. For field and form: default 5. For ref: seconds to wait and collect hits '
                          'before answering; omitted returns at once with the hooks armed (run report later).'},
 'fnv_camera': {'action': 'What to do: get (read camera), set (free camera), orbit (around a reference), pov '
                          "(first/third person), restore (back to the player's view).",
                'yaw': 'Camera heading in degrees (0 = north, clockwise). For action=orbit it is relative to the '
                       "reference's heading: 180 in front, 0 behind.",
                'pitch': 'Look-down angle in degrees; positive looks down.',
                'distance': "For action=orbit: distance from the reference's centre in game units. Omit it and use "
                            'fit=true to derive it.',
                'fit': "For action=orbit: if true, choose the distance from the reference's size and the FOV.",
                'fov': 'Field of view in degrees; changes the world FOV when given.',
                'view': 'For action=pov: switch to first or third person.',
                'freeze': 'For action=set: if true (default) the world is stopped while the camera is free.'},
 'fnv_class': {'class_name': 'The engine class name, such as Actor, TESObjectREFR or BaseProcess.',
               'action': 'layout (default) gives the fields; tree the base and derived classes; vtable the virtual '
                         'table slots.',
               'include_virtuals': 'For action=layout: also list the virtual functions the headers declare.',
               'compiler': "For action=layout: add the compiler's full member list from the symbol database.",
               'limit': 'For tree: maximum classes (default 200); for vtable: maximum slots (default 80).',
               'offset': 'For action=vtable: the first slot returned, for paging.'},
 'fnv_code': {'action': "Step: checkout (copy a function's source), edit (find/replace in the copy), build (compile "
                        'and link), compare (run original and new side by side), swap/unswap (activate or undo), list '
                        '(active swaps), export (patch set and loader).',
              'function': 'Engine function to work on, by name or address. Used by checkout, edit, build, compare, '
                          'swap, unswap.',
              'find': 'For action=edit: text to find inside the function copy.',
              'replace': 'For action=edit: text that replaces the found text.',
              'args': 'For action=compare: list of argument objects to call the original and the new code with.',
              'this': 'For action=compare: the this-pointer or reference for a method call.',
              'returns': 'For action=compare: type of the return value (void, int, float, ptr, form...), used to '
                         'compare the results.',
              'mode': "For action=compare: call (default) runs both with given args; shadow records the game's own "
                      'calls of the original and replays them on the new code.',
              'seconds': "For action=compare with mode=shadow: how long to record the game's calls. Default 5.",
              'nargs': 'For action=compare with mode=shadow: number of arguments the function takes, 0 to 8.'},
 'fnv_command_info': {'name': 'The console or script command, by full or short name, such as GetActorValue or GetAV.'},
 'fnv_companion_install': {'target': 'Where to install: mo2 (the dev profile, as its own MO2 mod), game (the game '
                                     "folder's Data\\NVSE\\Plugins) or auto (default: mo2 when the project names an "
                                     'MO2 instance, else it asks for game).',
                           'action': 'install (default) builds the companion with Zig and installs it; uninstall turns '
                                     'the MO2 mod off or removes the file this tool put into the game folder.'},
 'fnv_compile_ingame': {'source': 'The script source text to compile (a whole script, starting with scn). Nothing '
                                  'runs.',
                        'include_bytecode': 'If true (default), the answer includes the compiled bytecode as hex.'},
 'fnv_condition_eval': {'tab_subjects': 'For perk entries: the reference for each condition tab after tab 0, in order '
                                        '(tab 0 is the perk owner, subject). For example ["player", "MyNPC"].',
                        'only_failed': 'If true, list only the conditions that fail. Default false lists them all.'},
 'fnv_conflicts': {'plugin': 'Only records this plugin touches. Omitted lists conflicts across all active plugins.',
                   'record_type': 'Only records of this four-letter type, such as WEAP, NPC_ or QUST.',
                   'include_identical': 'If true, also list overrides that are identical to the record they override. '
                                        'Default false.',
                   'limit': 'Maximum number of records returned, 1 to 200. Default 50.',
                   'offset': 'Number of records to skip, for paging. Default 0.'},
 'fnv_console': {'line': 'One console line, as typed in the game console, such as "player.additem f 100" or "tgm". Up '
                         'to 500 characters.'},
 'fnv_coverage': {'action': 'What to do: start (arm line traps), stop (report lines run and never run, save the run), '
                            'diff (compare two saved runs).',
                  'files': 'For action=start: decomp source files to trace, for example tesobjectrefr.cpp.',
                  'functions': 'For action=start: names of engine functions to trace, as an alternative to files.',
                  'name': 'For action=start: name for the saved run (plain name, no paths). Omitted gives a coverage- '
                          'timestamp name.',
                  'a': 'For action=diff: the first saved run to compare.',
                  'b': 'For action=diff: the second saved run; the result lists lines that ran in one but not the '
                       'other.'},
 'fnv_crash': {'action': 'explain (default) reads the newest crash log with names; latest parses it raw; history '
                         'groups all logs; context reads companion context files; capture_on, capture_off, '
                         'capture_status.',
               'path': 'For explain and latest: a crash log file to read instead of the newest one.',
               'max_frames': 'For explain and latest: maximum stack frames decoded, 5 to 400. Default 40.',
               'limit': 'For history: crash signatures returned (default 40, up to 200). For context: context files '
                        'returned, 1 to 20 (default 3).',
               'include_shutdown': "For history: if true (default), also list crashes during the game's exit cleanup "
                                   '(marked; usually not your mod).'},
 'fnv_crawl': {'action': 'What to check: cells (visit every cell the plugin touches), dialogue (lines of one NPC), '
                         'quest (set quest stages in order), items (give, equip and use each new item).',
               'plugin': 'For action=cells and items: the plugin whose cells or new items are checked.',
               'max_cells': 'For action=cells: maximum number of cells to visit. Default 20.',
               'settle_frames': 'For action=cells: frames to wait in each cell for it to settle before checking. '
                                'Default 90.',
               'photos': 'For action=cells: take a photo in each cell. Default true.',
               'limit': 'Maximum rows returned. Default 60.',
               'path': 'For action=quest: the quest stages to set in order.'},
 'fnv_damage_log': {'action': 'start clears the log and installs the hook, read (default) returns recent hits (it '
                              'starts on first use), stop removes the hook.',
                    'limit': 'For read: maximum number of hits returned, newest last, 1 to 1000. Default 50.'},
 'fnv_debug': {'action': 'What to do: break sets/lists/clears stops; status, stack, locals read the stop; eval and set '
                         'work on the stopped frame; continue, step, until, return, skip resume; freeze/thaw/threads '
                         'act on threads; dump writes a minidump.',
               'op': 'For action=break: set creates a breakpoint (needs location), list shows them, clear removes one '
                     '(break_id). Omitted with no location behaves as list.',
               'location': "For break, until and skip: a function name, 0xADDRESS, file:line, or 'fault' (break only) "
                           'naming where to stop or resume.',
               'break_id': 'For action=break op=clear: the id of the breakpoint to remove.',
               'when': "For break op=set: a predicate such as 'frame >= 100'; the breakpoint only counts or stops when "
                       'it holds.',
               'hits': 'For break op=set: stop from the Nth hit that passes the when predicate (1 stops on the first).',
               'thread': 'For break op=set: main stops only on the main thread, any stops on whichever thread hits it.',
               'do': 'For break op=set: stop (halt the game at the breakpoint) or log (record the log expressions and '
                     'keep running).',
               'log': 'For break op=set with do=log: expressions whose values are recorded at each hit instead of '
                      'stopping.',
               'depth': 'For action=stack: how many stack frames to return.',
               'with_locals': 'For action=stack: also include the local variables of each frame. Default false.',
               'frame': 'For locals, eval and set: the stack frame number to work in, 0 being the innermost frame.',
               'expression': 'For eval: the expression to evaluate in the stopped frame. For set: the target to '
                             'change, such as eax, a local or this->m_fHealth.',
               'value': 'For set: the number to store in the expression target. For return: the value the function '
                        'returns.',
               'mode': 'For action=step: instruction, into, over, out or line, choosing how far to advance before '
                       'stopping again.',
               'file': 'For action=dump: file name for the minidump, written in the bridge state folder.'},
 'fnv_detection': {'recompute': "If true and the observer is an NPC, run the game's detection calculation now and "
                                'capture its 25 inputs (a hook for the call: needs fnv.engine_control full). False '
                                '(default) only reads the stored detection records.'},
 'fnv_dialogue': {'action': 'now (default) reads the conversation on screen; tree evaluates every response a speaker '
                            'could give for a topic.',
                  'scope': 'For action=tree: speaker (default) keeps responses written for this speaker or available '
                           'now; available keeps only those; all lists every response.',
                  'include_text': 'For action=tree: include the response text.',
                  'limit': 'For action=tree: maximum responses returned. Default 60.'},
 'fnv_dialogue_choose': {'choices': 'Responses to pick in order: an option index (0 = first shown), a substring of the '
                                    'prompt, or an INFO form id. Omitted returns the options shown.',
                         'close': 'If true, end the conversation after the last choice. Default false leaves it open.',
                         'timeout_seconds': 'Seconds to wait for the speaker to finish each line and for options to '
                                            'appear, 3 to 180. Default 30.'},
 'fnv_engine_call': {'function': 'The engine function to call: a decomp name or an address.',
                     'this': 'The object for a thiscall: a reference spec or a 0xADDRESS. Leave empty for functions '
                             'without a this pointer.',
                     'args': 'Arguments in order, each an object {type, value} with type one of int, bool, ptr, ref, '
                             'form, float, double or string.',
                     'returns': "How to read the function's return value: void, int, uint, bool, ptr, float, double or "
                                'form. Default int.',
                     'convention': 'Calling convention override: cdecl, stdcall, thiscall or fastcall. When null it is '
                                   'taken from the decomp signature.',
                     'allow_unproven': 'When true, also allows functions not yet proven; the call then runs guarded '
                                       'and a fault returns CALL_FAULTED instead of crashing. Default false.'},
 'fnv_engine_get': {'args': 'Arguments in order. A plain value is typed from the decomp signature; or give {type, '
                            'value} with type int, bool, float, double, ptr, ref or form.'},
 'fnv_events': {'since': 'Return companion events (loads, saves, script errors) after this sequence number. Pass the '
                         'next value of the previous answer; 0 (default) returns all kept events.',
                'subscribe': 'xNVSE event names to start recording in the game, such as onactivate, onhit, ondeath, '
                             'onadd, onload.',
                'unsubscribe': 'Event names to stop recording, or ["all"].',
                'sink_since': 'Return recorded game events (from subscribe) after this sequence number. Omitted with '
                              'no subscribe returns none.',
                'ref': 'With sink_since: keep only events about this reference, as a form id such as 0x0001F00D.'},
 'fnv_field_watch': {'action': 'find snapshots an object before and after trigger lines and lists what changed; watch '
                               'sets a hardware watchpoint and reports each function that touched the address. Default '
                               'watch.',
                     'ref': 'The object to snapshot (find, defaults to the player) or the base for ref + offset in '
                            'watch. Trigger console lines also run on it.',
                     'offset': "For watch: byte offset added to the ref's address to get the watched address. Default "
                               '0.',
                     'address': 'For watch: the memory address to watch (instead of ref + offset), for example '
                                '0x01234567.',
                     'length': 'For watch: size in bytes of the watched region, 1, 2 or 4. Default 4.',
                     'access': 'For watch: write catches writes only, readwrite catches reads and writes. Default '
                               'write.',
                     'scope': 'For watch: main watches the main thread only, all watches every thread. Default main.',
                     'trigger': 'Console lines run on the ref while watching (watch) or between the two snapshots '
                                '(find).',
                     'trigger_ref': 'For watch: reference the trigger console lines run on, when different from ref.',
                     'seconds': 'For watch: seconds to keep watching after the trigger lines have run (or with no '
                                'trigger). Default 0, no extra wait.',
                     'size': 'For find: number of bytes of the object to snapshot. Default 4096.'},
 'fnv_flow': {'action': "trace arms a breakpoint on the function's entry and records every statement it executes; call "
                        'runs the function itself with the given args and traces it, returning its eax.',
              'function': 'The engine function to trace or call, by name or address.',
              'when': "For trace: optional predicate over the function's arguments and locals; only runs that satisfy "
                      'it are traced.',
              'depth': '0 traces only the function itself; 1 also traces direct callees that have source lines.',
              'max_steps': 'Cap on the number of statements recorded; leave null for the default limit.',
              'args': 'For action=call: the arguments, as numbers, floats, or form / reference names for pointers.',
              'this': 'For action=call: the object for a thiscall, as a reference name or spec.'},
 'fnv_form': {'action': "create makes an empty or cloned form; reload applies a rebuilt plugin's changed fields to "
                        "live forms; changes lists what this session made; bytes decodes a live form's record bytes; "
                        'delete removes a form made this session.',
              'type': 'For create: the four-letter record type of the new empty form, such as STAT, MISC or ARMO.',
              'name': 'For create: the name to give the new form.',
              'plugin': 'For reload: the plugin name or the path of the rebuilt plugin file to read changed records '
                        'from.',
              'records': 'For reload: optional list of records to restrict the reload to.',
              'since': 'For changes: only report changes after this point in time (a number); leave null for '
                       'everything this session.'},
 'fnv_form_edit': {'field': "Field path in the form's class layout, such as damage or data.value; nested fields are "
                            'joined with dots.',
                   'as_type': "Write the field as this type instead of the layout's: u8, i8, u16, i16, u32, i32, f32, "
                              'f64, ptr or bool.'},
 'fnv_formid_live': {'plugin': 'The plugin file name, such as MyMod.esp (5 to 100 characters).'},
 'fnv_func': {'action': "find (default) searches functions; code shows one function's decomp source; callers and "
                        'callees walk the call graph; strings finds functions whose code contains the query text.',
              'mode': 'For action=find: auto, name, code, regex or address search.',
              'status': 'For action=find: keep only these proof statuses (proven, class_proven, probable, decomp, '
                        'unnamed).',
              'limit': 'For find, callers, callees and strings: maximum number of functions returned.',
              'offset': 'For find and strings: how many results to skip, for paging. For code: the first source line '
                        'returned (0 is the start; a cut answer gives next).',
              'depth': 'For callers and callees: how many call levels to walk, 1 to 4.',
              'max_lines': 'For action=code: maximum number of source lines returned. Default 400.'},
 'fnv_game': {'action': 'launch (start the game and wait until ready), quit, or restart (quit if running, then '
                        'launch).',
              'force': 'For quit: if true (default), terminate the game when the console quit and closing '
                       'the window do not end it.',
              'timeout_seconds': 'Seconds to wait. launch and restart: 20 to 600, default 240. quit: 5 to 120, default '
                                 '20.',
              'via': 'For launch and restart: mo2 (the MO2 dev profile), direct (nvse_loader.exe in the game folder) '
                     'or auto (default: mo2 when the project names an MO2 instance).'},
 'fnv_global_find': {'query': 'Name or part of a name to find, such as fCombatDistance or BSFadeNode.',
                     'kind': 'Only this kind of object: Setting (game settings), RTTI (classes), hkClass or hkTypeInfo '
                             '(Havok). Omitted searches all.',
                     'limit': 'Maximum number of results, 1 to 200. Default 25.'},
 'fnv_globals': {'query': 'Keep only globals whose EditorID contains this text. Omitted lists all.',
                 'set': 'Values to write, by EditorID, such as {"GameHour": 12}. Shorts and longs take whole numbers. '
                        'Each write is journaled and undoable; needs full control.',
                 'limit': 'Maximum number of globals returned, 1 to 2000. Default 200.'},
 'fnv_guide': {'action': 'list (default) shows the guides; topic gives one guide; find searches tools and guides; '
                         'engine searches saved research notes and recorded engine facts.',
               'words': 'For topic: a guide name (crash, start) or words describing the problem. For find and engine: '
                        'the words to search for. Not used by list.',
               'limit': 'For find and engine: maximum results, 1 to 100. Default 20.',
               'offset': 'For find and engine: results to skip, for paging. Default 0.',
               'brief': 'For find and engine: if true, each row has only its kind, name, action and example call '
                        '(find) or file, line and heading (engine).'},
 'fnv_hook_set': {'action': 'skip bypasses the function and returns value; result runs it and replaces its result; arg '
                            'replaces one parameter; script runs a console line on each call; read shows hit info; '
                            'remove deletes the hook; list shows hooks. Default skip.',
                  'value': 'The value to return (skip), the replacement result (result) or the replacement parameter '
                           '(arg).',
                  'value_type': 'How to interpret value: int, float, bool, ptr, ref or form.',
                  'arg': 'For action=arg: index of the parameter to replace, starting at 0.',
                  'filter': 'Only apply the hook to matching calls: {on: this, caller or param0..7, ref or value}.',
                  'limit': 'Apply the hook to only the first N calls; 0 means no limit. Default 0.',
                  'script': 'A console or script line run on the main thread each time the function is called. Used by '
                            'action=script, and with other actions to also run a line.',
                  'pop_bytes': 'For action=skip: number of stack bytes to pop on return, overriding the amount derived '
                               'from the calling convention.',
                  'allow_unproven': 'When true, also allows functions that are not proven. Default false.'},
 'fnv_hooks': {'action': 'list (default) shows the watch hooks the companion has installed; remove takes one off.',
               'hook_id': 'For remove: the hook id as list shows it, or "all".'},
 'fnv_hud': {'action': 'read (default) describes the HUD; post shows a corner message (needs full control).',
             'text': 'For post: the message text, up to 250 characters.',
             'seconds': 'For post: how long the message stays on screen, above 0 up to 30. Default 3.'},
 'fnv_input': {'action': 'tap presses a control, key or mouse input briefly; hold keeps it down for duration_ms; '
                         'aim_at turns the player toward target; activate makes the player activate target.',
               'control': 'Named game control to press, such as forward, backward, left, right, attack, activate, aim, '
                          'jump, sneak, run, ready, vats or pipboy.',
               'key': 'DirectInput key code to press instead of a named control.',
               'duration_ms': 'For hold: how long to hold the input, in milliseconds; the companion releases it after '
                              'this time even if the bridge goes away. Default 0.',
               'frames': 'Hold the input for exactly this many game frames and return when they have run; for '
                         'tap/hold, and required with button or mouse.',
               'key_name': 'Key name such as E, Space, Escape, F1, 1 or Up, converted to a key code. Alternative to '
                           'key.',
               'button': 'Mouse button to press: 0 left, 1 right (up to 7). Needs frames.',
               'mouse': 'Mouse movement [dx, dy] applied each frame. Needs frames.'},
 'fnv_install': {'plugin_path': 'Path of the built .esp or .esm file to install, such as the plugin fnv_spec build '
                                'wrote.',
                 'mod_name': "MO2 mod folder name to install into. Default: the plugin's name. Not used on the direct "
                             'install.'},
 'fnv_inventory': {'limit': 'Maximum number of item stacks returned, 1 to 1000. Default 300.'},
 'fnv_journal': {'action': "list (default): the changes made by the act tools; calls: the bridge's own call log.",
                 'limit': 'Rows, newest last, 1 to 1000 (list: default 100; calls: 10 per table).',
                 'include_undone': 'If true (default), also list changes that were already undone.',
                 'tool': 'calls: one tool only.'},
 'fnv_leveled_roll': {'level': "The level to roll at, 1 to 255. Default: the player's level.",
                      'rolls': 'How many times to roll the list, 1 to 5000. Default 200.',
                      'seed': 'Seed the random generator first (0 to 4294967295) so the rolls repeat exactly. Needs '
                              'full control.'},
 'fnv_logs': {'sources': 'Which logs: nvse, console, crash, jip, mo2, error, havok. Omitted reads all of them.',
              'since': 'last (default) returns lines added since the previous call; start returns the whole file.',
              'max_lines': 'Maximum lines returned per log, 1 to 5000. Default 200.'},
 'fnv_look': {'types': 'Only return references of these kinds: actors, doors, containers, activators, items, '
                       'furniture, statics, lights. Null returns all kinds.',
              'limit': 'Maximum number of references returned, largest on screen first. Default 20.',
              'radius': 'Only consider references within this distance of the player, in game units. Default 6000.',
              'fields': 'Keep only these keys for each reference in the result; null returns all fields.',
              'rays': 'Line-of-sight ray budget; references past the budget report line of sight as unknown. Default '
                      '40.',
              'include_hidden': 'When true, also include references that are in range but not currently visible. '
                                'Default false.'},
 'fnv_memory': {'action': 'pools (default), address_space, alloc_trace (allocations by call site) or refcount (who '
                          "changes an object's reference count).",
                'seconds': 'address_space: seconds between two samples to show growth, 0 (default) to 120. alloc_trace '
                           'and refcount: seconds to record, above 0 up to 60, default 3.',
                'top': 'Maximum rows in each ranked list, 1 to 200. Default 20.',
                'pointer': 'For refcount: the address of the NiRefObject to watch, as 0x....'},
 'fnv_menu': {'action': "state (default) reads the menus; tiles reads one menu's live tile tree.",
              'path': 'For action=tiles: keep only tiles whose path contains this text.',
              'depth': 'For action=tiles: how deep to read the tile tree. Default 8.',
              'max_tiles': 'For action=tiles: maximum number of tiles returned. Default 400.',
              'traits': 'For action=tiles: keep only these trait names, such as _x or _visible.'},
 'fnv_move': {'cell': 'Name (editor ID) of the cell to move to. The player moves by coc; other refs also need a '
                      'position in that cell.',
              'timeout_seconds': 'How long to wait for the destination cell to load when the player changes cells, in '
                                 'seconds. Default 180.'},
 'fnv_name_dossier': {'function': 'The engine function, by name or 0x address.',
                      'harvest': 'Use only this harvest id (from fnv_probe harvest) for the runtime evidence. Omitted '
                                 'uses every harvest.',
                      'max_items': 'Maximum entries in each list (callers, classes, chains). Default 12.'},
 'fnv_name_export': {'path': 'Output file: a .json under fnvsym/traces or <state>/traces. Omitted writes '
                             'fnvsym/traces/fnvmcp-<date>.json.'},
 'fnv_name_oracle': {'action': "run compares a function's results with known script commands and form fields; purity "
                               'only checks it is free of side effects; calibrate runs the method blind on proven '
                               'getters; sweep runs it on every unproven pure getter. Default run.',
                     'function': 'The engine function to test. Needed for run and purity.',
                     'mode': 'For run: call invokes the function on nearby objects, sampled uses recorded probe '
                             'samples, auto picks (calls it when proven side-effect free). Default auto.',
                     'harvest': 'For run and sweep: optional number controlling how many samples are harvested; null '
                                'uses the default.',
                     'radius': 'Distance around the player, in game units, within which objects are gathered. Default '
                               '8000.',
                     'limit': 'For calibrate and sweep: maximum number of functions to process. Default 400.'},
 'fnv_name_status': {'name_or_address': 'An engine function name (such as Actor::GetLevel) or address (0x...) to look '
                                        'up.'},
 'fnv_name_test': {'action': 'test (default) runs one hypothesis, battery skips each function during each panel, '
                             'panels lists the battery panels.',
                   'hypothesis': 'For test: {function, intervention {action skip|result|arg, value, arg}, panel or '
                                 'target/setup/trigger/observe/expect, predict blocked|normal, reps >= 3}.',
                   'functions': 'For battery: the functions to test, by name or address. Omitted uses the built-in key '
                                'set.',
                   'panels': 'For battery: the panel names to run (from action=panels). Omitted runs every panel.',
                   'reps': 'For battery: repetitions of each run, each with a control. Default 3.'},
 'fnv_native': {'action': 'What to do: headers (show module API and engine types), build (compile C to a DLL), load, '
                          'unload, reload, call (run an exported function), log (read module logs), list (modules and '
                          'built DLLs).',
                'name': 'Module name. Used by build, load, call, unload and reload; build writes <name>-<hash>.dll and '
                        'load picks the newest build of that name.',
                'source': 'For action=build or reload: C source text, or the path of a .c file. A one-line argument is '
                          'treated as a path.',
                'functions': 'For action=headers: engine function names to emit as FNV_ADDR_<name> addresses and typed '
                             'FNV_<name> pointers.',
                'classes': 'For action=headers: class names whose padded struct layouts are emitted from the symbol '
                           'database.',
                'export': 'For action=call: name of the exported function in the loaded module to run on the main '
                          'thread.',
                'args': 'For action=call: numeric arguments (integers or floats) passed to the exported function.',
                'since': 'For action=log: return log lines after this sequence number (at most 500). Use next from '
                         'the last log answer, or logNext from a load, call or unload; default 0 is the newest 200 '
                         'lines.'},
 'fnv_object': {'action': 'identify (default) says what memory a pointer is in; find lists live objects of a class; '
                          'referrers lists where an address is stored.',
                'pointer': 'For identify and referrers: the address, as 0x....',
                'class_name': 'For find: the class whose live objects to list, such as Character or BSFadeNode.',
                'limit': 'Maximum number of objects or places returned, 1 to 5000. Default 200.'},
 'fnv_shader': {'action': 'compile, engine_list, engine_show, template, effect_add, effect_set, effect_list, effect_remove, replace, restore, status or export.',
               'source': 'HLSL text. compile: any profile; effect_add: a ps_3_0 pixel shader; replace: a drop-in for the engine shader (see template). #include is refused.',
               'profile': 'For compile: target profile. Default ps_3_0.',
               'name': 'effect_*: the effect name (letters, digits, _ . -). engine_show, replace, restore (or all), template: an engine shader name.',
               'filter': 'For engine_list: part of a name, or a pattern with * or ?.',
               'order': 'For effect_add and effect_set: draw order, lowest first.',
               'enabled': 'For effect_set: turn the effect on or off.',
               'params': 'For effect_add and effect_set: {name: number or up to 4 numbers} for the float4 constants at c2 and up. Unset ones use their HLSL default.',
               'disasm': 'For engine_show: add the disassembly. Game content: never save or publish it.',
               'install': 'For export: copy the plugin and packages into the mod profile (the game must be closed).',
               'set': 'For export: save the live state under this name in sets/ and export it. Left out: the live state.',
               'photo': 'For effect_add, effect_set, effect_remove, replace: take before and after pictures (skipped in menus). Default true.'},
 'fnv_overlay': {'action': 'text draws words on screen, marker places a visible object in the world, clear removes the '
                           'texts and markers this tool made.',
                 'text': "For action=text: the words to draw on screen with the engine's debug text layer.",
                 'x': 'For action=text: horizontal screen position of the text. The same place again replaces the text '
                      'there.',
                 'y': 'For action=text: vertical screen position of the text. The same place again replaces the text '
                      'there.',
                 'position': 'For action=marker: [x, y, z] in game units where the marker is placed. Use this or ref.',
                 'scale': 'For action=marker: size multiplier of the marker object (1.0 = normal).'},
 'fnv_patch': {'bytes': 'Raw hex bytes to write at the address, for example 90 90. One of bytes, nop, ret, jmp or call '
                        'selects the patch.',
               'nop': 'Write this many NOP (0x90) bytes at the address.',
               'ret': 'Write a ret instruction; the value is the number of stack bytes to pop (0 for a plain ret).',
               'jmp': 'Write a jump to this target, given as a 0x address or an engine function name.',
               'call': 'Write a call to this target, given as a 0x address or an engine function name.',
               'expect': 'Hex bytes that must currently be at the address; the patch is refused if they differ.',
               'force': 'If true, patch even where another module already changed the bytes (compared with the clean '
                        'exe). Does not override companion hooks.'},
 'fnv_patch_map': {'include_bytes': 'If true, include the original and patched bytes of each patch. Default false.',
                   'limit': 'Maximum number of patches returned, 1 to 5000. Default 400.'},
 'fnv_perk_entries': {'action': 'start hooks the perk entry-point dispatcher, read lists recorded checks, stop removes '
                                'the hooks, evaluate runs one value through the dispatcher now.',
                      'entry_point': 'Perk entry point number (0 to 73), for example 0 weapon damage, 8 to-hit chance, '
                                     '9 XP, 17 buy price. For start it limits hooking to that one; evaluate needs it.',
                      'limit': 'For action=read: maximum number of recorded checks returned.',
                      'value': 'For action=evaluate: the starting value passed through the entry point.',
                      'filters': 'For action=evaluate: filter forms for the entry point, for example [weapon, target] '
                                 'for weapon damage.'},
 'fnv_photo': {'action': 'ref (tiled views of a reference), cell (above and four corners), view (current view), sheet '
                         '(ref views or the view), sequence (film strip of frames), compare (view against a baseline '
                         'image).',
               'angles': 'For ref and sheet: view angles; names front, three_quarter, side, back, top, or [yaw, pitch] '
                         'pairs in degrees. Default is a set of named angles.',
               'size': 'Image size in pixels for each picture.',
               'hide_hud': 'If true (default), hide the HUD while taking the picture.',
               'label': 'If true, draw the fnv_look screen rectangles of references on the picture (view action).',
               'frames': 'For action=sequence: number of frames to capture (default 10).',
               'every': 'For action=sequence: capture one image every this many frames.',
               'baseline': 'For action=compare: path of the baseline image to compare the current view against.',
               'distance': 'For ref views: camera distance from the reference in game units. Default is chosen from '
                           'its size.',
               'name': 'Optional file name prefix for the saved PNG files.'},
 'fnv_physics': {'action': 'controller (actor state and fall), body (collision chain and velocity), set (velocity or '
                           'impulse), triggers (record trigger events), projectiles (in flight), query (is a spot '
                           'free), layers (collision layer names).',
                 'velocity': 'For action=set: [x, y, z] in game units per second (an actor: a push).',
                 'angular_velocity': 'For action=set: [x, y, z] in radians per second.',
                 'impulse': 'For action=set: [x, y, z], mass x units per second.',
                 'seconds': 'For action=triggers: how long to record trigger events, in seconds.',
                 'position': 'For action=query: [x, y, z] in game units of the spot to test.',
                 'radius': 'For action=query: radius in game units around the position to test for free space.'},
 'fnv_place': {'action': 'at places a base form, array places several, nudge/rotate/scale change a placed ref, snap '
                         'drops it on the surface, check tests floating or sinking, list shows placements, remove '
                         'deletes one or all.',
               'scale': "Scale factor. For action=at it sets the new object's scale; for action=scale it is the new "
                        'scale of ref.',
               'shape': 'For action=array: row, grid or circle layout.',
               'count': 'For action=array: how many objects to place.',
               'spacing': 'For action=array: distance between neighbouring objects in game units.',
               'columns': 'For action=array shape=grid: number of columns per row.',
               'radius': 'For action=array shape=circle: circle radius in game units.',
               'snap': 'If true, drop the placed object onto the surface below it (the bottom of its bounds on the '
                       'ground under its footprint).',
               'rest': 'For snapping (at/array with snap=true, and action=snap): low (default) rests the bottom on the '
                       'lowest ground under the footprint so no corner floats; centre on the ground under its origin; '
                       'high on the highest ground so nothing sinks.',
               'local': "For action=nudge: if true, the offset is in the object's own axes instead of world axes.",
               'align': 'For action=snap: if true, also tilt the object to the slope of the ground under its footprint '
                        'at its heading.',
               'export': 'For action=at: false makes a test placement (PlaceAtMe near a ref) that fnv_author export '
                         'leaves out. Default true.',
               'distance': 'For action=at with export=false: how far from the near ref to place, in game units.',
               'direction': 'For action=at with export=false: 0 front, 1 back, 2 left, 3 right of the near ref.'},
 'fnv_play': {'action': 'goto walks to a ref or position, look_at turns the view, activate presses activate on a ref, '
                        'talk, loot, equip, use, drop work on a ref, stop releases held inputs.',
              'direct': 'For action=activate: if true, activate without requiring the ref to be under the crosshair.',
              'max_seconds': 'For action=goto: give up walking after this many seconds.',
              'run': 'For action=goto: if true, run instead of walk.'},
 'fnv_plugin_build': {'action': 'scaffold (a starting source), build (compile with Zig), install (as an MO2 mod, game '
                                "closed) or log (the module's log file).",
                      'name': 'The module name; it names the plugin, its folder and its files.',
                      'source': 'For build: the C source text, or the path of a .c file. Omitted builds the source '
                                'scaffold wrote.',
                      'hooks': 'For build: the engine functions the module hooks, by name or address; their prologues '
                               'are planned from the game.'},
 'fnv_probe': {'action': 'plan, install, status, read, remove, clear, config, harvest or harvests; see the tool '
                         'description for what each does.',
               'statuses': 'For plan and install: select functions with these naming statuses. Default is every status '
                           'that is not proven.',
               'functions': 'For plan and install: function names or addresses to select. For read: the functions '
                            'whose samples and callers to return.',
               'start': 'For plan and install: start address (hex) of an address range to select, used with end.',
               'end': 'For plan and install: end address (hex) of the address range to select, used with start.',
               'cls': 'For plan and install: select the functions of this class.',
               'callees_of': 'For plan and install: select the callees of these functions.',
               'limit': 'For plan and install: maximum number of functions selected.',
               'part': 'For plan and install: a slice k/n of the selection, to probe it in several parts.',
               'samples': 'Number of first calls recorded per function with this, arguments and vtables (install and '
                          'config).',
               'exit_samples': 'How many of those samples also record the result on return (install and config).',
               'every': 'One in this many later calls also records its caller (install and config).',
               'dry_run': 'Only valid for action=install: do the planning and checks without patching the game.',
               'top': 'For action=status: how many of the most-called functions to list.',
               'int3': 'For plan and install: also probe functions with no entry jump and no call site, using a '
                       'one-byte trap.',
               'int3_budget': 'For int3 probes: number of calls after which a probe removes itself.',
               'notes': 'For action=harvest: free-text note stored with the harvest.'},
 'fnv_profile': {'action': 'sample records where frame time goes; diff compares two saved profiles; frame times each '
                           'subsystem of the main loop with timing hooks.',
                 'seconds': 'For sample (default 5, up to 120) and frame (0.5 to 30, default 3): how long to measure, '
                            'in seconds.',
                 'rate': 'For action=sample: samples per second.',
                 'threads': 'For action=sample: sample only the main thread or all threads.',
                 'name': 'For action=sample: name prefix for the saved profile files (default profile). Must be a '
                         'plain file name.',
                 'before': 'For action=diff: the first profile, as a saved file path, a file name, or a name prefix '
                           '(newest match).',
                 'after': 'For action=diff: the second profile to compare, same forms as before.',
                 'top': 'How many functions to list in the report.'},
 'fnv_profile_set': {'mods': 'MO2 mods to turn on or off, by name, such as {"My Mod": true}.',
                     'plugins': 'Plugins to turn on or off, by file name, such as {"MyMod.esp": false}.',
                     'dry_run': 'If true, show the changes without writing them. Default false.'},
 'fnv_quest': {'action': 'state (default): stages, objectives and script variables; targets: where each objective '
                         'marker points and why one is missing.'},
 'fnv_raycast': {'layer': 'Which collision test to use: line_of_sight (default), path_pick or projectile.',
                 'ground_at': 'Object with x and y in game units; instead of your ray, cast one straight down there and '
                              'return the ground height (terrainHeight when it hits the landscape, onRef when an '
                              'object is on top).'},
 'fnv_record': {'action': 'show (default) decodes one record; find searches records; users lists what references a '
                          'record.',
                'query': 'For action=find: EditorID or name substring, form id, or Plugin.esp:LOCALID.',
                'record_type': 'For action=find: keep only this record type, such as WEAP, NPC_ or QUST.',
                'plugin': 'For action=find: keep only records of this plugin.',
                'types': 'For action=users: keep only users of these record types.',
                'all_versions': 'For action=show: also decode every override, not only the winning version.',
                'max_bytes': 'For action=show: bytes shown of each undecoded subrecord.',
                'limit': 'For find (1 to 200, default 25) and users (default 100): maximum results.',
                'offset': 'For find and users: how many results to skip, for paging.'},
 'fnv_refs_near': {'radius': 'Distance from the player in game units, above 0 up to 100000. Default 2000.',
                   'form_type': 'Only references of this form type code, such as 42 (NPC) or 43 (creature). 0 '
                                '(default) means all types.',
                   'base': 'Only references of this base form, as a form id such as 0x00015038.',
                   'limit': 'Maximum number of references returned, nearest first, 1 to 300. Default 50.'},
 'fnv_render': {'action': 'toggle (a console render toggle), imagespace (apply or remove a modifier), shaders (reload '
                          'shaders) or lights (list scene lights).',
                'name': 'For toggle: wireframe, sky, grass, trees, leaves, lod, menus, borders, primitives, collision, '
                        'test_light, motion_blur, eof_imagespace or debug_text. For imagespace: the IMAD form.',
                'arg': 'For toggle: an extra argument passed to the console command, when it takes one.',
                'remove': 'For imagespace: if true, remove the modifier instead of applying it. Default false.'},
 'fnv_replay': {'action': 'record (start recording input), stop (write the replay), play (replay one) or list (saved '
                          'replays).',
                'name': 'For record and play: the replay name.',
                'seed': 'For record: the random seed set at the start (0 to 4294967295). Default 20261111; play uses '
                        'the recorded seed.',
                'load': 'For play: if true (default), load the recorded start save first.'},
 'fnv_rng': {'seed': "Seed the game's random generator with this number (0 to 4294967295). Needs full control. Omitted "
                     'only reads its state.'},
 'fnv_run_script': {'source': 'An xNVSE expression such as "player.GetAV Health * 2", or a script snippet of several '
                              'lines. Up to 20000 characters.'},
 'fnv_save_diff': {'types': 'Keep only changed forms of these record types, four-letter codes such as QUST or ACHR. '
                            'Omitted shows all types.',
                   'forms': 'Keep only these form ids (hex, optional 0x, up to 8 digits). Omitted shows every changed '
                            'form.',
                   'limit': 'Maximum number of changed forms returned, 1 to 1000. Default 200.'},
 'fnv_save_inspect': {'types': 'Record types to keep in the changed-form table, as four-letter codes such as QUST, '
                               'ACHR, REFR. Null shows all types.',
                      'forms': 'Form ids to keep (hex, optional 0x prefix, up to 8 digits). Null shows every changed '
                               'form.',
                      'limit': 'Maximum number of changed forms returned per page.',
                      'offset': 'Number of changed forms to skip, for paging through the table.',
                      'screenshot': "If true, also write the save's thumbnail image as a PNG file. Default false."},
 'fnv_screenshot': {'name': 'Name for the saved PNG file (letters, digits, _ and -). Default "screen".',
                    'max_width': 'The image is scaled down to at most this width in pixels, 320 to 3840. Default 1280.',
                    'method': "auto (default) tries the engine's own capture, then the window; engine or window forces "
                              'one.'},
 'fnv_script_coverage': {'action': 'start (begin recording), stop (report lines run and never run), run (start, run a '
                                   'scenario folder, stop) or report (numbers so far).',
                         'plugin': 'For start and run: only count scripts of this plugin. Omitted counts every script '
                                   'seen.',
                         'folder': 'For run: the folder of YAML scenarios to run.'},
 'fnv_script_debug': {'action': 'What to do: break (set, list or clear breakpoints), status, vars (list script '
                                'variables), set (change a variable), eval (run a snippet), step, continue, freeze or '
                                'thaw.',
                      'op': 'For action=break: set adds a breakpoint, list shows them, clear removes them. If omitted: '
                            'set when a script, line, opcode or when is given, else list.',
                      'script': 'For action=break: the quest or script (name or form id) to stop in. Required when a '
                                'when condition names script variables.',
                      'line': 'For action=break: source line number in the script; the game stops before that '
                              'statement runs.',
                      'opcode': 'For action=break: stop on statements with this command opcode (0 to 65535) instead of '
                                'at a line.',
                      'when': 'For action=break: a condition that must hold for the stop, in predicate text or over '
                              "the script's variables. It stops only when true.",
                      'break_id': 'For action=break with op=clear: the number of the one breakpoint to remove. If '
                                  'omitted, all breakpoints are cleared.',
                      'name': 'For action=set: the name of the script variable to change, as action=vars lists it.',
                      'value': 'For action=set: the number to write into the variable. Must be numeric.',
                      'expression': 'For action=eval: a script snippet to run on the stopped reference, such as '
                                    'GetStage QuestName.',
                      'mode': 'For action=step: line runs to the next line, over steps over called functions, out runs '
                              'to the first statement outside the current run. Default line.'},
 'fnv_script_errors': {'since': 'last (default) returns errors since the previous call; start returns every error '
                                'since the game started.'},
 'fnv_script_explain': {'depth': 'How many levels of engine calls to follow from each command handler, 1 to 3. Default '
                                 '2.',
                        'code_lines': 'Lines of decompiled code shown per handler, 0 to 400. Default 60.'},
 'fnv_script_hot_swap': {'source': 'The new script source text to compile and swap in.',
                         'force': 'If true, swap even when existing variables would move or disappear (instances keep '
                                  'values by index). Default false refuses.'},
 'fnv_script_instances': {'script': 'Without of: keep only instances of this script (form id) or quest name.',
                          'include_variables': "If true (default), include each instance's variables with their "
                                               'values.',
                          'limit': 'Maximum number of instances returned, 1 to 300. Default 60.'},
 'fnv_script_lint': {'source': 'The script source text to check, starting with the scn line.',
                     'check_forms': 'If true (default), check that each EditorID exists in an active plugin and has '
                                    'the right record type.'},
 'fnv_script_log': {'action': 'Which log: commands (statements of every script), history (world-changing commands run '
                              'on a ref), events (block flags raised), raise (raise a block flag on a ref), errors '
                              '(script errors since the last call).',
                    'mode': 'For action=commands or events: start begins recording, read returns what was recorded '
                            '(default), stop ends it.',
                    'script': 'For action=commands with mode read: keep only statements run by this script.',
                    'ref': 'For history: the reference whose commands to list. For commands read: keep only that '
                           'reference. For raise: the reference to raise the flag on.',
                    'opcode': 'For action=commands with mode read: keep only statements with this command opcode.',
                    'flag': 'For action=raise: the block flag to raise on the reference, such as OnAdd or '
                            'OnTriggerEnter.',
                    'since': 'Record sequence number to read after (the next value an earlier call returned). For '
                             'errors: last (default) or start.',
                    'limit': 'Maximum number of records returned. Default 200 when omitted.'},
 'fnv_script_profile': {'action': 'start clears the counters, read (default) gives the top scripts (it starts and '
                                  'waits on first use), stop removes the hooks.',
                        'seconds': 'On the first read: how long to measure before reading, 0.2 to 60. Default 3.',
                        'top': 'Number of scripts listed, by total time, 1 to 200. Default 25.'},
 'fnv_script_set': {'target': 'The quest (stage, objective, variable), the reference (variable) or the global, by '
                              'EditorID or form id.',
                    'kind': 'What to set: stage, objective_completed, objective_displayed, variable or global.',
                    'name': 'For kind=variable: the script variable name.',
                    'index': 'For kind=objective_completed or objective_displayed: the objective index.'},
 'fnv_script_trace': {'action': 'start installs the hook, read (default) returns new statements (it starts on first '
                                'use), stop removes it.',
                      'source': "The script's source text; with it, statements are mapped to source lines and lines "
                                'never run are listed.',
                      'limit': 'Maximum statements returned per read, 1 to 4096. Default 200.'},
 'fnv_scripts_compile': {'only': 'Only compile these scripts of the project spec, by EditorID. Omitted compiles all of '
                                 'them.'},
 'fnv_session': {'action': 'start readies the game and opens a session, mark records a named point, end reverts the '
                           "session's changes and reports, status shows the session cheaply.",
                 'label': 'For action=mark: the name of the point that fnv_health and the warning log report from.',
                 'deterministic': 'For action=start: if true, run deterministically (fixed frame step, seeded random '
                                  'numbers). Default false.',
                 'quit_game': 'For action=end: if true, quit the game after the report. Default false leaves it '
                              'running.',
                 'timeout_seconds': 'For action=start: seconds to wait for the game to launch, load and start '
                                    'simulating. Default 240.'},
 'fnv_setting_info': {'name': 'The setting name, such as fCombatDistance, or an INI setting with its section, such as '
                              'bAlwaysActive:General.'},
 'fnv_setting_set': {'name': 'The game setting (fCombatDistance) or INI setting with its section '
                             '(bAlwaysActive:General).',
                     'value': 'The new value: a number, or text for an s setting. The old value is journaled.'},
 'fnv_snapshot': {'action': 'capture (default) saves chosen state under a name; diff compares two snapshots, or one '
                            'with now.',
                  'name': 'For capture: the snapshot name.',
                  'items': 'For capture: what to save: "player", or items like {eval: expr}, {actor_value: Health, '
                           'ref: player}, {stage: quest}, {global: name}, {variable: name, of: quest}, {item_count: '
                           'form, ref: player}.',
                  'before': 'For diff: the name of the first snapshot.',
                  'after': 'For diff: the name of the second snapshot. Omitted captures the same items again now and '
                           'compares.'},
 'fnv_spec': {'action': 'What to do: validate (dry run, writes nothing), build (write the plugin and receipt), inspect '
                        '(summarise a plugin on disk), lookup (find records by EditorID for the spec), fields (the '
                        'named fields a record type takes in "set").',
              'spec': 'For action=validate: an inline spec object to check instead of the project spec file. Omitted '
                      'checks the project spec.',
              'which': 'For action=inspect: build (default) is the last build; installed is the copy fnv_install put '
                       'in the MO2 dev profile, else the one in Data.',
              'query': 'For action=lookup: part of an EditorID (2 to 100 characters) to find in the spec and the '
                       'indexed masters.',
              'record_type': 'For action=lookup: keep only records of this four-letter type, such as STAT or MISC. '
                             'For action=fields: the type whose named fields to list (ARMO, LVLI, TERM...).',
              'limit': 'For action=lookup: maximum master records returned. Default 25.'},
 'fnv_symbolize': {'addresses': 'Addresses to name, as hex strings such as 0x0086A4B2 (1 to 200).',
                   'modules': 'Loaded modules for addresses outside FalloutNV.exe: {name, base, size} as a crash log '
                              'lists them.'},
 'fnv_symbols': {'action': "status checks the symbol database, lines gives a function's line table, locals its "
                           "parameters and locals, where maps file and line to code addresses, type gives a type's "
                           'members.',
                 'line': 'For action=where: the source line number in the given file to map to code addresses.',
                 'limit': 'Maximum number of rows returned.',
                 'offset': 'Number of rows to skip, for paging.'},
 'fnv_systems': {'action': 'Which game system to query: formulas (catalog), formula, sweep, progression, needs, perk, '
                           'barter, zone or crime.',
                 'name': 'For formula and sweep: the formula or engine function name. For perk: the PERK. For zone: '
                         'the ECZN. For barter: an optional vendor.',
                 'args': 'For formula and sweep: the argument values to pass. For barter: the list of items to price.',
                 'index': 'For action=sweep: the zero-based position in args of the argument to vary over the range.',
                 'start': 'For action=sweep: the first value of the swept argument.',
                 'stop': 'For action=sweep: the last value of the swept argument.',
                 'steps': 'For action=sweep: number of values between start and stop. Default 10.'},
 'fnv_test': {'action': 'run (default) runs one scenario file or inline yaml, run_all every scenario in a folder, '
                        'steps the steps given inline.',
              'path': 'For action=run: the scenario YAML file to run. A relative path is looked up in fnv.tests_dir, '
                      'then in the shipped examples (fnv-tests/hello-wasteland.yaml).',
              'yaml': 'For action=run: the scenario as YAML text, instead of a file.',
              'folder': "For action=run_all: the folder of scenarios. Default: the project's fnv.tests_dir.",
              'steps': 'The scenario steps to run in order, in the same form as a YAML scenario file (run, script, '
                       'wait, expect, set, input, move, step, call, and so on).',
              'name': "Name of this scenario, used in the report (with action=run it replaces the file's own "
                      "name). Default 'inline'.",
              'plugins': 'Plugins the scenario needs; the run stops with PLUGIN_NOT_LOADED if one is not loaded in the '
                         'dev profile (install it with fnv_install first).',
              'setup': 'Steps run before the main steps, in the same step format.',
              'teardown': 'Steps run after the main steps, in the same step format.',
              'deterministic': 'When true, fixes the frame step at 60 fps and seeds the random generator so runs are '
                               'repeatable. Default false.',
              'repeat': 'Run the whole scenario this many times and report the steps that pass in one run and fail in '
                        'another (values that differ with the same outcome are listed apart). Default 1.',
              'timeout_seconds': 'Time limit for the scenario run, in seconds. Default 240.',
              'relaunch_on_crash': 'When true (default), the game is relaunched if it crashes during the run.'},
 'fnv_test_save': {'action': 'list (default), save (the current game), load (waits until gameplay) or delete.',
                   'name': 'For save, load and delete: the test save name (letters, digits, _ and -); the file gets '
                           'the prefix fnvmcp_test_.',
                   'timeout_seconds': 'For load: seconds to wait for gameplay, 20 to 600. Default 180.'},
 'fnv_threads': {'action': 'list (default) shows every thread and spin lock; hang samples stacks to say where a frozen '
                           'game is stuck.',
                 'samples': 'For hang: how many stack samples to take, 1 to 20. Default 5.',
                 'interval_ms': 'For hang: milliseconds between samples, 50 to 5000. Default 250.',
                 'all_threads': 'For hang: if true, sample every thread, not only the main thread. Default false.'},
 'fnv_time': {'set_hour': 'Jump the game clock to this hour of day (0 up to but not including 24, fractions allowed).',
              'advance_hours': 'Advance the game clock by this many hours. Days, months and years roll over like the '
                               "game's calendar.",
              'timescale': "Set the game's time multiplier (game time per real time). 0 is allowed.",
              'mode': "globals (default) moves the clock directly; engine_wait runs the game's own one-hour wait step "
                      'per hour and needs allow_unproven.',
              'evaluate_packages': 'For mode=globals: if true (default), nearby actors re-evaluate their AI packages '
                                   'after the time change.',
              'allow_unproven': 'If true, allow an engine function that is not yet proven. Needed for '
                                'mode=engine_wait. Default false.',
              'action': 'calendar (default) reads or sets the calendar; pause, resume, force_resume, hold, release, '
                        'step, fixed and speed drive the game clock and the frame gate; status reads them.',
              'frames': 'For action=step: how many frames to advance, 1 to 10000.',
              'fps': 'For action=step: the fixed frame rate to step at. For action=fixed: the fixed frame rate, 0 '
                     'turns the fixed step off.',
              'x': 'For action=speed: the time multiplier, 0.05 to 10.',
              'timeout_seconds': 'For action=hold: seconds without calls after which the gate opens by itself. Default '
                                 '120.'},
 'fnv_trace': {'action': 'start installs an observe-only hook (default), read returns calls since the last read, stop '
                         'removes the hook, list shows running traces.',
               'filter': 'Keep only calls where this, caller or paramN matches, such as {on: this, ref: MyNPC} or {on: '
                         'param1, value: 5}.',
               'limit': 'Maximum number of calls returned by a read.',
               'include_calls': 'If true (default), a read lists the individual calls along with the counts; if false, '
                                'counts only.',
               'allow_unproven': 'If true, allow tracing any function of the engine map, not only proven ones. A wrong '
                                 'address can still crash the game. Default false.',
               'when': 'For action=start: conditions that must all hold for a call to be recorded, each like {on: '
                       'this|caller|param0..7, ref|value}. Null records every call.',
               'stack': 'For action=start: if true, record the call stack of each hit, so a read groups the hits in '
                        'commonStacks. Default false.'},
 'fnv_ui_do': {'action': 'click a tile, press a button by label, wait hours, sleep, fast_travel to a marker, message '
                         '(press a message box button), close or back the top menu, or type text into the text entry '
                         'menu.',
               'target': 'For action=click: the tile path or name to click (fnv_menu tiles lists them).',
               'label': 'For action=button: the text on the button, such as Take All or Done. For action=type: the '
                        'text to enter.',
               'menu': 'For click, button and close: the menu to act on, by number or name (InventoryMenu or '
                       'inventory), instead of the one at the top. An unknown name is an error.',
               'hours': 'For action=wait or sleep: number of hours to pass, 1 to 24. Default 1 when omitted.',
               'button': 'For action=message: the message box button to press, as an index or its label.'},
 'fnv_undo': {'count': 'How many of the last undoable changes to undo, 1 to 100. Default 1.',
              'journal_id': 'Undo only this journal entry (its id as fnv_journal shows it).',
              'all': 'If true, undo every undoable change of the current game process and list those that cannot be '
                     'undone.'},
 'fnv_vats_odds': {'limb': 'Only this body part: -1 whole body, 0 torso, 1 and 2 head, 3 to 6 arms, 7 to 12 legs, 13 '
                           'brain, 14 weapon. Omitted gives all.',
                   'include_terms': 'If true (default), explain the gun formula term by term (spread, target area, '
                                    'visibility, settings).'},
 'fnv_wait_for': {'frames': 'Wait until this many frames have passed. Give exactly one condition.',
                  'seconds': 'Wait this many seconds, measured by the clock choice. Give exactly one condition.',
                  'clock': 'For seconds: game (default) counts game seconds scaled by the time multiplier, real counts '
                           'wall-clock seconds.',
                  'predicate': 'Wait until this condition text is true, such as frame >= 500 or [0x011DEA0C]:u8 == 1.',
                  'loaded': 'If true, wait until the game has finished loading and is simulating. Default false.',
                  'timeout': 'Real seconds to wait before giving up. Default 60.',
                  'max_frames': 'Upper limit on frames run during a predicate wait. Default 3600.'},
 'fnv_warnings': {'action': "status checks the setting and files, enable turns the warning log on in xNVSE's config, "
                            'read shows lines, summary counts by category and plugin, clear sets the mark, capture '
                            "hooks the engine's Warning function.",
                  'mode': 'For action=capture: start, stop or status of the live capture. With no mode, capture '
                          'starts.',
                  'since': 'For read: mark (default) reads from the last mark, start reads the whole files, a number '
                           'reads from that line.',
                  'category': 'For read: keep only this warning category, such as MASTERFILE, SCRIPTS, MODELS, '
                              'TEXTURES, ANIMATION, AI, Audio or other.',
                  'text': 'For read: keep only warnings containing this text.',
                  'plugin': 'For read: keep only warnings about this plugin.',
                  'source': 'For read: keep only the engine log or the havok log.',
                  'limit': 'Maximum number of warning lines returned per page. Default 100.',
                  'offset': 'Number of lines to skip, for paging.',
                  'brief': 'If true, return a shorter result.',
                  'top': 'For action=summary: how many of the most repeated messages to list. Default 10.'},
 'fnv_watch': {'items': 'What to sample: expressions such as player.GetAV Health, or mappings like {eval, ref}, '
                        '{actor_value, ref}, {position: ref}, {mem, path}. With frames, memory-read predicate text, at '
                        'most 16.',
               'seconds': 'How long to sample, in seconds. Default 5. Not used when frames is given.',
               'interval_ms': 'Milliseconds between samples. Default 100. Not used when frames is given.',
               'until': 'An expression that ends sampling early as soon as it is true.',
               'keep_unpaused': 'If true (default), the game is kept running while sampling.',
               'frames': 'If set, sample frame by frame inside the game for this many frames instead of by time.',
               'every': 'With frames: take a sample every this many frames. Default 1.'},
 'fnv_window': {'action': 'status (default), release_mouse (free the cursor while the game runs), capture_mouse, '
                          'unpause (close the pause menu), background (no pause on focus loss) or foreground.',
                'persist': "If true, write bBackground Mouse=1 to the dev profile's Fallout.ini so every launch leaves "
                           'the mouse free; false removes it. Omitted changes nothing.'},
 'fnv_world_edit': {'action': 'Edit to make: move, rotate, scale, transform (any of these), enable, disable, delete, '
                              'duplicate, weather (force or release), imagespace (start a modifier), imagespace_stop.',
                    'position': 'For move/transform: [x, y, z] in game units. A null entry keeps that axis unchanged.',
                    'rotation': 'For rotate/transform: [x, y, z] angles in degrees. A null entry keeps that axis '
                                'unchanged.',
                    'scale': 'For scale/transform: new scale factor of the reference (1 = normal).',
                    'fade': 'For action=disable: if true the reference fades out instead of vanishing at once.',
                    'weather': 'For action=weather: the weather form to force, or "release" to drop the override.',
                    'override': 'For action=weather: true (default) forces it as an override; false clears the '
                                'override.',
                    'imagespace': 'For imagespace and imagespace_stop: the image-space modifier form to start or stop.',
                    'strength': 'For action=imagespace: strength of the modifier. Default 1.',
                    'distance': 'For action=duplicate: distance in game units for placing the copy. Default 0.',
                    'count': 'For action=duplicate: how many copies to place, 1 to 20.'}}

PARAM_FIXES: dict[str, dict[str, tuple[str, str]]] = {}
