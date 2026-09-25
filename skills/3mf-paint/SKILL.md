---
name: 3mf-paint
description: Edit per-filament paint inside a finished 3MF, and split a model by colour into separate parts for gluing. Straighten ragged colour borders, clean speckle, repaint zones, add a filament to a project; cut into parts with proper joints — a flat cut, a blind socket in both halves and a separately printed pin. Use when the user complains about uneven or jagged colour borders on a downloaded model, asks to paint a specific place with another filament, to remove paint artefacts left by a generator or by their own brush work, to check the paint before printing, and also when they want to print every colour as a separate part instead of filament changes and ask about the seam, the joint, gluing, pins and sockets. Triggers — paint, paint_color, colour borders, colour transitions, speckle, Color Painting, split triangles, AMS colours in a .3mf, split by colour, one part per colour, seam, joint, glue, pin, socket, paint_split; покраска, paint_color, границы цвета, цветные переходы, крап по всей модели после генерации, дроблёные треугольники, AMS-цвета в .3mf, разрезать по цветам, нарезка по цветам, отдельной деталью на цвет, шов, стык, склеить, штифт, ниша, закрасить другим филаментом, зубчатые границы, «проверь покраску перед печатью», «добавь пятый филамент в проект», добавить филамент, перекрасить зону, артефакты после ручной кисти, убрать крап.
---

# Paint inside a finished 3MF

**The geometry never changes here.** Only `paint_color` values change;
vertices and face indices are rewritten byte for byte. To change the *shape*
while keeping the colour, use `tools/writeverts.py` — it moves vertices without
touching face numbering. Anything that rebuilds the mesh breaks the colour
(skill **mesh-repair**).

Intermediate npz files and renders go in `work/`; only the finished file is put
next to the model.

Code formats, split triangles and the profile surgery for adding a filament:
[references/paint-format.md](references/paint-format.md). Splitting a model
into parts: [references/split-to-parts.md](references/split-to-parts.md).

## The precision limit

A colour border can only run along triangle edges, unless it was drawn with the
GUI brush, which splits triangles. On a generated mesh the average edge is
comparable to the nozzle line width, so **the border's precision is already at
the print's precision — do not chase more.**

A ragged border is not bad colour but a one-face zigzag. **Cure it by
shortening the border, not by repainting.**

## The working cycle

```bash
UV="uv run --quiet --with numpy --with scipy --with PyMaxflow python"
$UV tools/paint.py parse   models/part.3mf work/p.npz
$UV tools/paint.py explode models/part.3mf work/e.npz --object Cube
$UV tools/paint.py stats   work/p.npz
uv run --with numpy python tools/paintview.py render work/p.npz work/before.png \
    --eye 0,-150,14 --target 0,-20,14 --fov 30 --size 1100x900
$UV tools/paint.py smooth work/p.npz work/p2.npz --band 4 --lam 1.0 --core 3
$UV tools/paint.py write  models/part.3mf work/p2.npz models/part_clean.3mf
```

**Put the `stats` numbers into the answer**: border length before and after,
speckle patches removed, percentage of area repainted.

Smoothing minimises repainted area against border length, with the border cost
discounted on creases. That is why the border settles onto geometric edges — a
moustache silhouette, the edge of a brow, a hairline — instead of merely
blurring.

| Key | What it does | Start at |
|---|---|---|
| `--band` | how far the border may move, in faces | 4 |
| `--lam` | weight of attachment to the original paint | 1.0 |
| `--core` | depth at which a patch's core is pinned | 3 |
| `--splits` | `keep` leaves hand brushwork alone, `free` hands it to the graph cut | `keep` |

## Rules

**One filament is not one part.** Before editing "the zone of such-and-such
colour", decompose it into connected components, measure each, and **look at a
projection coloured by component**. Selecting by coordinates alone sends the
edit to whichever part happens to match the box, and bounding boxes do not
reveal the mistake.

**Despeckle before smoothing.** Smoothing ragged paint breaks large patches
apart, after which they fail the width threshold too.

**A colour fragment narrower than the nozzle line does not print.**
`paint_despeckle.py` measures effective width `w = 2·S/L` and gives the
fragment to the neighbour it shares the longest border with. Work in real
millimetres — pass the scale from `<build>` with `--scale`. A threshold around
1.3 line widths removes artefacts while leaving real small details; higher
thresholds start eating them. **Print the fragment list with widths before
removing anything.**

**Smoothing eats small decoration.** A detail a couple of millimetres across
has no faces at the required `--core` depth and cannot be protected that way.
Smooth everything, then restore the small filaments verbatim from the version
before smoothing; their own jaggedness is a fraction of the line width and does
not show.

**Bites are cured by morphological closing, not by smoothing.** A wedge biting
into a zone has a short border, so smoothing keeps it forever. Grow the region
by k faces and shrink it back: indentations narrower than 2k vanish and the
region itself does not change.

**Flood filling by creases leaks on organic shapes.** Creases on a smoothed
sculpt are soft, and a fill runs from a waistcoat over the whole figure. Apply
the same idea softly, as a discount in the energy, never as a hard barrier.

**Fused bodies leak too.** A hand and a holster are one shell, so a fill by
colour runs from arm to leg. Separate by geometry — a coordinate cut-off or a
normal direction.

**Turn a mesh for viewing by rotating it, never by swapping axes.** A
permutation is a mirror: the determinant is negative, every face winding
inverts, and the render shows the inside while looking plausible. Check with a
ray or with the paint, not by eye.

**Write a filament code on every face**, the background included. The
convention "no attribute means the object's extruder" is known only to the
slicer, and outside it such faces read as an arbitrary colour. `paint.py write`
still leaves untouched faces alone when editing someone else's file — a minimal
diff matters more; normalising a whole file is a separate deliberate step with
`paint_normalize.py`.

**Agree invented details by size before pinning them.** If a detail is not in
the original, show its size first.

**Success from the CLI proves nothing.** `--export-3mf` exits zero and returns
every colour on files the GUI refuses to open. **A human must open it in the
GUI**, and that has to be asked for plainly.

## Colour that arrived as a texture

Generator output carries colour as an image on a UV unwrap, not as
`paint_color`. **The generator bakes shadows into the texture, so zones do not
separate by brightness** — shaded skin is darker than lit hair. Use a
shading-independent feature instead: warmth, `(R − B) / brightness`.

Inside a dark region colour is useless — hair, cloth, shoes and wood differ by
single units. Isolate those by geometry and **verify by rendering, not by
numbers**.

Order: bake the colour on the ORIGINAL mesh, since only it has UVs; repair the
geometry; transfer with `paint_transfer.py`; smooth. Computing `stats` before
the repair is meaningless.

**Brightness alone cannot separate a raised detail from its background** —
highlights on a textured surface cross any threshold. Combine brightness with
relief height above a heavily smoothed copy of the mesh, computed at two
smoothing scales, taking the maximum: one scale loses thin details, the other
cannot see thick ones. Effective width then answers the real question, which is
**what will print at all** — anything below the nozzle line stays in the
background colour.

Texture highlights produce speckle larger than ordinary speckle, which
smoothing does not take. Remove it with a separate pass giving each undersized
patch to its longest-bordering neighbour.

**This is the one case where a border may end up shorter than the original's.**
That rule protects an author's drawing; paint derived from a shadowed texture
is ragged to begin with.

## Splitting into parts instead of changing filament

```bash
$UV tools/paint_split.py plan   work/p.npz --scale S
$UV tools/paint_split.py joints work/p.npz --scale S --plane-cut auto --emit work/joints.json
$UV tools/paint_split.py cut    work/p.npz work/parts --scale S --plane-cut auto
uv run --quiet python tools/pivot_joint.py work/joints.json
```

`--plane-cut auto` moves a jointed seam from the colour contour onto a plane;
**it must be identical for `joints` and `cut`, because it changes the mesh.**

**Say before starting whether the model is suitable at all.** A socket lives
inside the body of a part, and a colour smeared as a patch over the surface has
no body — `cut` reports the mean thickness of each part, and a thin crust means
there will be no joint there.

Joint rules and tolerances:
[references/split-to-parts.md](references/split-to-parts.md).

## Checks before handing over

1. Maximum coordinate deviation from the source is exactly zero. **Compare
   numbers, not md5** — the slicer rewrites line endings on save.
2. Round-trip through the slicer: per-code face counts match what was written.
3. The render has been opened and looked at; split faces draw bright pink.
4. Per-filament areas before and after are named. A colour dropping by percents
   means a narrow detail was eaten.
5. The user has been told they must open the file in the GUI.

## Presenting the result

`paintview.py` renders without the GUI; `pick` and `grid` report which face and
coordinates lie under a pixel, so a region is chosen by pointing at the picture
rather than by guessing coordinates. Show before and after as two renders from
one camera.
