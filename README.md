# bambu-3mf-tools

Command-line tools for the parts of Bambu Studio work the GUI does not reach:
reading and rewriting **per-triangle paint** inside a 3MF, repairing meshes
without losing that paint, cutting a model into printable parts with real
solids, and slicing from the CLI to get weight and time.

Written for a **Bambu Lab A1** and Bambu Studio on macOS. Most of it applies to
any Bambu machine, and to **Orca Slicer**, which is a Bambu Studio fork sharing
both the `paint_color` triangle format and `project_settings.config`. It is not
a general 3D modelling toolkit: 28 of the 30 scripts touch the 3MF container or
the slicer's presets.

## Why these exist

Bambu Studio paints a model by subdividing triangles and packing a filament
index into a bitfield on each subtriangle (`TriangleSelector::serialize`). That
representation is invisible in the GUI and fragile under every mesh operation:
repair a hole, weld a seam, or move a vertex with an ordinary tool and the paint
is silently lost or scrambled. The tools here read and write that format
directly, so mesh work and paint survive each other.

The second theme is honesty about what is actually printable. `figcheck.py`
casts rays to find where supports will really be needed, `gcode_report.py`
answers "are there supports in this file, yes or no" from the sliced G-code
rather than from the checkbox, and `solid_cut.py` verifies that the parts it cut
do not interpenetrate and still add up to the original volume.

## Layout — this matters

This repository **is** the `tools/` directory of a print project. Configuration
and scratch files live one level above it, and the code depends on that:
`hardware.py` resolves its config as `__file__.parent.parent / "hardware.json"`,
and `slice.sh` writes its output to `../work/out`.

```
my-print-project/
├── hardware.json      ← your machine and filaments (from hardware.example.json)
├── models/            ← your models
├── work/              ← scratch; slice.sh writes work/out/
└── tools/             ← THIS REPOSITORY
    ├── hardware.py
    ├── paint.py
    └── ...
```

So clone it into a project, not next to one:

```bash
git clone https://github.com/khmelevartem/bambu-3mf-tools.git my-print-project/tools
cp my-print-project/tools/hardware.example.json my-print-project/hardware.json
$EDITOR my-print-project/hardware.json
python3 my-print-project/tools/hardware.py     # prints what it resolved
```

`hardware.json` is the single source for nozzle diameter, layer height, line
width and preset names. No script keeps those as constants: change the nozzle in
the machine, edit `nozzle.installed`, and every tool measures by the new one.
For a one-off run with the other nozzle, without editing the file:
`A1_NOZZLE=0.2 python3 tools/figcheck.py …`

The preset names in `hardware.json` must exist in your Bambu Studio
installation, spelled exactly as its profile JSON spells them. Take the numbers
from the installed profiles with `resolve_profile.py`, not from the wiki.

## Dependencies

Nothing to install ahead of time. Scripts that need packages document a `uv`
invocation in their own docstring, e.g.

```bash
uv run --quiet --with numpy --with scipy --with trimesh python tools/paint.py …
```

| Needs | Scripts |
|---|---|
| stdlib only | `hardware.py`, `printcheck.py`, `gcode_report.py`, `patch3mf.py`, `resolve_profile.py`, `retune_project.py`, `set_extruder.py`, `paint_normalize.py` |
| `numpy` (+`scipy`) | most of the paint and mesh tools |
| `trimesh` | `balljoint.py`, `figcheck.py`, `make_multicolor_3mf.py` |
| `shapely`, `scikit-image` | `solid_cut.py`, `meshsolid.py` |
| `Pillow` | `refcompare.py` |
| Blender as a module (`bpy`) | `meshfix.py`, `pivot_joint.py`, `solid_cut.py` |
| Bambu Studio installed | `slice.sh`, `resolve_profile.py`, `figopt.py` |

`slice.sh` expects the macOS app bundle at
`/Applications/BambuStudio.app`; on Linux point `BS` at your binary.

## The tools

**Paint inside a 3MF** — the `paint_color` triangle format of Bambu Studio and
Orca.

| Script | What it does |
|---|---|
| `paint.py` | parse, measure, normalise and write paint; add a filament to a project; `explode` splits brush strokes into single-colour subtriangles |
| `paint_normalize.py` | write an explicit filament code onto faces that carry none — outside the slicer an empty code reads as the wrong colour |
| `paint_despeckle.py` | drop colour fragments thinner than one nozzle line: speckle and hairline tattoo artifacts |
| `paint_transfer.py` | carry paint from an old mesh onto a new one by geometry |
| `paint_split.py` | cost of splitting by colour, seams that are sound butt-joints, and a socket-and-pin plan |
| `paintview.py` | render the paint headlessly; `pick`/`grid` report what lies under a pixel |

**Mesh repair that preserves paint.**

| Script | What it does |
|---|---|
| `meshdoctor.py` | diagnosis by defect class, with the nozzle taken into account |
| `meshfix.py` | repair through headless Blender; `--put` swaps the mesh back inside a 3MF |
| `weldmesh.py` | weld vertices and close UV-unwrap seams without losing paint |
| `fixtjoints.py` | un-bake the T-joints the Bambu paint brush leaves, by fanning from the centroid |
| `meshsolid.py` | rebuild a torn mesh as a solid by winding number, where Blender's voxel remesh only adds a crust |
| `writeverts.py` | move vertices in a 3MF without renumbering faces — reshape with paint intact |

**Solid editing and splitting into parts.**

| Script | What it does |
|---|---|
| `solid_cut.py` | cut volume with proper solids: planes and cylinders, sockets and pins, an inlay under a painted zone on a curved surface; verdict "boss or separate part"; checks that parts neither interpenetrate nor fail to reassemble |
| `pivot_joint.py` | joints between parts: a cut across the axis, paired blind sockets with tolerances, separate pins, socket floor and wall checks |
| `balljoint.py` | **other people's** ball joints: find them by voting on normals, profile a socket with rays, restore a filled socket with a lathe-shaped cutter, assemble and measure interpenetration |
| `partedit.py` | edit a foreign 3MF part-wise — weld a piece on, fill a socket, saw off, drill — without losing paint |
| `graft.py` | rebuild a feature that was cut off: section, ribbon along an arc, faces appended at the end of the 3MF |
| `place3mf.py` | put an object back at the centre of the bed after an export moved it off the edge |

**Printability checks.**

| Script | What it does |
|---|---|
| `printcheck.py` | watertightness, surface kind, bounding box, overhangs. **Lies on foreign meshes** — use `meshdoctor.py` for those |
| `figcheck.py` | organic shapes: first-layer area, height of the bottom slice, supports found by ray casting, a stair-step map; `--rx/--ry` sweeps tilt |
| `refcompare.py` | overlay the model's projection on a photo and measure the deltas |

**Slicing and project settings.**

| Script | What it does |
|---|---|
| `slice.sh` | weight, time, G-code and a `.3mf` project; `--supports` slices with supports. Writes to `../work/out/` |
| `gcode_report.py` | sliced G-code broken down by line type, and a straight "supports: yes / no" |
| `figopt.py` | audit a project before printing: settings, colour changes, the price of flushing, facts read back from the G-code |
| `retune_project.py` | port print settings into a project a human saved in the GUI, without touching their paint |
| `patch3mf.py` | change keys in `project_settings.config` inside a 3MF; drop a filament from a project |
| `set_extruder.py` | assign filaments to objects by part name, after importing single-colour STLs |
| `make_multicolor_3mf.py` | build a painted project from a mesh plus an array of zones |
| `resolve_profile.py` | flatten a profile's `inherits` and `include` chain for the CLI |
| `hardware.py` | read `hardware.json`: nozzle, layer, line width, preset names |

## Caveats

- **In-code documentation is in Russian.** The scripts carry long docstrings
  recording what was measured, on which model, and which approach was tried and
  rejected. That is the most valuable part of this repository and it has not
  been translated.
- Numbers and tolerances were measured on one A1 with a textured PEI plate and
  Bambu PLA. Treat them as starting points.
- Bambu Studio's GLB importer merges all meshes into one object, so parts must
  be handed over as separate STLs.
- These tools write to your files. Keep a copy of anything you painted by hand
  before pointing a script at it.

## License

MIT — see [LICENSE](LICENSE).
