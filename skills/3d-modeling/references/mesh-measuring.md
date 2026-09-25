# Measuring a foreign mesh

Read this when the input is a mesh with no source (STL, 3MF, `object_N.model`)
and something has to be attached to it while keeping it compatible with the
rest of a set: replacing a coupling, extending an end, letting in a mount,
adjusting a fit. For editing the archive itself see
[foreign-3mf.md](foreign-3mf.md). A part built from scratch in your own code
does not need any of this.

A mesh records a result without its intent: no dimensions, only triangles.
**Recovering the intent is the design work itself** — it determines what is
possible at all.

## Measure first whatever can invalidate the plan

Before starting, ask: **which unknown, if it turns out the wrong way, forces
the work to be thrown away?** Measure that one.

A single ray probe down the length of a part can end a whole plan — by showing,
say, that a mechanism's cavity begins a millimetre behind the face you intended
to cut a socket into. Elaborate reconstruction of section outlines only refines
radii, and refining radii is worthless while the plan is still dead.

**A hard constraint lives in the surroundings, not in the thing being
attached.** The interface of a graft is easy to measure; the invariants of the
place it is let into are hard. **Measure the surroundings before measuring what
you are copying.**

## Recover the intent, not the numbers

Measurement yields numbers carrying discretisation noise. The intent appears
when they add up:

```
62.7933 + 45.2067 = 108.000 exactly   <- the designed length between faces
```

**Keep comparing measurements until a round number or an exact identity
appears** — that is the designed value. Until then you are copying noise. Write
the round number into your own code, not the measured one.

**Write the round numbers out separately.** They are the proof of the author's
intent, and they are what to cite when explaining a decision to a person.

## Reach every key number by two independent routes

A centre found from the coordinate extremes and the same centre found from two
points on the arc will disagree, and both are "measurements". Only a third fact
reveals which is wrong.

A circle in a mesh is a polygon, and its radius is written nowhere. **The
extreme point in x does not lie at the centre's latitude.** Compute it
properly:

```python
# centre (cx, yc) and R from two arc points: the extreme in x and the extreme in y
# p1 = (cx, y1) is the arc apex, p2 = (x2, y2) is the extreme in x
yc = ((x2 - cx)**2 + y2**2 - y1**2) / (2 * (y2 - y1))
R  = abs(y1 - yc)
```

**Verify the result with a third point on the arc**, not by eye.

## A direct probe beats indirect evidence

Photographs of the product and a PDF instruction sheet are interesting and
slow. A dull ASCII height map answers sooner and more precisely. **Documentation
and pictures give intent and vocabulary, not state.**

**Never measure from renders.** A render is a sanity check; numbers are the
measure.

Two probes repay themselves many times over:

```python
# 1. Height of the top at a point: intersect a +z ray with all triangles
#    (Moller-Trumbore, vectorised over the array), sort by t.
#    Print it as an ASCII map — relief and voids show at once, and this is
#    the only way to find internal cavities and pocket depths.

# 2. A flat section at z = const: segments on triangles, stitched into loops
#    by coincident endpoints.
```

**A socket open to an end face is not a hole.** It runs continuously with the
outer contour, so it never appears in a list of closed loops. Find it by
walking the outer loop in order and watching where the chamfer, the slot and
the arc are.

**Work in the local frame of the feature**, with the end face at zero and the
body in the positive direction. In world coordinates, chamfers on an angled
branch look like planes normal to the axes and do not resemble chamfers at all
until converted.

## Put the result into code as named constants

Mark them as measured and give them a faceting tolerance, so the work is not
repeated. **Keep that file next to the model, not in `work/`** — `work/` is
expendable and gets deleted.

**Write the round numbers into this reference as well**, not only into the
code: a code file disappears together with its model folder, and what survives
is what was written into the documentation.

## Checking a rework

A sample of points inside the part must match the original **everywhere except
the edited zone**, and a new tenon or socket must match the original ones in
the local frame of the end face. That catches both a shift of a millimetre and
a mechanism touched by accident.
