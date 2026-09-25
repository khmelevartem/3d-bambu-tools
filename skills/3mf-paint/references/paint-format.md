# The paint format, and the profile

## Codes on a triangle

A `paint_color` value is not a filament number but a bit stream describing the
triangle's subdivision. On an unsplit triangle it is a state code:

| code | filament | | code | filament |
|---|---|---|---|---|
| no attribute | the object's extruder | | `1C` | 4 |
| `4` | 1 | | `2C` | 5 |
| `8` | 2 | | `3C` | 6 |
| `0C` | 3 | | `4C` | 7 |

For filament N ≥ 3 the form is `hex(N-3) + "C"`, up to N = 17.

## A face without the attribute is "state 0"

It prints with the filament assigned to the object in `model_settings.config`
under `<metadata key="extruder">`. When that is 1, "no attribute" and `4` are
the same thing **to the slicer**.

**When assembling a file, write a code on every face, the background
included.** Skipping the largest zone shortens the file negligibly — the XML is
zipped — and breaks every reader that is not the slicer: outside Bambu Studio
such faces take an arbitrary material. MakerWorld originals carry a code on
every face without exception.

**An explicit background code shifts the slice slightly**, because state 0 and
an explicit extruder 1 go through different segmentation branches and colour
borders move by fractions of a line width. Do not claim it has no effect;
structurally nothing changes.

**When editing someone else's finished file, leave the original variant where
the colour did not change** — a minimal diff matters more than uniformity.
Normalising a whole file is a separate, deliberate operation:

```bash
python3 tools/paint_normalize.py in.3mf out.3mf
```

It adds the object's extruder code to every face lacking one and then compares
input with output triangle by triangle. **A mismatch exits non-zero and the
file must not be handed over.**

## Split triangles

Anything not in the table is a triangle painted in several colours: the GUI
brush splits it into subtriangles and writes the subdivision tree as a bit
stream.

Editing paint does not require decoding it. Either keep the string verbatim and
exclude the face (`--splits keep`), or hand the face to the graph cut with zero
attachment cost (`--splits free`) so it joins whichever side shortens the
border. **The second erases sub-triangle precision**: afterwards the border
runs along mesh edges only.

**Cutting a model by colour does require decoding**, because the whole colour
border can lie inside brush-split faces. `explode` decomposes split faces into
subtriangles and welds them into a mesh where every face is single-coloured:

```bash
$UV tools/paint.py explode project.3mf work/p.npz --object Cube
```

`--object` selects by name or number; each object lives in its own
`3D/Objects/object_N.model`.

### The bit stream

| Field | Bits | Meaning |
|---|---|---|
| sides split | 2 | 0 — a leaf, 1–3 — a node |
| the special side | 2 | nodes only; always 0 with three cuts |
| leaf state | 2 | 0 — the object's extruder, 1 and 2 — filaments 1 and 2 |
| long state | 2 + 4 | `11` followed by the nibble `N − 3` |

The string is read **right to left**: nibbles are inserted at the front, and
within a nibble the least significant bit comes first. Side `t` runs between
vertices `t` and `t+1`. One cut splits the special side; two cuts split the
sides meeting at the vertex of the special side; three split all of them.
**Children are stored back to front.**

**Always render the result after `explode`.** A wrong child order produces a
recognisable fractal mess instead of clean zones, which is obvious in a picture
and invisible in the numbers. `Metadata/plate_1.png` inside the 3MF is Bambu
Studio's own render and serves as the reference to match.

**The mesh produced by `explode` is for colour only.** Neighbouring faces split
to different depths, so it carries T-joints and open edges; cut the original
mesh, never this one.

Meshes arrive with no split codes — those appear only after hand brushwork.

## Speckle after generation

Generated paint arrives with hundreds of isolated islands one or two triangles
across, each an extra filament change on its layer. A threshold of a couple of
square millimetres removes them while leaving real small features such as the
whites of eyes.

## Adding a filament to a project

Filament settings in `Metadata/project_settings.config` are **not one value per
filament**. Lists come in three kinds: one entry per filament, two entries per
filament (normal and high-flow nozzle), and four entries per filament.

**Extend all of them.** Leave one list at the old length and Bambu Studio reads
past the end of a vector; the dialog mentions `vector` and then reports that
the file contains no geometry, on perfectly intact geometry.

Special cases:

- `filament_self_index` — append the new filament's own number, not a copy of
  another block;
- `flush_volumes_matrix` — an N x N matrix, rebuilt as (N+1) x (N+1);
- `filament_nozzle_map`, `filament_volume_map` — fixed slots, do not touch;
- `filament_maps` and `filament_volume_maps` on the plate in
  `Metadata/model_settings.config` — append one value each;
- write JSON with `ensure_ascii=True` and CRLF, as Bambu Studio does.

**Lists of the same length that must not be extended** because they are not
about filaments: `machine_max_*`, `extruder_*`, `printable_area`,
`bed_exclude_area`, `print_compatible_printers`, `nozzle_diameter`.

`tools/paint.py filament source.3mf new.3mf '#C12E1F'` does all of it and
fails if any list was left at the old length.

### How many colours fit

AMS lite has four slots. A fifth filament is accepted by the project and by the
slicer, but **whether such a print runs on a single AMS lite is not covered by
Bambu's documentation** — there is no page about a colour limit. Check at slot
assignment in the GUI; the minus button in the Filament block is a cheap
rollback.

A fallback needing no profile edit: write the paint with the fifth filament's
code into a four-filament project — the slicer preserves the code — and ask the
user to add the filament and pick its colour by hand.

## Round-trip check

The slicer loads the file with its own loader and writes the attributes back;
per-code face counts must match what was marked up.

```bash
"$BS" --export-3mf o.3mf --outputdir "$PWD/out" model.3mf
unzip -p out/o.3mf 3D/Objects/object_1.model \
  | grep -o 'paint_color="[0-9A-Fa-f]*"' | sort | uniq -c
```

It works only when the file contains `project_settings.config`.

**It does not prove the file opens in the GUI.** The CLI is more tolerant and
does not read some settings at all.

## Geometry check

Compare **numbers, not md5**: saving from the GUI rewrites `.model` with
different line endings, so md5 diverges on completely intact geometry. The
correct check is the maximum vertex-coordinate deviation from the source, and
it must be exactly zero.
