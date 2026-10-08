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
not show. A long narrow strip behaves the other way: it vanishes at any
`--lam`, and pinning its core with `--core` is what keeps it. When a colour
loses area after smoothing, look for such strips first.

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
Bambu Studio's own split of a textured GLB into filaments merges neighbouring
tones — a silver chain into a grey sweater — so check every zone it returns.

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
cannot see thick ones. Keep or drop a relief component by its highest point,
not its average: the rims of torn holes in cloth rise as a low band, a real
raised detail stands above them. Effective width then answers the real
question, which is **what will print at all** — anything below the nozzle line stays in the
background colour.

Texture highlights produce speckle larger than ordinary speckle — up to about
ten square millimetres — which smoothing does not take. Remove it with a
separate pass giving each undersized patch to its longest-bordering neighbour,
raising the area threshold in steps.

**This is the one case where a border may end up shorter than the original's.**
That rule protects an author's drawing; paint derived from a shadowed texture
is ragged to begin with.

## Splitting into parts instead of changing filament

### Principles — they outrank every recipe below and in the reference

1. **Cut along real physical bodies, the way the thing would be assembled in
   the real world.** A boot comes off a leg, an eye sits in a socket, a
   microphone is held in a fist. Ask what the separate pieces would be if the
   object were manufactured, and cut there — not wherever the paint happens to
   change.
2. **The seam is as smooth as the relief allows; the paint only advises.**
   Paint on a generated or hand-brushed model is coarse and its border is
   ragged. Leaving the painted border by a few lines for a seam that is
   physically right and smoother is the correct trade, not a loss — name how
   much colour moved, but do not keep a jagged seam to save it.
3. **There are two kinds of cut, and every part is one of them.**
   - **Surface and pin.** The mating surface need not be flat — it follows
     the border — but it is smooth like a soap film: no steps, no creases, no
     sharp changes of slope, so the printed parts differ from the model as
     little as possible and seat against each other. Decide how each part
     prints *before* placing the pin: when the cut is a plane that also serves
     as the part's footing on the bed, make the pin a separate body and drill
     blind sockets into both parts. **A part that must turn is cut on a plane**,
     always.
   - **Inlay.** Something small laid on top or set into a pocket — brows,
     eyes, a badge. No pin. It needs a **flat bottom to print on**, so that its
     side walls stand vertical and come out smooth for the pocket.
4. **A simple body is rebuilt from geometry, not cut out of the mesh.** Round
   eyes, a microphone as a solid of revolution, a cylindrical chair leg:
   rebuilding them controls the shape exactly and removes the asymmetry that a
   generated mesh almost always has. Fit the primitive to the mesh, show its
   dimensions, then use it as both the part and the cutter.
5. **One logical part per pass, then the human checks it.** Brows — check.
   Eyes — check. Boots — check. Several coloured parts go in one pass only
   when their assembly has to be designed together — teeth, tongue and palate
   inside a mouth. **Never cut the whole model at once.** Each pass ends with
   renders of the new parts and the remaining body, the cut surface and the
   joint, and waits for the answer before the next part.

### Tools

```bash
$UV tools/paint_split.py plan   work/p.npz --scale S
$UV tools/paint_split.py joints work/p.npz --scale S --plane-cut auto --emit work/joints.json
$UV tools/paint_split.py cut    work/p.npz work/parts --scale S --plane-cut auto
uv run --quiet python tools/pivot_joint.py work/joints.json
```

Relief on an organic body — hair, a beard, a moustache, a ring in an ear — is
cut by `relief_cut.py`: `sheet` for a zone lying on the surface, `drape` for a
relief overhanging it, `ring` for a ring rebuilt as a body of revolution,
`check` for intersections, removal sweeps and where the paint ended up.

```bash
RC="uv run --with numpy --with scipy --with trimesh --with manifold3d --with shapely --with mapbox_earcut --with scikit-image --with pymeshfix --with rtree python tools/relief_cut.py"
$RC sheet --paint work/p.npz --labels work/parts.npy --zone 1 --axis 0,0.5,0.87 --plane 48.3 --out work/hair
$RC drape --paint work/p.npz --labels work/parts.npy --zone 3 --body work/hair_rest.stl --axis 0,-0.77,-0.64 --out work/must
$RC ring  --paint work/p.npz --zone 1 --at X,Y,Z --at X,Y,Z --same --lip 3.2 --body work/must_rest.stl --out work/gauge
$RC check work/gauge_rest.stl work/hair_part.stl@0,0.5,0.87 work/must_part.stl@0,-0.77,-0.64 --src work/body.stl
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
