# Hello Crash: scenario S2 (a load order crashes on entering a cell)

Entering `HelloCrashRoom` crashes the game: the crate there (`HelloCrashCrate`) uses `meshes\hellocrash\crate.nif`,
a loose file of the mod whose root node's only child link points at its strip data instead of the strips. For the
scenario the mesh is a copy of the vanilla `clutter\crate\cratelarge01.nif` with that one link changed (the root's
child list starts at byte 542 of that file; its first entry, 6, becomes 10). Meshes are game assets and are not in
the repository.

1. `fnv_debug break location=fault` (the crash hold), then `coc HelloCrashRoom`.
2. The loader thread faults in `NiAVObject::AttachParent`; the game stays alive, held. The main thread waits for
   the loader, so the companion cannot answer: `fnv_debug status` reads the fault from outside and names the
   function, its callers (`NiNode::SetChildAt`, `NiStream::Load`, `QueuedModel::Run`) and `hellocrash\crate.nif`
   on the faulting thread's stack.
3. `fnv_asset where path=meshes\hellocrash\crate.nif`: the HelloCrash mod folder; `fnv_record_find
   plugin=HelloCrash.esp`: the STAT that uses it.
4. `fnv_quit force=true`.

A root node that claims 268 million children hangs the game instead (the load job never finishes).
