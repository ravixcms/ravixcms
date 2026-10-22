import unittest

from django.conf import settings

if not settings.SHOP_ENABLED:  # ماژول فروشگاه خاموش است (RAVIX_SHOP_ENABLED=false)؛ این تست‌ها موضوعیت ندارند
    raise unittest.SkipTest("فروشگاه غیرفعال است")
