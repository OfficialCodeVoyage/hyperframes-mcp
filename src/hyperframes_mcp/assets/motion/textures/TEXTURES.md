# Textures of the motion library

Three transparent overlay tiles for the `finish` block (paper dust and fine scratches). They are derived from two maps of
[ambientCG](https://ambientcg.com), downloaded on 2026-10-05, and are not photographs of anything: each is a mask of specks and
scratches, painted in the paper colour (light specks, scratches) and the ink colour (a few dark specks) of `tokens.json`.

## Licence

ambientCG states: "All ambientCG assets are provided under the Creative Commons CC0 1.0 Universal License. This applies to the
downloadable asset files and the material preview renders" (https://docs.ambientcg.com/license, read 2026-10-05), and each asset
page says the same ("released under the Creative Commons CC0 license, making them free to use without attribution - even in
commercial circumstances"). Licence for the maps and for the tiles derived from them: **CC0 1.0** (SPDX `CC0-1.0`). No credit is
required; the source is recorded here and in `../THIRD_PARTY.md` anyway.

## Sources

| Asset | Page | Download (2K JPG zip) | File used | sha256 of that file |
|---|---|---|---|---|
| Scratches 003 | https://ambientcg.com/view?id=Scratches003 | https://ambientcg.com/get?file=Scratches003_2K-JPG.zip (18598002 bytes) | `Scratches003_2K-JPG_Opacity.jpg` (2048 x 2048, 8-bit grey) | `4cd1a45ff8fef9f1ac945b783fc762a66e1058cff444c386e7ca9868f0f6cb85` |
| Surface Imperfections 015 | https://ambientcg.com/view?id=SurfaceImperfections015 | https://ambientcg.com/get?file=SurfaceImperfections015_2K-JPG.zip (31351965 bytes) | `SurfaceImperfections015_2K-JPG_Opacity.jpg` (2048 x 2048, 8-bit grey) | `50ff5018cb21951bb53b8cc75303671852e73ae7e494d424f62e7b9c7c2e1d56` |

`SurfaceImperfections014` (the example in the brief) was looked at first and set aside: its map is a mottled stain, not dust, and at any
opacity it reads as grime. The thin scratches of `Scratches003` and the tiny specks of `SurfaceImperfections015` are what the finish needs.

## Files

| File | What it is | Size | Bytes |
|---|---|---|---|
| `finish-dust-a.png` | tile 1: window (0, 0) of the two maps | 1920 x 1080 RGBA | 351241 |
| `finish-dust-b.png` | tile 2: window (128, 968), mirrored left to right | 1920 x 1080 RGBA | 342550 |
| `finish-dust-c.png` | tile 3: window (60, 520), mirrored top to bottom | 1920 x 1080 RGBA | 364644 |
| `make_tiles.py` | the recipe below as code | | |

About 4% of each tile's pixels carry any alpha; the rest is transparent. All three are under the 1 MB each the brief allows.

## How the tiles were made

`python assets/motion/textures/make_tiles.py SCRATCHES_OPACITY.jpg DUST_OPACITY.jpg OUT_DIR` with the two files above gives
byte-identical copies of the three tiles (checked). In words, for each tile:

1. take a 1920 x 1080 window of each map (the offsets above), mirrored as listed, so the three tiles do not repeat each other;
2. dust: the white top-hat (bright spots smaller than 5 px) of the dust map, kept above 0.52 and ramped to full over 0.25, gives light
   specks; the black top-hat (dark spots smaller than 5 px), above 0.42, gives dark specks (at 80% strength). A softer cut kept half
   the frame covered: the dust map is a mottled surface and only its smallest bright and dark spots read as dust;
3. scratches: the scratch map, above 0.20 and ramped over 0.40, at 80% strength, in light;
4. light specks and scratches are painted in the `paper` colour of `tokens.json`, dark specks in its `ink` colour; the alpha is
   the strength of the speck, everything else is alpha 0; saved as an optimised RGBA PNG.

The block fades the whole overlay by `dustOpacity` (0.35 by default), so the strengths above are the ceiling, not the look.
