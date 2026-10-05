# Splitting by colour into separate parts

Instead of changing filament inside one print: one part per colour, each in its
own filament, glued afterwards. The prime tower and all the flushing go away.
In exchange a seam appears at every colour border, and the whole question is
**what holds that seam**.

A lid against a lid holds nothing. Both are printed in layers, each has its own
stepped surface, and glue lets them set a millimetre out of place. Only two
joints work.

**The principles in SKILL.md come first** — physical bodies, a smooth seam
over faithful paint, surface-and-pin versus inlay, simple bodies rebuilt from
geometry, one part per pass. Where a recipe below seems to disagree, the
principles win.

## The two joints

**A cut across the part, a blind socket in both halves, a pin.** The mating
faces are plane against plane where the border allows a plane, and otherwise a
smooth surface fitted to the border — a relaxed membrane with no steps or
creases, never the capped paint contour itself. The socket is a rectangular pocket in
each half; the pin is a separate body, or grafted onto one half.

| Tolerance | Value | Why |
|---|---|---|
| pin cross-section below socket | **0.20 mm** (0.10 per side) | enters without rattling |
| pin shorter than the two depths together | **up to ~1.4 mm** | otherwise the pin bottoms out before the halves meet and leaves a gap on the visible joint |

**Use a rectangular cross-section, never round.** A round pin leaves the parts
free to rotate about it.

### A joint that turns

When the part is *meant* to turn — a head, a limb, a lid — invert that rule:
a round pin along the axis of rotation. **Loosen the fit to 0.10 mm per side
and no further.** A joint that turns still has to hold the part where it was
put. Measured on assembled figures: 0.25 per side lets a head on a 6 mm pin
rock four degrees and the face swings a millimetre; 0.15 per side on a 2 mm
wrist pin will not hold a raised hand at the height it was left; 0.10 per side
on 3 and 4.5 mm shoulder pins turns freely and holds. Five more things decide
whether it turns or wobbles.

**A pin printed lying down comes out fatter, and that is luck, not design.**
Its underside sags by about a tenth of a millimetre, which turns a loose fit
into a good one and a good fit into a press. Stand the pin axis up so it prints
at its real diameter, and set the fit by the number.

**Leave slack at the floor of the socket.** Size the socket deeper than the pin
protrudes, by 0.3-0.5 mm. With the two equal, the pin touches the floor in the
same instant the faces meet, and the part ends up hanging on the pin instead of
resting on the collar around it, where it wobbles. In EasySlice this is
`tip_extra`; it defaults to zero, so it has to be asked for.

**Put the seam under an overhang, not on a silhouette line.** A part that turns
carries its seam to every angle, so a seam on a visible line is visible from
somewhere. Under an overhang - the underside of a head that juts out past the
shoulders - it stays hidden through the whole rotation.

**Measure the travel; do not assume it is free.** Rotate the part's sections
about the axis in 2 degree steps and take the smallest gap to every other part
at each angle, excluding points within the pin radius or the pin itself answers
every time. A neighbouring raised arm may leave only a sector free, and that
sector is the honest answer to give. Check the assembly the same way: lift the
part along the axis in steps and confirm the clearance grows monotonically, or
it cannot be put on at all.

**A pin inside a slender column carries the whole bending moment.** A glued
butt joint across a column holds only as well as the pin crossing it: a 3 mm
pin of 3 mm diameter inside a 5.8 mm column snaps the first time the assembled
piece is dropped. Spend the room on length before diameter — the glued cylinder
is what carries the moment, while widening the pin eats the wall it is glued
to. Make the pin at least as long as the column is wide and keep about a
millimetre of wall around the socket.

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

### When no primitive fits, restore the base body

A colour lying on the surface as an applied layer — hair on a head, a patch on a
limb — has no plane and no cylinder around it. **Do not cut along its painted
contour.** Where the surface runs parallel to the withdrawal direction, a
contour wobble of 0.3 mm moves the cut wall by centimetres, and the part comes
out with fins and with flakes of the neighbour's zone on it.

Cut with the **base body the layer sits on**: the lower part is that restored
body, the upper part is everything lying on it as a layer. A cut by a body
crosses the surface almost everywhere, so the rim is defined.

How the body is built:

1. a centre inside the body, and a radial function `R(u)` sampled on an
   icosphere (subdivision 6, about 41 thousand directions);
2. a superquadric `|x/A|^p + |y/B|^p + |z/C|^p = 1` fitted to the lower zone as
   the base;
3. a biharmonic correction driven by the lower zone's own data;
4. two clamps: `R` ≥ the radius of its own zone, so nothing of the part is lost,
   and `R` ≤ the smallest radius of the neighbour's zone, so nothing of the
   neighbour is taken. **The second clamp wins** — a neighbour's colour on the
   part is worse than one's own colour given to the neighbour.

**A boss of the base body stays with the base body** — an ear, a brow ridge, a
heel. Cutting it off to follow the colour is the worse trade: a badly cut part
and its seam show more than a wrongly coloured millimetre.

**The clearance pocket is a Minkowski sum with a ball, not a radial offset.** On
the vertical wall of a boss a radial offset gives no clearance at all.

### A body of revolution restores both sides at once

Furniture under a figure — a stool, a base, a post — is almost always a solid of
revolution, and whatever stands on it is fused into it. Do not trace the line of
fusion. Sample the profile `R(z)` **only in the angular sectors the neighbour
does not reach**, rebuild the solid from it, and the dents the neighbour pressed
into it are simply absent: the part comes out smooth without any repair step.
The neighbour is then the original mesh minus that same solid, offset by the
clearance. Both surfaces come from the same numbers, so they cannot disagree.

**A profile `R(z)` is only meaningful where the section is a single loop.**
Look at the horizontal section before sampling anything: a footrest ring on
spokes shows three separate outlines at that height — the post and two arcs of
the rod — and a percentile of radius merges them into a solid disc. The part
then passes every mesh check and still is not the thing that was there. Where
the section has several loops, fit each element on its own: the ring as a torus
about its own axis, each spoke as a cylinder along its own direction, and union
them. Fit the spoke direction by sweeping the angle and keeping the one that
makes its cross-section roundest — principal-component axes are useless on a
piece that is barely longer than it is thick.

**The axis may move with height.** Elements that look coaxial often are not — a
footrest ring can sit most of a millimetre off the post it hangs on. Fit a
circle per layer and filter the centres with a **median**, never a mean: the
centre steps by that millimetre at the ring's edge, and an averaging window
smears the step over its whole width, which throws the radius out by millimetres
on the layers next to it.

**Take the profile near the top of the spread, not at its median.** A moulded
base is round to about a tenth of a millimetre, and a median profile leaves the
high side of it outside the cutter: the difference comes back as shells a
hundredth of a millimetre thick.

**Cut the neighbour with the restored part itself, inflated by the clearance —
not with a primitive that merely covers it.** A cylinder thrown over a ring is
larger than the ring everywhere else, and it takes a wedge out of the sole with
a vertical wall and a step where its rim crosses the shoe. The restored ring
subtracts only where the shoe actually pressed in — tenths of a millimetre —
and leaves a pad of exactly that footprint. That pad is still a dent in the
neighbour, so decide first who keeps the shared material: see *When the two
parts share the same material* below.

**Inflate the cutter along vertex normals, not by scaling the radius.** On a
nearly horizontal surface a radial offset barely moves the surface along its
normal, and the subtraction leaves shells a hundredth of a millimetre thick.

**Judge boolean debris by volume, not by area.** The shells left along a nearly
tangent surface carry thousands of faces and tens of square millimetres while
their volume is a hundredth of a cubic millimetre; an area threshold keeps them
and a volume threshold does not. Set it in absolute units — a few cubic
millimetres — so that genuine small bodies belonging to the model survive it.

### A prop fused into a hand is not in the mesh at all

A microphone in a fist, a bottle in a grip, a hilt in a palm: the sculpt is one
surface, and the prop **has no surface where the hand covers it**. There is
nothing to cut there. The giveaway is in the paint — the prop's colour patch
comes apart into two components, one each side of the hand.

Measure it and build it again as a solid of revolution. That gives a part with
no boolean debris on it at all, and a channel in the hand that is an exact
cylinder instead of a cast of a sculpted gap.

**Fit the axis on two features at once, never on the visible one alone.** Only
a few millimetres of bare shaft usually show, and a direction fitted on that
short lever is wrong by several degrees — on an overhang ten times as long it
throws the head's centre a couple of millimetres off the axis, and the rebuilt
prop comes out bent. Fit the shaft cylinder and the head sphere together, with
the sphere's centre constrained to lie **on** the cylinder's axis.

**Take the profile as a median per slice, over faces interior to the colour
patch.** The patch's boundary faces are the fillet running into the finger and
read as much as a millimetre too large; a maximum or a high percentile builds
that fillet into the part. Fill the slices the hand hides by interpolating
between the exposed ends. Check the rebuild against the sculpt by radius: a few
hundredths of a millimetre through the middle of the distribution is the sign
that the profile is right.

**The channel is the monotone envelope of the profile, not the profile.** A
shaft that widens towards the head — they nearly all do — jams in a channel
that copies it one to one: the fat end has to pass the narrow start. Bore
`radius(t) = max(r(u), u <= t) + clearance` and the part slides the whole way
with exactly the clearance. The price is a slightly loose fit deep in the
channel, which is under the fingers and invisible.

**Work out how the part goes in before cutting anything.** Three routes, and
usually only one is open: sideways out of the groove needs the fingers to open
a full 180 degrees, which a grip never does; in from the tail needs the head to
pass the shaft's channel, which it never does; so it goes in from the head end,
down the channel. Say how far it travels, and check the clearance at every step
of that travel rather than at the seated position only.

**Expect a small float and measure it instead of assuming a stop.** The part
seats when its collar meets the rim of the hand, which is rarely the sculpted
position: a couple of tenths of a millimetre deeper is normal, repeatable, and
cheaper to state than to remove — removing it means adding a shoulder to the
part, and that changes the silhouette.

**Check what else stands near the axis before extending the cutter past the
part.** The cutter has to run out beyond both ends of the prop, and a wrist pin
or a neighbouring finger can sit inside that extension. Measure the distance
from the cutter's axis to the pin's axis along the pin's whole length, and
compare the mating face's area before and after: it must not change at all.

### A raised strap: interpolate the garment under it

A tie, a belt, a strap lying on a body is a zone that stands proud of the
surface along part of its length and hangs clear of it along the rest. Neither
a plane nor an offset of its own face gives the back of that part. **The back
is the surface underneath, continued across the zone.**

Parametrise the body as `r(angle, height)` about an axis through it, take the
zone as a region in that plane, and solve Laplace's equation for `r` inside the
region with the surrounding surface as the boundary value. Weight the stencil
by the real spacing — `dz` one way, `r·dtheta` the other — or the solution
leans along the finer axis.

**The floor under that surface has to be a fraction of a line width, not a
millimetre.** A minimum-thickness clamp is what puts a strap-shaped trench in
the garment: wherever the strap's true standoff is less than the floor, the
floor wins and the cutter digs a groove the strap never touched. There are no
shirts with a slot cut for the tie. Clamp at about 0.25 mm — under half a
0.4 nozzle line — and the base garment keeps a continuous surface, with a real
recess only where the part genuinely presses into it, such as under a knot.
Never clamp from above at all: where the strap lifts clear of the body, its
true standoff is the answer.

**Put the one pin where a recess already exists.** A pin halfway along a strap
that merely lies on the garment is a hole punched in sound material. The knot,
the buckle, the boss — wherever the part already cuts into the body's geometry —
is the only place a socket costs nothing.

**Where the zone tucks under a lapel or a flap, its own border is not boundary
data.** The flap stands further out than the zone, and interpolating from it
drives the reconstructed surface outside the part, giving negative thickness.
Feed that band from the trustworthy neighbours instead — the zone above and the
garment below — and substitute the radius of the body underneath, taken from
wherever that body is exposed.

### The cutter is a bounded shell, and its outer face belongs in the air

Cutting such a zone with an open wedge takes whatever the wedge reaches at a
larger radius — a thigh in front of a hanging strap, a collar over a neck. Bound
the cutter outside as well as inside.

Choose that outer bound from the **air gap**, not from a fixed offset: cast the
ray past the zone, find the next entry into the solid, and put the face at about
half of that gap while keeping a clear margin below it. Measure the gap from the
raw sampled radius raised by a local maximum, never from a smoothed one — a
smoothed surface sits below the real one, and the real one then reads as the
obstacle. A face laid a few hundredths of a millimetre off a surface it is
parallel to perforates the part: it weaves in and out and leaves a sieve.

**Do not make the cutter's grid finer than the model's own triangles.** A
cutter meshed an order finer shreds the boolean into hundreds of edges with
three faces. A step of about the model's edge length leaves none, and its
staircase on the contour is then visible instead, so **smooth the positions of
the boundary nodes** rather than refining the grid: a dozen Laplacian passes
over the nodes that lie on the mask's border, moving them in the parameter
plane, take the steps out and drop the residual genus with them.

**When the cutter's outer face is forced within a fraction of a millimetre of a
neighbouring surface, stop cutting and move vertices instead.** A patch of skin
under an overhanging lapel leaves no room for an outer face, and the boolean
returns a sieve. Build the part from the model's own faces: copy the zone,
displace its vertices inward, and close it with a reversed copy plus a rim wall.
Measured on the same figure — the tie, whose side wall is nearly radial, came
out genus 3 with a cutter and 41 % of its faces self-intersecting by
displacement; the neck patch under the lapel came out genus 41 by cutter and
genus 0 by displacement. The zone decides which tool, not preference.

**Displace along the surface normal, not along the radius**, and **taper the
depth to zero at the zone's border** over about a millimetre of geodesic
distance. A radial offset collapses a near-radial side wall; a full-depth step
across one triangle stands faces on edge and they overlap.

### A plane along a drawn edge sits on the edge, not beside it

Where the seam follows a sharp step in the surface — the edge of a lapel, a
collar, a raised strap — fit the cutting plane to that edge by least squares and
put it **on** the edge. Standing it a tenth or two inside leaves a ribbon of the
floor narrower than the nozzle line: it cannot print, and the boolean breaks it
into slivers that read as a saw in the preview. Either the plane is on the edge,
or it is further out than one line width — nothing in between.

Fitting is worth doing on the real edge rather than on three points: sample the
step at every slice by the largest jump of the surface along the sweep, drop the
outliers, and refit. A residual of a few hundredths of a millimetre means the
edge is straight enough for a plane; a residual approaching a tenth means the
seam wants something other than a plane.

The chips such a plane shears off the step are **dropped, not given to the
part**: `"keep": "largest"` in the part's specification leaves the biggest body
of the intersection and reports what it threw away.

### Subtract the cutter, not the part

The remainder is `source − cutter`, never `source − part`. The part is the
largest body of `source ∩ cutter`; subtracting that body puts the chips back,
touching the shell at a vertex instead of merging into it, and a tenth of a
cubic millimetre of debris brings back non-manifold edges and a genus of several
units. The cutter is a clean body, so the difference is clean, the chips stay in
the remainder where they belong, and the two volumes add back up to within what
was dropped — `check` reports that residual as the shortfall.

**Collapse edges shorter than ~0.05 mm at the very end — on the part, and on
the remainder only along the cut.** Every boolean leaves slivers along its
cuts; welding the short edges takes them out without moving the surface, and
the face count drops by a fifth or so. A sculpted body has edges that short of
its own: collapsed everywhere, a head loses a third of its faces and gains
hundreds of non-manifold edges. On the remainder restrict the collapse to faces
touching the new walls, or use the solver's own simplify with a tolerance of
a few microns. Check afterwards that both bodies still deviate from the source
surface by microns.

### A post standing where the cutter passes keeps its footing

A neck, a peg, any post rising from the surface the cutter crosses: measure it
first — centre, radius, the plane it stands on — and **subtract its own cylinder
from the cutter**, over the full height, grown by a collar of about half a
millimetre. Without that the cutter's back face shaves the front of the post's
base, and the post is left standing on a part of its footprint. The back of the
pocket then follows the cylinder instead of a plane, which also keeps the part
thicker than a flat back face would at the same clearance.

Take the cutter's top **above** the plane the post stands on, not level with it:
level leaves a film of the original over the part, and that surface then prints
in the body's filament instead of the part's. Verify by rays, not by eye — cast
down over the part's footprint and check that nothing of the body is above it.

### Mould pins into the cutter's inner face

Welding a separate cylinder onto a cut part with UNION leaves a ring of pinches
along the seam, as a socket drilled across a kerf does. Sink the pin into the
cutter instead: lower the cutter's inner surface by the pin's length inside a
disc, and the pin comes out carved from the part's own material, with no second
boolean. The socket cutter gets the same disc, deeper by the floor slack and
wider by the fit.

### One cutter for both parts, or the pocket must contain the part

Prefer **one cutter for both sides of the seam**: cut the part with it and the
body with the same one, so the two mate with no designed clearance at all and
are located by the pin and held by glue. A strap on a garment has nowhere to put
a clearance anyway — the gap would read as a groove around the part.

When a clearance is genuinely needed, two cutters — an exact one for the part, a
grown one for the pocket — only give it if the grown one **contains** the exact
one everywhere. One boolean
checks it: `exact − grown` must be empty. It is broken by every shortcut:

* deriving the grown radii from the same field but averaging over a wider mask,
  which lifts the floor;
* a mask cleanup that drops a cell the exact mask had — keep the union;
* blending two neighbouring zones in one coarse cell — take the extremes, not
  the mean;
* applying two grown cutters one after the other, which re-cuts a surface the
  first one already made. Cut one pocket with one cutter for both parts.

Grow the floor by the depth clearance and the outline by one grid cell, and
leave the outer face alone: it is in the air already, and growing it drives it
into whatever lies beyond.

**Two parts seated in the same pocket need a gap between them too** — at least
one cell of the cutter's grid. Sharing a face means the printed parts fight for
the same space.

### Splitting along the colour border, and why the patch is not free

**Smooth the contour before cutting along it.** A generator's paint border
wanders by tenths of a millimetre from one triangle to the next, and the cut
inherits every tooth. The teeth are finer than the nozzle, so they print as
fluff that has to be scraped off both parts before they will seat — and
wherever the seam crosses a visible surface, the same teeth show as a step of
two or three tenths: a hairline that looks sawn, a ridge down a sideburn, a
bump on a cheek that reads as a modelling error rather than a seam. Smooth the
contour until no excursion is smaller than one extrusion line, then cut. The
smoothing moves nodes *along* the border, so the surface stays where it was —
the same trick that takes the staircase out of a pocket wall.

Selecting one filament's faces and capping the holes divides the mesh exactly:
the two volumes add back up to the original. The capping is where it goes wrong.

**Do not recompute the winding of the halves.** In a generated figure a limb
usually enters the torso by interpenetration; once half the faces are taken
away, a normals-recalculate reads that limb as a cavity and turns it inside
out — hundreds of wrongly wound edges and a volume short by a quarter. Inherit
the winding from the source mesh and build each patch triangle from the
neighbouring face's own traversal: for a boundary edge that runs v0→v1 inside
its one remaining face, add the triangle [v1, v0, centroid].

**A fan patch loops forever on a pinhole.** Where the two colours meet at a
point, the new face lands on top of an existing one, the edge gains a third
face, removing it reopens the hole, and the count never changes. Weld the
vertices of such a boundary instead, with a threshold growing from 0.02 to
0.45 mm — the holes are tenths of a millimetre across and the welding costs
a fraction of a cubic millimetre.

**A long, non-convex border makes a fan patch a sail.** Where a trouser leg
lies against a post for several millimetres of height, the fan spans the gap as
a flat membrane: a blade sticking out of the sole on one part and a fin
standing off the post on the other. **Use the mesh library's own hole fill
instead of a fan** — it triangulates inside the border and invents no centroid,
and the two halves then add back up to the original volume exactly.

**Relax a large patch; a bare fill is a faceted pit.** A colour border wanders
around its own mean plane by a few tenths of a millimetre, and a fill that only
triangulates the border reproduces that saw. Subdivide the patch a couple of
times with grid fill, then run a Laplacian smooth a few dozen times with the
border vertices pinned: the patch becomes a taut membrane, which is what the
underside of a heel or a sole should look like. The membrane sags by a few
cubic millimetres — check the sum of the halves against the original and say so.

**Under the contact patch neither part has a surface of its own.** Where the
two colours meet, the source is one lump of material and the border is the weld
line; whatever fills the hole is invented, and the only real choice is which
part gets it.

### When the two parts share the same material

Along a contact the two bodies of the source interpenetrate — a sole grazing a
footrest bar overlaps it by a few tenths of a millimetre. Both parts cannot
keep it. Give it to the part whose surface is seen and shaped (a sole, a face,
a painted panel), and move the other one away.

**Measure the overlap before choosing how to move.** Sample the keeping part's
surface, evaluate the signed depth inside the other part's restored profile,
and take the maximum, not the median: the median is a fraction of the worst.

**Moving a ring inward is not the same as thinning it.** Which direction helps
follows from where around the section the overlap sits. A sole that grazes the
upper-outer shoulder of a bar is cleared by thinning and lowering; pulling the
ring towards its own axis drives the bar into whatever stands on the inner
side, and there is usually something. Search the two or three parameters
together against the measured surface instead of guessing one.

**Take the contact zone out of the neighbour's cutter.** The keeping part owns
everything there, so there is nothing to cut, while the cutter's own clearance
would carve the dent straight back and set the gap from below. Subtract the
zone from the cutter and the gap is then set only by how far the other part was
moved — which can be a few hundredths of a millimetre, so the two still read as
touching on the finished figure.

**A strut has to end on the axis of the ring it joins.** Its length was chosen
against the original section; thin the ring and the tip comes out through the
skin as a spike. Solve for the length instead of keeping the old number: it is
the distance from the strut's own origin to the ring's centre circle.

### Choosing the seam plane when the colour border is the seam

A limb usually gives several closed borders between the two filaments, and the
longest one is not always the joint: on a closed fist the border around the
bore was 92 mm long while the wrist was 24 mm. Fit a plane to every border loop
and score it by **how much surface area ends up on the wrong side of it** —
area of the cut colour left behind, plus area of the other colour carried
along. The wrist scored 4.5 %, the bore 17 %. Report the winner's residue with
the plane: below about 1 % the seam lands where the drawing already was, and a
few percent means the colour edge will visibly move.

## Decide the assembly order before placing pins

**Every part must have exactly one direction along which it goes into place.**
Check this before the pins, never after: a vertical pin into the base plus a
horizontal pin into a neighbour would require inserting the part in two
directions at once. Moving a pin to another face of the joint fixes it for
nothing; re-cutting afterwards does not.

## Drill the socket into the thick part, grow the pin on the thin one

Which half carries which is a free choice, and on a limb the wall decides it.
A wrist tapers: measure with rays across the axis, stepping along it to the
full socket depth, and a wall that is 2.3 mm at the cut can be 1.3 mm three
millimetres in. A socket there breaks through. A pin there only adds material,
because a symmetric pin grows inward as much as it protrudes, and the forearm
behind the cut holds a steady 2.9-3.2 mm and takes the socket without
complaint. The symptom of having it backwards is not an obvious hole — it is
degenerate faces and shells touching along the cylinder.

## A pin is as deep inside the part as it is long outside it

Cutting tools that graft a pin — this repository's, and the Blender add-ons —
build it symmetric about the cut plane: half protrudes into the socket, half is
buried in the part carrying it. On a solid block that is free. On a figurine
the buried half goes straight through a thin wall and comes out inside a
cavity: the bore of a fist, the hollow of a boot, a channel meant for another
part. Nothing in the tool notices, and the preview shows a clean part.

**Measure the wall before trusting the pin.** March along the pin axis into the
part in 0.1 mm steps and test a ring of points at the pin radius against the
original mesh; the depth is the last step where every point is still inside.
Where it is shorter than the pin's half-length, cut again without a pin and
graft your own cylinder: the protrusion stays, the buried part becomes the
measured depth minus 0.2 mm.

A thinner pin reaches deeper when the obstacle is a round channel — halving the
diameter roughly doubled the usable depth in one case. Trading diameter for
depth is the better deal whenever the joint is glued anyway.

**The buried length is not what holds the joint.** The bond is the pin's
cross-section at the cut face plus the glue, so even 0.5 mm of burial is
serviceable; say the number out loud rather than pretending the fit is
unchanged.

### A cut-off part returns on a sleeve, not on a flat face

Cutting a part flush and seating it on a boss of 0.30 mm is not a joint: a rim
of fractions of a millimetre neither holds nor positions anything, it only marks
the place. Build the cutter cylinder as **the part plus a sleeve continuation**,
and put a blind socket on the same axis in the mating body. The sleeve length
comes from the part's own size — about 3 mm at Ø4. The diameter is limited not
by the part but by **the mating body's wall at its narrowest point**: measure it
with sections parallel to the cut plane before choosing it.

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

### Inlays inside a cavity

Teeth, a tongue and the dark of a mouth; an ear canal; a nostril. Several
inlays share one cavity, each with its own extraction direction, and they get
in each other's way. Treat them as one assembly, not as separate inlays.

- **Fix the insertion order first**, then cut in that order: part *k* is the
  largest body of `head_{k-1} ∩ cutter_k`, and `head_k = head_{k-1} − pocket_k`.
  Parts cut this way cannot overlap, and the volumes add back up.
- **Visibility is judged on the head with the later pockets already cut.** A
  zone hidden behind the lower teeth becomes visible once their pocket is
  there; take the mask from a ray along `−d` whose first hit carries the zone's
  colour on that head.
- **The cutter's cap lies in the air of the cavity**, as a heightfield over the
  outline: the visible surface plus a margin over the zone, and no higher than
  the cavity wall beside it. A flat cap at the outer surface swallows the lip.
- **The floor comes from the zone alone.** Computing it over a ring around the
  outline puts it as deep as the deepest wall the ring touches.
- **Clip the outline by the cavity walls.** Where a wall runs parallel to `d`,
  the prism slices a groove into it; take the outline inside the free area by
  a few hundredths of a millimetre.
- **Check every insertion path by sweeping**: move the part from far outside to
  its seat in steps of 0.1 mm against the head and the parts already inserted,
  and require zero intersection volume at every step. When a straight path is
  blocked, try a path of two straight legs — out along the part's own axis,
  then out through the cavity opening. Name the path to the person who
  assembles it.
- **A band of colour thinner than two lines is not a part.** Grow it onto the
  neighbouring part of the same colour, and give its pocket the width of the
  channel it has to travel through, not of the band.
- **Throw away the head's fragments between pockets.** Two pockets a few tenths
  apart leave slivers floating in the cavity; keep the largest body.

### Hair, a beard: one smooth sheet instead of a pocket

Hair that wraps past a hemisphere — forehead to nape — cannot come off as a
shell of constant thickness in any single direction. Cut it with **one smooth
sheet** `s = h(u,v)`, a height field along the removal direction `a`: the part
is `{s > h}`, the rest `{s < h − gap}`. Removal is then guaranteed by
construction, and the sheet is both the floor and the side wall.

- **Choose `a` by two numbers**: the share of the zone's area that has the
  rest of the body above it along `a` (keep under 1 %), and the part's volume.
  For a head of hair, up and back at about 60° from horizontal beats straight
  up; a beard comes off downwards, a moustache forwards.
- **Bounds per column, not a target surface.** Part surface points and points
  at depth `T` under them must lie above `h`; rest surface points and points at
  depth `M` under them below. Near the seam both depths ramp down with the
  distance to the smoothed border times `tan 50°`, so the sheet meets the skin
  at an angle instead of running along it.
- **Solve for the flattest `h` between the bounds**: a membrane relaxation
  with clamping, coarse grid to fine. It cuts deep where nothing objects and
  thin where it has to.
- **Smooth the bounds before solving, conservatively** — lower the upper bound
  and raise the lower one by an eroded and blurred envelope. Raw per-cell
  extremes of sampled points carry the sampling noise into the sheet.
- **Smooth the sheet mesh in 3D afterwards (Taubin, ~60 passes).** Where the
  sheet is steep relative to `a`, the grid diagonals leave a saw of a tenth of
  a millimetre that no 2D blur removes.
- **A print base is a pinned plane.** When the remaining body prints on the
  cut, fix `h` to the plane over the largest feasible region, set the plane
  above the highest point of the rest, and cap the sheet there, so nothing of
  the rest rises above it. Keep the pinned vertices out of the 3D smoothing,
  and re-pin after any blur — otherwise the plateau sinks and only its rim
  touches the bed.
- **A thin relief — a moustache — still needs prism walls.** With nothing
  between the bounds, the sheet alone is rough. Bound it with an exact prism
  over the smoothed footprint, widened by a quarter of a millimetre so the
  relief's own side walls stay on the part, and give the pocket prism 0.2 mm
  per side. A raster clip leaves the grid staircase in the wall.
- **When a flat back is required, tilt the axis, not the floor.** A relief
  draped over a lip with a pocket right behind it has no flat floor along its
  own normal. Turn the extraction axis towards the neighbouring pocket's axis:
  the prism then runs alongside that pocket instead of into it. The price
  grows with the tilt — skin in the relief's shadow along the new axis ends up
  inside the prism. Scan the tilt and pick the one where the pocket clearance
  holds and the eaten skin is smallest; for a moustache that was 40° down,
  between straight ahead and the teeth axis.
- **Cut in sequence** (hair, then beard from what is left, then moustache);
  each sheet sees the previous pockets as the rest's surface and keeps its
  margin from them.

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

**Two things outrank overhang area when choosing which way up, in this order.**

1. **How the layers lie on the surface that gets looked at.** A surface that
   ends up facing upwards is built as a *top* surface, out of concentric
   perimeter rings, and on anything curved those rings read as contour lines —
   the same defect that puts rings on an eyeball printed dome-up. A surface
   that stands vertical is built from outer-wall perimeters stacked edge on
   edge and comes out even. A face, a front, a painted panel therefore prints
   **standing**, never lying.
2. **The area of the first layer.** Overhang area comes third, and is usually
   bought back with supports for a fraction of a gram.

**A part another part covers is laid covered-side down**, within that
constraint: the first layer, the elephant foot and the support scars then land
where nothing shows.

For a head the three candidate poses measure like this, on 8350 mm² of
surface, 1713 of it face:

| pose | first layer | contact hidden under hair | share of the face lying flat | support landing on the face |
|---|---|---|---|---|
| crown up | 474 mm² | 0 % | 5 % | 135 mm² |
| face up | 177 mm² | 100 % | **59 %** | 0 |
| **crown down** | **341 mm²** | **100 %** | **5 %** | **1 mm²** |

Face-up wins on overhangs and loses the model: more than half the face becomes
a top surface. Crown-down is the answer — the face stands vertical, the first
layer sits on the crown under the hair, and the collar the next part seats on
becomes a flat *top* surface instead of an elephant foot.

**The seat of a joint must never be the first layer.** The flat collar the next
part rests on is the one face that has to stay flat. Printed as the bottom
layer it domes, and the part then hangs on its pin and rocks instead of sitting
on the collar.

**An inlay is laid down by its extraction axis, not by its largest flat
facet.** The pocket's side wall is prismatic, so one of its facets is often the
biggest plane on the part — put *that* on the bed and the inlay prints on its
side, with the extraction axis horizontal and a staircase on the one surface
that has to slide. Take the axis from the pocket instead: it is the direction
the part is withdrawn in, which on a face or a shell is the local outward
normal of the body at that spot. Lay the part so that axis stands up.

**A patch inlay whose back follows a curved body has no flat face at all**, and
printing it needs one. Cut the back flat, perpendicular to the extraction axis,
taking off the least that gives a usable footprint — then **flatten the pocket
floor to match**, or the part no longer touches bottom and is held by the side
walls and the glue alone. The floor goes one depth-clearance below the cut
plane: measured by rays from the cut face, that turned a gap of 0.31 mm median
and 0.82 at worst into 0.050 median, 0.070 at the 95th percentile. Fill the
floor by moving the body's own vertices up to the plane, not by a boolean — the
patch is a thousand vertices against three quarters of a million faces, and the
rest of the body keeps its triangulation and its other sockets untouched.

**A round plug with a dome on top — an eye — stands on edge, axis horizontal.**
With the axis vertical every layer of the dome is a concentric circle and the
finished eyeball is visibly ringed. On edge the layers cut the dome in parallel
planes and nothing reads as a ring. The price is paid on the cylinder: the
first layers run along its own generatrix and spread, so such a plug needs
0.15 mm per side. At zero it has to be sanded before it will go in.

**A ball on a stalk is never printed ball-down**, and standing it up works only
if the support reaches the full height. Support that stops at mid-height leaves
the part free to wobble from there on, and every layer above goes down crooked.

**A thin flat part — a tie, a strap, a lapel — is laid flat, not stood up.**
Standing it up fails even behind a wall of supports.

**A pin that would be the part's only footing belongs to neither part.**
Printed waist down, a pair of cut-out trousers stood on the two 3 mm pins that
join them to the shirt: 7 mm² of first layer, and the part tore off the plate.
Drilling the pins out into blind sockets and printing them as separate dowels
turned the same pose into 49 mm² on the part's own surface, and cost neither
mating face anything. When deciding which half a pin grows on, count what the
part stands on without it.

**A pose that is a degree or two off is not that pose.** The same trousers at
1.9° off vertical had 24 mm² of first layer instead of 49: the patch degenerates
into a line along one edge. When an orientation is chosen for a flat face or a
cut plane, set it exactly.

**One failed first layer says nothing about the orientation.** Re-run the same
pose unchanged before changing anything: a part that spaghettied once often
prints on the second attempt with nothing altered.

**Hand the parts over already rotated for printing**, not in their original
orientation. Naming the orientation in words is not enough.

**A pin only prints round when its axis stands up.** Lay the part so the pin
axis is within about 30 degrees of vertical and let the support area and the
overhangs be optimised inside that constraint, not against it. A pin printed
lying down sags on its underside, and the 0.1 mm per side of a sliding fit does
not survive that; a small part standing on 4 mm of contact does survive, with a
brim. Sweep orientations with the axis as a hard filter and report both numbers
— contact area and overhang area — for the pose you hand over.

### Clearances

| Clearance | Value | Why |
|---|---|---|
| sideways, per side | **0.20 mm** | 0.30 shows as a visible gap |
| depth | **0.05 mm** | it is entirely how far the inlay sinks below the surface |
| sideways if the inlay must print as a staircase | 0.30 mm | the staircase eats the difference |
| sideways for a round plug printed on edge | **0.15 mm** | the first layers spread along the cylinder's generatrix |

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

### A strap restored by interpolation: measure its cross-section

The feather threshold at the edge of a patch is for the edge. When the body of
a narrow strap also sits near it, the strap prints as a hair and one printed
cord ends up visibly thinner than its twin. Cut across the strap and take the
inscribed circle of the section: **two extrusion lines is the floor**, and a
section area under 1 mm² is a cord that will not survive handling.

Cure it by lifting the outer face only — the back face of such a strap *is* the
garment underneath and may not move. Fade the lift to zero within half a
millimetre of the patch edge, or the outline of the strap grows and the drawing
changes.

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
| "by colour" | the loop wanders, there is no plane | do not cut along the loop: span it with a smooth relaxed surface, move the colour onto that surface, and pin across it |

## Choosing the cutting tool

| Situation | Tool |
|---|---|
| the seam is a plane or a cylinder, and the parts must sum back to the solid | `solid_cut.py` |
| a joint across an axis: one cut, a blind socket in each half, a pin | `pivot_joint.py` |
| the price of cutting by colour, which seams survive, the socket plan | `paint_split.py` |
| a whole limb cut off with a pin, planned by hand in the Blender viewport | **EasySlice Print**, an addon |

EasySlice Print is an equal member of this list, not a fallback: it plans the
cut where seeing the figure matters more than computing the seam. It is driven
headless as well — `plan.straight_section`, `plan.add_record`,
`bpy.ops.esp.build()`, whose `execute` blocks in the background. Its traps:

- **Repair the mesh first.** On a mesh that is not watertight the cut goes
  through and the socket is cut, but **the pin silently fails to weld**: the
  boolean union fails and the addon says nothing. Check by the part's extent
  along the cut normal — it must exceed the pin length. A part whose bounding
  box equals the pin cylinder, with the cut-off piece gone, is a failed weld.
- **Its pin is symmetric**: it grows as far into the part as it protrudes out of
  it. On a figurine that punches through the wall into a cavity — a fist around
  a cigar, the shaft of a boot. Ray-test first: a ring of points at the pin
  radius at depths 0.1…h inside the original mesh. Where it does not hold, cut
  with `add_pin=False` and graft a cylinder of your own — same protrusion,
  insertion = the measured depth minus 0.2 mm.
- It works on bare geometry and **loses the 3MF paint**; bring it back with
  `paint_transfer.py`.
- **Clean the mesh before writing it out, and judge it on the file, not in the
  scene.** The pin and the socket are cylinders that cross the plane of the
  kerf, and the solver leaves zero-area triangles and pairs of vertices a
  hundredth of a micron apart along that rim. In the scene they are harmless -
  connectivity is stored as indices and every edge still has two faces - but
  STL stores coordinates, and the slicer welds by them: those pairs merge and
  become non-manifold edges. `remove_doubles` at 1e-4 followed by
  `dissolve_degenerate` removes them and changes no shape. Run the mesh check
  on the exported file; a clean scene proves nothing.

**Drive its engine, not its operators, when the cut has to be exact.** The
viewport operators are modal - they want a line dragged by hand. `core.cutting`
takes the same job as numbers and runs anywhere: build a `ContactSpec` from an
explicit patch of vertices and faces plus `connectors.connector_matrix(centre,
direction, diameter, protrusion)`, wrap it in a `CutSpec` with `gap`,
`clearance`, `tip_extra` and `pin_side`, then `split_mesh` and
`apply_connectors`. `pin_side` names the half on the **+ side of the patch
normal**; the other half gets the socket.

**The patch is finite, and that is the point.** The cutter is the patch offset
by half the kerf either way, so it removes material only where the patch
reaches. A limb pressed against the part being cut - a raised arm beside a neck
- survives a cut that would otherwise take it, as long as the patch stops short
of it. Find the height where the two stop being one cross-section, and end the
patch in the gap between them, with about a millimetre of margin on each side.
Past its own rim the patch cuts nothing, so it may end in mid-air. What it must
still do is overrun the cross-section it is meant to divide, on every other
side, or the halves stay joined and the addon says the cut did not split the
part.

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
is ragged. Two ragged caps printed in layers do not meet: their steps differ
and do not cancel. A curved surface is not the problem — a smooth one prints
the same on both parts; the teeth of the drawing are.

`--plane-cut` moves the seam onto its plane honestly — faces crossed by the
plane are cut by it, and each piece takes the colour of its side. On a flat
seam this brings the rim within one layer height of the plane.

Widening the strip (`--plane-margin`) does not help and makes things worse
beyond a couple of millimetres: out there the seam runs where the surface is
nearly tangent to the plane, and the intersection is ill-defined in itself.

`auto` converts only the seams that will carry a pin. That is the reach of the
switch, not a reason to keep a ragged seam elsewhere: a plane or a smooth
surface moves the colour by a line or two, and that is the trade to take. Name
how much area changed colour.

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

**Pick the check that fits the seam.** A boolean lies where the parts share a
face: it answers a negative intersection, or a part's whole volume. Ray voting
lies where a part's face hangs in the air in front of the body: the parity
through a mesh of hundreds of thousands of faces breaks and reports a vertex
buried millimetres deep where a boolean finds nothing. Parts pressed face to
face are judged by volume balance and by point sampling; parts held apart by a
clearance are judged by the boolean.

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

**A volume that grew by exactly the operand's volume means the solver found no
intersection.** A pin unioned to a post should add only the part that sticks
out; if it adds the whole cylinder, the pin is hanging in a cavity. Generated
posts are often hollow — a cone-shaped funnel a few millimetres deep under the
seat, with a wall half a millimetre thick. Fill it with a cylinder before
placing the pin, and compare the increment with the expected one every time.

**Another model's part can sit inside the post, in its own cavity.** It belongs
to the other colour, so the colour split hands it to the other part, and the
cutter does not reach it because the cavity is missing from the cutter too.
Clear the interior with a cylinder along the post's axis, offset by the
clearance, and check afterwards that no vertex of one part lies inside the
other's outline.

**An intersection equal to a whole part's volume is the solver giving up, not
an overlap.** The same run then reports a union equal to the other part. Answer
the question with rays instead — five directions through a BVH, majority vote,
a couple of thousand sampled vertices — and report that count, not the boolean.

**When the exact solver returns an empty mesh, try the manifold one.** On a
large generated mesh `EXACT` silently yields nothing — or a union that merely
concatenates the shells — while the same operands run correctly through
`MANIFOLD`. Verify the cutter on a cube first: if it subtracts there, the fault
is in the input mesh, not in the tool, and changing its size will not help.
Outside Blender the `manifold3d` library gives the same answer in a fraction
of the time and is the better engine for a chain of a dozen cuts.

**Run `pymeshfix` after the manifold solver's simplify.** The result can carry
duplicated faces that make the STL non-watertight on reading, though the
solver called it manifold. `pymeshfix.clean_from_arrays` with
`remove_smallest_components=False` removes them without moving the surface.

**Blender's STL import fails on a path with a decomposed character.** macOS
keeps letters such as `й` decomposed (NFD) in file names, and the importer
reports the file missing. Copy the mesh to an ASCII path before the run.

**A flap hanging off one non-manifold edge is not a separate body.** Walk the
faces through edges and it belongs to the main shell, so a by-volume cull keeps
it, and three faces of it are all that is left of the report's open edges. Group
the faces through edges that have exactly two faces, and drop the groups that
are both tiny and thin.

**A pinch vertex survives the STL.** The manifold solver leaves single points
where two fans meet — typically around the root of a pin standing on a cut
plane — and the Euler characteristic comes out odd. Running the mesh through
the solver again separates them, but STL identifies vertices by coordinate and
they merge back on write. Move each of them a micron along its own normal.

**A part must not stick out of the body it came from.** Sample its vertices
and take the signed distance to the original mesh: anything positive outside is
a pin through a wall, or a patch that grew. Do not ask this question with a
boolean intersection — the two meshes share coplanar faces and the solver
returns garbage.

## Presenting the result

Name three numbers: how many seams were found, how many of them are flat, and
how many survived the exact check. Then the mean part thickness — it explains
the refusals better than anything else.
