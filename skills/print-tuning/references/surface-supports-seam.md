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

A face right at the computed threshold prints without support, rough but even:
acceptable on a joint face, not on a visible one.

**A part standing on an edge bulges along that edge.** The first layers and the
brim stand proud of the faces meeting at the edge. If one of them is a mating
face, the bulge has to be filed flat before assembly; keep mating faces off the
bed edge when the pose allows.

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

Judge a trunk by its narrowest neck: the support path length per layer of one
connected cluster, read from the G-code with arcs (`G2`/`G3`) included. A neck
of about 35 mm per layer still snaps under an overhang 15–20 mm above the bed;
aim for 60 or more. Per object, `tree_support_branch_diameter = 10` with
`tree_support_wall_count = 2` gives that for a fraction of a gram. Going larger
merges branches differently and thins the neck again — measure, do not
extrapolate. Tilting the part leaves the neck where it was, and normal support
in its place stands on thin pillars, so neither replaces thicker branches.

### Choose the pose by the scars the slice really leaves

`figcheck.py` predicts supports from the mesh; `gcode_report.py` says whether
there are any. Neither says which faces carry a scar after this slice and
whether those faces show. `supportmap.py` reads what the slicer laid down,
finds where every support column ends against the model and adds the contact
up face by face:

```bash
SM="uv run --with numpy --with scipy --with trimesh --with embreex python tools/supportmap.py"
$SM map   part.3mf --supports --joints auto --hide-fil 3
$SM sweep part.3mf --object Arm --poses "0,0;30,0;-30,0;0,45" --supports --max 6
$SM neck  plate_1.gcode
```

`map PROJECT.3mf [GCODE]` slices the project itself with its own settings
when no G-code is given (`--supports` switches supports on in a copy) and keeps
the pose exactly as it is in the project. It reports mm² of contact per object,
split into visible and hidden, and the largest visible spots with coordinates.
Hidden is what the assembly covers: a whole object (`--hide-object`), a face
mask (`--hide-mask NAME=faces.npy`), joint faces painted in their own filament
(`--hide-fil`), flat cut faces (`--joints auto`) and the inside of sockets
(`--niche`). `sweep` slices the same part in each listed tilt, ranks the poses
by visible contact, then time, then grams; every pose is a full slice, so keep
the list short and take the top row. `neck GCODE` lists the thinnest trunk
sections that still carry support above them, with diameter, height and
position — the place to thicken branches. Contact on side walls is not
counted, and `sweep` does not tell apart two objects with the same name.

## Layer height

The width of a step on the surface is `layer height / tan(angle to
horizontal)`: zero on a vertical wall, infinite on the crown of a dome.
**No layer height cures a horizontal top** — say so straight away.

Decide from the distribution of step width over area, not from an average; the
columns come from `figcheck.py`. Show **where** the bad areas sit — a histogram
of their area against z names the place rather than the model as a whole.

**A step narrower than the line width does not show on a face.** Count the area
with steps wider than the line width; the band between half a line and a full
line is not a defect and does not decide the pose.

**The pose sets the sheen as well as the steps.** A face standing as a
near-vertical wall prints glossy; a face slanted to the layers prints matte even
where no step shows. When two poses are both free of steps and scars, the finish
the piece wants decides, and faces seen side by side on one figure should share it.

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

One trap guards it: `seam_slope_type` takes `none` / `external` / `all`. The
GUI labels read as None / Outer perimeter / Outer perimeter and holes, and
writing `contour` from the label makes the CLI return `none` silently.

So **verify in the G-code, not in the setting**: with a scarf,
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
