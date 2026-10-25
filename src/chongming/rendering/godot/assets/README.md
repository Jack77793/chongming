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

## Runtime use

The file is the unmodified upstream GLTF. The renderer makes the following
runtime-only changes:

- load the embedded meshes and textures with `GLTFDocument`;
- convert card materials from alpha blending to alpha scissor to avoid sorting;
- split each tree mesh into a trunk surface and a crown-card surface so the
  crown can receive a small translation/scale wind deformation;
- choose variants, scale and placement deterministically from the scene seed;
- use all tree, bush, hedge, clover, grass and flower nodes, grouped into the
  scene's tree/shrub/grass categories.

## Tree impostors

`impostors/Tree-*.png` are transparent billboards rendered from the same GLTF
variants. They are used only below the far screen-size LOD threshold, where the
tree is a few dozen pixels tall. Regenerate them with:

```sh
CHONGMING_GODOT=/path/to/Godot
"$CHONGMING_GODOT" --path src/chongming/rendering/godot \
  --script tools/generate_vegetation_impostors.gd \
  --rendering-method gl_compatibility --audio-driver Dummy
```

The GLTF contains the complete kit; the scene renderer currently maps its
30 plant nodes into 12 tree, 6 shrub and 12 grass variants.
