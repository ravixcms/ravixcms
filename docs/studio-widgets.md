# ساخت ویجت برای Ravix Studio

ویجت‌ها از دو راه اضافه می‌شوند: **`studio_widgets` در `plugin.json`** (برای افزونه‌ها؛ راه اصلی) و `register_studio_widget` (برای ماژول‌های داخلی Ravix).

## ۱. از راه افزونه
در `plugin.json` (حداکثر ۱۰ ویجت):

```json
{
  "studio_widgets": [
    {
      "type": "hello",
      "title": "Hello box",
      "icon": "star",
      "render": "widgets:hello",
      "css": ".hi{color:#123456}",
      "fields": [
        {"name": "name", "kind": "text", "label": "Name", "default": "World", "max_len": 30},
        {"name": "size", "kind": "int", "label": "Size", "default": 3, "min": 1, "max": 9},
        {"name": "tone", "kind": "choice", "label": "Tone", "default": "a", "choices": [["a", "A"], ["b", "B"]]}
      ]
    }
  ]
}
```

و در `widgets.py` کنار افزونه:

```python
from django.utils.html import escape

def hello(props, ctx):
    return '<p class="hi">Hello, ' + escape(props["name"]) + '</p>'
```

- نوع ثبت‌شده `<slug>.hello` است و فقط تا وقتی افزونه فعال است در پالت می‌آید. صفحه‌های منتشرشده‌ای که از آن استفاده کرده‌اند HTML ذخیره‌شده‌شان را نگه می‌دارند.
- انواع فیلد: `text`, `textarea`, `markdown`, `color`, `int`, `choice`, `bool`, `media`, `url`, `icon` (حداکثر ۲۰ فیلد). فیلد HTML خام برای افزونه‌ها وجود ندارد.
- `render(props, ctx)` یک رشتهٔ HTML برمی‌گرداند؛ Ravix خروجی را با همان فهرست مجاز HTML سفارشی (nh3) پاک می‌کند. خطای تابع کل صفحه را نمی‌شکند.
- `css` حداکثر ۸۰۰۰ نویسه، ساده؛ هر انتخابگر زیر کلاس ویجت قرار می‌گیرد و `url()` و `@import` مجاز نیست.
- ظاهر مشترک (فاصله، پس‌زمینه، حاشیه، نمایش در دسکتاپ/تبلت/موبایل) را Studio خودش می‌دهد.

## ۲. از راه کد (ماژول داخلی)

```python
from apps.studio.registry import register_studio_widget
from django.utils.html import escape
from apps.studio.fields import Field

def render(props, ctx):
    return f'<p class="hi">{escape(props["name"])}</p>'

register_studio_widget("hello", title="Hello", icon="star", fields=[Field("name", "text", "Name", default="World", max_len=30)], render=render, css=".hi{color:#123456}")
```

## ۳. آزمون
`apps/studio/tests/test_plugin_widgets.py` نمونهٔ کامل ثبت، چرخهٔ عمر (فعال/غیرفعال) و پاک‌سازی خروجی است.
