# Splitting by colour into separate parts

Instead of changing filament inside one print: one part per colour, each in its
own filament, glued afterwards. The prime tower and all the flushing go away.
In exchange a seam appears at every colour border, and the whole question is
**what holds that seam**.

A lid against a lid holds nothing. Both are printed in layers, each has its own
stepped surface, and glue lets them set a millimetre out of place. Only two
joints work.

## The two joints

**A flat cut across the part, a blind socket in both halves, a pin.** The
mating faces are plane against plane. The socket is a rectangular pocket in
each half; the pin is a separate body, or grafted onto one half.

| Tolerance | Value | Why |
|---|---|---|
| pin cross-section below socket | **0.20 mm** (0.10 per side) | enters without rattling |
| pin shorter than the two depths together | **up to ~1.4 mm** | otherwise the pin bottoms out before the halves meet and leaves a gap on the visible joint |

**Use a rectangular cross-section, never round.** A round pin leaves the parts
free to rotate about it.

**A prismatic inlay in a pocket**, where the colour lies as a patch on the
surface. Outside it is the model's original surface; inwards it runs as a
**strictly prismatic** side wall with a flat bottom, and the mating part
carries the same pocket. **The pocket wall holds the inlay, not the glue.**

## A part is a slice of the solid, not a crust

**Every part is the intersection of the original solid with half-spaces and
cylinders.** Then the parts sum back to the original solid and a socket is
always drilled into material.

Peeling the surface of one colour and capping it produces a shell with a hollow
inside; a socket placed in it misses the material entirely. `solid_cut.py check`
proves the assembly with three numbers:

- pairwise intersections between parts are zero, or they cannot physically
  meet;
- the union of the parts does not protrude beyond the original solid;
- the volume deficit equals the arithmetic of the clearances — **"roughly
  similar" is not a pass.**

## Internal surfaces come from numbers, not from the paint border

A brush-drawn border wobbles, and cutting along it puts the same wobble into
the joint. **Fit a primitive to the border and cut with the primitive.**

```bash
$UV tools/paint.py explode project.3mf work/e.npz --object Sphere
$UV tools/solid_cut.py fit work/e.npz
python3 tools/solid_cut.py cut   work/spec.json
python3 tools/solid_cut.py check body.stl part*.stl
```

`fit` reports, per border loop, a normal, an offset, the **rms from its own
plane** and the spread of the radius. Read it as: rms below the layer height
means a plane will cut it; radius spread below a tenth means a circle, so use a
cylinder.

**`fit` cannot see a border running along the model's own edges**, because
there is then no pair of adjacent faces of different colours anywhere. Such a
plane has to be derived — and **named out loud as a derivation, not as a
measurement.**

## Decide the assembly order before placing pins

**Every part must have exactly one direction along which it goes into place.**
Check this before the pins, never after: a vertical pin into the base plus a
horizontal pin into a neighbour would require inserting the part in two
directions at once. Moving a pin to another face of the joint fixes it for
nothing; re-cutting afterwards does not.

## When the colour comes from overlapping bodies

A model may be coloured not with the brush but as several overlapping bodies,
each with its own extruder in `model_settings.config`. **The colour of a point
is set by whichever body comes later in the list.**

The bodies live in `3D/Objects/object_N.model`, and their placement is the
`transform` on `<component>` in `3D/3dmodel.model`. **Do not take the matrix
from `model_settings.config` for geometry** — it disagrees with the real one.

**Cut by colour, not by bodies.** Two overlapping bodies of the same filament
are one part with no seam between them. Union the same-coloured bodies first,
then subtract the later bodies of other colours.

**Compute that union from the meshes, not from freshly built primitives.** A
cutter cylinder and a mesh cylinder of different segment counts almost coincide,
and the boolean solver returns garbage on such surfaces.

## An inlay in a curved surface

A stroke drawn on a cylinder cannot be pulled out of a pocket with radial
walls — one side always locks. **Enlarging the model does not fix this**: the
zone's sweep along the arc does not depend on scale. Scale only fixes the width
of the stroke.

What works:

- the extraction direction is the **mean normal of the zone**, one for the
  whole zone;
- the pocket walls are a prism along it, the bottom a plane perpendicular to it;
- the depth at the deepest point is the edge thickness **plus the zone's extent
  along that direction**, or the inlay tapers to nothing at the edges.

The price is a variable inlay thickness, which is acceptable: the requirements
are that the visible wall is clean and the part is strong enough to be pulled
off its supports and pressed in by hand.

**Never cut a drawn stroke into segments along the arc.** It would reduce the
sweep per piece, but it puts seams across the visible pattern, which is the
whole point of the exercise. One stroke is one part, however wedge-shaped it
comes out. If the wedge genuinely will not work, there are two ways out, and
the first needs asking: enlarging the model is a last resort, because its size
is fixed either structurally or by proportion with other pieces. The second is
to leave the zone as paint with a filament change.

Clean the brush contour first — its teeth are microns and there is no reason to
carry them into plastic. `solid_cut.py inlay` does this, reports the zone width
in nozzle lines, and complains when the zone wraps past the extraction
direction.

```bash
$UV tools/paint.py explode project.3mf work/e.npz --object Cylinder
uv run --with numpy --with scipy --with shapely --with mapbox_earcut \
       --with scikit-image python tools/solid_cut.py inlay work/e.npz \
       --filament 4 --scale 2 --out work/parts/stroke
```

## Cut a zone, or leave it as a boss?

An island zone has no choice — it is an inlay. When a zone **touches a body of
the same colour**, it could instead stay as a boss on the neighbouring part.
**By default do not cut**: an extra seam inside one colour is a visible line
that was not there.

Four checks, in order. `solid_cut.py inlay --assembly` computes the first two.

| # | Check | Threshold | What failure means |
|---|---|---|---|
| 1 | extractable along the assembly direction `a` | `min(n·a) > 0` | part of the zone faces backwards; the neighbour will not go on |
| 2 | blade along the contour | share of area with `n·a < 0.5` below 5 % | the prism wall meets the surface tangentially and leaves a blade along the contour |
| 3 | where the boss points while its owner prints | not into the bed | otherwise the part must print dome-down on supports over the whole visible surface |
| 4 | if cutting anyway: where the new seam lands | on an existing seam | a new visible line inside one colour — **ask, do not decide** |

The blade angle is `arcsin(n·a)`. At 30° a blade thinner than the nozzle line
runs 0.7 mm from the contour, which is tolerable; at 6° it runs 4 mm and
crumbles on first assembly.

## Printing cut parts

**The fit is held by the side face, so lay the part flat**: the flat pocket
bottom on the bed, the extraction direction up, the visible face up. Then the
prism walls stand vertically and come out smooth.

Standing a part on its edge to protect the face gives a staircase on the side
face instead, and a staircase against a staircase binds.

**Do not lay a curved face down** — on the bed it rests on a point.

The price of flat is that the visible face is on top. A flat face prints
perfectly that way. A **curved** face gets stair steps, and **that is cured by
layer height, not by orientation**: such parts are small, so a separate fine-
layer print, a height-range modifier, variable layer height or a 0.2 nozzle all
cost little.

**Hand the parts over already rotated for printing**, not in their original
orientation. Naming the orientation in words is not enough.

### Clearances

| Clearance | Value | Why |
|---|---|---|
| sideways, per side | **0.20 mm** | 0.30 shows as a visible gap |
| depth | **0.05 mm** | it is entirely how far the inlay sinks below the surface |
| sideways if the inlay must print as a staircase | 0.30 mm | the staircase eats the difference |

**The depth clearance is not the side clearance.** The inlay is stopped by the
pocket floor, so however much deeper the floor is, that is how far the part
sinks below the surface.

**The fit must be sliding plus glue, never a press fit.** Pressing loads the
wall across the layers, the weakest axis in FDM.

### The neck of an inlay: measure cross-section, not width

A narrow waist in the zone breaks in the hand, during removal from the bed or
during insertion. **Width alone does not predict it** — a respectable width
percentile can hide a thin neck.

`inlay` finds the neck honestly: the erosion radius at which the zone falls in
two, and the thickness there. It distinguishes two failures:

- the smaller piece is under 10 % of the zone — **a tip breaks off**, tolerable;
- the pieces are comparable — **the part breaks in half**, unacceptable.

The threshold is a neck cross-section of 5 mm². **Cure it with thickness, not
width**: the width is the drawing and must not be touched, while the thickness
hides inside the pocket. Raise `--edge` until the neck clears the threshold,
then **re-measure the walls** to neighbouring sockets, because the pocket is
now deeper.

### Infill under a socket

Spiral and concentric patterns run along the wall and touch it at one point,
leaving it unsupported across; pressing an insert in splits a layer beside the
socket.

**A part something is pressed into must use a lattice infill** — Cubic, Grid or
Gyroid. No density setting rescues a concentric or spiral pattern here.

### A pin is grafted on only if it does not break the orientation

**Decide each part's print orientation first, the pin second.** A boss may be
grafted on when it points up or sideways. When it points down — as it does on
any half that must lie on its cut plane — the pin becomes a separate part, and
that is not a defeat.

**Find the pin's place by measurement, not by eye.** An obvious central
position can leave a fraction of a millimetre of wall to a neighbouring pocket,
because pockets run deeper towards the centre than they look. Sweep a grid of
centres and depths and take the one with a real wall.

## Three seam archetypes

| Archetype | Sign | What to do |
|---|---|---|
| a separate shell | the colour occupies a whole mesh body | nothing — it is already a part |
| a flat joint | the seam loop nearly lies in a plane | cut on that plane, socket both sides, pin |
| a patch inlay | one loop, the piece faces one way | prismatic pocket |
| "by colour" | the loop wanders, there is no plane | nothing straightens it, only glue — **name that price out loud** |

## The working cycle

```bash
UV="uv run --quiet --with numpy --with scipy python"
$UV tools/paint.py parse models/figure.3mf work/p.npz
$UV tools/paint_split.py plan   work/p.npz --scale S
$UV tools/paint_split.py joints work/p.npz --scale S --plane-cut auto --emit work/joints.json
$UV tools/paint_split.py cut    work/p.npz work/parts --scale S --plane-cut auto
uv run --quiet python tools/pivot_joint.py work/joints.json
```

**`--plane-cut` must be identical for `joints` and `cut`**: it changes the
mesh, and the socket plan must be computed on the same mesh the parts are cut
from.

Between those steps, **fix the part paths in `work/joints.json`** — `joints`
fills them in by filament name.

`joints` reports per seam the length, the cross-section, the deviation of the
rim from its own plane, and a verdict. Refusals are named in words rather than
left blank.

`cut` reports the **mean thickness of each part** — the first number that shows
whether a socketed joint is possible at all.

## Cutting on a plane instead of along the colour contour

The mating surface of a colour-contour cut is a capped drawing, and a drawing
need not be flat. Two wavy caps printed in layers do not meet: their steps
differ and do not cancel.

`--plane-cut` moves the seam onto its plane honestly — faces crossed by the
plane are cut by it, and each piece takes the colour of its side. On a flat
seam this brings the rim within one layer height of the plane.

Widening the strip (`--plane-margin`) does not help and makes things worse
beyond a couple of millimetres: out there the seam runs where the surface is
nearly tangent to the plane, and the intersection is ill-defined in itself.

**A plane changes the author's drawing**, so `auto` converts only the seams
that will carry a pin. Other flat seams gain nothing from a plane and would
shift the colour for free.

## How the socket is chosen

Everything follows from geometry; no coordinates are set by hand.

1. **The seam plane** — least squares over the loop's vertices. The seam counts
   as flat when the rim deviates less than `--flat`.
2. **The socket centre** — the centre of the largest circle inscribed in the
   loop. **Not the centroid**: on a concave section it lands on the rim or
   outside the contour.
3. **The radius** — inscribed radius minus the wall minus a margin, rounded
   down. The wall defaults to three nozzle lines, from `hardware.json`.
4. **The depth** — marched along the axis while measuring the wall **with rays
   across the axis**. At least 1.5 mm and at least the radius, at most 2.5
   radii.
5. **Three ceilings on depth**: the wall runs out; the axis hits the cap of a
   neighbouring seam; the axis reaches the far surface of the part.
6. **The side** — which piece lies on which side of the plane. The sign of a
   normal computed from the loop is arbitrary in itself.

`pivot_joint.py` carries the tolerances: the socket is wider than the pin by
`fit` per side — tighter in the half that sits under glue, looser in the half
that goes on by hand — and the pin is a millimetre shorter than the sum of the
depths.

## The tool's own checks

`pivot_joint.py` does not trust its own plan:

- **before drilling**, it intersects the cutter with the part. Below 75 %
  material, **the whole joint is cancelled**, both sockets and the pin — half a
  joint is useless because the pin has nothing to stand in;
- **after drilling**, it verifies that material remains behind the socket floor
  and that the box around the socket is full.

**A volume difference cannot be used for this**: a cutter that misses the part
is not discarded but welded in inverted, and the difference lies.

## Why most seams do not survive

**A socket lives inside a body, and a colour smeared as a patch over the
surface has no body.** A shirt under a waistcoat cut out by colour is a crust a
few millimetres thick; a socket does not fit into it and the joint is
cancelled. The route rule follows:

> If the colour is a separate piece of a limb — a boot, a trouser leg, a sleeve
> — cut across it with a plane and fit a pin. If the colour is a drawing on the
> surface — a shirt under a waistcoat, a stripe, a tattoo — no part will come of
> it: either keep the filament change, or make an inlay in a pocket.

## Rejected

**Repainting whole faces** to move a seam onto a plane, instead of cutting
triangles. The border starts zigzagging by one triangle, the seam grows by half
again and the piece count multiplies — a comb that nothing can then straighten.
Cut the faces instead.

**`--flatten`**, which lays loop vertices exactly onto the seam plane without
touching the paint, applies to very few seams in practice. Kept as a fallback.

## Traps

**Never measure a wall as the distance to the nearest vertex.** On a seated
figure the nearest vertex to a wrist axis can be a coat flap rather than the
sleeve wall. Measure with rays across the axis: a ray from inside hits its own
wall first.

**The neighbour across a seam is not the one sharing the longest border.** A
neighbour lying on the same side of the plane takes the socket past the body.
The neighbour is whoever is on the other side of the plane.

**The sign of a seam normal is arbitrary**, so sockets aimed by it alone go
outside the body or straight through a wall.

**A part is bounded by the caps of its own seams**, not only by the mesh — a
socket can run into the cap of a neighbouring seam. A cap is the loop tightened
in its plane, and the axis is intersected with it directly.

**And by the far surface.** Check with a ray forward along the axis, or the
socket becomes a through hole with a paper-thin floor.

**Cut wider than you paint.** An edge gains a new vertex, and the face on the
other side of that edge must learn about it even when its colour does not
change. Otherwise a hanging vertex is left at the strip's edge, the seam loop
forks, and one joint gets counted twice. Change colour inside the strip, but
split **every** face the cut touches.

**The plane eats a third colour caught in the strip.** Limit the strip to the
two filaments that actually meet at that seam.

**The plane shears thin slivers off neighbours** where a drawing approached the
seam at a shallow angle. They cannot be printed: after the cut, give slivers
below a square millimetre to their neighbour, the same way speckle is handled.

**Measure the socket floor across the whole section, not on the axis.** The
surface above the rim of a socket is closer than the surface above its centre.

## Presenting the result

Name three numbers: how many seams were found, how many of them are flat, and
how many survived the exact check. Then the mean part thickness — it explains
the refusals better than anything else.
