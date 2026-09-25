# Without a printer

Bambu Studio is needed by exactly two scripts: `slice.sh` runs it, and
`resolve_profile.py` reads the presets out of its bundle. Everything else is
plain Python over meshes and 3MF archives, so most of this repository is usable
with no slicer and no printer at all — as a workshop for fixing, measuring,
painting and splitting models that will be printed elsewhere, or not at all.

**What works.** Mesh diagnosis and repair (`meshdoctor.py`, `meshfix.py`,
`meshsolid.py`, `weldmesh.py`, `fixtjoints.py`), reshaping and solid editing
(`writeverts.py`, `partedit.py`, `graft.py`, `solid_cut.py`, `pivot_joint.py`,
`balljoint.py`), geometry checks (`printcheck.py`, `figcheck.py`), the whole
paint side (`paint.py`, `paint_split.py`, `paint_despeckle.py`,
`paint_transfer.py`, `paintview.py`) and comparison against a picture
(`refcompare.py`). Of the skills, **3mf-paint**, **mesh-repair** and
**model-vs-reference** apply in full, and most of **3d-modeling** does: none of
them closes with a slice, which happens only where the answer turns on it.

Blender is a separate requirement and an unrelated one: `meshfix.py`,
`solid_cut.py` and `pivot_joint.py` drive it headlessly whether or not a slicer
exists.

**What does not.** Slicing itself, and what only it can produce: no slice, no
grams and no hours for your own file. The **print-tuning** skill rests on
slicing a file two ways and comparing, so its main method is gone.

Its other tools are not, though, because they read files rather than make them.
`figopt.py audit` and `figopt.py colors`, `retune_project.py` and `patch3mf.py`
work on any 3MF that already carries settings — a downloaded project does.
`gcode_report.py` and `figopt.py gcode` work on any Bambu Studio G-code,
including one someone else sliced and sent.

`make_multicolor_3mf.py` sits in between. Building the settings block needs the
presets, so it stops with "профиль не найден"; with **`--no-project`** it writes
the geometry and the `paint_color` triangles and leaves settings out. That file
is a complete painted model to hand on — but `paint.py filament` and
`patch3mf.py` have no settings to edit in it, and say so.

**`hardware.json` is still required**, and the part of it that matters changes.
The non-slicing tools read physical numbers from it — nozzle diameter, layer
height, line width, density — and judge by them: what is thinner than a nozzle
line, how wide a stair step comes out, whether a colour patch can be printed at
all. Set those to the machine the model is going to be printed on. The preset
names are read only by the slicing path, so until something slices they are
inert, and the file copied from `hardware.example.json` runs as it is.

Modelling from code — OpenSCAD with BOSL2, or build123d, as the **3d-modeling**
skill describes — needs neither the slicer nor this repository's tools to run.
