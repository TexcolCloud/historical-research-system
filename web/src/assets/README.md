# Frontend fonts

The four WOFF2 files are static 400/600 subsets of
[Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) and
[Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc).
Their original OFL 1.1 notices are included under `web/public/assets/fonts` and copied to `/assets/fonts/` in each build. Internal derivative family names use `HrsSans`/`HrsSerif`.

Subsets contain GB2312 Chinese glyphs, additional traditional characters,
Latin and common punctuation;
rare characters use the configured system fallback. Font loading uses
`font-display: swap`; no external font request is required at runtime.
