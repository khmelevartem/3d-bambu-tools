# Surface, supports and the seam

Angles here are counted **from the horizontal**: 90° means the face points
straight down, 0° is a vertical wall. Bambu's `support_threshold_angle` uses
the opposite convention; convert with `threshold = 90 − our angle`.

## Supports

On a figurine with a mane, a cloak or wings, most overhangs hang over the model
itself rather than over the bed, and `support_on_build_plate_only` leaves them
silently unsupported. `figcheck.py` casts a ray down from every overhanging
face and reports how much area rests on the model. Above roughly fifteen
percent, set `support_on_build_plate_only = 0`.

Use `support_style = tree_organic`: fewer contact points, and it comes off by
hand without marks. Set `support_top_z_distance` to 2 layers — **at one layer
PLA welds to PLA and tears off together with the surface.**

### The support threshold is computed, not guessed

Per layer a wall inclined at α shifts sideways by `h / tan α`, and neighbouring
lines must overlap by at least half, or the edge curls up and the nozzle
catches it:

```
threshold = atan( layer height / (line width / 2) )
```

`figcheck.py` prints the column. Bambu's stock values are bolder — they rely on
slowdown over overhangs and on part cooling. **For a display piece take the
computed value.**

**Raising the threshold above the computed one is paid for on the visible
side.** Every degree of margin turns another band of wall into supported
overhang, and the support leaves its marks there whether the wall needed it or
not. Measure it as area, not as grams: take the faces a support would touch,
intersect them with the faces that are exposed in the assembled figure, and
compare. Going back to the computed value cuts the support landing on visible
surface several times over while `Outer wall` does not move, so the saving is
free. The margin belongs on the one part that drooped, as a per-object
override, not on the whole project.

**Cutting a part in two to ease its supports does not pay.** Two halves collect
more support on visible surface than the whole part did.

**A lone tree trunk standing apart from the part snaps on a bed-slinger.** A
trunk a few millimetres thick in a single wall, rising beside a tall part,
breaks off as the bed swings. Find such trunks in the preview and give that
object thicker branches with two walls.

## Layer height

The width of a step on the surface is `layer height / tan(angle to
horizontal)`: zero on a vertical wall, infinite on the crown of a dome.
**No layer height cures a horizontal top** — say so straight away.

Decide from the distribution of step width over area, not from an average; the
columns come from `figcheck.py`. Show **where** the bad areas sit — a histogram
of their area against z names the place rather than the model as a whole.

A second sign in favour of a finer layer is the weight of the `Floating
vertical shell` line type in `gcode_report.py`.

Take the outer perimeter speed from a High Quality profile rather than a Fine
one: Fine runs it several times faster, and a bed-slinger rings on a tall part.
`wall_generator = arachne` is better than the classic generator on organic
shapes at the same time cost.

## The seam

Where the seam runs is read from the G-code: the first extrusion of every
`; FEATURE: Outer wall` stretch is a seam point. Collect them and count how
many landed on the visible half.

An aligned seam position settles seams into folds by itself. A rear seam lines
them up dead centre and is usually worse.

**The scarf joint is the main lever**: the seam is smeared along the perimeter
with a gradual rise in Z, leaving no column of dots, and it costs minutes on a
multi-hour print.

Two traps guard it:

1. `seam_slope_type` takes `none` / `external` / `all`. The GUI labels read as
   None / Outer perimeter / Outer perimeter and holes, and writing `contour`
   from the label makes the CLI return `none` silently.
2. **The filament setting wins over the process setting.** While
   `override_filament_scarf_seam_setting = 0`, the filament profile's
   `filament_scarf_seam_type` applies and `seam_slope_type` is ignored.

Because of both, **verify in the G-code, not in the setting**: with a scarf,
outer perimeter extrusions carry a Z change inside the layer. Any G-code parser
reads arcs as well as lines: Bambu writes curves as `G2`/`G3`, and a parser
that takes `G1` alone sees every round path in pieces.

```python
feat = None
withz = tot = 0
for line in open(gcode_path, errors="ignore"):
    if line.startswith("; FEATURE:"):
        feat = line[10:].strip()
        continue
    if feat == "Outer wall" and line[:3] in ("G1 ", "G2 ", "G3 ") \
            and " E" in line and "E-" not in line:
        tot += 1
        if " Z" in line:
            withz += 1
```

Roughly one scarf per layer is the working result; zero means it never engaged.

Add `seam_placement_away_from_overhangs = 1` so the seam does not land on an
overhang.
