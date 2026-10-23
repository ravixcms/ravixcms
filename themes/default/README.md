# Ravix default theme (`default`)

The reference theme of Ravix. To start a new theme from it:

```bash
python manage.py startheme my-theme
python manage.py check_themes my-theme
```

- **No JavaScript:** every part (menu, search, pagination) works without scripts.
- **Colours, font and text size** come from “Customize appearance” and are used only through CSS variables with the `--rv-` prefix
  (`--rv-primary`, `--rv-on-primary`, `--rv-primary-soft`, `--rv-bg`, `--rv-surface`, `--rv-border`, `--rv-text`, `--rv-muted`,
  `--rv-font`, `--rv-font-size`). In the theme's CSS give these variables a default value only with `:where(:root)`, so they never
  override the administrator's choice.
- Whenever you change the CSS, raise `version` in `theme.json` so browsers refresh their cache.
- All text is marked for translation: use `{% trans %}` in templates and add `locale/<lang>.json` files (see docs/translation.md).
