# Working in a 3D-print project

These are the rules of `tools/`: command-line tools for Bambu Studio 3MF files,
plus the skills that say how to use them. They are meant to be driven by an AI
agent working alongside the person who owns the printer. The file lives in
`tools/AGENTS.md` and is usually read through a link at the project root, so
paths below are written from that root.

## Layout contract

The repository must be checked out as `tools/` inside a project folder.
Configuration and scratch files live one level above it, and the code depends
on that: `hardware.py` resolves its config as `__file__.parent.parent /
"hardware.json"`, and `slice.sh` writes to `../work/out`.

```
my-print-project/
├── hardware.json      <- the machine and filaments (from hardware.example.json)
├── models/            <- what gets printed, plus reference images
├── work/              <- scratch; expendable
└── tools/             <- THIS REPOSITORY
    ├── skills/        <- the skills; see below
    └── *.py, *.sh
```

Paths inside the skills are written relative to the project root
(`tools/paint.py`), so run commands from there.

## Skills

`skills/` holds five skills. An agent that supports the Claude Code skill
convention picks them up from `.claude/skills/<name>`, so link or copy them:

```bash
for s in tools/skills/*/; do ln -s "../../$s" ".claude/skills/$(basename $s)"; done
```

| Skill | When it applies |
|---|---|
| `3d-modeling` | make, rework, measure or slice a part; the head of the set, with a routing table to the others |
| `mesh-repair` | the slicer complains about the mesh, or a model will not slice |
| `3mf-paint` | per-filament paint inside a finished 3MF, and splitting a model by colour into parts |
| `print-tuning` | cut plastic and time on a specific file before printing |
| `model-vs-reference` | does the model still look like the reference picture |

Each skill's front matter carries its own trigger list. The bodies are written
as instructions, not as a history of how they were arrived at.

## Rules that apply to the whole repository

**`hardware.json` is the single source for the machine.** Nozzle diameter,
layer height, line width and preset names are read through `tools/hardware.py`
and are never hard-coded, and never taken from memory when answering. One-off
override without editing the file: the `A1_NOZZLE` environment variable.

**Everything temporary goes in `work/`** — intermediate meshes, npz files,
renders, slices, throwaway scripts. A model folder holds only what gets
printed, plus reference images. When a finished artefact is placed next to a
model, say so.

**A tool that carries its own dependency header runs through plain `uv run`.**
`figcheck.py` and `paint_normalize.py` start with a PEP 723 block, so
`uv run tools/figcheck.py part.stl` installs exactly what they need. Adding
`--with ...` to such a call replaces the header instead of extending it, and
the tool then dies deep inside a library on a missing transitive dependency —
`trimesh` needs `shapely` and `networkx` to turn a section into polygons. Tools
without a header take their dependencies on the command line, as their own
docstring shows.

**Measure; do not assume.** Most of what is recorded here contradicts a
plausible default, which is why it is written down at all. Where a tool reports
a number, quote the number in the answer rather than a verdict: "supports
0.6 g, 2 min", not "supports are fine".

**A green CLI run does not prove a file opens in the GUI.** Bambu Studio's
command line and its interface read projects through different code paths.
Anything that must open in the interface has to be checked by a person, and the
request to check has to say exactly what to look at.

**Do not repair what cannot reach the plastic.** A defect finer than the nozzle
will not exist in print, and repairing it risks the paint and the topology for
nothing.

**Paint is bound to triangle indices.** Anything that rebuilds a mesh destroys
it. Tools that preserve face numbering (`writeverts.py`, `weldmesh.py`,
`fixtjoints.py`, `partedit.py`, `graft.py`) exist precisely so that paint
survives; prefer them, and transfer the colour by geometry only when there is
no alternative.

**These tools write to the person's files.** Keep a copy of anything painted by
hand before pointing a script at it, and never overwrite a file the person is
editing without checking its modification time first.

## Contributing

In-code documentation states rules, thresholds and contracts. It does not carry
dates, file names from someone's disk, or an account of what was tried before
the current approach; that belongs in commit messages. Terminal output is in
Russian, because the tools were written for a Russian-speaking owner; the
documentation is in English.
