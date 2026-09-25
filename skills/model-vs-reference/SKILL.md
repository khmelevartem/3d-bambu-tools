---
name: model-vs-reference
description: Compare a 3D model against a reference picture — overlay the model's projection on the photo at one scale and measure how far each feature has drifted, in millimetres. Use whenever the user asks whether the model still looks like the original, asks to compare with a picture, to overlay it semi-transparently, to show where the eyes, moustache, nose or brows have moved, to check proportions after sculpting, or doubts whether a feature shifted after their own edits in Blender. Triggers — reference, original picture, overlay, deviation, face proportions, "it stopped looking like it", check against the photo, verify after an edit; референс, оригинальная картинка, наложение, расхождения, разошлись глаза, усы, брови, нос, пропорции лица, «стало не похоже», сверить с фото, похоже ли на оригинал, проверить после правки в блендере.
---

# Comparing a model with a picture

A sculpt is judged by eye, from a picture. This skill turns that judgement into
millimetres and produces a picture in which the same thing is visible.

`tools/refcompare.py` lays an orthographic projection of the model over the
photograph at one scale, measures the silhouette by sections, and computes
where each feature has drifted to.

```bash
UV="uv run --quiet --with numpy --with scipy --with pillow python"
$UV tools/refcompare.py fit "models/part.3mf" "models/reference.jpg" \
    --out work/cmp.npz --cut-from-top 45
$UV tools/refcompare.py measure work/cmp.npz --report work/numbers.txt \
    --debug-png work/features.png
$UV tools/refcompare.py sheet work/cmp.npz "work/comparison.png" --notes work/numbers.txt
```

`fit` reads a 3MF or an STL, rasterises the view and searches for a scale and
two offsets maximising silhouette agreement. `--view back|left|right` selects
another angle. In zsh a `$UV` shorthand does not expand into several words —
write the command out in full.

## Read the silhouette agreement first

It is always printed, and it decides whether anything else is worth reading.
Below 90 % the tool complains and there is nothing to compare until the cause
is found:

| Symptom | Cause |
|---|---|
| agreement near zero | wrong `--view`, or the figure is cropped in the picture |
| well below 90 % | the picture has perspective; the projection is orthographic |
| just below 90 % | `--cut-from-top` let a chair or the shoulders into the comparison |

**Orthographic projection is a hard limit.** A reference shot from close range
cannot be fitted: its near parts are larger than its far ones and the model's
are not. Studio renders and generator output are usually close to orthographic.

## What to trust

**The silhouette by sections** is the most reliable output: model width against
picture width at a series of heights, plus the offset of the centre. These
answer whether the overall size and the seating of the head match.

**Feature offsets** are reliable as positions. They come from correlation: the
silhouette of a dark patch from the picture is slid across the model's relief
and the position where the surface beneath bulges most is taken.

**Feature sizes are not comparable in general.** In the picture a feature is
drawn; on the model it is sculpted, and the edge of a patch on relief depends
on the threshold chosen. The exception is a round feature: the tool fits a
sphere and computes the circle along which the ball emerges from the surface —
that circle is what to compare with the drawn one.

**Open the feature map (`--debug-png`).** Red is the patch from the picture,
green is where correlation placed it. If the green landed on the wrong feature,
the number in the report is meaningless, and that is visible in a second.

## Rules

**Never fit by the feature you intend to judge.** Aligning by the eyes and then
measuring the eyes proves only that they are where you put them. Fit by the
silhouette of the head, which does not depend on small features; if the
silhouette itself was reworked, fit by something known to be untouched with
`--scale/--cx/--cy`.

**Apply the project's transform before measuring.** In a 3MF project the mesh
sits in its own units and the matrix in `<build><item transform=…>` converts it
to millimetres. Without it every millimetre is wrong. `refcompare.py` applies
it; any home-made parser must too — check the bounding box against what Bambu
Studio shows in the object properties.

**Compute relief only on surface turned towards the camera** (`--facing 0.6`).
Where a cheek or chin turns away, depth collapses and drags the baseline down,
swelling the relief until neighbouring features merge into one patch. The
symptom is a feature reported many times its area, with green smeared across
the map.

**Do not segment the relief into patches and match them to the picture.** A
patch merges with its neighbour or is cut by the window edge, there is nothing
to score a pair with, and assignment slides along a chain so that every number
lies plausibly. Measure by correlation, without segmentation.

**Before naming the size of a feature, define what counts as its edge** and
check that on a magnified picture. On a ball fused into a surface, the steepest
slope of the depth sits well inside the real edge, so a radius taken from it is
too small.

**Seed triangles with points by projected area when rasterising.** Splatting
vertices leaves salt-and-pepper and tears the normals even on a mesh of a
million vertices.

## Checks before handing over

1. Silhouette agreement is stated. Without it the numbers mean nothing.
2. The feature map has been opened; every green patch sits on its own feature.
3. The comparison sheet has been opened — half the disagreements show on a
   50/50 overlay before they show in the table.
4. Numbers from the picture and from the report do not contradict each other.
   If they do, trust the eyes and look for the error in the measurement.
5. The answer says what matched, not only what drifted.
6. A size that is disputable (drawn against sculpted) is named as disputable.

## Presenting the result

A sheet of five panels: the picture, the model, a 50/50 overlay, the model's
outline over the picture, and the numbers. A picture is read faster than a
table and catches what numbers do not show.

Files go in `work/`. A finished sheet may be put next to the model, but say so
and offer to remove it.
