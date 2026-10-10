# Third-party files in the motion library

What this folder (`src/hyperframes_mcp/assets/motion`) copies from elsewhere, under which licence, and what we changed. The
repository-wide list is `THIRD_PARTY.md` at the root; this file covers only this folder and is the one to extend when a
component is vendored.

## GSAP

GSAP is **not committed here and not in the package**. `hyperframes-mcp setup` installs it from npm into
`~/.hyperframes-mcp/data/node`, and installing a block into a HyperFrames project copies
`node_modules/gsap/dist/gsap.min.js` from there to the project's `vendor/gsap.min.js` (`src/hyperframes_mcp/motion/registry.py`,
`gsap_source`). The person who uses the library fetches it from npm under its own terms.

| | |
|---|---|
| File | `gsap/dist/gsap.min.js` of the npm package, copied unmodified into a project (sha256 `c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280`) |
| Version | 3.14.2, pinned in `src/hyperframes_mcp/node/package.json` and its `package-lock.json` |
| Licence | GSAP Standard License ("no charge"), https://gsap.com/standard-license; the file header reads "Copyright 2025, GreenSock. All rights reserved. Subject to the terms at https://gsap.com/standard-license" |
| Note | **This is not an OSI-approved open source licence**, which is why it is never copied into this repository or its package. Read the terms at https://gsap.com/standard-license before changing how the library is used or shared. |
| Why a local copy in the project | A composition must never load GSAP from a CDN: the renderer runs without the network. |

## Fonts

All SIL Open Font License 1.1, unmodified, with the licence text beside each family. File names, sizes, source URLs and the
tabular-figure findings are in `fonts/FONTS.md`.

| Family | Files | Licence | Source |
|---|---|---|---|
| Newsreader | `fonts/Newsreader-Variable.ttf`, `fonts/Newsreader-Italic-Variable.ttf` | OFL-1.1, `fonts/OFL-Newsreader.txt` | `ofl/newsreader` in https://github.com/google/fonts |
| IBM Plex Sans Condensed | `fonts/IBMPlexSansCondensed-Medium.ttf`, `-SemiBold.ttf`, `-Bold.ttf` | OFL-1.1, `fonts/OFL-IBMPlexSansCondensed.txt` | `ofl/ibmplexsanscondensed` in https://github.com/google/fonts |
| Courier Prime | `fonts/CourierPrime-Regular.ttf`, `fonts/CourierPrime-Bold.ttf` | OFL-1.1, `fonts/OFL-CourierPrime.txt` | `ofl/courierprime` in https://github.com/google/fonts |

## Textures

The three `finish` overlay tiles in `textures/` are derived (a mask of specks and scratches, painted in the paper and ink colours)
from two ambientCG maps. File sizes, recipe and hashes of the sources are in `textures/TEXTURES.md`.

| Asset | Source URL | Licence | Size | What we changed |
|---|---|---|---|---|
| Scratches 003 (`Scratches003_2K-JPG_Opacity.jpg`) | https://ambientcg.com/view?id=Scratches003 | CC0 1.0 | 2048 x 2048 source; tiles 1920 x 1080 | cropped to 1920 x 1080 windows, thresholded to light scratches, painted in the paper colour; `textures/finish-dust-a.png`, `-b.png`, `-c.png` (351241, 342550 and 364644 bytes) |
| Surface Imperfections 015 (`SurfaceImperfections015_2K-JPG_Opacity.jpg`) | https://ambientcg.com/view?id=SurfaceImperfections015 | CC0 1.0 | 2048 x 2048 source; tiles 1920 x 1080 | cropped and mirrored to three windows, only its smallest bright and dark spots kept (top-hat), painted in the paper and ink colours; same three files |

## Vendored items

A component copied from an upstream registry (HyperFrames or another) is the starting point only: it is rewritten to the
component contract, keeps its licence notice in its header comment, and gets a row here and a `source` (url and pinned SHA)
plus `notes` in `registry.json`. A test fails when a registry item with a `source` is missing from this table.

| Item | Source URL | Pinned SHA | Licence | What we changed |
|---|---|---|---|---|
| `lower-third` | https://github.com/heygen-com/hyperframes, `registry/blocks/lt-accent-underline` (the block `lt-accent-underline`) | `568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c` | Apache-2.0, Copyright 2026 HeyGen, Inc. | Kept the stack of name, drawn rule and role and the staggered entrance; rewrote to the contract: variables, a paper band with a double rule in ink, tokens only, no overshoot eases, no text shadow, the timeline fills `durationSeconds`, a short slot throws. |
| `act-title` | https://github.com/heygen-com/hyperframes, `registry/components/titlecard-calm` (the component `titlecard-calm`) | `568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c` | Apache-2.0, Copyright 2026 HeyGen, Inc. | Kept the kicker over a large title and the fade of the whole lockup; rewrote to the contract: a block on the 1920 by 1080 canvas, variables, night ground with gold kicker and rule, a word-by-word clip-path reveal, no drift, no sine ease, the timeline fills `durationSeconds`, a short slot throws. |
| `quote-card` | https://github.com/nateherkai/hyperframes-student-kit, `style-library/01-vox-explainer/cards/tier2/t2-lb-pullquote.html` | `0d30152a82b9ceb93cfdd9bdbf46f0d5ab3cde86` | MIT, Copyright (c) 2026 Nate Herk | Kept the opening quotation mark, the quote and the attribution line; rewrote to the contract: variables, a paper card over the night ground, tokens only, no overshoot or step eases, lines broken from font advance widths and revealed one by one, the timeline fills `durationSeconds`, a short slot throws. |
| newspaper-clipping (from `torn-paper-rip`) | https://github.com/heygen-com/hyperframes/tree/568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c/registry/components/torn-paper-rip | 568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c | Apache-2.0 | Idea only, no line copied: one seeded edge table (slow noise wander plus a per-vertex jitter) and a lighter paper-core band along the tear. Rewritten with a mulberry32 generator seeded by `edgeSeed`, a static edge, token colours, no tear animation, no filter, no shadow |
| newspaper-clipping (from `freeze-frame-dressing`) | https://github.com/heygen-com/hyperframes/tree/568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c/registry/blocks/freeze-frame-dressing | 568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c | Apache-2.0 | Idea only: translucent tape strips on a sheet. Rewritten as two tilted strips with pinked ends in a paper tint, no shadow, no flash |
| newspaper-clipping (from `t1-section-tornstrip`, `t1-section-classified`) | https://github.com/nateherkai/hyperframes-student-kit/tree/0d30152a82b9ceb93cfdd9bdbf46f0d5ab3cde86/style-library/01-vox-explainer/cards/tier1 | 0d30152a82b9ceb93cfdd9bdbf46f0d5ab3cde86 | MIT | Idea only: a title on a clip-path torn paper strip, taped paper cards. Rewritten with tokens only, no CDN script, no drop shadow, no bounce or stepped ease, `fromTo` on one paused timeline |
| `map-kit` (`data/map-germany.js`, `data/map-europe.js`, `data/map-world.js`, built by `python -m hyperframes_mcp.motion map-data`) | https://github.com/nvkelso/natural-earth-vector (the `geojson/` folder) | `ca96624a56bd078437bca8184e78163e5039ad19` | Natural Earth, public domain (https://www.naturalearthdata.com/about/terms-of-use/); no credit needed | Clipped to each region's window, projected (equirectangular, Lambert conformal conic, Natural Earth), simplified with Douglas-Peucker (0.15, 0.5 and 1.0 map units), land and boundary lines and rivers as SVG paths, cities as a table. Source files, sha256 and parameters: `data/DATA.md`. |

The projections in `js/map-lib.js` and `src/hyperframes_mcp/motion/maps.py` are written by us from published formulas, not copied: the Lambert
conformal conic from Snyder, *Map Projections: A Working Manual* (USGS Professional Paper 1395, a US government work), and the
Natural Earth projection from its polynomial, designed by Tom Patterson (2007) and approximated by Bojan Savric with Patterson and
Bernhard Jenny (ETH Zurich); the coefficients are those published in PROJ's `natearth.c` (MIT), read on 2026-10-05, and the tests
run the formula on known points.

Items that are ours (`source: null` in `registry.json`, for example `credit-line`) are hand-written and share no code with
any upstream item; their licence field is `Apache-2.0`.
