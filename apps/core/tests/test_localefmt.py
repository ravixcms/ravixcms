import datetime

from django.test import SimpleTestCase
from django.utils import timezone, translation

from apps.core import localefmt
from apps.core.templatetags import ravix_ui
from apps.public.formatting import date_display, reading_time

WHEN = datetime.datetime(2026, 10, 23, 15, 30, tzinfo=datetime.timezone.utc)


class LocaleFormattingTests(SimpleTestCase):
    def test_persian_uses_jalali_and_persian_digits(self):
        with translation.override("fa"):
            self.assertTrue(localefmt.is_persian())
            self.assertEqual(ravix_ui.fa_digits(2026), "۲۰۲۶")
            self.assertEqual(ravix_ui.money(1234567), "۱٬۲۳۴٬۵۶۷")
            self.assertEqual(ravix_ui.jalali(WHEN.date()), "۱۴۰۵/۰۸/۰۱")
            self.assertIn("آبان", date_display(WHEN))
            self.assertTrue(reading_time("<p>word</p>").startswith("۱"))

    def test_other_languages_use_gregorian_and_latin_digits(self):
        for language in ("en", "ar", "tr", "es", "fr", "de", "ru", "pt-br", "zh-hans"):
            with self.subTest(language=language), translation.override(language):
                self.assertFalse(localefmt.is_persian())
                self.assertEqual(ravix_ui.fa_digits(2026), "2026")
                for text in (ravix_ui.jalali(WHEN.date()), ravix_ui.jalali_datetime(WHEN), date_display(WHEN), ravix_ui.money(1234567)):
                    self.assertTrue(any(c.isdigit() for c in text))
                    self.assertFalse(any("۰" <= c <= "۹" for c in text), text)
                self.assertIn("2026", date_display(WHEN))
                self.assertNotIn("1405", ravix_ui.jalali(WHEN.date()))

    def test_locale_specific_formats(self):
        with translation.override("de"):
            self.assertEqual(ravix_ui.money(1234567), "1.234.567")
            self.assertEqual(ravix_ui.jalali(WHEN.date()), "23.10.2026")
        with translation.override("en"):
            self.assertEqual(ravix_ui.money(1234567), "1,234,567")
        with translation.override("zh-hans"):
            self.assertEqual(ravix_ui.money(1234567), "1,234,567")  # no separator in the locale: falls back to ","

    def test_datetime_uses_active_timezone(self):
        with translation.override("en"), timezone.override(datetime.timezone(datetime.timedelta(hours=-5))):
            self.assertIn("10:30", ravix_ui.jalali_datetime(WHEN))

    def test_font_stack_follows_the_script(self):
        persian = "'Vazirmatn', Tahoma, 'Segoe UI', sans-serif"
        with translation.override("fa"):
            self.assertEqual(localefmt.adapt_font_stack(persian), persian)
        with translation.override("ar"):
            self.assertEqual(localefmt.adapt_font_stack(persian), persian)
        with translation.override("ru"):
            self.assertTrue(localefmt.adapt_font_stack(persian).startswith("'Inter'"))
        with translation.override("zh-hans"):
            self.assertIn("Microsoft YaHei", localefmt.adapt_font_stack(persian))
            self.assertIn("PingFang", localefmt.adapt_font_stack("system-ui, sans-serif"))
        with translation.override("de"):
            self.assertEqual(localefmt.adapt_font_stack("system-ui, sans-serif"), "system-ui, sans-serif")

    def test_open_graph_locale(self):
        with translation.override("pt-br"):
            self.assertEqual(localefmt.og_locale(), "pt_BR")
        with translation.override("fa"):
            self.assertEqual(localefmt.og_locale(), "fa_IR")
