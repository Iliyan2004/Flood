# SAILAB Depth assets — v3

13 recolorable SVG silhouettes with named moving/flood-relevant parts. **100 SVG user units = 1 metre**. Each `viewBox` ends on the ground at its native height. All SVGs remain vector shapes; no raster imagery, gradients or filters are embedded in the sprites.

## Colour palette
CSS variables within the inline SVG: `--ink`, `--teal`, `--teal-dark`, `--cream`, `--sand`, `--sand-dark`, `--terracotta`, `--brick-dark`, `--metal`, `--metal-dark`, `--glass`, `--gold`. Override on an *inline SVG element*, e.g. `svg.style.setProperty('--teal', '#15a7af')`. External `<img src>` SVGs cannot be recolored by page-level CSS alone. Individual named `id` elements (e.g. `front_wheel`, `main_door`, `window_frame`) remain available for additional animation or masks. Consult `manifest.json`.

## Calibrated rendering
At `pxPerM = 100`, native SVG pixel widths and heights are exactly metre-calibrated. For another scale, apply identical factor to every sprite: `widthPx = nativeSvgWidth * pxPerM / 100`, `heightPx = nativeSvgHeight * pxPerM / 100` and align **bottom edges at one ground level**. Never use independently sized 'contain' thumbnails for measurements. Scene water height is `depthM * pxPerM`, applied from ground upward, not per-sprite.

Water can be drawn as a semi-transparent layer on top of silhouettes for teaching. It does not model velocity, wall strength, floating objects or pressure. Use actual validated geometry for physical flood-damage modelling; an attractive SVG is not a validated engineering geometry or loss model.

## Demo
Run `python -m http.server 8000` from this folder, and open `http://localhost:8000/demo.html`. This loads the 13 individual files into one canvas at the same physical scale; independent style overrides and waterline reference marks work. All data stays local.

## Production QA still recommended
Review silhouettes with Pakistani field references, remove decorative overlaps, inspect window sill and plinth dimensions, test at 40 px/m and 100 px/m, and verify pointer and colour contrast. This pack is a functional design iteration, not an illustration studio sign-off.
