---
name: 3d-modeling
description: Create and rework 3D models in code for a Bambu Lab printer — OpenSCAD with BOSL2, build123d, printability checks, slicing through the Bambu Studio CLI, assembling and editing .3mf projects. Use when the user asks to make, rework, measure or print a part, to compute its weight in grams and its print time, to deal with overhangs, supports, clearances and fits, to fix a model downloaded from MakerWorld, to set a surface texture, and when the nozzle or the spool has changed and the print has to be recomputed. Triggers — .scad, .stl, .3mf, OpenSCAD, BOSL2, build123d, slicer, slicing, G-code, supports, fuzzy skin, nozzle, hardware.json; «сделай подставку под телефон», сделай деталь с нуля, напечатай деталь, переделай модель, обмерь, нарежь, «сколько граммов и сколько будет печататься эта деталь», посчитай вес в граммах и время печати, «этот свес не провиснет», свесы и нависания, поддержки, зазоры и посадки, поправь модель с MakerWorld, фактура поверхности, нечёткая оболочка, сменил сопло, сменил катушку, OpenSCAD, BOSL2, build123d, нарезка, G-код.
---

# Modelling in code

Code, then model, then check, then slice, then a human at the screen.
Functional parts are written in code. Organic shapes may be generated and then
arrive as a foreign mesh, going through the usual route: measure, repair,
scale.

**The machine, filaments, nozzles and preset names live in `hardware.json`, and
only there.** Scripts read it through `tools/hardware.py`; take numbers for an
answer from there, never from memory.

**Everything temporary goes in `work/`** — npz files, intermediate renders,
slices, throwaway scripts. The model folder holds only what gets printed, plus
references. A finished result may be put next to the model, but say so.

## Where to go

| Task | Where |
|---|---|
| a new part from scratch | the working cycle below, the `.scad` template, the printability rules |
| attach something to a foreign mesh | [measuring first](references/mesh-measuring.md) — it decides what is possible at all |
| a feature on a foreign model is cut off: a strap, a handle | [graft a piece](references/foreign-3mf.md) |
| rework a downloaded project | [reworking a foreign 3MF](references/foreign-3mf.md) |
| a figurine, a statuette, an animal | [figurines](references/figurines.md) |
| what to change so it prints faster and cheaper | skill **print-tuning** |
| does it still look like the reference picture | skill **model-vs-reference** |
| weight, time, supports | `./tools/slice.sh --supports part.stl` |
| colour, texture, fuzzy skin | [surface](references/surface.md) |
| fix the paint in a finished 3MF | skill **3mf-paint** |
| print each colour as a separate part and glue | skill **3mf-paint**, [splitting into parts](../3mf-paint/references/split-to-parts.md) |
| a GLB from a generator, with a texture | the colour is an image, not filaments: **3mf-paint** for the texture, **mesh-repair** for the mesh |
| the assembled project will not open in the GUI | [CLI traps](references/bambu-cli.md) |
| the slicer complains about the mesh | skill **mesh-repair** |
| the nozzle, spool or plate changed | edit `hardware.json`, and nothing else |

## Tools

**OpenSCAD** is the main one — rendering takes a fraction of a second.
**BOSL2** adds threads, fillets, gears and snap fits. **build123d** is for when
a STEP file is needed.

Project scripts live in `tools/`, with dependencies pulled in through
`uv run --with …`.

| Script | For |
|---|---|
| `slice.sh` | weight, time, G-code and a project that opens in the GUI; `--supports` slices with supports |
| `gcode_report.py` | the slice broken down by line type, and whether supports are present |
| `printcheck.py` | watertightness, genus, bounding box, overhangs. **Gives false holes on imported meshes** — use `meshdoctor.py` for foreign files |
| `meshdoctor.py` | mesh diagnosis by defect class, with the nozzle taken into account |
| `meshfix.py` | repair through Blender; extraction and write-back of a mesh inside a 3MF |
| `figcheck.py` | organic shapes: first-layer area, bottom slice height, where supports will land, a stair-step map; `--rx/--ry` for tilt |
| `figopt.py` | a project before printing: settings audit, colour changes, flush |
| `retune_project.py` | port settings into a project a human saved in the GUI |
| `resolve_profile.py` | flatten a profile's `inherits` and `include` chain for the CLI |
| `hardware.py` | read `hardware.json` |
| `refcompare.py` | overlay the model's projection on a photo and measure deviations |
| `make_multicolor_3mf.py` | a project painted per triangle, without the brush |
| `paint.py`, `paintview.py` | edit and render paint in a finished 3MF |
| `paint_normalize.py` | add a filament code to faces lacking `paint_color` |
| `paint_transfer.py` | move paint from an old mesh to a new one by geometry |
| `writeverts.py` | move vertices inside a 3MF without touching face numbering — **shape edits without losing paint** |
| `partedit.py` | add a `normal_part` / `negative_part` to an object, changing a foreign model's shape without touching its mesh or paint |
| `graft.py` | grow a missing piece onto a foreign mesh, appending faces at the end so the paint stays |

## The working cycle

```bash
openscad -o work/part.stl --hardwarnings --summary all --summary-file work/part.json part.scad
python3 tools/printcheck.py work/part.stl
openscad -o work/view.png --imgsize=900,700 --autocenter --viewall --camera=0,0,0,60,0,35,0 part.scad
open -a BambuStudio work/part.stl
```

**Slice when the answer depends on it, not as a closing ritual.** The person
slices in Bambu Studio before printing anyway, so a slice here is not how they
learn the weight — it is how this skill learns something it would otherwise be
guessing: whether supports are actually generated, and what a change did to the
print. Those two, and nothing else:

```bash
./tools/slice.sh work/part.stl              # stock profile, WITHOUT supports
./tools/slice.sh --supports work/part.stl
```

When it is a `.3mf` project being sliced, it goes with **its own profile**,
without `--load-settings`, so the check is of the file itself and not of
whatever the presets would impose on it:

```bash
"$BS" --outputdir "$PWD/work/out" --slice 1 path/project.3mf
python3 tools/gcode_report.py work/out/plate_1.gcode
```

## Checklist before handing over

1. `--hardwarnings` passed.
2. `printcheck.py`: watertight, the genus matches the intent, the bounding box
   fits the bed.
3. The PNG render has been **opened and looked at** from at least one angle —
   half of all geometry errors are visible only that way.
4. Does the answer claim anything only a slice knows — supports, grams, hours?
   If it does, **the file that will be printed** was sliced, not an STL lying
   next to it, and the last line of `gcode_report.py` was read. If it does not,
   there is nothing here to slice.
5. Any number that came from `slice.sh` is named as the CLI's. The person
   slices in the GUI, with the presets selected there, and that is the number
   they will print by; presenting the CLI's as final misstates it.
6. No stray files with other settings are left in `work/out/`, and nothing
   temporary appeared in the model folder.
7. In Bambu Studio the part appears as a separate object of the expected size.

**For a foreign mesh the order differs**: `printcheck.py` lies on it, so
`meshdoctor.py` goes first. Skill **mesh-repair**.

**Genus is the main check; watertightness proves nothing.** A model can be a
correct mesh and still be the wrong part — a blind hole instead of a through
hole gives genus 0 instead of 1. Genus is computed from the number of shells as
`(2·shells − chi)/2`; a single-shell formula returns negative values on a
multi-body part. "Undetermined" is an honest answer: an open mesh has no genus,
and an odd Euler characteristic means the surface is not simple.

`printcheck.py` computes the weight as if the part were solid; the real weight
comes from `slice.sh`, and the two differ by a multiple. Its overhang threshold
of 45° is the general FDM rule, while Bambu supports from a shallower angle.

## Supports are a separate item, not a by-product of slicing

**`slice.sh` slices without supports by default**, because the stock profile
has `enable_support = 0`. Such a slice looks like a full check while describing
a print that will not stand up.

**Decide from the G-code, not from the profile.** `gcode_report.py` counts
grams by line type from the `; FEATURE:` markers and ends with a plain
supports-yes/no line. `slice.sh` calls it; for a foreign slice, call it by
hand.

- **Always name the number** — "supports 0.6 g, 2 min", or "no supports, no
  overhangs steeper than the threshold remain". Silence about supports is read
  as "all fine".
- On a foreign model, read the `Description` inside the 3MF: authors often
  state where supports are needed.
- Compare the slice of a rework against the slice of the original — the same
  set of line types proves the edit added no new overhangs.

`slice.sh` builds the support profile itself from the stock process of the same
nozzle. **The support style stays stock**, so an organic style has to be set
separately. The flattened-profile cache is tagged by nozzle, so a stale one is
not picked up after a nozzle change.

**Both profiles send `curr_bed_type` from `hardware.json`.** Without it the CLI
falls back to a plate type that heats the bed far below what the filament
needs.

## Printability rules

The table is for a 0.4 nozzle; `hardware.json` says which one is installed.

| Parameter | At 0.4 | How it is derived |
|---|---|---|
| minimum wall | 1.2 mm | 3 perimeters = 3 x nozzle |
| loaded wall | 2–3 mm | 5–7 perimeters |
| overhangs without supports | to 45° | general FDM rule, nozzle-independent |
| bridge | to about 50 mm | nozzle-independent |
| hole for a bolt | nominal + 0.4 mm | **measured at 0.4, not verified at 0.2** |
| free fit | 0.4 mm clearance | same |
| snap fit, tight fit | 0.2 mm clearance | same |

**Wall thickness rescales with the nozzle arithmetically. Fits and clearances
do not** — they were derived by printing at 0.4 and are not a pure function of
nozzle width. For a part that will print with another nozzle, **print a test
coupon rather than scaling the number.**

Faceting finer than the print resolution is invisible, so there is no accuracy
argument for reaching for STEP.

### Which nozzle to use

**The choice is not about quality** but about what physically fits through the
nozzle: the filament profile for the finer nozzle cuts the volumetric flow by
roughly an order of magnitude, making prints several times longer.

**A fine layer through the 0.4 nozzle is both finer and about twice as fast as
a coarser layer through the 0.2 nozzle.** So a fine layer on 0.4 is the working
answer to "I want it smoother". Take the 0.2 nozzle only when a line narrower
than the 0.4 line must fit into the part: fine lettering, a mesh, a
single-perimeter wall, small mechanisms.

**Name the price before the work, not after**: changing the nozzle also means
recomputing every slice and editing `hardware.json`.

## File template

```openscad
// All dimensions in mm. Z up, the part rests on z=0.
$fa = 1; $fs = 0.2;          // faceting below print resolution

NOZZLE = 0.4;                // check against hardware.json
WALL   = 3 * NOZZLE;         // minimum for a load-bearing wall
FIT    = 0.4;                // free-fit clearance (measured at 0.4)
```

OpenSCAD cannot read JSON, so this constant is a copy — **it must be verified
against `hardware.json` by eye before rendering.**

## Answering about geometry

**Treat an observation made from a picture as a signal about a real problem**
and look for numerical confirmation until it is found or disproved. Do not
close the question with one estimate along one axis.

**Check a joint in the joint's own coordinate system** — both across it, for
the envelope, and along its normal, for protrusion past the mating plane.
Checking only the transverse coordinate hides an overlap entirely.

**Name the measured number**, not "it touches" or "it clears": "2.70 mm of
protrusion, moved out by 4 mm, 1.30 mm to spare".

**Treat what is already written in the project's own `.md` files, this skill
included, as a draft rather than a source of truth**, and say so explicitly
when delegating.

## Verify by the path the human will take

The CLI and the GUI read a project through different code paths. `--info`,
`--export-png`, `--export-3mf` and `--slice` all swallow files the GUI refuses
to display, so **success in the CLI does not prove the file opens in the GUI.**
The GUI's symptom is two messages in a row: an invalid configuration, then no
geometry data. Ways to check without a human at the screen, and their limits,
are in [references/bambu-cli.md](references/bambu-cli.md).

## Library traps

- **BOSL2 child coordinates** are measured from the parent's centre, not from
  its anchor. An anchor on a cutout inside `diff()` yields a blind hole instead
  of a through hole — do not anchor a cutout.
- **build123d does not fuse bodies that merely touch.** Overlap them by a
  millimetre or two and check the body count.
- **`fillet` over a set of edges fails as a whole** if even one edge cannot be
  filleted. Walk the edges one at a time inside `try`.

## Reference material

- [references/bambu-cli.md](references/bambu-cli.md)
- [references/figurines.md](references/figurines.md)
- [references/foreign-3mf.md](references/foreign-3mf.md)
- [references/mesh-measuring.md](references/mesh-measuring.md)
- [references/surface.md](references/surface.md)
