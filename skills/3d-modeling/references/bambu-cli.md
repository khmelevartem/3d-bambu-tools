# Bambu Studio: the CLI and the file formats

```bash
BS=/Applications/BambuStudio.app/Contents/MacOS/BambuStudio
```

## CLI traps

**None of this is in the official documentation** — it is all established by
experiment, so re-check it rather than trusting it blindly.

1. `--outputdir` is mandatory and must be absolute, while the name given to
   `--export-3mf` must carry no path. Otherwise the slicer writes its temporary
   file relative to the filesystem root and dies.
2. **`--load-settings` accepts only a flat config.** A profile with `inherits`
   is not rejected: inheritance is silently ignored and the slicer's own
   defaults are substituted, so the slice "succeeds" with the wrong infill,
   first-layer width and brim. Flatten with `tools/resolve_profile.py`.
3. A flattened profile must keep `"from": "system"`, or the CLI refuses it as
   unsupported.
4. **A machine profile has a second assembly branch, the `include` key**,
   independent of `inherits`. The real start G-code — warm-up, bed levelling,
   nozzle wipe, flow calibration — lives in template files listed there, not in
   the machine profile itself. Resolving only `inherits` bottoms out at a
   generic common profile with a stub start, so the part prints at the stub's
   temperature without calibration and the time estimate comes out short.
   `resolve_profile.py` follows `include`. **Verify in the finished G-code**
   that the bed and nozzle temperature lines and the calibration commands are
   present.
5. `--export-png` takes a plate number, not a filename; a filename produces a
   parameter error.
6. macOS has no `timeout` command, so a `timeout … BambuStudio …` wrapper
   silently never starts and looks like a hung slicer.
7. If the CLI hangs with no output at all, macOS may be holding an invisible
   window-restore dialog after a crash. Check that the application's
   `ApplePersistenceIgnoreState` default is set.
8. **An invalid value for an enumerated setting is not rejected — the CLI
   silently substitutes its own.** The slice "succeeds" and the report says
   nothing. **The GUI labels do not match the config values**, so translating a
   label into a value by hand is how this happens.
9. **`curr_bed_type` defaults to a cold plate type**, and the bed then heats
   far below what PLA on a textured PEI plate needs. Write the real plate into
   the process profile. `slice.sh` substitutes it from `hardware.json`.
10. **Some settings live in the filament profile and override the process
    profile.** The scarf seam is the known case: while
    `override_filament_scarf_seam_setting = 0`, `filament_scarf_seam_type` from
    the filament wins and `seam_slope_type` from the process is ignored.

**Hence the rule: after slicing, read what was actually applied** — not the
profile you handed in, but the config inside the finished project:

```bash
unzip -p work/out/name.gcode.3mf Metadata/project_settings.config > work/applied.json
```

And whether a setting not only arrived but took effect is visible only in the
G-code itself: supports through the `; FEATURE:` markers (`gcode_report.py`
does this), a scarf seam through a Z rise inside the outer perimeter,
temperatures through the heating commands.

Flags beyond the basics: `--assemble`, `--export-stl` / `--export-stls`,
`--export-png` with `--camera-view`, `--scale`, `--rotate-x/y`,
`--clone-objects`, `--skip-objects`, `--repetitions`, `--ensure-on-bed`,
`--convert-unit`, `--export-settings`.

## Four different things share the 3MF extension

| What | Written by | What Bambu Studio does |
|---|---|---|
| STL | OpenSCAD, build123d | opens silently |
| 3MF per the specification | build123d | opens, but warns that only geometry was loaded |
| 3MF from `--export-3mf` | the Bambu Studio CLI | opens after the quotes are repaired, below |
| 3MF from `--min-save` | the Bambu Studio CLI | contains no geometry at all |

**The CLI writes `compatible_printers` with unescaped quotes inside an XML
attribute.** The parser then fails on a malformed token and the GUI reports
that the file contains no geometry data — while the geometry is intact. The
only cure is post-processing the finished file: replace the inner quotes with
entities and rebuild the zip. `slice.sh` does this itself. It cannot be fixed
through the profile: as a string the key still serialises with quotes, and
without the key slicing fails as incompatible.

## Handing over a painted project so that it opens

**A project whose `project_settings.config` was assembled in code does not open
in the GUI.** Neither re-exporting it through the CLI nor supplying a
hand-written `different_settings_to_system` changes that.

The route that works:

1. Build the project with **`--no-project`**: geometry and `paint_color` are
   there and the config is absent entirely. The GUI has nothing to reject and
   opens the file with the user's current settings.
2. The person edits the paint and saves. The file now has a config written by
   Bambu Studio itself, and it is guaranteed to open.
3. Send settings **into their file** with `tools/retune_project.py`, which
   carries values over from a reference project and changes nothing structural.

**Never invent `different_settings_to_system`.** That single key is the
difference between a file that opens and one that does not; everything else may
be byte-identical.

The keys listed there are not ours either — a person set them by hand, and a
reference project carries its own decisions for the same keys.
`retune_project.py` preserves them; without that, a settings port wipes their
work.

**Say this consequence out loud:** the GUI takes the system preset and overlays
only the keys from that list. Edits absent from it do reach a CLI slice, but
the person will not see them in the GUI. Numbers from `slice.sh` and
`figopt.py` on such a file therefore describe the CLI slice, not what the GUI
will show.

**A person saves the file several times in a row.** Check the file's
modification time before building on it, and afterwards compare a fingerprint —
the triangle count and the `paint_color` code counters must match between their
file and the result. `retune_project.py` does this and fails on a mismatch.

## Checking whether a file opens, without a human at the screen

The autosave folder substitutes for opening the GUI. Bambu Studio creates a
backup directory on every launch, including CLI runs, so the directory's
existence means nothing. **What matters is whether `3D/Objects/*.model` appears
inside it**, which happens only after the project has genuinely loaded.

**Always run a known-good file through the same check in the same session**, as
a control — not once at the beginning.

**What this does not distinguish**: a file the GUI rejected from a file the GUI
is showing a modal dialog about. Project files with a config do not pass the
test at all, including ones Bambu Studio wrote itself, so **treat the method as
a hypothesis until a human confirms.**

## Checking per-triangle paint

The code table, split triangles, adding a filament and speckle analysis are in
the **3mf-paint** skill, `references/paint-format.md`. They are not duplicated
here.

The short check that the slicer itself reads the paint, and not only our
parser, is a round-trip: per-code face counts must match what was marked up.
It works only when the file contains a config. **A green round-trip does not
prove the file opens in the GUI.**

## Opening several files at once

Bambu Studio asks whether to load the files as a single object with multiple
parts:

| What is being opened | Answer |
|---|---|
| unrelated parts onto one plate | **No** — otherwise they lose independence |
| colour pieces of one part | **Yes** — the pieces stay in place, each with its filament |

The CLI does not merge by default; `--assemble` is what merges.
