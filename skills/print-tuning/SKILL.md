---
name: print-tuning
description: Cut the plastic and the print time of one specific file without losing visible quality — check the project settings, redundant perimeters and infill, ironing, supports, the prime tower, placement and tilt on the bed, the layer height, and on a multicolour model count the filament changes and the price of flushing. Use when the user says "optimise this figurine", "check this file before printing", "why does it take so long", "how many colour changes are in here", "do I need a prime tower", "which layer height should I take", "how should it sit on the bed", "can it be tilted", "reduce the plastic", "cut the print time", "how do I print this cheaper without losing quality", or brings a downloaded .3mf and asks what to change in it. Triggers — optimise print, project settings, prime tower, filament changes, flush, plastic usage, print time, bed orientation, part tilt, layer height, stair stepping, seam, supports on a figurine; «оптимизируй эту фигурку», «проверь файл перед печатью», «почему так долго печатается», «сколько здесь смен цвета», «сколько уйдёт на промывку», «нужна ли башня очистки», «какой слой взять», «как положить на стол», «можно ли наклонить», «уменьши расход», «сократи время печати», «напечатать дешевле без потери качества», «проверь настройки проекта»; оптимизировать печать, настройки проекта, башня очистки, смены филамента, промывка, расход пластика, время печати, ориентация на столе, наклон детали, высота слоя, слоистость, ступеньки, шов, поддержки фигурки.
---

# Optimising a file before printing

`figcheck.py` answers whether the geometry is printable. This skill answers
what to change so the print costs less plastic and less time while looking the
same.

Most claims here are measurements on Bambu Studio and an A1-class machine, not
documented behaviour — the Bambu wiki does not cover them. Treat them as
starting points and re-measure on the file at hand.

## The goal, and the test for it

Fewest grams and hours at undiminished visible quality. Quality is three
quantities that must not move silently:

| What is held | How it is measured |
|---|---|
| the surface you can see | grams and time of `Outer wall` in `gcode_report.py` |
| smoothness | layer height |
| likeness | number of filaments |

**If `Outer wall` is identical before and after, the visible surface was not
touched, and the saving is free.** Anything that moves one of the three is a
price; the user decides whether to pay it.

The machine comes from `hardware.json`. Geometry preparation is in
[figurines.md](../3d-modeling/references/figurines.md). Surface, supports and
the seam are in [surface-supports-seam.md](references/surface-supports-seam.md).

## Order of work

```bash
python3 tools/retune_project.py human_file.3mf -o work/ready.3mf --from ref.3mf
python3 tools/figopt.py audit  "models/…/part.3mf"
uv run --with numpy python tools/figopt.py colors "…/part.3mf"
uv run tools/figcheck.py part.stl
"$BS" --outputdir "$PWD/work/out" --slice 1 "…/part.3mf"
python3 tools/figopt.py gcode work/out/plate_1.gcode
```

Step 0 applies only when the file came from a person who saved it in the GUI.

**Decide by two slices, before and after. A rule is never enough on its own** —
several of the rules below contradict common FDM wisdom.

## Reading the audit

`audit` reports three levels. A **BLOCKER** means the file will not print as it
stands: another printer in the profile, another nozzle, or an empty
`different_settings_to_system`, which makes the GUI fall back to the system
preset and everything in the file decorative. **IMPORTANT** prints, but not as
the user expects. **ADVICE** is quality.

Name blockers first, and say that the printer and the profile are switched by
hand in the GUI — the CLI is not equivalent (see
`3d-modeling/references/foreign-3mf.md`).

**When a print time looks absurd, suspect the nozzle selected in the GUI before
looking for infill to shave.** A project without `project_settings.config`
opens with the user's current settings, not the file's. A 0.2 nozzle cuts the
flow by an order of magnitude, which shows up immediately in the estimate. Read
`printer_settings_id`, `print_settings_id` and `nozzle_diameter` from the file
and compare with `hardware.json`; `audit` does this itself. The cure is a
settings port with `retune_project.py`, not hand-editing single keys.

On a multicolour model a finer layer costs twice: it multiplies both layers and
filament changes.

## The price of colour

`colors` works from paint distributed over layers and predicts filament
changes, layers per colour and flush cost before any slicing.

**A colour is paid for by the number of layers it appears on, not by its
area.** A small detail smeared up the height costs more than a large patch
confined to one band.

Removing a filament is a **paid** lever: the detail it drew disappears with it.
Squeeze the free levers first, then put the choice to the user with the price
in grams and hours, and render what will be lost (`paintview.py`, skill
**3mf-paint**). Never remove a colour silently.

## Free levers

Quality does not drop:

- `wall_loops` to 2 — the outer wall is laid identically either way, and inner
  walls add strength a display piece does not need;
- `sparse_infill_density` to 10 % — nobody looks at infill; the slicer
  compensates partly in solid infill and bridges;
- `ironing_type = no ironing` on organic shapes — there are almost no flat top
  surfaces to iron. On a part with a flat top, decide from the `Ironing` grams
  instead.

Quality rises for almost nothing:

| Edit | What it gives |
|---|---|
| `seam_slope_type = external` | the seam is smeared along the perimeter instead of stacking into a column of dots |
| `seam_position` aligned | seams settle into folds; a rear seam lines them up dead centre |
| `support_style = tree_organic` | fewer contact points, comes off without marks |
| `support_top_z_distance` = 2 layers | at one layer PLA welds to PLA and tears off with the surface |
| `wall_generator = arachne` | fewer bridges on organic shapes at the same time |

**A scarf seam stays off while `override_filament_scarf_seam_setting = 0`**:
the filament profile's `filament_scarf_seam_type` wins over the process
setting. Verify in the G-code, never in the setting — with a scarf, outer
perimeter extrusions carry a Z change inside the layer. The check and the
second trap are in
[surface-supports-seam.md](references/surface-supports-seam.md).

**Supports go only where they are needed.** `support_on_build_plate_only`
leaves overhangs that rest on the model itself unsupported; check by ray cast
with `figcheck.py`.

## Flush volumes per colour pair

On a dark base colour Bambu's computed `flush_volumes_matrix` is inflated: a
transition into dark forgives a remnant of the previous colour, and the matrix
does not know that.

**Cut transitions into dark; leave transitions into light alone.** An
under-flush in dark brown is invisible; in a white collar or a skin-tone face
it shows at once. Halving the column of the dark base colour is safe.

The global `flush_multiplier` is a poor substitute — it trims the dangerous
light-bound pairs too. Edit the one column with `patch3mf.py`; the same place
in the GUI is the flush volume table of colour pairs.

A part whose colours are two transitions, one of them into a light face, gains
nothing here. Its lever is the number of changes instead.

## What is not a lever on an A1

**The prime tower does not absorb flush.** The bulk of a filament change goes
to waste through nozzle cleaning, so the tower adds its own plastic and time on
top. Leave it off unless a measurement on the file at hand says otherwise;
`audit` reports it as *measure*, not *enable*.

**Flushing into infill drains nowhere**, because the flush is written into the
tower and the infill takes only the remainder. Raising infill does not help
either — the extra plastic goes into the part.

**Flushing into objects silently turns multicolour off**: it collapses the
print to a handful of tool changes with no error from the slicer. After any
settings edit, read the filament-change count; if it moved differently than
intended, the edit did something else.

## Editing settings

`patch3mf.py` changes keys inside `Metadata/project_settings.config` and
carries the rest of the archive verbatim; `--drop-filament N` removes a
filament from the project entirely. Check one edit at a time.

**An edit reaches the GUI only together with `different_settings_to_system`.**
That key lists what differs from the system preset. Absent, it means the author
changed nothing; once you edit, it must appear, or the GUI takes the system
preset and never sees the edit while the CLI reports success.

For a different layer height, lay down a real process profile of that height
(`resolve_profile.py`) and put your differences on top, rather than overwriting
`layer_height` alone: speeds, bottom layers and support thresholds all belong
to the layer, and `print_settings_id` must not contradict it.

## Tilt

**Tilting does not reduce colour changes.** It stretches every colour zone up
the height, so more layers carry several colours. Measure changes *per layer*,
never in total: a tilted part is shorter, holds fewer layers, and its total
falls without anything improving.

A tilt is a technique about overhangs and about where the staircase lands, and
it is decided by the stair-step distribution from `figcheck.py`, not by the
footprint and not by colour changes. It removes a coarse staircase from
horizontal places and in exchange smears a fine step over the whole figure,
face included — on a display piece that is usually a loss.

**A collapsed footprint after a tilt is not an argument against it**: the
bottom is re-cut in the new pose and the footprint comes back, often larger and
in one patch. What to enter on the minus side is the wall angle above the cut.

The one thing a tilt buys is moving overhangs off the model onto the bed — and
unticking `support_on_build_plate_only` achieves the same for nothing.

Placement: check that the object sits entirely inside the bed. "Nothing to be
sliced, either the print is empty or no object is fully inside the print
volume" is a placement problem, set in the GUI.

## Layer height

Decided by the distribution of stair steps over the area, not by preference —
the `figcheck.py` columns, detailed in
[surface-supports-seam.md](references/surface-supports-seam.md).

**On a multicolour model the layer height is the most expensive lever there
is**, more than supports and infill together: a filament change costs a roughly
fixed amount of plastic regardless of layer height, and the flush is linear in
the number of layers.

The forgotten second lever is the nozzle: a fine layer through a 0.4 nozzle is
both finer and faster than a coarser one through a 0.2 nozzle — the table is in
the **3d-modeling** skill.

## Filament count is also a hardware limit

AMS lite has four slots and a second AMS does not attach. A project with five
colours is either reduced to four or printed with a manual spool change. The
cheapest colour to drop is the one living on the most layers while covering the
least area.

## A part something is pressed into

If a part has a socket or a pocket for an insert, the infill pattern stops
being free: spiral and concentric patterns run along the wall and do not hold
it across. Use a lattice — Cubic, Grid or Gyroid. Orientation and clearances
for such parts are in **3mf-paint**, `references/split-to-parts.md`.

## Checks before handing over

1. Sliced twice, as is and with the proposed edit.
2. `Outer wall` compared before and after; a difference makes the edit paid,
   whether or not that was intended.
3. Grams and hours named in both directions, with flushing on its own line.
4. Blockers named first, with the note that the printer and profile are
   switched by hand.
5. Supports stated plainly — present or not, and how many grams.
6. If a colour is being dropped, a render of what disappears.
7. Experiment files in `work/`; only the final project next to the model, and
   that said out loud.
