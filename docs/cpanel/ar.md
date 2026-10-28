# تثبيت Ravix على استضافة cPanel المشتركة

![](../img/cpanel-flow.svg)

1. cPanel ← Setup Python App ← Create Application: بايثون 3.11 أو أحدث، Application root = ravix، ملف البدء passenger_wsgi.py، نقطة الدخول application.
2. ارفع ملف ravix-<الإصدار>-cpanel.zip عبر File Manager إلى مجلد ravix ثم Extract.
3. اضغط Run Pip Install (ملف requirements-cpanel.txt) ثم Restart.
4. أنشئ قاعدة بيانات ومستخدماً في MySQL® Databases مع All privileges.
5. افتح موقعك وأدخل رمز التثبيت من الملف ravix-data/install-key.txt (بجوار مجلد ravix)، ثم أكمل الخطوات.
6. أضف سطر Cron الذي تعرضه الصفحة الأخيرة (كل 5 دقائق).

عند ظهور خطأ 503 اقرأ ravix-data/logs/startup-error.txt؛ تأكد من أن بايثون 3.11+ وأن الحزم مثبتة.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
