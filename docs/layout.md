# Layout and setup

This repository **is** the `tools/` directory of a print project. Configuration
and scratch files live one level above it, and the code depends on that:
`hardware.py` resolves its config as `__file__.parent.parent / "hardware.json"`,
and `slice.sh` writes its output to `../work/out`.

```
my-print-project/
├── hardware.json      ← your machine and filaments (from hardware.example.json)
├── models/            ← your models
├── work/              ← scratch; slice.sh writes work/out/
└── tools/             ← THIS REPOSITORY
    ├── hardware.py
    ├── paint.py
    └── ...
```

The install commands are in the [README](../README.md). Two notes on them:

Both symlinks point into `tools/`, so a `git pull` there updates the rules and
the skills in place. If the project already has its own `AGENTS.md` or
`CLAUDE.md`, keep it and point at `tools/AGENTS.md` from there.

`hardware.json` is the single source for nozzle diameter, layer height, line
width and preset names — no script keeps those as constants. Change the nozzle
in the machine, edit `nozzle.installed`, done. One-off overrides:
`A1_NOZZLE=0.2` for the nozzle, `A1_HARDWARE=/path/to.json` for the whole file.

Preset names must exist in your Bambu Studio installation, spelled exactly as
its profile JSON spells them. Take the numbers from the installed profiles with
`resolve_profile.py`, not from the wiki.

## Dependencies

Nothing to install ahead of time. Scripts that need packages document a `uv`
invocation in their own docstring, e.g.

```bash
uv run --quiet --with numpy --with scipy --with trimesh python tools/paint.py …
```

| Needs | Scripts |
|---|---|
| stdlib only | `hardware.py`, `printcheck.py`, `gcode_report.py`, `patch3mf.py`, `resolve_profile.py`, `retune_project.py`, `set_extruder.py`, `paint_normalize.py` |
| `numpy` (+`scipy`) | most of the paint and mesh tools |
| `trimesh` | `balljoint.py`, `figcheck.py`, `make_multicolor_3mf.py` |
| `shapely`, `scikit-image` | `solid_cut.py`, `meshsolid.py` |
| `Pillow` | `refcompare.py` |
| Blender installed as an application | `meshfix.py`, `pivot_joint.py`, `solid_cut.py` |
| Bambu Studio installed | `slice.sh`, `resolve_profile.py`, `figopt.py` |

Blender is needed as an **installed application**, not as the `bpy` package:
those three scripts relaunch themselves inside it headlessly. Both it and Bambu
Studio default to the macOS bundle and are overridden by environment variable:

```bash
BS=/usr/bin/bambu-studio BLENDER=/usr/bin/blender python3 tools/meshfix.py …
```

`resolve_profile.py` reads the presets straight out of the Bambu Studio bundle
and has no such override — outside macOS, edit `ROOT` in it.
