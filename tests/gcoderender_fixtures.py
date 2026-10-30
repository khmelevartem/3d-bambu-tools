"""A hand-written G-code and its target part for gcoderender.py. Deterministic.

    uv run --with numpy --with trimesh python tests/gcoderender_fixtures.py <outdir>

target.stl   relief 0.6 mm on z 2.0-2.6, part coordinates: a bar 10x3.78 (nine lines) at the
             origin, a dot d 0.8 at (14, 2), a disc d 4 at (20, 2).
plate.gcode  one base layer on filament 1 and the top layer z 2.6 on filament 2,
             shifted by (+100, +50): the bar filled by straight lines, the disc by
             full G2 circles, the dot left out - the way a slicer drops it.
             Every line is 0.42 wide at layer 0.2, inset half a line from the
             outline on all sides, so the footprints centre exactly.
"""
import sys, pathlib
import numpy as np
import trimesh

out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
DX, DY, W, H, DIA = 100.0, 50.0, 0.42, 0.2, 1.75
A = np.pi * (DIA / 2) ** 2
E = lambda L: ((W - H) * H + np.pi * H * H / 4) * L / A

bar = trimesh.creation.box(extents=(10, 3.78, 0.6)); bar.apply_translation((5, 1.89, 2.3))
dot = trimesh.creation.cylinder(radius=0.4, height=0.6, sections=64); dot.apply_translation((14, 2, 2.3))
disc = trimesh.creation.cylinder(radius=2.0, height=0.6, sections=128); disc.apply_translation((20, 2, 2.3))
trimesh.util.concatenate([bar, dot, disc]).export(out / "target.stl")

g = ["; HEADER_BLOCK_START", "; filament_diameter: 1.75,1.75", "; HEADER_BLOCK_END",
     "M83", "M620 S0A", "; Z_HEIGHT: 0.2", "; LAYER_HEIGHT: 0.2", "; FEATURE: Outer wall",
     f"G1 X{DX - 2:.3f} Y{DY - 2:.3f} F3000", f"G1 X{DX + 24:.3f} Y{DY - 2:.3f} E{E(26):.5f}",
     f"G1 X{DX + 24:.3f} Y{DY + 6:.3f} E{E(8):.5f}",
     "; FEATURE: Prime tower", f"G1 X{DX + 60:.3f} Y{DY:.3f} F3000", f"G1 X{DX + 80:.3f} Y{DY:.3f} E{E(20):.5f}",
     "M620 S1A", "; Z_HEIGHT: 2.6", "; LAYER_HEIGHT: 0.2", "; FEATURE: Top surface"]
for y in np.arange(0.21, 3.80, W):
    g += [f"G1 X{DX + 0.21:.3f} Y{DY + y:.3f} F3000", f"G1 X{DX + 9.79:.3f} Y{DY + y:.3f} E{E(9.58):.5f}"]
g.append("; FEATURE: Outer wall")
for r in (1.79, 1.37, 0.95, 0.53, 0.11):
    sx, sy = DX + 20 + r, DY + 2
    g += [f"G1 X{sx:.3f} Y{sy:.3f} F3000", f"G2 X{sx:.3f} Y{sy:.3f} I{-r:.3f} J0 E{E(2 * np.pi * r):.5f}"]
(out / "plate.gcode").write_text("\n".join(g) + "\n")
print("фикстуры:", out)
