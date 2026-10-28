"""Import the first page builder's layouts, versions and approval states into Ravix Studio.

Nothing in the old tables is changed or deleted (they stay as the rollback source). The migration is idempotent: a page that already has a
Studio layout is skipped. Reversing it removes only the layouts that were imported here (``legacy_design_id`` is set on them); layouts
made in Studio itself are kept. Published pages are drawn again by ``studio_rebuild`` / the post-migrate hook (their stored HTML starts empty).
"""
import logging

from django.db import migrations

from apps.studio.legacy_import import v1_to_v2

logger = logging.getLogger("ravix.studio")


def _convert(doc):
    if isinstance(doc, dict) and doc.get("schema_version") == 1:
        try:
            return v1_to_v2(doc), 2
        except Exception:  # noqa: BLE001 — keep the original; the editor upgrades (or reports) it when opened
            logger.warning("Ravix Studio could not convert a page builder document during import; it was kept as it was")
            return doc, 1
    return doc, (doc.get("schema_version", 2) if isinstance(doc, dict) else 2)


def forwards(apps, schema_editor):
    PageDesign = apps.get_model("builder", "PageDesign")
    DesignVersion = apps.get_model("builder", "DesignVersion")
    StudioDocument = apps.get_model("studio", "StudioDocument")
    StudioVersion = apps.get_model("studio", "StudioVersion")
    for design in PageDesign.objects.all().order_by("pk"):
        if StudioDocument.objects.filter(legacy_design_id=design.pk).exists() or StudioDocument.objects.filter(page_id=design.page_id).exists():
            continue
        draft, _ = _convert(design.draft)
        doc = StudioDocument.objects.create(page_id=design.page_id, draft=draft, draft_rev=design.revision, updated_by_id=design.updated_by_id,
                                            legacy_design_id=design.pk)
        StudioDocument.objects.filter(pk=doc.pk).update(created_at=design.created_at, updated_at=design.updated_at)
        mapping = {}
        for v in DesignVersion.objects.filter(design_id=design.pk).order_by("number"):
            content, schema_version = _convert(v.document)
            sv = StudioVersion.objects.create(
                document=doc, number=v.number, schema_version=schema_version, content=content, state=v.state, is_auto=v.is_auto, note=v.note,
                created_by_id=v.created_by_id, reviewed_by_id=v.reviewed_by_id, reviewed_at=v.reviewed_at, review_note=v.review_note,
            )
            StudioVersion.objects.filter(pk=sv.pk).update(created_at=v.created_at)
            mapping[v.pk] = sv.pk
        if design.published_version_id and design.published_version_id in mapping:
            StudioDocument.objects.filter(pk=doc.pk).update(published_version_id=mapping[design.published_version_id])
    if PageDesign.objects.exists():  # a site that already used the page builder keeps its editor: the module starts switched on
        ModuleState = apps.get_model("core", "ModuleState")
        ModuleState.objects.get_or_create(key="studio", defaults={"enabled": True})


def backwards(apps, schema_editor):
    StudioDocument = apps.get_model("studio", "StudioDocument")
    StudioDocument.objects.filter(legacy_design_id__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0001_initial"),
        ("builder", "0002_i18n_msgids"),
        ("core", "0013_modulestate"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
