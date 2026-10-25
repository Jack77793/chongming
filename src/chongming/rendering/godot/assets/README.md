# Vendored low-poly exterior plants

`shapespark_low_poly_plants.gltf` is the **Shapespark Low-Poly Exterior Plants Kit**,
obtained from:

<https://github.com/shapespark/shapespark-assets/tree/main/shapespark-low-poly-plants-kit>

The kit is dedicated to the public domain under **CC0 1.0 Universal**. The full
license text is in [`CC0-1.0.txt`](CC0-1.0.txt). Attribution is not required by
CC0, but we record the source here and in the project documentation.

SHA-256 of the vendored file:

```text
ac33eec69ab0ace22817af29a40a8cf3a4bdf5e560ffdd060a5cd9c4b9b7a2b7
```

## Local modifications

The file is the unmodified upstream GLTF. The renderer makes the following
runtime-only changes:

- load the embedded meshes and textures with `GLTFDocument`;
- convert card materials from alpha blending to alpha scissor to avoid sorting;
- split each tree mesh into a trunk surface and a crown-card surface so the
  crown can receive a small translation/scale wind deformation;
- choose variants, scale and placement deterministically from the scene seed.

The GLTF contains the complete kit (trees, bushes, hedges, clover, grass and
flowers). Only the tree, bush and grass nodes are currently used by the scene
renderer; the remaining nodes stay available for later vegetation work.
