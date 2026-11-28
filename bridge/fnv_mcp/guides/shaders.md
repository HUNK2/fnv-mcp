# HLSL shaders in the game
Summary: Write a screen effect or replace an engine shader in HLSL, see it in the running game, tune it, undo it, and export it as a plugin.
Words: shader, hlsl, pixel shader, post process, screen effect, filter, color grading, vignette, effect, ps_3_0, replace shader, sdp, shaderpackage

Use this to change how the game looks with HLSL: a whole-screen effect drawn over the finished frame (under the
HUD), or a swap of one of the engine's own pixel shaders. Everything runs through `fnv_shader`.

## Steps

1. **Start from a template.** `fnv_shader action=template` gives a screen effect that works. With `name` of an
   engine shader (find one with `engine_list filter=...`) it gives a drop-in starting point for a replacement.
2. **Compile.** `compile source=...` returns errors with line numbers, the version, the slots and the constants.
   It needs no game and never returns the bytes.
3. **Put it in.** `effect_add name=... source=...` for a screen effect, or `replace name=<engine shader> source=...`.
   Both take a picture before and after (`photo=false` skips them): look at them.
4. **Tune.** `effect_set name=... params={"tint": [1, 0.8, 0.6, 1]}`; `order` changes the draw order, `enabled=false`
   turns it off. `effect_list` shows the live effects and their cost; `status` shows the renderer.
5. **Undo.** Every change is journaled: `fnv_undo`. `effect_remove`, `restore name=all` do the same by name. `effect_remove` and `restore` also drop the saved copy
   that `export` would bake, and that works with the game closed (`effect_remove name=all` clears every saved effect).
6. **Export.** `export set=<name>` saves the state as a set and builds an xNVSE plugin (`fnvmcp_shaders_<set>`) and
   patched shader packages; `install=true` copies them into the mod profile (the game must be closed).

## Parameters

- `source`: HLSL text (no `#include`). `compile` takes any `profile` (default ps_3_0); `effect_add` needs a ps_3_0
  pixel shader; `replace` needs a drop-in for the engine shader (`template name=...`).
- `name`: the effect name (1-40 letters, digits, `_ . -`; not starting with `.` or `-`) for `effect_*`; an engine shader name for `engine_show`,
  `replace`, `restore` (`all` restores every one) and `template`.
- `params`: `{name: number or up to 4 numbers}` for the float4 constants at c2 and up. `order`: draw order, lowest
  first. `enabled`: effect on or off. `filter` (`engine_list`): part of a name, or a pattern with `*` or `?`.
- `disasm` (`engine_show`): adds the disassembly. `photo`: before and after pictures (default true).
- `export`: `set` saves the live state under that name and exports it; `install` copies into the mod profile.

## What the answers mean

- Effect registers: `s0` is the scene, `s1` the HDR scene (black when the game has none this frame, `status`
  `hdrInput` says which). `c0` = (time in seconds, frame delta in seconds, width, height); `c1` = (1/width,
  1/height, frame mod 65536, 0). Your params are float4 constants at `c2` and up. A param you do not set uses its
  HLSL default value. An unknown name or more than 4 numbers is refused with the valid names and registers.
- `status` `renderer`: `nonSystemD3D9` true means ENB, NVR or ReShade may interfere; `deviceLost`; `hdrInput`;
  `drawing` (an effect drew in the last 60 frames).
- Effects do not draw in menus or the pause menu (`status` `inMenu`). The change is still made and journaled; the
  pictures are skipped with a note: close the menu and look again.
- `engine_show disasm=true` returns the engine's own code: the answer carries `gameContent: true`. Never save,
  commit or publish it. `compile` and `template` of your own source are yours.

## Known traps

- `CALL_SITE_TAKEN`: effects and the live module patch the call at 0x00B55AD6. An installed exported plugin
  (`fnvmcp_shaders_<set>`) holds the same site, so live `effect_add` then fails; disable that plugin's mod to edit
  live. Another renderer hook (ENB, ReShade) can hold it as well. Nothing is changed when it fails.
- `engine_list` and `engine_show` never load the shader module (it patches game code). They scan live objects only when
  an earlier `effect_add` or `replace` loaded it; otherwise they answer from the package alone and say live objects
  are unknown (`liveObjects` is null).
- Limits: no depth input, no per-object shaders, at most 8 effects.
- A game restart ends all live effects and replacements; the sources stay saved and `export` still offers them.
- A replacement must be a drop-in for the original (same inputs and constants): start from `template name=...`.

## Checks that prove the fix

- The picture after the change differs from the one before (`changedPercent`), and the look is what you meant.
- `fnv_shader action=effect_list`: the effect is listed, enabled, not `failed`, with a small `costUs`.
- `fnv_shader action=status`: `module` true, no warnings, `renderer.drawing` true while you are out of menus.
- After `export install=true` and a game start: `fnv_status` lists the plugin, and the look is the same as live.
