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
outer perimeter extrusions carry a Z change inside the layer.

```python
feat = None
withz = tot = 0
for line in open(gcode_path, errors="ignore"):
    if line.startswith("; FEATURE:"):
        feat = line[10:].strip()
        continue
    if feat == "Outer wall" and line.startswith("G1 ") and " E" in line and "E-" not in line:
        tot += 1
        if " Z" in line:
            withz += 1
```

Roughly one scarf per layer is the working result; zero means it never engaged.

Add `seam_placement_away_from_overhangs = 1` so the seam does not land on an
overhang.
