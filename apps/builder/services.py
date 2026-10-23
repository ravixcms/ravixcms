"""
منطق کسب‌وکار صفحه‌ساز: ذخیره‌ی پیش‌نمایش، نسخه‌بندی، گردش تأیید و انتشار، بازیابی. همه‌ی ورودی‌ها پیش از ذخیره اعتبارسنجی می‌شوند.

گردش کار:
* ذخیره‌ی خودکار/دستی فقط «پیش‌نویس» (draft) را به‌روز می‌کند و به سایت عمومی ربطی ندارد.
* «ذخیره‌ی نسخه»: snapshot ثابت از پیش‌نویس (برای بازیابی).
* «ارسال برای تأیید»: snapshot با وضعیت pending (هر طرح حداکثر یک نسخه‌ی در انتظار دارد؛ ارسال جدید قبلی را به «ذخیره‌شده» برمی‌گرداند).
* «تأیید و انتشار» (``builder.publish``): نسخه‌ی pending ← approved و اشاره‌گر ``published_version`` عوض می‌شود. مدیر می‌تواند پیش‌نویس
  را مستقیم هم منتشر کند. نویسنده فقط ارسال می‌کند.
* بازیابی: قبل از جایگزینی پیش‌نویس، یک snapshot خودکار از وضعیت فعلی گرفته می‌شود تا هیچ کاری از دست نرود.
"""
from django.utils.translation import gettext, gettext_lazy
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.content.models import Page
from apps.media_library.models import Media

from .models import DesignVersion, PageDesign
from .schema import DocumentInvalid, empty_document, media_ids, validate_document

OBJECT_TYPE = gettext_lazy("Page builder layout")


class ConflictError(Exception):
    """پیش‌نویس در این فاصله جای دیگری ذخیره شده است."""

    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__("conflict")


class WorkflowError(Exception):
    """عملیات در وضعیت فعلی مجاز نیست (پیام فارسی برای کاربر)."""


def get_design(page: Page) -> PageDesign:
    design, _ = PageDesign.objects.get_or_create(page=page, defaults={"draft": empty_document()})
    return design


def check_media(doc: dict) -> None:
    """رسانه‌های ارجاع‌داده‌شده باید وجود داشته و تصویر باشند (در غیر این صورت DocumentInvalid)."""
    ids = media_ids(doc)
    if not ids:
        return
    ok = set(Media.objects.filter(pk__in=ids, kind=Media.Kind.IMAGE).values_list("pk", flat=True))
    missing = sorted(ids - ok)
    if missing:
        raise DocumentInvalid([("$", (gettext("The selected media is not valid or is not an image (ID: %(items)s).") % {"items": ', '.join(map(str, missing))}))])


def _next_number(design: PageDesign) -> int:
    return (design.versions.aggregate(m=Max("number"))["m"] or 0) + 1


def _snapshot(design, user, *, state=DesignVersion.State.SAVED, note="", auto=False, document=None) -> DesignVersion:
    return DesignVersion.objects.create(
        design=design, number=_next_number(design), document=document if document is not None else design.draft,
        state=state, note=note[:200], is_auto=auto, created_by=user,
    )


@transaction.atomic
def save_draft(design_id: int, raw_doc, base_revision, user) -> PageDesign:
    doc = validate_document(raw_doc)
    check_media(doc)
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    if base_revision != design.revision:
        raise ConflictError(design.revision)
    design.draft = doc
    design.revision += 1
    design.updated_by = user
    design.save(update_fields=["draft", "revision", "updated_by", "updated_at"])
    return design


@transaction.atomic
def save_version(design_id: int, user, note="") -> DesignVersion:
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    version = _snapshot(design, user, note=note or gettext("Manual save"))
    log_event(actor=user, action=AuditLog.Action.CREATE, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s of the page layout was saved.") % {"number": version.number}))
    return version


def _strict_draft(design: PageDesign) -> dict:
    doc = validate_document(design.draft, strict=True)
    check_media(doc)
    if not doc["sections"]:
        raise WorkflowError(gettext("The page is empty; first add at least one section."))
    return doc


@transaction.atomic
def submit_for_review(design_id: int, user, note="") -> DesignVersion:
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    doc = _strict_draft(design)
    design.versions.filter(state=DesignVersion.State.PENDING).update(state=DesignVersion.State.SAVED)
    version = _snapshot(design, user, state=DesignVersion.State.PENDING, note=note or gettext("Submit for approval"), document=doc)
    log_event(actor=user, action=AuditLog.Action.UPDATE, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s was submitted for approval.") % {"number": version.number}))
    return version


def _require_publisher(user):
    if not user.has_cap("builder.publish"):
        raise PermissionDenied


def _make_live(design: PageDesign, version: DesignVersion, user, note: str):
    version.state = DesignVersion.State.APPROVED
    version.reviewed_by, version.reviewed_at, version.review_note = user, timezone.now(), note[:200]
    version.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_note"])
    design.published_version = version
    design.save(update_fields=["published_version", "updated_at"])


@transaction.atomic
def approve(design_id: int, number: int, user, note="") -> DesignVersion:
    _require_publisher(user)
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    version = design.versions.select_for_update().get(number=number)
    if version.state != DesignVersion.State.PENDING:
        raise WorkflowError(gettext("Only a version awaiting approval can be approved."))
    doc = validate_document(version.document, strict=True)
    check_media(doc)
    _make_live(design, version, user, note)
    log_event(actor=user, action=AuditLog.Action.PUBLISH, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s was approved and published.") % {"number": version.number}))
    return version


@transaction.atomic
def reject(design_id: int, number: int, user, note="") -> DesignVersion:
    _require_publisher(user)
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    version = design.versions.select_for_update().get(number=number)
    if version.state != DesignVersion.State.PENDING:
        raise WorkflowError(gettext("Only a version awaiting approval can be rejected."))
    version.state = DesignVersion.State.REJECTED
    version.reviewed_by, version.reviewed_at, version.review_note = user, timezone.now(), note[:200]
    version.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_note"])
    log_event(actor=user, action=AuditLog.Action.UPDATE, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s was rejected.") % {"number": version.number}))
    return version


@transaction.atomic
def publish_draft(design_id: int, user, note="") -> DesignVersion:
    """انتشار مستقیم پیش‌نویس توسط مدیر (بدون مرحله‌ی تأیید جدا؛ خودِ مدیر تأییدکننده است)."""
    _require_publisher(user)
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    doc = _strict_draft(design)
    version = _snapshot(design, user, note=note or gettext("Direct publish"), document=doc)
    _make_live(design, version, user, "")
    log_event(actor=user, action=AuditLog.Action.PUBLISH, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s was published directly.") % {"number": version.number}))
    return version


@transaction.atomic
def unpublish(design_id: int, user) -> None:
    _require_publisher(user)
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    if design.published_version_id is None:
        raise WorkflowError(gettext("No layout is published."))
    design.published_version = None
    design.save(update_fields=["published_version", "updated_at"])
    log_event(actor=user, action=AuditLog.Action.UPDATE, obj=design.page, object_type=OBJECT_TYPE,
              message=gettext("The layout was unpublished; the page went back to its text content."))


@transaction.atomic
def restore(design_id: int, number: int, user) -> PageDesign:
    design = PageDesign.objects.select_for_update().get(pk=design_id)
    version = design.versions.get(number=number)
    doc = validate_document(version.document)  # نسخه‌ی قدیمی با مهاجرت ارتقا می‌یابد
    last = design.versions.order_by("-number").first()
    if last is None or last.document != design.draft:
        _snapshot(design, user, note=(gettext("Automatic: before restoring version %(number)s") % {"number": number}), auto=True)
    design.draft = doc
    design.revision += 1
    design.updated_by = user
    design.save(update_fields=["draft", "revision", "updated_by", "updated_at"])
    log_event(actor=user, action=AuditLog.Action.UPDATE, obj=design.page, object_type=OBJECT_TYPE,
              message=(gettext("Version %(number)s was restored to the draft.") % {"number": number}))
    return design
