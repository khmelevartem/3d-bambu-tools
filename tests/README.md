# Regression suite

Runs every tool in this repository against generated fixtures and checks what
came out, not merely that the process exited zero.

```bash
python3 tests/run.py                # everything
python3 tests/run.py --only=paint   # cases whose name contains "paint"
```

Exit code 0 means every case that could run, passed. **A missing Bambu Studio
or Blender is a skip, not a failure** — the suite has to be runnable on a
machine with neither, which is also the machine most likely to break those
paths without noticing.

## The fixtures are synthetic on purpose

Real models cannot ship here: they are someone's files, and they are large. So
`fixtures.py` generates shapes that exercise the same code paths — an organic
body with a jutting arm for overhangs, a sphere damaged with one defect of each
class, two disjoint shells, a torus for genus, and painted projects built from
zone arrays. They are deterministic: the same bytes and the same numbers on
every machine, which is what makes an assertion on a number possible at all.

They are built on first run into `tests/.fixtures/` and left there. Delete that
directory to rebuild.

**What synthetic fixtures do not cover**: meshes from generators, paint applied
with the Bambu brush, projects saved by the GUI. Those live in whoever's model
folder, so that layer of the regression belongs next to the models, outside
this repository.

## Fixed hardware

Every case runs with `A1_HARDWARE` pointing at `hardware.example.json`, so the
expected numbers do not depend on what the person running it has in their own
`hardware.json`. Never assert against the live config.

## Adding a case

```python
from harness import case, run, num, close, contains, work, FIX, TOOLS

@case("meshdoctor:a clean sphere comes out clean")
def _():
    out = run([TOOLS / "meshdoctor.py", FIX / "ball.stl"], deps=("numpy", "scipy"))
    close(num(out, r"объём ([\d.]+) мм³"), 14015.5, 0.1, "volume")
    contains(out, "ВЕРДИКТ: ЧИСТО")
```

- `run(argv, deps=…, expect=…)` runs a tool the way a person does: through
  `uv run --with …` when `deps` are given, through the system python3 when they
  are not. It fails the case on an unexpected exit code and on a traceback
  printed alongside a zero one.
- `needs=("bambu",)` or `needs=("blender",)` marks a prerequisite. Without it
  the case is skipped rather than failed.
- `work("name")` gives a clean scratch directory. Nothing is ever written next
  to a model.
- Assert on **content**. A case that only checks the exit code passes on a tool
  that has started answering nonsense.

## The hook

`hooks/pre-push` runs the suite before every push; install it with
`git config core.hooksPath hooks`. `SKIP_REGRESS=1 git push` pushes past a red
suite, which is what you want when the thing being fixed is the test itself.
