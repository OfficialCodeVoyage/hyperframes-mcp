# Fonts of the motion library

Three families, all SIL Open Font License 1.1 (SPDX `OFL-1.1`). Downloaded on 2026-10-05 from the `google/fonts` repository
on GitHub (branch `main`, folder `ofl/<family>/`), unmodified; only the two Newsreader files were renamed, to drop the
brackets and comma of the axis list (`Newsreader[opsz,wght].ttf` became `Newsreader-Variable.ttf`). The byte sizes below
are those of the files in this folder and equal the sizes GitHub listed for the originals.

I read each folder's `OFL.txt` (the licence text is "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in all three) and each
`METADATA.pb` (`license: "OFL"` in all three). The upstream projects named there:

| Family | Upstream | Commit that `METADATA.pb` records |
|---|---|---|
| Newsreader | https://github.com/productiontype/NewsReader (Production Type) | `1ece6a8bfe5db1a2b90c76cc1fe5d3b2eed5dcf3` |
| IBM Plex Sans Condensed | https://github.com/googlefonts/plex (IBM, Bold Monday) | `71d012bccb31a2e282cc46de63b387ff7f676287` |
| Courier Prime | https://github.com/quoteunquoteapps/CourierPrime (Alan Dague-Greene) | `7fd585a2dd4c1612c79b3308e300923d1c13df93` |

Roles are set in `assets/motion/tokens.json` (`fonts`): Newsreader is `display` (headings, quotes), IBM Plex Sans Condensed is
`label` (lower thirds, labels), Courier Prime is `mono` (ledger and document text only).

| Family | File | Licence | Source URL | Bytes |
|---|---|---|---|---|
| Newsreader | `Newsreader-Variable.ttf` (roman, axes wght 200-800 and opsz 6-72) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/newsreader/Newsreader%5Bopsz,wght%5D.ttf | 451664 |
| Newsreader | `Newsreader-Italic-Variable.ttf` (italic, same axes) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/newsreader/Newsreader-Italic%5Bopsz,wght%5D.ttf | 495684 |
| Newsreader | `OFL-Newsreader.txt` | licence text | https://raw.githubusercontent.com/google/fonts/main/ofl/newsreader/OFL.txt | 4394 |
| IBM Plex Sans Condensed | `IBMPlexSansCondensed-Medium.ttf` (500) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsanscondensed/IBMPlexSansCondensed-Medium.ttf | 111176 |
| IBM Plex Sans Condensed | `IBMPlexSansCondensed-SemiBold.ttf` (600) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsanscondensed/IBMPlexSansCondensed-SemiBold.ttf | 111476 |
| IBM Plex Sans Condensed | `IBMPlexSansCondensed-Bold.ttf` (700) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsanscondensed/IBMPlexSansCondensed-Bold.ttf | 112228 |
| IBM Plex Sans Condensed | `OFL-IBMPlexSansCondensed.txt` | licence text (Reserved Font Name "Plex") | https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsanscondensed/OFL.txt | 4456 |
| Courier Prime | `CourierPrime-Regular.ttf` (400) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/courierprime/CourierPrime-Regular.ttf | 71188 |
| Courier Prime | `CourierPrime-Bold.ttf` (700) | OFL-1.1 | https://raw.githubusercontent.com/google/fonts/main/ofl/courierprime/CourierPrime-Bold.ttf | 72856 |
| Courier Prime | `OFL-CourierPrime.txt` | licence text | https://raw.githubusercontent.com/google/fonts/main/ofl/courierprime/OFL.txt | 4403 |

## Figures

Measured on these files with fontTools (the width of the digits 0 to 9 in the `hmtx` table, and the `tnum` feature in `GSUB`):

- **IBM Plex Sans Condensed** (all three weights): every digit has the same advance width (540 units), so figures are tabular by default.
- **Courier Prime** (both weights): monospaced, every digit 1228 units wide, tabular by default.
- **Newsreader roman**: every digit 1100 units, so the default figures are equal width; the font also has `tnum` and `pnum`.
- **Newsreader italic**: digits are 1100 or 1300 units wide, so the default figures are *not* tabular; the font has `tnum`. Use
  `font-variant-numeric: tabular-nums` for any number set in italic Newsreader that changes while it is on screen.

The licence does not allow selling the fonts by themselves and asks that the licence text travel with them: `install` copies
the family's `OFL-*.txt` next to the font files it writes into a project (`assets/fonts/`). A font name under a Reserved Font
Name (here "Plex") may not be used for a modified font; none of these files is modified.
