"""به‌روزرسانی یکپارچهٔ Ravix از خط فرمان (همیشه در دسترس؛ مستقل از RAVIX_UPDATE_WEB_APPLY)."""
from django.core.management.base import BaseCommand, CommandError

from apps.updater import pipeline, sources, versions
from apps.updater.errors import UpdateError
from apps.updater.models import UpdateRun


class Command(BaseCommand):
    help = "status | check | apply core|theme|plugin | rollback --run ID | history"

    def add_arguments(self, p):
        p.add_argument("action", choices=["status", "check", "apply", "rollback", "history"])
        p.add_argument("target", nargs="?", choices=["core", "theme", "plugin"])
        p.add_argument("--slug", default="")
        p.add_argument("--to", dest="target_version", default="", help="نسخهٔ مشخص (پیش‌فرض: آخرین نسخهٔ سازگار)")
        p.add_argument("--run", type=int, default=0)
        p.add_argument("--dry-run", action="store_true", help="دریافت و اعتبارسنجی کامل بدون تغییر فایل‌ها")
        p.add_argument("--yes-data-loss", action="store_true", help="تأیید اینکه بازگشت، داده‌های بعد از ارتقا را از بین می‌برد")
        p.add_argument("--user", default="", help="نام‌کاربری/ایمیل مدیر (برای افزونه‌ها لازم است)")

    def _user(self, ident):
        if not ident:
            return None
        from django.contrib.auth import get_user_model

        U = get_user_model()
        u = U.objects.filter(email=ident).first() or U.objects.filter(username=ident).first()
        if u is None:
            raise CommandError("کاربر پیدا نشد.")
        return u

    def handle(self, *a, **o):
        act = o["action"]
        try:
            if act == "status":
                v = versions.component_versions()
                self.stdout.write(f"هستهٔ در حال اجرا: {v['core']['running']} | روی دیسک: {v['core']['disk']}")
                if v["core"]["restart_pending"]:
                    self.stdout.write(self.style.WARNING("کد روی دیسک جدیدتر از پروسهٔ در حال اجراست؛ سرویس را راه‌اندازی مجدد کنید."))
                lock = pipeline.lock_state()
                if lock:
                    self.stdout.write(self.style.WARNING(f"قفل فعال: {lock['operation']} از {lock['since']}"))
                pipeline.mark_interrupted()
                for r in UpdateRun.objects.filter(status="needs_recovery")[:5]:
                    self.stdout.write(self.style.ERROR(f"نیازمند بازیابی: اجرا #{r.pk}\n{r.recovery}"))
            elif act == "check":
                if not sources.has_source():
                    raise CommandError("منبع به‌روزرسانی تنظیم نشده است (RAVIX_UPDATE_URL یا RAVIX_UPDATE_DIR).")
                plan = pipeline.plan_core(sources.cached_index(force=True))
                if not plan["latest"]:
                    self.stdout.write(f"به‌روز است ({plan['current']}).")
                else:
                    self.stdout.write(f"نسخهٔ جدید: {plan['latest']['version']} (فعلی {plan['current']})")
                    for p in plan["problems"]:
                        self.stdout.write(self.style.ERROR("مانع: " + p))
                    for w in plan["warnings"]:
                        self.stdout.write(self.style.WARNING("هشدار: " + w))
            elif act == "history":
                for r in UpdateRun.objects.all()[:20]:
                    self.stdout.write(f"#{r.pk} {r.started_at:%Y-%m-%d %H:%M} {r.component}:{r.slug or '-'} {r.from_version}→{r.to_version} [{r.status}]")
            elif act == "apply":
                if not o["target"]:
                    raise CommandError("هدف را مشخص کنید: core | theme | plugin")
                ver = o["target_version"] or None
                if o["target"] == "core":
                    run = pipeline.apply_core(ver, self._user(o["user"]), dry_run=o["dry_run"])
                elif o["target"] == "theme":
                    if not o["slug"]:
                        raise CommandError("--slug لازم است.")
                    run = pipeline.apply_theme(o["slug"], ver, self._user(o["user"]), dry_run=o["dry_run"])
                else:
                    if not (o["slug"] and o["user"]):
                        raise CommandError("--slug و --user (مدیر) لازم است.")
                    run = pipeline.apply_plugin(o["slug"], ver, self._user(o["user"]))
                self.stdout.write(self.style.SUCCESS(f"اجرا #{run.pk}: {run.get_status_display()} — {run.from_version} → {run.to_version}"))
                self.stdout.write(run.recovery)
            elif act == "rollback":
                if not o["run"]:
                    raise CommandError("--run لازم است.")
                run = pipeline.rollback_run(o["run"], self._user(o["user"]), confirm_data_loss=o["yes_data_loss"])
                self.stdout.write(self.style.SUCCESS(f"بازگشت انجام شد (اجرا #{run.pk}). {run.recovery}"))
        except UpdateError as exc:
            raise CommandError(str(exc))
