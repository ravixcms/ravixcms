# Nova

A modern, clean theme for [Ravix](https://ravixcms.com) — made for an open-source product site that also runs a blog.
Version 1.1.0 · api_version 1 · author Ravix.

## Highlights
- **Home page**: large hero (title, tagline and two buttons), a feature grid, and latest posts as image cards.
- **Dark and light mode**: follows the visitor's system and has a toggle button (remembered in `localStorage`).
- **10 languages**: fa, en, ar, tr, es, fr, de, ru, pt-br, zh-hans. RTL for fa/ar, LTR for the rest, using CSS logical
  properties only; fonts, digits and calendars are provided by Ravix per language; the language switcher is in the footer.
- **Fast and self-contained**: no framework, no external fonts/scripts; two tiny same-origin scripts (`theme-init.js`,
  `theme.js`), CSP-safe. The page is fully usable without JavaScript.
- **Accessible**: skip link, visible focus, semantic landmarks, AA contrast, `prefers-reduced-motion`.
- Primary colour, fonts and size come from the panel's *Customize* screen (`--rv-*` variables).

## Setting up the home page (Customize → Home page)
- **Hero**: title, subtitle, an optional Markdown body and **up to three buttons** (the first is filled, the others outlined).
- **Features** section: one line per item, `icon | title | short text`; each item becomes a card with an icon.
- **Text** section: Markdown (headings, lists, links, bold…), optional buttons.
- **Latest posts**: cards with the featured image. **Categories** and **Page** sections work as in any Ravix theme.
- **Languages**: every language has its own tab in the same screen for the hero, features, text and button labels; an empty field falls back to the default language.

## Install
Panel → Themes → Upload ZIP (`nova-1.1.0.zip`), then activate and run `collectstatic` if your deployment needs it.

## Notes
In dark mode Nova uses its own dark palette for background, surface, text and border; the panel's primary colour is kept
(lightened automatically for text and links so that contrast stays readable).
