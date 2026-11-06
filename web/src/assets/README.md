# Frontend assets

## Product illustration

`product/youth-1915-page21.jpg` is a 960px scan thumbnail of Chen Duxiu's
《敬告青年》, in the first issue of 《青年杂志》 (1915), image sequence page 21.
The [Wikimedia Commons file description](https://commons.wikimedia.org/wiki/File:新青年_第1卷第1號.djvu)
marks the original as public domain. The
[page record](https://zh.wikisource.org/wiki/Page:新青年_第1卷第1號.djvu/21)
identifies the title and author. Retrieved 2026-11-07.

The website preserves the downloaded pixels. Cropping and the title-region
outline are display layers for a labeled feature demonstration, not OCR,
model review evidence or a user decision. It is a public product asset,
not material copied from a user's library. The example source note is not
presented as an original footnote.

## Fonts

The four WOFF2 files are static 400/600 subsets of
[Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) and
[Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc).
Their original OFL 1.1 notices are included beside the fonts and exported
as build assets. Internal derivative family names use `HrsSans`/`HrsSerif`.

Subsets contain GB2312 Chinese glyphs, the traditional glyphs in the product example,
Latin and common punctuation;
rare characters use the configured system fallback. Font loading uses
`font-display: swap`; no external font request is required at runtime.
