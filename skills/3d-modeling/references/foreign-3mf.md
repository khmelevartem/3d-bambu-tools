# Reworking a downloaded model

**A foreign project is edited by swapping pieces inside the original archive,
never by reassembling it from scratch.** Only a file whose shell Bambu Studio
itself wrote is known to open in the GUI.

## Swapping the mesh

If something has to be attached to a foreign mesh while keeping it compatible
with the rest of a set, start with [mesh-measuring.md](mesh-measuring.md):
measuring comes before boolean operations and decides what is possible.

Take the mesh out of `3D/Objects/object_N.model`, edit it, and write it back
**into the same XML shell**, correcting `face_count` in the metadata and in
`mesh_stat` inside `Metadata/model_settings.config` — without that the GUI
shows the old face count. Other parts, the plate layout and the preview stay as
they were.

`tools/meshfix.py --put N mesh.stl` already does that half. If the geometry is
edited **by moving vertices** without changing the triangle order, use
`tools/writeverts.py` instead: it preserves face numbering, and with it the
paint.

- **Hand over the original archive with the mesh swapped in.** Do not assemble
  a project with the CLI and do not hand over a bare STL when the project has
  other parts and a plate layout.
- **Exchange geometry through OFF rather than STL.** OpenSCAD writes OFF as an
  indexed mesh, so float32 precision and topology survive. (It appends RGB to
  each face; skip the extra numbers when parsing.)
- **Check closedness from the OFF indices**, not with `printcheck.py` on an
  STL, which gives false positives on imported meshes.
- **Verify the rework against the original**: a sample of interior points must
  match everywhere except the edited zone, and a new tenon or socket must match
  the originals in the local frame of the end face.

## Editing by parts, without touching the mesh at all

The cheapest way to change a foreign model is to add a **part** to the object
rather than cutting its mesh. A `normal_part` adds volume, a `negative_part`
subtracts it, and both live as separate meshes. **The author's mesh stays byte
for byte, and so does the paint** — `paint_color` is bound to the triangle
index, and any boolean loses it.

```bash
python3 tools/partedit.py list  project.3mf
python3 tools/partedit.py apply job.json
```

Format rules, checked against the slicer source and confirmed by slicing:

- The part type is the `subtype` attribute on `<part>`: `normal_part`,
  `negative_part`, `modifier_part`, `support_blocker`, `support_enforcer`.
  **An unknown string is silently read as `normal_part`** — a typo here is not
  an error but a quiet failure.
- `<part id=…>` finds its mesh by matching the id of a component's `objectid`,
  not by position. A part with a foreign id also becomes `normal_part`.
- **Order decides**: a negative volume is subtracted only from parts listed
  above it. Append new parts at the end of both `<components>` and `<object>`.
- **Do not cut a flat bottom with a part.** Sink the object instead — the
  slicer prints nothing below the bed. A cutter box additionally drags the
  bounding box down and the part looks sunken in the GUI.
- **Previews do not show negative parts.** Verify such an edit by slicing only:
  layer profile, height, filament changes, supports. A first-layer area far
  below the expected one means the part is hanging above the bed, not that the
  cutter failed.

## Grafting a missing piece

A different task from swapping a mesh: a feature on the author's model is **cut
off** — a strap stops short, a handle is broken. Rebuilding the whole mesh for
that is not allowed when the model is painted.

**New faces are appended at the end of `<triangles>`**, the numbering of the
old ones does not change, and the paint stays in place face for face. The new
piece lives in the same object as a separate closed shell and simply overlaps
the existing bodies — **no boolean union is needed**, because the slicer merges
intersecting bodies of one object itself.

```bash
UV="uv run --quiet --with numpy --with scipy python"
$UV tools/graft.py section model.3mf --at x,y,z --dir dx,dy,dz --scale S
$UV tools/graft.py ribbon  model.3mf work/r.npz --at … --dir … --up … --to … --to-dir …
$UV tools/graft.py put     model.3mf work/r.npz new.3mf --filament 1
```

- **Take the profile from the mesh itself, never invent it.** A capsule of
  measured width and thickness leaves a visible step at the joint, because a
  real strap has a flattened section sitting off-centre on the body. `section`
  cuts across the axis and returns the contour as it is; `ribbon` sweeps
  exactly that.
- **Both ends must run inside existing bodies** by a millimetre or two. The
  start goes into the depth of the broken feature, not against its end face;
  the finish goes inside the second support.
- **Prove the merge by slicing, not by eye**: `Inner wall` in the G-code must
  not grow. If it grew, the bodies did not intersect and the slicer is printing
  a wall inside the part.
- **`meshdoctor.py` will then honestly call it a junk island**, because there
  are now two bodies and the second is tiny. That is a false alarm in exactly
  one case: the piece was added deliberately and overlaps the main body. Check
  the overlap and **never run `--dropjunk`** on it.
- **A spanning ribbon is an overhang.** A nearly horizontal graft sags without
  supports. Check `enable_support` in the project **before** the work and name
  the number.

Find the `--at` and `--to` points by sections and by pointing at a render.
Horizontal sections at several heights show a broken feature as a separate
small contour: its centres give the axis, a neighbouring pair gives `--dir`,
and the height at which the contour disappears is the broken end face.

## Swapping the print profile

A downloaded project carries its author's print profile in
`Metadata/project_settings.config`, often for a different printer, and the GUI
then refuses to print it while the geometry opens normally.

### What works and what only looks like it works

**Feeding system preset files to `--load-settings` is useless.** The CLI
accepts them, exits zero, and changes the profile names in the output — while
the values stay the author's. Those files are deltas over `inherits` and
contain a dozen keys; only what is at their top level gets applied. **The sign
of a hollow swap is a new profile name with the old `layer_height` and
`line_width`.**

Flatten them first with `tools/resolve_profile.py`:

```bash
python3 tools/resolve_profile.py "<machine preset>" work/m.json
python3 tools/resolve_profile.py "<process preset>" work/p.json
python3 tools/resolve_profile.py "<filament preset>" work/f.json
"$BS" --load-settings "work/m.json;work/p.json" \
      --load-filaments "work/f.json;work/f.json" \
      --export-3mf out.3mf --outputdir "$PWD/out" foreign.3mf
```

**Even with flattened profiles the CLI is not equivalent to switching the
printer in the GUI.** Differences from the system profile remain, and two of
them are not cosmetic:

- the machine acceleration limits are written by the CLI itself — neither the
  author's values nor the target printer's;
- **`enable_prime_tower` is forced to 0**, even when the loaded process and the
  original project both had it on. For a multicolour print that changes the job
  entirely.

So: **the human switches a foreign project's profile in the GUI** — open,
change the printer, save. The CLI is fine for a single-colour part and for
measurements, not for a print-ready multicolour project.

**Verify a swap key by key against the flattened system profile**, never by
searching for the printer's name as a substring: the start G-code is full of
coordinate strings that match such a grep by accident.

**The profile file cannot simply be deleted.** It is referenced nowhere, yet
without it the loader crashes. The idea that the application will substitute
its own does not work.

**Swap in a profile written by Bambu Studio itself**, not one assembled by
hand — it carries hundreds of interdependent keys. Obtain one by slicing any
part and extracting `Metadata/project_settings.config` from the result. Then
lay the author's part-specific choices on top — supports, perimeters, tower —
and keep the target printer's line widths and speeds. **Check
`curr_bed_type`**: the CLI writes a default plate type that heats the bed far
below what the filament needs. If the profile has one filament while the author
declared two, fix `filament_maps` in `model_settings.config`.

### Values are not enough — `different_settings_to_system` is required

**The GUI does not read the values straight through.** It takes the system
preset named by `print_settings_id` and overlays only the keys listed in
`different_settings_to_system` — an array with one entry per preset: process
first, then filaments, printer last, keys separated by `;`.

**If that list is empty, everything else in the file is decorative.** A project
can carry supports enabled while the GUI shows them off, because the list was
empty and the system preset won. **The CLI does not perform this check** and
slices from the flat config, so slicing does not catch the error.

**Build the list from your own edits rather than writing it by hand**: a key
belongs in it when it exists in the process preset and its value differs from
the system one. Keys that come from the project rather than from the process
preset then drop out by themselves.

```python
proc_keys = set(json.loads(pathlib.Path("tools/profiles/process-0.4.json").read_text()))
changed = sorted(k for k, v in OVERRIDE.items()
                 if k in proc_keys and base.get(k) != v)
dss = list(base.get("different_settings_to_system") or [""])
dss[0] = ";".join(changed)
```

Any downloaded project is a reference for this: its author edited settings in
the GUI, and Bambu Studio filled the list in.

### What this check does not cover

Slicing a file **with its own embedded profile** and reading `printer_model`,
`curr_bed_type` and the bed temperature from the G-code proves the profile is
self-consistent. It proves **neither that the file opens in the GUI, nor which
checkboxes a person will see there.** Both need a human at the screen — ask
them to look, and say exactly what to look at.

## The import dialog decides whose settings apply

On importing a foreign 3MF, the choice between importing only the geometry, the
model with its profile, or only the profile determines whose settings win.
