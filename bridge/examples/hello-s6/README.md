# Hello S6: scenario S6 (a new mesh looks wrong)

One static crate (`HelloS6Crate`) whose mesh, `meshes\hellos6\crate.nif`, is a loose file of the mod. For the
scenario the mesh is a copy of the vanilla `clutter\crate\cratelarge01.nif` with its diffuse texture misspelled
(`CrateLarge0l.dds`); put it in the MO2 mod folder (`mods\HelloS6\meshes\hellos6\`) before launching. Meshes are
game assets and are not in the repository.

1. `fnv_place at base=HelloS6Crate`, then `fnv_photo ref=<the placed reference>` from four angles (next to a vanilla
   `CrateLarge01` it has no paint).
2. `fnv_asset_check plugin=HelloS6.esp`: `nifTexturesMissing` names the texture and the mesh.
3. Fix the path in the mesh file, `fnv_asset reload path=meshes\hellos6\crate.nif` (0.66 s, no restart), photograph
   again.
