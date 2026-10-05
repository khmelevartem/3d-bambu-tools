---
name: mesh-repair
description: Repair a broken mesh before printing — holes, non-manifold edges, reversed faces, degenerate triangles, junk islands, bodies merely touching instead of merged. Also thin a mesh that carries far more triangles than the nozzle can print — the million-triangle output of an image-to-3D generator. Use when Bambu Studio complains on import about "non-manifold edges" or "reversed faces detected, which may affect rendering" and suggests a third-party repair tool, when a downloaded model will not slice or slices strangely, when crumbs appear next to the part in the preview, or when a mesh stopped being closed after boolean operations, voxel sculpting or an edit in Blender. Triggers — non-manifold, reversed faces, holes in the model, watertight, manifold, negative volume, STL repair, voxel remesh, meshdoctor, meshfix, too many triangles, simplify a mesh, decimate, reduce polygon count, lighten a generated model; non-manifold, дырки в модели, ремонт STL, битая сетка, слайсер ругается на сетку, крошки рядом с деталью, модель не режется, вывернутые грани, отрицательный объём, «после Blender сетка перестала быть замкнутой», булевы операции, воксельная лепка, watertight, manifold, почини сетку, слишком много треугольников, упростить сетку, прорядить сетку, убрать лишние треугольники, уменьшить число полигонов, облегчить сгенерированную модель, файл модели огромный, сетка после нейросети.
---

# Mesh repair

Diagnosis and repair are separate, because **many meshes the slicer complains
about need no repair**: the defect is smaller than the nozzle and will not
exist in plastic.

```bash
python3 tools/meshdoctor.py file.stl
python3 tools/meshfix.py   file.stl -o fixed.stl
```

`meshdoctor` is self-contained numpy. `meshfix` relaunches itself inside
Blender with `--factory-startup`, so it may be run while Blender is open and it
never touches user preferences; the 3D Print Toolbox addon is therefore
unavailable and unnecessary.

Intermediate meshes, npz files and trial variants go in `work/`; only the
finished file is put next to the model, and that is said out loud.

## Ask the size before asking the cure

A defect smaller than the extruder's strand cannot reach the plastic. Measure
size, not count:

| What is measured | Threshold |
|---|---|
| hole perimeter | below pi x nozzle — swept over |
| non-manifold edge length | below the nozzle — swept over |
| volume of a separate body | below 0.1 % of the main one — junk, drop it |

Thresholds come from the installed nozzle in `hardware.json`. To test against
another nozzle without editing the file, set `A1_NOZZLE`. `meshdoctor` applies
them and returns **MINOR** when everything is below threshold.

**Read the units before the verdict.** Inside a 3MF the mesh may sit in its own
units, with `<build><item transform=…>` converting to millimetres.
`BambuStudio --info` does not apply that matrix either, so both report a
bounding box far smaller than the real part — and a "smaller than the nozzle"
verdict then errs on the dangerous side. `meshdoctor` prints the scale on a
separate `!!` line.

## Order of work

1. `meshdoctor` on the original. It also reports whether there is paint.
2. **If the 3MF has paint, stop** and read
   [references/3mf-repair.md](references/3mf-repair.md): repair renumbers
   triangles and the paint is bound to that numbering.
3. Act on the verdict:

| Verdict | Action |
|---|---|
| **CLEAN** | nothing |
| **MINOR** | nothing, unless the slicer actually failed |
| **AUTO** | `meshfix.py in.stl -o out.stl` |
| **BY HAND** | organic shape — `meshfix.py … --remesh 0.2 --dropjunk`; a mating part — do not repair |
| **EMPTY** | there is no mesh in the file |

4. If there was paint, restore it by geometry:

```bash
UV="uv run --quiet --with numpy --with scipy --with PyMaxflow python"
$UV tools/paint.py parse original.3mf work/old.npz
$UV tools/paint.py parse fixed.3mf    work/new.npz
$UV tools/paint_transfer.py work/old.npz work/new.npz work/moved.npz
$UV tools/paint.py smooth work/moved.npz work/final.npz --band 8 --lam 1.0 --core 3
$UV tools/paint.py write  fixed.3mf work/final.npz ready.3mf
```

   Take `--band` twice as wide as usual: a rebuilt mesh is finer, so a given
   number of steps covers half the distance.

5. `meshdoctor` and `BambuStudio --info` on the result. A volume matching the
   original proves the topology was fixed and the shape was not.

## Too many triangles

A mesh out of an image-to-3D generator carries one to three million triangles
with an edge around 0.15 mm, against a 0.42 mm strand and a 0.20 mm layer.
Three to nine triangles then fall inside one extruded line and the slicer
averages them away.

```bash
uv run --quiet --with numpy --with scipy --with fast-simplification \
    python tools/meshsimplify.py in.stl -o out.stl
```

**The target is a deviation in millimetres, never a percentage.** Ten per cent
off a smooth ball is free; ten per cent off a face is a lost nose. The tool
bisects on the face count, measuring each probe two ways, and stops at the
default tolerance of a twentieth of the layer height.

The default has a second, independent justification: the stock A1 process
carries `resolution = 0.012` mm, the tolerance with which the slicer simplifies
the contour itself. A mesh finer than that is thrown away inside Bambu Studio
regardless.

**Thinning is paid for in print time, not in plastic.** The slicer fits arcs
(G2/G3) to a smooth contour and prints them at full speed; a contour of long
straight chords is not recognised as an arc and the head decelerates at every
corner. The plastic is identical to the hundredth of a gram, the time is not:
around a tenth of the original face count it grows by one or two per cent, and
it keeps growing as the mesh gets coarser. Thin for file size and for the
running time of the other tools, not for the print.

**A part that mates with another part is thinned with `--strict`.** On sharp
cut planes and sockets the fast engine welds T-seams and splits shells, and a
mesh that was closed comes back open — the report says so. The strict engine
refuses any collapse that would change the topology; it is an order of
magnitude slower and, on such a part, also the more accurate of the two. Check
the fit afterwards by cross-sections rather than by the bounding box: a socket
diameter is what a clearance is spent on.

**Thin before painting.** A collapse renumbers every triangle and the paint is
bound to that numbering. On a project already painted the route is
`meshfix.py --extract`, this tool, `meshfix.py --put`, then `paint_transfer.py`.

**A generator mesh is over-tesselated, not noisy.** Twenty Taubin passes move
its vertices by hundredths of a millimetre, which is what a smooth surface
does. Quadric collapse keeps curvature and does not straighten anything, so
"make the shape cleaner" is not what this tool is for, and there is normally
nothing to smooth.

**Repair first, thin second.** On a mesh full of junk islands the collapse
spends its budget on the junk.

## Defect classes

| Defect | What it is | Cure | Cost |
|---|---|---|---|
| open edge | a hole in the shell | `--holes` | the patch is flat and shows on a curved surface |
| T-joint | a foreign vertex sits on an edge | `fixtjoints.py` | none, the paint code is inherited |
| non-manifold edge | an extra sheet glued on, or two bodies fused along an edge | voxel rebuild only | details finer than the voxel, new topology |
| inconsistent winding | a face turned inside out | `--normals` | none |
| degenerate face | zero area | `--degenerate` | none |
| duplicate face | two identical faces | `--dedup` | a hole may open beneath |
| junk island | a small separate body | `--dropjunk` | none |
| bodies merely touching | pieces in contact, not united | rebuild, or unite in the source | the wall at the joint is two halves without fusion |

`meshdoctor` counts bodies through all edges and through manifold ones
separately; a discrepancy between the two counts means bodies merely touching.

## Rules

**Collapse a degenerate face, never delete it.** Deleting takes a shared edge
from its neighbours and opens a hole that was not in the file.

**In 3MF, OBJ and OFF, connectivity is the author's indices — never weld
vertices.** Welding stitches separate sheets into non-manifold edges that do
not exist. Only STL, which stores three vertices per triangle, needs welding to
recover connectivity at all.

**`BambuStudio --info` reports the already-repaired file.** It collapses
degenerate faces, fixes reversed ones and removes duplicates on load, as
Blender does on import. A discrepancy with `meshdoctor` is the list of what the
slicer fixes for you, not an error; compare volumes instead.

**`manifold = yes` does not mean closed.** That flag covers only non-manifold
edges; holes appear on a separate `open_edges` line. Read both.

**A brush-painted file tears itself, and mends for free.** Saving a project
bakes strokes into geometry: painted triangles are split, their neighbours are
not, and a foreign vertex is left on the shared edge. The shape is intact and
the volume correct while the counts look catastrophic. Reaching for a voxel
rebuild there costs all the paint. Tell it apart and fix it:

1. `meshdoctor.py` — many open edges, no non-manifold, shape intact;
2. `weldmesh.py … --no-fill` — zero vertices collapsed, so these are not UV
   seams;
3. `fixtjoints.py in.3mf --dry`, then without `--dry`;
4. `meshdoctor.py` again. Remaining holes are real ones for `weldmesh.py`.

The `fixtjoints.py` tolerance is in file units and is deliberately tight.
**Do not widen it blindly** — past a narrow margin it stops meaning "a vertex
on the edge" and starts meaning "a vertex nearby". The symptom is a non-zero
`дублей граней` in the output line, with open edges left over: a nearby vertex
fanned into a face folds it back onto its neighbour. Coordinates are written
with six decimals, so a real T-vertex sits on its edge to about 1e-6.

**Repair in one pass, then compare — never stack passes.** Hole filling on a
torn rim leaves slivers and T-seams; the next pass's degenerate-face cleanup
opens fresh open edges, those get stitched in turn, and non-manifold edges
accumulate. Run a single pass, take `BambuStudio --info` after it and after any
further pass, and **keep the best result, not the last one**. Ranking:
`manifold = yes` first, then fewer bodies, then fewer open edges — the slicer
closes open edges by itself, non-manifold edges it does not.

**A mesh torn by the slicer's own cut and split tools is not a broken
original.** Editing geometry in the GUI leaves hundreds of three-face islands
and thousands of open edges at an unchanged volume. Repair it as any torn mesh,
and cut with the project's own tools instead.

**Pointwise repair never cures a non-manifold edge.** Hole filling, vertex
merging, normal recalculation and general-purpose mesh libraries have no such
operation. Only a rebuild from the volume does, because the surface is then
closed by construction.

```bash
python3 tools/meshfix.py in.stl -o out.stl --remesh 0.2 --normals --dropjunk --smooth 3
```

**`--smooth` is mandatory after a voxel rebuild.** The rebuild leaves a ripple
of half a voxel: invisible in plastic, conspicuous on screen on a sculpted
surface. Three Laplacian passes remove it at a cost of tens of microns.

Voxel size: half the nozzle for figurines, finer when small texture matters and
memory allows. **Above 0.25 mm edges start to disappear.**

**Never run `--normals` before a rebuild on a torn mesh.** Recalculating
normals on a soup of open pieces can invert the whole model.

**On a torn mesh Blender's voxel remesh produces a crust, not a body.** It
reports zero open and zero non-manifold edges while the volume is a tiny
fraction of the real one, and the model shows through like lace. Diagnose by
running it twice: **the volume of a crust grows linearly with voxel size**,
while a real body's volume barely depends on it. Filling holes first does not
help.

The cure is `tools/meshsolid.py`, which decides the interior by winding number
along three axes with a majority vote, and places the surface with a signed
distance field rather than binary occupancy. It requires consistent winding
inside each piece. **Its voxel is given in the units of the input** — a mesh
taken straight from a 3MF is in file units, so real millimetres must be divided
by the scale from `<build>`.

**Never voxelise a part that mates with another part.** A rebuild steals tenths
of a millimetre of bounding box, which is a large share of a fit clearance.
Voxels are for organic shapes, where size is judged by eye; a mating part is
repaired in the model source instead.

**Removing duplicate faces can open holes.** Always compare `meshdoctor` before
and after and keep the better result, not the later one.

**To edit shape without losing paint, move vertices only** — `writeverts.py`
keeps face numbering, and everything else requires a colour transfer.

**Slice as is before repairing.** The slicer's import warning is a list of what
it could not fix itself, not a ban on printing.

**Fix geometry before measuring paint.** On a torn mesh the patch counter sees
broken connectivity rather than spots of colour, so speckle statistics are
meaningless and chasing them is harmful.

**A voxel rebuild shallows out narrow grooves, and paint hidden on their walls
surfaces.** This is a resolution limit, not a paint error: a groove narrower
than two voxels cannot survive. The cure is voxel size or a touch-up with the
brush — smoothing the paint makes it worse, because the graph cut profits from
merging the two dark regions.

**Smoothing erases a fine repeating pattern** — stripes, checks. A stripe one
or two faces wide has more perimeter than area, so erasing it is always
profitable at any weight. The choice between clean borders without the pattern
and the pattern with a staircase belongs to the owner: show both as renders and
ask. Do not look for a middle ground; it produces gnawed remnants, worse than
either extreme. **The sign that smoothing must be switched off is a resulting
border shorter than the original's.**

**An extra body is not automatically junk.** It may be a real part standing
apart: check the distance between bodies, not only their count. A gap narrower
than the nozzle line never fuses, and the part would print separately and fall
off with the supports. The cure is moving that body's vertices into overlap —
the slicer merges intersecting bodies itself.

**A deliberately grafted piece is also called junk by the verdict**, which
counts volume and does not know the bodies intersect. Tell them apart by
overlap, not by size, and never run `--dropjunk` on a graft.

**Add `--dropjunk` after every rebuild** and confirm the body count; a rebuild
leaves zero-volume crumbs.

**Compute body connectivity on an STL only after welding by coordinate**, or
the parse returns thousands of false one-face bodies.

**Verify repair code taken from the internet by running it.** Popular snippets
call methods that no longer exist, and the libraries they need are not in the
usual install line.

**Widening the weld tolerance does not cure non-manifold edges; it breeds
them.** On a generated figurine 19 non-manifold edges became 22, 54 and 90 as
the tolerance went 0.005, 0.02, 0.05 mm, while the volume never moved. Welding
answers "separate sheets that should be one", not "three faces on one edge".

**What does cure a handful of non-manifold edges is local surgery.** Delete
every face whose centre lies within about one nozzle line of the defective
edge's midpoint, then fill the hole and recompute normals. On the same figurine
a 0.4 mm ball around each of 19 edges took out 467 faces and left the mesh
closed and manifold, with the volume unchanged to the first decimal. A wider
ball is worse, not safer: at 0.8 mm the patch itself tore open again. Do this
on a local copy and compare the counts; it is a last resort after the ordered
route above, and it does change the shape inside the ball.

**Pick the boolean solver by measurement, and check that it ran at all.**
Blender's manifold solver refuses a mesh that is not manifold and silently
returns the input: the face count and the volume come back identical, no error
is raised, and the "result" is the untouched original. Compare both numbers
after every boolean. The exact solver always runs, but on a dense organic mesh
it leaves two orders of magnitude more non-manifold edges than the manifold
solver does on the same union (7035 against 42 on a 1.6-million-face figure).
So: repair the inputs, use the manifold solver, and keep the exact one as the
fallback for inputs that cannot be made manifold.

**A boolean between a part and the body it was cut from is not a check.** They
share whole coplanar surfaces, and the exact solver returns nonsense there —
intersections came back as negative volumes on parts that were provably fine.
To ask "does this part stick out of the original", sample its vertices and take
the signed distance to the original mesh.

## Checking the result

1. `meshdoctor`: verdict CLEAN, and as many bodies as intended.
2. Volume compared with the original. Off by more than a percent, find out why:
   closing holes adds body, recomputing normals fixes the sign, welding pulls
   the surface in. If none of those explains it, the repair damaged the shape.
3. Bounding box: tens of microns after a rebuild is normal, tenths of a
   millimetre are not.
4. `BambuStudio --info`: `manifold = yes`, no `open_edges` line — a second
   opinion from the slicer's own parser, where `meshdoctor` reads the file's
   indices. Costs nothing, and is skipped where Bambu Studio is not installed.

**Do not close a repair with a slice.** It answers none of the questions above:
a green CLI run does not prove the file opens in the GUI, and the weight is the
person's to read in their own slicer. Slice only when the answer being written
claims something about supports or about the print itself.

## Reference material

- [references/3mf-repair.md](references/3mf-repair.md)
