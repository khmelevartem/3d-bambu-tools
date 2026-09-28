# Editing the shape of a finished mesh

A mesh that already exists — from a generator or from someone's model — is
edited numerically: the mesh goes into numpy and the coordinates are moved. What
follows is how to move them without destroying the part of the shape a person
actually looks at.

## Zone the edit before moving a single vertex

**Everything a person reads as a face moves rigidly only.** Head, beak, eyes,
ear cups: a translation or one uniform scale, never a per-axis coefficient.
Matching silhouettes slice by slice and applying the result to the whole mesh
skews the head and turns round shapes into ovals, which is seen immediately.

**Wrong proportions usually sit in the body.** A head of the right size that
merely sits a few millimetres too far back needs a translation, not a warp — a
per-axis correction there solves a problem that does not exist and breaks what
was already right.

Put the smooth transition band on the neck and the collar, where the geometry of
the two zones is shared anyway. Then prove the zoning with a number: **the
spread of the residual displacement over the face zone must be exactly zero.**

**The fold threshold**: the shift Δ divided by the width of the transition band
must stay below 1, or the mesh folds into itself.

## Moving a sculpted detail: slide it along the base surface

A detail welded into the skin — a beard into a chin, a brow, a scar — cannot be
shifted along an axis. A weight field on a curved surface drags the skin
vertically, and where the surface is steep the form runs away by millimetres.

Restore the base surface under the detail and move the vertices **along it**:

1. `RBFInterpolator`, `thin_plate_spline`, smoothing 1e-4, over points of clean
   skin. It closes the hole under the detail smoothly; polynomials and MLS with
   a wide support are off by 0.1–0.2 mm there.
2. Move by arc length along that surface, recomputing the height from the same
   surface. The skin then slides over itself and the detail travels whole,
   together with its rim.
3. Cut the zone off from its neighbours by coordinate — a neighbouring detail
   gets a shift of exactly zero — and narrow the zone towards the far rim so the
   fold is not dragged past the detail. Ramps are `smoothstep`, not linear.

**The trap is the overhanging underside of a neighbouring detail.** Pulling it
down leaves a visible flap hanging a millimetre above the skin. Freeze the
underside **by vertex normal**: it points down (`ny ≈ −0.95`), while the
detail's own upper rim points up and its face is near zero, so the freeze is
`ny < −0.5` and above the detail's bottom. Afterwards lift the strip left
between the details back onto the base surface, weighting by distance to the
frozen zone and to the detail's body, or triangles tear in the crease. Finish
off any remaining flipped triangles with a local Laplacian relaxation over their
1-ring.

**Proof that the rest of the shape is intact**: cast rays from the new
coordinates into the original mesh — the deviation of untouched skin stays in
microns — plus zero flipped normals and unchanged facet and non-manifold counts
in `--info`.

## Resizing a feature to a picture

Measure first, in the skill **model-vs-reference**: the scale comes from a
feature-to-feature distance, and the disagreement shows up on a silhouette
profile by columns, not in a bounding box.

The edit itself is an **affine map of a band, per column of x**: the segment
[bottom, top] of the model is stretched linearly onto the segment from the
picture, `y' = a(x) + b(x)·y`. Outside the band, a cubic Hermite decays to zero
over Ru/Rd with the derivative matched at the rim — a linear decay leaves a
crease in the skin.

- **Carry the relief with the vertex**: `h = z − base`, multiplied by
  `0.5 + 0.5·b`. Without it a squeezed feature becomes a fat caterpillar.
- **Do not move x at all.** The Jacobian then degenerates to `1 + ∂d/∂y`, and
  checking that it stays above zero is enough to rule out folds.
- To fix a local wedge rather than the whole height, **anchor the rim that is
  already correct** (`y' = top + b·(y − top)`), so the neighbour above it does
  not move at all.
- **Skin that does not exist** — under a feature welded to its neighbour —
  comes from stretching the neighbour's own smooth slope. That is allowed when
  the price is measured and named: how far the slope's base sank, and above
  which height the shift is exactly zero. Check the new skin by the Laplacian of
  `h` against untouched skin; it should come out smoother than the model's own.
- **Bristle grooves are closed morphologically, not by smoothing.**
  `grey_closing` with a disc of about 1 mm lifts only the groove floors and
  leaves ridges and the contour step untouched; Gaussian and Laplacian smoothing
  either miss grooves wider than the mesh step or sink the dome and spoil the
  rim. Compute the lift field from the `(x, y)` grid, never from the vertex's own
  `h`: otherwise the underside of an overhanging rim flies up and leaves
  hundreds of flipped triangles.

## New geometry is a separate closed body

A detail added on top of an old one is built as its own closed body and left
overlapping; the slicer merges them. Deforming the existing surface outwards to
grow it is the harder route and rarely the right one.
