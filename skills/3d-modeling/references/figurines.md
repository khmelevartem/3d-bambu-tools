# Figurines: preparing a downloaded organic model

Statuettes, busts, animals — anything with no flat faces and no right angles.
This covers **geometry preparation** only: what the part will stand on and what
size it should be. Print settings live in the **print-tuning** skill.

**The structural rules do not apply here.** Minimum wall thickness and fit
clearances are about mechanisms, not about a display piece.

```bash
uv run tools/figcheck.py model.stl
```

`printcheck.py` answers "this is a correct mesh"; `figcheck.py` answers "this
is a printable part". **Both checks are needed — they are about different
things.**

Angles here are counted **from the horizontal**: 90° means the face points
straight down, 0° is a vertical wall. Bambu's `support_threshold_angle` uses
the opposite convention; convert with `threshold = 90 − our angle`.

## A flat bottom: organic models do not have one

A downloaded figurine almost always stands on a rounded belly or on paws and
touches the bed nearly at a point. A first-layer area of a square millimetre or
two is not "it will stick badly" — **it will not print.**

Cure it by slicing the bottom off. **Choose the cut height from the wall angle
just above the cut, never by eye**: on a dome, the lower you cut, the more the
body flares above the cut, producing an overhang straight off the bed.

```
wall angle = atan( 1 / ((dA/dz) / perimeter) )
```

`figcheck.py` prints that column. **Take the first height at which the angle
reaches roughly 60°** — it prints unsupported from there. Cutting lower saves a
millimetre of height and buys a skirt of supports around the whole bottom. The
volume lost to a couple of millimetres is negligible.

**Cut with a boolean intersection against a box, not with a plane slice.** A
capped plane slice leaves a non-watertight cap on a mesh that has defects; a
boolean does not.

After the cut, **check that the centre of mass lies inside the convex hull of
the contact patches**, and name the margin in millimetres.

**A brim is almost always needed.** Even a large first-layer area is usually
several separate islands that only connect higher up. Use `brim_type =
outer_only`.

## Matching the size of a bought figurine

"Make them look proportionate" is **not solved by overall height**: figurines
have different poses, and a seated and a standing animal of the same height
cannot be the same character scale.

**Product listings are useless for this.** A whole product line is described
with one copied phrase about approximate height, with no pose and no box
dimensions. A generalisation about a line's standard box size says nothing
about a specific figurine — it is someone else's article, not a measurement.

**Measure the head.** A stylised figurine changes pose and body, but the head
stays the character's scale. Take two dimensions a person can measure with a
ruler without argument: head width at its widest, from the front, and head
depth from nose to back of skull, from the side. Take the same two from the
model by sections. **If both ratios agree to within a couple of percent, the
same thing was measured** and the coefficient can be computed.

Whether the character should then be slightly smaller is the owner's call: 90 %
of the reference head reads as a different product line, 94 % as a younger
sibling, 99 % as a twin.

### Never compare against the screen

Holding a bought figurine up to the monitor **lies systematically in one
direction**, because two errors add up:

- **Parallax.** The hand holding the figurine is in front of the screen, which
  magnifies it by roughly the ratio of the two distances to the camera.
- **Perspective projection.** The 3D view is perspective by default, so copies
  standing at different depths on the plate are not even comparable with each
  other. Switch the perspective view off and use a front view.

The honest method is **a 1:1 paper template**: the model's silhouette at
several scales as vector output on an exactly A4 page, printed at 100 % — not
"fit to page" — with a control line of known length to confirm the printer did
not rescale. Then hold the real figurine against the paper. Both objects lie in
one plane and no camera is involved.
