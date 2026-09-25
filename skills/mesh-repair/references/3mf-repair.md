# Repairing a mesh inside a 3MF project

Read this when the thing to repair is a project rather than a bare STL. A
project carries the plate layout, secondary parts, the author's print profile
and possibly the paint; handing back one repaired STL throws all of that away.
The general technique for swapping pieces inside the archive is in
`3d-modeling/references/foreign-3mf.md`.

## Paint decides whether repair is possible at all

Paint is a `paint_color` attribute on every triangle, bound by the triangle's
index. **Any repair changes the number and the order of triangles, so the paint
becomes meaningless** — it cannot be carried over, because the faces it
referred to no longer exist.

A face without the attribute means filament 1. **Comparing the share of
"painted" faces before and after a repair therefore proves nothing** — count
the codes instead. And because only the slicer knows that convention, put the
background colour on explicitly after a repair and transfer; see **3mf-paint**,
`references/paint-format.md`.

Paint is restored by geometry rather than by index: `paint_transfer.py` takes
the colour from the nearest point on the old surface, `paint.py smooth`
straightens the border, `paint.py write` puts it back. The full route is in
SKILL.md.

Before repairing, ask whether the defect is worth it. When the defects are
finer than the nozzle, repainting is pure added risk.

## A mesh from GLB tears along UV seams — repair it without losing colour

Generators duplicate a vertex on a UV seam: same coordinates, different
indices, because the texture cannot be attached otherwise. Bambu Studio writes
that into the 3MF as is, producing counts far more alarming than the actual
state — hundreds of bodies and tens of thousands of open edges on an intact
shape.

```bash
uv run --with numpy --with scipy python tools/weldmesh.py in.3mf out.3mf
```

**Welding changes only indices, so face order is preserved and the paint stays
in place.** Patches for the remaining holes are appended at the end of the list
and take the colour of the neighbouring face. On a large painted mesh this is
the only way to repair connectivity — a voxel rebuild would cost all the paint.

The threshold is an exact coordinate match. **Loosening it does not help**: it
produces degenerate faces and extra non-manifold edges without closing a single
hole.

## Extract, repair, put back

```bash
python3 tools/meshfix.py project.3mf                       # -> project__objN.stl
python3 tools/meshfix.py project__obj1.stl -o fixed.stl
python3 tools/meshfix.py project.3mf --put 1 fixed.stl -o new.3mf
```

`--put` replaces the vertices and triangles of one object and corrects
`face_count` in `mesh_stat`. Plates, previews, secondary parts and the author's
`project_settings.config` stay as they were.

**`--info` does not prove the file will open in the GUI.** That is a separate
check, by a human at the screen — see `3d-modeling/references/bambu-cli.md`.

## `mesh_stat` is Bambu Studio's own repair log

Each object in `Metadata/model_settings.config` carries a `mesh_stat` line
reporting what Bambu Studio repaired on load. All zeros mean the author saved
from an already clean mesh, so the defects arose earlier, at the modelling
stage.

When swapping a mesh, correct `face_count`, or the GUI shows the old count.
The other fields are the author's.

## What Bambu Studio repairs silently

| Defect | Repaired on load |
|---|---|
| degenerate faces | yes, collapsed |
| reversed faces | yes |
| duplicate faces | yes |
| holes | no, reported as `open_edges` |
| non-manifold edges | no, reported as `non_manifold_edges` |

**The import warning is therefore a list of what Bambu could not repair.** What
it does repair never reaches the warning.

## What the hand brush loses

A composite code is the subdivision tree of one particular triangle, of its
vertices in its winding order. **It cannot be carried to another face under any
circumstances**, decoded or not, which is why `paint_transfer.py` excludes
split faces from the source and the target takes its colour from the nearest
single-colour face.

Sub-triangle precision is lost irrecoverably in any rebuild. After smoothing,
the border settles onto a geometric edge, which is the print's precision limit
anyway. **If there is a lot of precious hand work, that is an argument for not
repairing at all.**

## Traps specific to swapping

**Geometry can be edited without losing paint as long as face numbering is
untouched**: `writeverts.py` rewrites coordinates and leaves the triangle list
verbatim, so no colour transfer is needed. `--put`, repair and rebuilds all
break the numbering.

**The object may be sitting outside the bed.** "Nothing to be sliced, either
the print is empty or no object is fully inside the print volume" is a
placement problem, set in the GUI, not a mesh problem.
