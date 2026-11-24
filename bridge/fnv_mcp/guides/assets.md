# Make meshes, animations and textures
Summary: Turn a .glb model, a keys file or an image into a NIF, KF or DDS in your mod folder, check it, use it in a record, see it in the game.
Words: mesh, model, nif, kf, animation, texture, dds, glb, gltf, blender, png, image, normal map, convert, export, import, preview, extract, static

`fnv_asset` makes the files offline: no game, no NifSkope, no GECK. Files go under the mod folder of the MO2 dev
profile (`mods\<options.mod>`, default the project plugin's name), at the Data path given in `out`. On the direct
install they go in the game's Data, only with `fnv.allow_direct_install_changes`. Making files needs
`engine_control` full.

## Steps

1. **Start from a file.** Put the source in the project, state or examples folder. Meshes and animations: glTF
   binary (`.glb`; Blender: File > Export > glTF 2.0, format glTF Binary). Textures: PNG, JPEG, TGA or BMP. To start
   from a vanilla file: `fnv_asset action=extract path=meshes\clutter\toolbox.nif` copies it to the state folder.
2. **A static mesh.** `fnv_asset action=make_nif path=<crate.glb> out=meshes\mymod\crate.nif`.
   - glTF is y up in metres; the mesh comes out z up in game units (about 70 per metre). A source already z up:
     `options.up="z"`; another size: `options.scale` (game units per source unit).
   - Each material's base colour texture is written as `textures\mymod\<image>.dds`, its normal map (or a flat one)
     as `<image>_n.dds`. `options.texture` names an existing diffuse texture instead.
   - Not made yet: collision. The mesh has none, so the player walks through it.
3. **A texture.** `fnv_asset action=make_dds path=<rust.png> out=textures\mymod\rust.dds`: DXT1 when opaque, DXT5
   with alpha, every mip level; sides that are not powers of two are resized (a warning says so). A normal map:
   `options={"normal": true, "specular": "<spec.png>"}`.
4. **An animation.** `fnv_asset action=make_kf path=<wave.json> out=meshes\characters\_male\idleanims\mywave.kf
   options={"skeleton": "meshes\\characters\\_male\\skeleton.nif"}`. The report lists `notInSkeleton`, node names
   the skeleton lacks. A keys file is in the game's axes and units:
   `{"name": "SpecialIdle", "loop": false, "length": 1.0, "nodes": {"Bip01 R UpperArm":
   {"rotation": [[0, [1, 0, 0, 0]], [0.5, [0.7071, 0, 0, 0.7071]]]}}}` (rotation w, x, y, z). From a `.glb`,
   `options.animation` picks the clip. The engine takes a KF's animation group from its sequence name, so name it
   after a group (SpecialIdle for a one-off motion, Idle, ...); a name that is no group cannot be played.
   Play it: `fnv_anim action=play ref=player kf=meshes\mymod\wave.kf` (any actor; the second play restarts it).
   `fnv_anim action=reload ref=<actor>` rebuilds an actor's 3D (the player too).
5. **Check it.** `fnv_asset action=preview path=meshes\mymod\crate.nif` draws it front and side with its size;
   `action=nif` and `action=dds` check headers, links, texture paths and mips.
6. **Use it.** A record names the mesh, its path below `meshes\`: in the spec, `"MyCrate": {"type": "STAT",
   "model": "mymod\\crate.nif", "bounds": [...]}`, the bounds being `obnd` from the make_nif report. Build,
   `fnv_install`, launch; place it with `fnv_place` and look with `fnv_photo`.
7. **Change it while the game runs.** Make the file again at the same `out`, then `fnv_asset action=reload
   path=<out>`. A file new to the mod folder is seen only by a game started after it was made (MO2 lays the mod
   folders over Data at launch).

## What the answers mean

- `make_nif`: `size` and `bounds` in game units, `obnd` the record bounds to copy into the spec, `textures` the DDS
  paths it wrote, `warnings` (a texture it could not make, a resized side).
- `make_kf`: `sequence` is the name the engine reads as the animation group, `textKeys` the keys written (`start`,
  yours, `end`), `notInSkeleton` keyed nodes the skeleton lacks (they do nothing).
- `make_dds`: `format` (DXT1 or DXT5), `width`, `height`, `mipLevels`.
- `fnv_anim play kf`: `loaded` is true the first time (the KF was added to the actor), false after (the same
  sequence was started again); `group` is the group the engine found; `state` 1 means animating.
- `fnv_asset reload`: `refs` is how many references were rebuilt with the new file.

## Known traps

- A file new to a mod folder is not seen until the game is started again (MO2 maps the folders at launch); a file
  it already mapped can be remade and reloaded.
- A KF whose sequence name is no animation group is refused (`KF_NO_GROUP`).
- A remade KF is not read again while the game runs: the model loader keeps the first one. Restart.
- `fnv_anim state` lists what the body sections play, not a KF started with `kf`; read that sequence (the address in
  the answer) at +0x44 (state) and +0x34 (its clock).
- Made meshes have no collision.
- With the game window out of focus the pause menu opens and the world stops; `fnv_window unpause` first.

## Checks that prove the fix

- `fnv_asset action=nif` or `action=dds` on the made file reports no problem.
- `fnv_photo` of the placed object shows it, in the right colours.
- After a reload, `fnv_asset action=file_log` shows the file opened again.
- Live: the regress script in the tools folder, suites `assets` and `anim`.
