# Third-party code and data

What this repository copies or derives from, under which licence, and where it lives. The motion library's own list
(fonts, textures, the blocks derived from HyperFrames and the student kit, the map data) is
`src/hyperframes_mcp/assets/motion/THIRD_PARTY.md`; this file covers the rest and names what is installed rather than
copied.

## Icon subset (`src/hyperframes_mcp/assets/icons`)

A curated subset, 410 icons in all (about 180 KB of SVG, the licence texts and `manifest.json`), of three open icon sets,
for the `search_icons` and `save_vector` tools (`src/hyperframes_mcp/motion/vectors.py`). Each icon is the upstream file at
the pinned commit, kept as it is except for these changes, made to every file: the XML declaration, comments, `id`,
`width`, `height` and a transparent bounding-box path (Tabler) dropped; whitespace in the path data collapsed; one root
`fill="currentColor"` (Maki, Material Symbols) or the root `stroke="currentColor"` Tabler already has, so the
`graphic-stamp` block can recolour it. `assets/icons/manifest.json` lists every icon with its set, tags and upstream file,
and every set with its licence file, version and pinned commit. The tags of Maki are ours (it ships none); those of Tabler
are the ones in the header of each upstream file; those of Material Symbols come from Google's icon metadata. No icon of
these licences needs an on-screen credit; the notice is kept in the manifest and in each vector's record.

| Set | Source | Pinned commit | Licence | Notice | Our files |
|---|---|---|---|---|---|
| Maki | https://github.com/mapbox/maki, `icons/` (tag v8.0.1) | `9fa4d9df8ba25ce84190ecfb237a14d807e7dfbc` | CC0-1.0, `assets/icons/maki/LICENSE.txt` | Mapbox, public domain dedication | `assets/icons/maki/*.svg` |
| Tabler Icons | https://github.com/tabler/tabler-icons, `icons/outline/` (tag v3.49.0) | `bbed884d15354b5cebf2493371f20dc2d5e83eaf` | MIT, `assets/icons/tabler/LICENSE.txt` | Copyright (c) 2020-2026 Paweł Kuna | `assets/icons/tabler/*.svg` |
| Material Symbols | https://github.com/google/material-design-icons, `symbols/web/<name>/materialsymbolsoutlined/<name>_24px.svg` (the master branch of 2026-10-02) | `737e3324305806514d7909874fa1818ae1808232` | Apache-2.0, `assets/icons/material-symbols/LICENSE.txt` | Copyright Google LLC | `assets/icons/material-symbols/*.svg` |

## Blocks derived from HyperFrames and the student kit

Some blocks of the motion library start from components of the HyperFrames registry (Apache-2.0, Copyright 2026 HeyGen,
Inc.) or of nateherkai/hyperframes-student-kit (MIT, Copyright (c) 2026 Nate Herk). Each is rewritten to the component
contract and keeps its notice in its header comment; the pinned commits and what was changed are in
`src/hyperframes_mcp/assets/motion/THIRD_PARTY.md`. This project is not affiliated with or endorsed by HeyGen.

## Installed, not copied (`hyperframes-mcp setup`)

`hyperframes-mcp setup` runs `npm ci` against `src/hyperframes_mcp/node/package-lock.json` and puts these in
`~/.hyperframes-mcp/data/node`. Nothing below is committed here.

| Package | Version | Licence | Used for |
|---|---|---|---|
| `hyperframes` | pinned in `node/package.json` | Apache-2.0 | the CLI the server runs for timeline edits, history, lint and render checks |
| `gsap` | pinned in `node/package.json` | GSAP Standard "no charge" License, https://gsap.com/standard-license | the animation library every block page loads. It is **not an OSI-approved open source licence** and is therefore never copied into this repository or its package; read its terms before you change how a composition is rendered or shared |
| `d3`, `topojson-client` | pinned | ISC | map blocks |
| `world-atlas` | pinned | ISC; the data is Natural Earth, public domain | the map data the map blocks draw |

## Python dependencies

Installed from PyPI by `pip` / `uv`; see `pyproject.toml` for the list and each package for its licence.
