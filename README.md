# 3d-bambu-tools

Command-line tools for Bambu Studio `.3mf` files. Skills included, so an
agent can drive them.

**Do**

- Per-triangle paint — read, measure, rewrite. Repair a mesh, keep the paint.
- Split by colour into parts. Flat seams, blind sockets, printed pins.
- Printability — overhangs, supports, stair steps, genus, first-layer area.
- Slice from the CLI — grams, hours, supports yes/no.

**Facts**

- 30 tools, 5 skills, 115 regression cases.
- **Bambu Studio optional.** Only `slice.sh` needs it. Mesh, paint, split,
  measure: no slicer, no printer → [docs/without-a-printer.md](docs/without-a-printer.md)
- Blender needed by 3 tools. Free, separate install.
- Also Orca Slicer — same `paint_color` format, same `project_settings.config`.
- Built on a Bambu Lab A1, macOS. Its numbers are starting points.
- Terminal output: Russian. Docs, docstrings, skills: English.
- MIT.

## Install

Clone **into** a project, as its `tools/`. The layout is fixed —
`hardware.py` reads `../hardware.json`, `slice.sh` writes `../work/out`.

```bash
git clone https://github.com/khmelevartem/3d-bambu-tools.git my-print-project/tools
cd my-print-project
cp tools/hardware.example.json hardware.json   # your machine and filaments
python3 tools/hardware.py                      # prints what it resolved
```

For an agent — link the skills and the rules to the project root:

```bash
mkdir -p .claude/skills
for s in tools/skills/*/; do ln -s "../../$s" ".claude/skills/$(basename $s)"; done
ln -s tools/AGENTS.md AGENTS.md
```

Details: [docs/layout.md](docs/layout.md) — dependencies, preset names,
`BS` and `BLENDER` overrides.

## Skills

| Skill | For |
|---|---|
| `3d-modeling` | make, rework, measure, slice; routes to the rest |
| `mesh-repair` | holes, non-manifold, crumbs, will not slice |
| `3mf-paint` | paint edits, split by colour, seams and joints |
| `print-tuning` | cut plastic and time on one file |
| `model-vs-reference` | does it still look like the original |

## The tools

**Paint inside a 3MF** — the `paint_color` triangle format.

| Script | What it does |
|---|---|
| `paint.py` | parse, measure, normalise, write paint; add a filament; `explode` → one colour per triangle |
| `paint_normalize.py` | write an explicit filament code on faces that carry none — empty reads wrong outside the slicer |
| `paint_despeckle.py` | drop colour fragments thinner than a nozzle line |
| `paint_transfer.py` | carry paint from an old mesh onto a new one, by geometry |
| `paint_split.py` | cost of splitting by colour; seams fit for a butt joint; socket-and-pin plan |
| `paintview.py` | render paint headlessly; `pick`/`grid` say what is under a pixel |

**Mesh repair, paint preserved.**

| Script | What it does |
|---|---|
| `meshdoctor.py` | diagnosis by defect class, nozzle taken into account |
| `meshfix.py` | repair through headless Blender; `--put` swaps the mesh back into a 3MF |
| `weldmesh.py` | weld vertices, close unwrap seams, keep paint |
| `fixtjoints.py` | un-bake the T-joints the Bambu brush leaves; keeps paint |
| `meshsolid.py` | rebuild a torn mesh as a solid by winding number |
| `writeverts.py` | move vertices without renumbering faces — reshape, keep paint |

**Solid editing, splitting into parts.**

| Script | What it does |
|---|---|
| `solid_cut.py` | cut volume with real solids: planes, cylinders, sockets, pins, inlays. Verifies the parts reassemble |
| `pivot_joint.py` | joints: cut across the axis, paired blind sockets, separate pins |
| `balljoint.py` | **other people's** ball joints: find, profile, restore, assemble, measure |
| `partedit.py` | edit a foreign 3MF part-wise — weld on, fill, saw, drill; keeps paint |
| `graft.py` | rebuild a feature that was cut off: section, ribbon, faces appended |
| `place3mf.py` | put an object back on the middle of the bed |

**Printability.**

| Script | What it does |
|---|---|
| `printcheck.py` | watertight, genus, bounding box, overhangs. **Lies on foreign meshes** — use `meshdoctor.py` |
| `figcheck.py` | organic shapes: first-layer area, bottom slice, supports by ray, stair-step map; `--rx/--ry` sweeps tilt |
| `refcompare.py` | overlay the model on a photo, measure the drift |

**Slicing, project settings.**

| Script | What it does |
|---|---|
| `slice.sh` | weight, time, G-code, `.3mf`; `--supports` with supports. Writes `../work/out/` |
| `gcode_report.py` | G-code by line type, and supports yes / no |
| `figopt.py` | audit a project: settings, colour changes, flush cost, facts from the G-code |
| `retune_project.py` | port print settings into a project a human saved, keep their paint |
| `patch3mf.py` | change keys in `project_settings.config`; drop a filament |
| `set_extruder.py` | assign filaments by part name, after importing single-colour STLs |
| `make_multicolor_3mf.py` | build a painted project from a mesh plus zones |
| `resolve_profile.py` | flatten a profile's `inherits` and `include` chain |
| `hardware.py` | read `hardware.json`: nozzle, layer, line width, preset names |
## More

- [AGENTS.md](AGENTS.md) — rules for an agent working here
- [tests/README.md](tests/README.md) — the regression suite, `python3 tests/run.py`
- [docs/layout.md](docs/layout.md), [docs/without-a-printer.md](docs/without-a-printer.md)
