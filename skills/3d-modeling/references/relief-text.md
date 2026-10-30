# Raised lettering and artwork over a base

A plate — a sign, a certificate, a label — with text and a drawing raised over
a base, one filament per colour. **What decides the result is the thinnest
stroke against the nozzle line**, not the layer height: a flat plate has no
stair steps.

## Source

- **Take a vector PDF over a raster.** Render it at 0.015 mm per pixel or finer
  (`pymupdf`) and trace the masks. A typical 2000 px picture of an A5 page
  gives 0.075 mm per pixel, and small letters come out melted.
- **Classify colours by connected component or by position, not per pixel.**
  Anti-aliased edges and desaturated stretches of one path fall into the wrong
  class. Take the peak brightness of each component; take a drawing that is a
  single path by its region on the page.
- **An embedded font is a subset**: only the glyphs used in the layout exist.
  Another weight of a letter is not there to take; the only lever is an outline
  offset, with the limits below.

## What prints through the 0.4 nozzle with Arachne

Relief 0.6 mm (3 layers of 0.2) for the main colour; a secondary tone is the
same filament one layer lower, 0.4 mm, which saves an AMS slot and the colour
changes.

| Feature | Prints | Does not |
|---|---|---|
| cap height | 3.0 mm and up | 2.1 mm is guessed, not read |
| stroke, median | 0.34 mm and up | — |
| stroke, thinnest 10 % | 0.29 mm and up | 0.26 mm: the word wobbles |
| round dot | 0.9 mm and up | below about 0.85 mm it vanishes, the G-code has no move there |
| tapered stroke tip | — | ends 0.1–0.2 mm short |

Measure the stroke on the raster of the final geometry: twice the distance
transform sampled on the skeleton, per line of text.

**Arachne is mandatory.** The classic wall generator drops strokes narrower
than the line outright — all of the thin secondary text and about a quarter of
the regular text.

**Dots.** Grow every isolated round dot to 0.9 mm, and re-check after any change
of scale. Grow only while the dot stays at least one line width clear of its
neighbour — at a small size a grown full stop merges into the letter. A solid
dot of about 1.4–1.5 mm can come out with a pinhole in its centre; replace it by
a copy of a dot of the same artwork that printed solid.

**Hand-drawn strokes about one line wide** lose whole segments, such as the stem
of a letter. Thicken the artwork by 0.06 mm on each side.

**Tapered tips are not fixed by settings or by stamping.** `min_feature_size`
does not change the lost length. Square stamps placed on the tips fail: the rule
that finds a tip also fires at sharp inner corners, and a stamp that touches
the stroke at one corner is dropped by polygon repair. Enlarging the artwork is
the only cure — the same 0.1–0.2 mm is less visible on a bigger letter.

## Text too small for the nozzle

**Enlarge the lines, breaking the proportions between them, before reaching for
the 0.2 nozzle.** The base is most of the volume of a plate, and the 0.2 profile
flows about 2 mm³/s: a 150×100×2 mm base takes over 4 h against about 1 h 10 min
through 0.4. A coarser layer on 0.2 saves time without losing letter width, but
not enough.

**A nozzle cannot be changed mid-print.** The job is sliced for one nozzle; a
second job probes the bed first and runs into the part already on it.

**Enlarge per line.** Scale each line about its left edge and baseline, stack the
rows from the bottom by ink-to-ink gaps, and solve one common gap so the block
fills the height between the margins. The width runs out first, against the
artwork beside the text — measure the clearance after every step.

**Change weight by thickening only.** An inward offset that fakes a regular
weight next to a bold one pushes the thinnest strokes below 0.29 mm. Tell rows
apart by relief height instead.

**Layout is judged by eye.** Send a picture of the layout and rebuild the model
only after the human approves it. A grid of equal gaps can look worse than an
uneven one that separates the header.

## Geometry hygiene

- `buffer(0)` on a polygon that touches itself at a point drops a lobe without a
  word. Use `make_valid`, and compare the area against the source raster.
- A hole that touches the outline at a point gives a non-manifold vertex after
  extrusion. Shrink every polygon by 0.002 mm before extruding.
- One STL per filament, assembled into one object (`--assemble`, or **Yes** to
  "a single object with multiple parts" in the GUI). Write the wall generator
  into the project together with `different_settings_to_system`, or the GUI
  falls back to the classic one.

## Verifying

Render the top relief layers from the G-code with `gcoderender.py`: every
extrusion move at its width from E, arcs `G2`/`G3` interpolated, the filament
taken from `M620 S<n>A`. Pass each colour's STL as `--target N=part.stl`; the
report lists every element covered less than half, with its place and size. **Compare against the
intended geometry, never against a derived one** — a broken derivative and its
own print match perfectly.

**The render proves presence, not legibility.** A render that showed every
letter in place printed as merged mush. The thresholds above come from prints;
for a size below them, print a strip with the smallest line first — ten to
fifteen minutes instead of the whole plate.
