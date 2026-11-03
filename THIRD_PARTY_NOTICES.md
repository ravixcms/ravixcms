# Third-party notices

Fonts bundled in `static/fonts/` (SIL Open Font License 1.1; full texts next to the fonts):
- Vazirmatn — `static/fonts/Vazirmatn-LICENSE.txt`
- Noto Naskh Arabic — `static/fonts/NotoNaskhArabic-OFL.txt`

JavaScript and icons bundled for Ravix Studio:
- SortableJS 1.15.6 (MIT) — `static/studio/vendor/sortable.min.js`, licence `static/studio/vendor/SORTABLE-LICENSE.txt`
- Lucide icons (ISC) — licence `static/studio/vendor/LUCIDE-LICENSE.txt`

Python dependencies are installed from PyPI and keep their own licenses
(Django – BSD-3, Django REST framework – BSD-3, Pillow – HPND, psycopg – LGPL-3.0,
gunicorn – MIT, PyMySQL – MIT, WhiteNoise – MIT, cryptography – Apache-2.0/BSD, nh3 – MIT, Markdown – BSD-3,
argon2-cffi – MIT, argon2-cffi-bindings – MIT (bundles the Argon2 reference C implementation, CC0-1.0 / Apache-2.0),
jdatetime – Python-2.0/MIT-style). See `requirements.lock` for exact versions;
check each package's metadata (`pip show -v <name>`) for the authoritative license.

Test-only (not shipped, not in `requirements.lock`): the official MCP Python SDK (`mcp`, `mcp_types`; MIT) and Playwright (Apache-2.0) are installed in CI only, for `scripts/e2e_mcp.py`.

Logo: the official Ravix logo (`static/brand/`) is set in Space Grotesk Bold (SIL Open Font License 1.1); the text is converted to outlines, so no font file is shipped or needed.
