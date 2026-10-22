# قالب پیش‌فرض راوین (`default`)

قالب مرجع Ravin CMS. برای ساخت قالب جدید از روی آن:

```bash
python manage.py startheme my-theme
python manage.py check_themes my-theme
```

- **بدون جاوااسکریپت:** همهٔ بخش‌ها (منو، جستجو، صفحه‌بندی) بدون اسکریپت کار می‌کنند.
- **رنگ، فونت و اندازهٔ متن** از «سفارشی‌سازی ظاهر» می‌آیند و فقط از متغیرهای CSS با پیشوند `--rv-` استفاده می‌شوند
  (`--rv-primary`، `--rv-on-primary`، `--rv-primary-soft`، `--rv-bg`، `--rv-surface`، `--rv-border`، `--rv-text`، `--rv-muted`،
  `--rv-font`، `--rv-font-size`). در CSS قالب این متغیرها را فقط با `:where(:root)` مقدار پیش‌فرض بدهید تا مقدار مدیر را نپوشانند.
- هر بار که CSS را تغییر می‌دهید `version` را در `theme.json` بالا ببرید تا کش مرورگر نو شود.
