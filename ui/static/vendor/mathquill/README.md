# MathQuill 0.10.1 stylesheet and Symbola font

Served from our own static files instead of cdnjs so math symbols render
offline and aren't blocked by the CSP (`font-src` allows only 'self' and
Google Fonts). `mathquill.js` itself still loads from cdnjs in base.html.

- `mathquill.css`: https://cdnjs.cloudflare.com/ajax/libs/mathquill/0.10.1/mathquill.css
  (sha384-hyelkzOwgzPmPEil1GOCRDI9tDelmUVzJSsiB1jmI77HxfQrEizlUdQJy2e3H2EC),
  with the `@font-face` `src` trimmed to woff2/woff.
- `font/Symbola.woff2`, `font/Symbola.woff`: from the same cdnjs release.

Licenses: MathQuill is MPL-2.0 (https://github.com/mathquill/mathquill).
Symbola (George Douros) is distributed with MathQuill; its license allows
redistribution.
