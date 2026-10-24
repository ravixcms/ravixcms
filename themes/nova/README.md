# Nova

A modern, clean theme for [Ravix](https://ravixcms.com) — made for an open-source product site that also runs a blog.
Version 1.0.0 · api_version 1 · author Ravix.

## Highlights
- **Home page**: large hero (title, tagline and two buttons), a feature grid, and latest posts as image cards.
- **Dark and light mode**: follows the visitor's system and has a toggle button (remembered in `localStorage`).
- **10 languages**: fa, en, ar, tr, es, fr, de, ru, pt-br, zh-hans. RTL for fa/ar, LTR for the rest, using CSS logical
  properties only; fonts, digits and calendars are provided by Ravix per language; the language switcher is in the footer.
- **Fast and self-contained**: no framework, no external fonts/scripts; two tiny same-origin scripts (`theme-init.js`,
  `theme.js`), CSP-safe. The page is fully usable without JavaScript.
- **Accessible**: skip link, visible focus, semantic landmarks, AA contrast, `prefers-reduced-motion`.
- Primary colour, fonts and size come from the panel's *Customize* screen (`--rv-*` variables).

## Setting up the home page
1. **Hero** section: title, subtitle and its *button* are the first button. The **second button** is the site header's
   call-to-action (*Customize → Header → CTA*).
2. **Features**: add a *Text* section whose body is a bullet list; each item (`**Title** description`) becomes a card.
3. **Latest posts** section: shown as cards with the featured image.

## Install
Panel → Themes → Upload ZIP (`nova-1.0.0.zip`), then activate and run `collectstatic` if your deployment needs it.

## Notes
In dark mode Nova uses its own dark palette for background, surface, text and border; the panel's primary colour is kept
(lightened automatically for text and links so that contrast stays readable).
