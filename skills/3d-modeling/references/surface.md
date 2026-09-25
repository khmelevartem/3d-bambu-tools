# Surface texture

Three different things get called "texture":

| | What it is | Prints |
|---|---|---|
| a UV image, a PBR material | colouring for rendering | no, it does not affect FDM |
| relief / displacement | geometry or nozzle path changes | yes |
| Color Painting | assignment to AMS filaments | yes, but that is colour, not texture |

## Fuzzy skin

It edits the G-code rather than the mesh: the nozzle wanders around the wall's
original path, so **the model cannot be damaged by it.**

| Setting | Key | Stock default |
|---|---|---|
| Fuzzy Skin | `fuzzy_skin` | `none` |
| Fuzzy skin thickness | `fuzzy_skin_thickness` | 0.3 |
| Fuzzy skin point distance | `fuzzy_skin_point_distance` | 0.8 |

The defaults give a coarse, plush roughness. **For a technical look take finer
and denser** — start at 0.2 / 0.2, outer surfaces only — and set a random seam
position, because the seam then hides in the roughness.

Finer control: `fuzzy_skin_mode`, `fuzzy_skin_noise_type`, `fuzzy_skin_scale`,
`fuzzy_skin_octaves`, `fuzzy_skin_persistence`, `fuzzy_skin_first_layer`. The
Extrusion and Combined modes **require the Arachne wall generator**.

## Relief from a height map

A greyscale map can deform a mesh through an external displacement service that
accepts STL, OBJ, 3MF and STEP. Limits stated by such services: results are
worse on organic shapes than on simple surfaces; different textures on
different parts need several export-import passes; a texture height above about
10 % of the smallest dimension causes self-intersections; and the output mesh
is decimated to a triangle limit.

**Variable layer height conflicts with the prime tower**, and therefore with
multicolour printing.
