"""
Business logic of Ravix Studio: saving drafts, versions, the approval workflow, publishing (rendering once and storing the result), rebuilds,
import and export. Everything that arrives from a browser is validated before it is stored.

Workflow (same roles as the first page builder):
* Autosave / manual save only update the *draft*; the site does not change.
* "Save version" takes a snapshot. "Submit for review" takes a *pending* snapshot (one pending version per layout at a time).
* A user with ``studio.publish`` approves a pending version (it goes live), rejects it, or publishes the draft directly. Authors only submit.
* Restoring a version puts it in the draft (after saving the current draft as an automatic version). Rolling back makes an older
  version live again. The live version is never deleted; older versions are pruned beyond ``STUDIO_MAX_VERSIONS``.
"""
import copy
import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.content.models import Page, Post
from apps.media_library.models import Media

from . import render as renderer
from .fields import DocumentInvalid
from .models import StudioDocument, StudioMediaUse, StudioVersion
from .schema import (
    SCHEMA_VERSION, empty_document, html_by_id, iter_nodes, media_ids, new_id, validate_document,
)

logger = logging.getLogger(__name__)
OBJECT_TYPE = gettext_lazy("Studio layout")
EXPORT_FORMAT = "ravix-studio"
EDIT_WINDOW = timedelta(seconds=120)


class ConflictError(Exception):
    """The draft was saved by someone else since this editor loaded it."""

    def __init__(self, current_revision, by=""):
        self.current_revision = current_revision
        self.by = by
        super().__init__("conflict")


class WorkflowError(Exception):
    """The action is not possible in the current state (message for the user)."""


# ------------------------------------------------------------------------------------------------------------- access
def target_of(kind: str, pk: int):
    model = {"page": Page, "post": Post}.get(kind)
    if model is None:
        raise Page.DoesNotExist
    return model.objects.get(pk=pk)


def get_document(target) -> StudioDocument:
    field = "page" if isinstance(target, Page) else "post"
    doc, _ = StudioDocument.objects.get_or_create(**{field: target}, defaults={"draft": empty_document()})
    return doc


def can_edit(user, document: StudioDocument) -> bool:
    if not user.is_authenticated or not user.has_cap("studio.edit"):
        return False
    target = document.target
    if document.post_id:
        return bool(target.user_can_edit(user))
    return True


def require_edit(user, document):
    if not can_edit(user, document):
        raise PermissionDenied


def require_publisher(user):
    if not user.has_cap("studio.publish"):
        raise PermissionDenied


def _audit(user, document, action, message):
    log_event(actor=user, action=action, obj=document.target, object_type=OBJECT_TYPE, message=message)


# ------------------------------------------------------------------------------------------------------------- validation
def check_media(doc: dict) -> None:
    """Every media file the document uses must exist; images where an image is expected (checked by kind)."""
    ids = media_ids(doc)
    if not ids:
        return
    found = set(Media.objects.filter(pk__in=ids).values_list("pk", flat=True))
    missing = sorted(ids - found)
    if missing:
        raise DocumentInvalid([("$", gettext("The selected media is not valid or is not an image (ID: %(items)s).") % {"items": ", ".join(map(str, missing))})])


def validate_for(document: StudioDocument, raw, user, *, strict=False) -> dict:
    can_html = user.has_cap("studio.html")
    existing = html_by_id(document.draft) if isinstance(document.draft, dict) and document.draft.get("schema_version") == SCHEMA_VERSION else {}
    doc = validate_document(raw, strict=strict, can_html=can_html, existing_html=existing)
    check_media(doc)
    return doc


# ------------------------------------------------------------------------------------------------------------- drafts
def _next_number(document) -> int:
    return (document.versions.aggregate(m=Max("number"))["m"] or 0) + 1


def _snapshot(document, user, *, state=StudioVersion.State.SAVED, note="", auto=False, content=None) -> StudioVersion:
    version = StudioVersion.objects.create(
        document=document, number=_next_number(document), content=content if content is not None else document.draft, schema_version=SCHEMA_VERSION,
        state=state, note=note[:200], is_auto=auto, created_by=user,
    )
    prune_versions(document)
    return version


def prune_versions(document) -> int:
    """Keep at most ``STUDIO_MAX_VERSIONS`` versions; the live one and the one awaiting approval are never removed."""
    cap = max(5, int(getattr(settings, "STUDIO_MAX_VERSIONS", 50)))
    total = document.versions.count()
    if total <= cap:
        return 0
    protected = {document.published_version_id}
    protected.update(document.versions.filter(state=StudioVersion.State.PENDING).values_list("pk", flat=True))
    doomed = list(document.versions.exclude(pk__in=[p for p in protected if p]).order_by("number").values_list("pk", flat=True)[: total - cap])
    StudioVersion.objects.filter(pk__in=doomed).delete()
    return len(doomed)


@transaction.atomic
def save_draft(document_id: int, raw, base_revision, user, *, overwrite=False) -> StudioDocument:
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    doc = validate_for(document, raw, user)
    if base_revision != document.draft_rev:
        if not overwrite:
            raise ConflictError(document.draft_rev, document.updated_by.display_name if document.updated_by_id else "")
        _snapshot(document, user, note=gettext("Automatic: before another save overwrote this draft"), auto=True)
    document.draft = doc
    document.draft_rev += 1
    document.updated_by = user
    document.editing_by, document.editing_at = user, timezone.now()
    document.save(update_fields=["draft", "draft_rev", "updated_by", "editing_by", "editing_at", "updated_at"])
    return document


def touch_editing(document: StudioDocument, user) -> dict | None:
    """Record that ``user`` has the editor open; returns who else had it open in the last two minutes (a warning, never a lock)."""
    other = None
    if document.editing_by_id and document.editing_by_id != user.pk and document.editing_at and timezone.now() - document.editing_at < EDIT_WINDOW:
        other = {"name": document.editing_by.display_name, "at": document.editing_at.isoformat()}
    StudioDocument.objects.filter(pk=document.pk).update(editing_by=user, editing_at=timezone.now())
    return other


@transaction.atomic
def save_version(document_id: int, user, note="") -> StudioVersion:
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    version = _snapshot(document, user, note=note or gettext("Manual save"))
    _audit(user, document, AuditLog.Action.CREATE, gettext("Version %(number)s of the layout was saved.") % {"number": version.number})
    return version


def _strict_draft(document, user) -> dict:
    doc = validate_for(document, document.draft, user, strict=True)
    if not doc["sections"]:
        raise WorkflowError(gettext("The page is empty; first add at least one section."))
    return doc


@transaction.atomic
def submit_for_review(document_id: int, user, note="") -> StudioVersion:
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    doc = _strict_draft(document, user)
    document.versions.filter(state=StudioVersion.State.PENDING).update(state=StudioVersion.State.SAVED)
    version = _snapshot(document, user, state=StudioVersion.State.PENDING, note=note or gettext("Submit for approval"), content=doc)
    _audit(user, document, AuditLog.Action.UPDATE, gettext("Version %(number)s was submitted for approval.") % {"number": version.number})
    return version


# ------------------------------------------------------------------------------------------------------------- publishing
def render_version(version: StudioVersion, *, make_files: bool) -> renderer.Rendered:
    """Render a version's content and store the result in it. Raises DocumentInvalid for a broken document."""
    doc = validate_document(version.content)
    check_media(doc)
    result = renderer.render_document(doc, make_files=make_files, validate=False)
    version.html, version.css, version.css_hash = result.html, result.css, result.css_hash
    version.widgets_used, version.scripts, version.frame_hosts = result.widgets, result.scripts, result.frame_hosts
    version.template, version.show_title = doc["meta"]["template"], bool(doc["meta"]["show_title"])
    version.renderer_version, version.rendered_at = renderer.RENDERER_VERSION, timezone.now()
    version.save(update_fields=["html", "css", "css_hash", "widgets_used", "scripts", "frame_hosts", "template", "show_title", "renderer_version", "rendered_at"])
    StudioMediaUse.objects.filter(version=version).delete()
    StudioMediaUse.objects.bulk_create([StudioMediaUse(version=version, media_id=pk) for pk in result.media])
    return result


def _clear_cache(version: StudioVersion):
    StudioVersion.objects.filter(pk=version.pk).update(html="", css="", css_hash="", widgets_used=[], scripts=[], frame_hosts=[], renderer_version=0)
    StudioMediaUse.objects.filter(version=version).delete()


def _make_live(document: StudioDocument, version: StudioVersion, user, note: str, *, approve=True):
    previous = document.published_version
    render_version(version, make_files=True)
    if approve:
        version.state = StudioVersion.State.APPROVED
        version.reviewed_by, version.reviewed_at, version.review_note = user, timezone.now(), note[:200]
        version.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_note"])
    document.published_version = version
    document.save(update_fields=["published_version", "updated_at"])
    if previous is not None and previous.pk != version.pk:
        _clear_cache(previous)
    target = document.target
    target.save(update_fields=["updated_at"])  # the page/post changed: feeds, sitemaps and "last updated" follow


@transaction.atomic
def approve(document_id: int, number: int, user, note="") -> StudioVersion:
    require_publisher(user)
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    version = document.versions.select_for_update().get(number=number)
    if version.state != StudioVersion.State.PENDING:
        raise WorkflowError(gettext("Only a version awaiting approval can be approved."))
    validate_document(version.content, strict=True)
    _make_live(document, version, user, note)
    _audit(user, document, AuditLog.Action.PUBLISH, gettext("Version %(number)s was approved and published.") % {"number": version.number})
    return version


@transaction.atomic
def reject(document_id: int, number: int, user, note="") -> StudioVersion:
    require_publisher(user)
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    version = document.versions.select_for_update().get(number=number)
    if version.state != StudioVersion.State.PENDING:
        raise WorkflowError(gettext("Only a version awaiting approval can be rejected."))
    version.state = StudioVersion.State.REJECTED
    version.reviewed_by, version.reviewed_at, version.review_note = user, timezone.now(), note[:200]
    version.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_note"])
    _audit(user, document, AuditLog.Action.UPDATE, gettext("Version %(number)s was rejected.") % {"number": version.number})
    return version


@transaction.atomic
def publish_draft(document_id: int, user, note="") -> StudioVersion:
    require_publisher(user)
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    doc = _strict_draft(document, user)
    version = _snapshot(document, user, note=note or gettext("Direct publish"), content=doc)
    _make_live(document, version, user, "")
    _audit(user, document, AuditLog.Action.PUBLISH, gettext("Version %(number)s was published directly.") % {"number": version.number})
    return version


@transaction.atomic
def rollback(document_id: int, number: int, user) -> StudioVersion:
    """Make an older version live again (the draft is not touched)."""
    require_publisher(user)
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    version = document.versions.select_for_update().get(number=number)
    if version.state in (StudioVersion.State.PENDING, StudioVersion.State.REJECTED) or version.pk == document.published_version_id:
        raise WorkflowError(gettext("This version cannot be made live."))
    validate_document(version.content, strict=True)
    _make_live(document, version, user, gettext("Rolled back"))
    _audit(user, document, AuditLog.Action.PUBLISH, gettext("Version %(number)s was made live again.") % {"number": version.number})
    return version


@transaction.atomic
def unpublish(document_id: int, user) -> None:
    require_publisher(user)
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    if document.published_version_id is None:
        raise WorkflowError(gettext("No layout is published."))
    previous = document.published_version
    document.published_version = None
    document.save(update_fields=["published_version", "updated_at"])
    _clear_cache(previous)
    document.target.save(update_fields=["updated_at"])
    _audit(user, document, AuditLog.Action.UPDATE, gettext("The layout was unpublished; the page went back to its text content."))


@transaction.atomic
def restore(document_id: int, number: int, user) -> StudioDocument:
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    version = document.versions.get(number=number)
    doc = validate_for(document, version.content, user)
    last = document.versions.order_by("-number").first()
    if last is None or last.content != document.draft:
        _snapshot(document, user, note=gettext("Automatic: before restoring version %(number)s") % {"number": number}, auto=True)
    document.draft = doc
    document.draft_rev += 1
    document.updated_by = user
    document.save(update_fields=["draft", "draft_rev", "updated_by", "updated_at"])
    _audit(user, document, AuditLog.Action.UPDATE, gettext("Version %(number)s was restored to the draft.") % {"number": number})
    return document


# ------------------------------------------------------------------------------------------------------------- rebuilds
def rebuild_version(version: StudioVersion) -> bool:
    """Re-render a live version from its stored content. A document that no longer validates is left as it was (the old HTML keeps showing)."""
    try:
        render_version(version, make_files=True)
        return True
    except DocumentInvalid:
        logger.warning("Ravix Studio could not rebuild version %s: its document is invalid; the stored page was kept", version.pk)
        return False


def live_versions():
    return StudioVersion.objects.filter(pk__in=StudioDocument.objects.exclude(published_version=None).values("published_version"))


def stale_versions():
    return live_versions().exclude(renderer_version=renderer.RENDERER_VERSION)


def rebuild_stale(*, force=False) -> int:
    qs = live_versions() if force else stale_versions()
    count = 0
    for version in qs:
        count += bool(rebuild_version(version))
    return count


def rebuild_for_media(media_id: int, *, deleted=False) -> int:
    ids = StudioMediaUse.objects.filter(media_id=media_id).values_list("version_id", flat=True)
    count = 0
    for version in StudioVersion.objects.filter(pk__in=list(ids)):
        if deleted:
            # The file is gone: draw the page without it (the document keeps its reference; an editor sees the missing image).
            try:
                doc = validate_document(version.content)
                result = renderer.render_document(doc, make_files=False, validate=False)
                version.html, version.css, version.css_hash = result.html, result.css, result.css_hash
                version.save(update_fields=["html", "css", "css_hash"])
                StudioMediaUse.objects.filter(version=version, media_id=media_id).delete()
                count += 1
            except DocumentInvalid:
                continue
        else:
            count += bool(rebuild_version(version))
    return count


# ------------------------------------------------------------------------------------------------------------- import / export
def export_document(document: StudioDocument, *, source="draft", section_id="") -> dict:
    content = document.draft
    if source == "live" and document.published_version_id:
        content = document.published_version.content
    doc = validate_document(content)
    if section_id:
        section = next((s for s in doc["sections"] if s["id"] == section_id), None)
        if section is None:
            raise WorkflowError(gettext("Section not found."))
        return {"format": EXPORT_FORMAT, "schema_version": SCHEMA_VERSION, "kind": "section", "content": section}
    return {"format": EXPORT_FORMAT, "schema_version": SCHEMA_VERSION, "kind": "page", "content": doc}


def _reid(doc: dict) -> dict:
    out = copy.deepcopy(doc)
    for sec in out["sections"]:
        sec["id"] = new_id()
    for kind, node in iter_nodes(out):
        node["id"] = new_id()
    return out


def _drop_missing_media(doc: dict) -> int:
    """Media IDs from another site mean nothing here: unknown ones are cleared (the widget shows empty) and counted."""
    from .registry import get_widget

    ids = media_ids(doc)
    known = set(Media.objects.filter(pk__in=ids).values_list("pk", flat=True)) if ids else set()
    cleared = 0
    for kind, node in iter_nodes(doc):
        for values in node.get("style", {}).values():
            if values.get("bg_media") and values["bg_media"] not in known:
                values.pop("bg_media")
                cleared += 1
        if kind != "widget":
            continue
        spec = get_widget(node["type"])
        if spec is None:
            continue
        for f in spec.fields:
            if f.kind == "media" and node["props"].get(f.name) and node["props"][f.name] not in known:
                node["props"][f.name] = None
                cleared += 1
            elif f.kind == "list":
                for item in node["props"].get(f.name, []):
                    for sub in f.item_fields:
                        if sub.kind == "media" and item.get(sub.name) and item[sub.name] not in known:
                            item[sub.name] = None
                            cleared += 1
    return cleared


def parse_import(payload, user) -> tuple[str, dict, int]:
    """(kind, validated document, cleared media references). Raises DocumentInvalid / WorkflowError."""
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise WorkflowError(gettext("This is not a Ravix Studio file."))
    version = payload.get("schema_version")
    kind = payload.get("kind")
    content = payload.get("content")
    if kind == "section":
        if not isinstance(content, dict):
            raise WorkflowError(gettext("This is not a Ravix Studio file."))
        content = {"schema_version": version, "meta": {}, "sections": [content]}
    elif kind != "page" or not isinstance(content, dict):
        raise WorkflowError(gettext("This is not a Ravix Studio file."))
    doc = validate_document(content, can_html=user.has_cap("studio.html"), existing_html={})
    cleared = _drop_missing_media(doc)
    return kind, doc, cleared


@transaction.atomic
def import_into_draft(document_id: int, payload, user, *, mode="replace") -> tuple[StudioDocument, int]:
    document = StudioDocument.objects.select_for_update().get(pk=document_id)
    require_edit(user, document)
    kind, doc, cleared = parse_import(payload, user)
    if mode == "append" or kind == "section":
        base = validate_document(document.draft, can_html=True)
        merged = copy.deepcopy(base)
        merged["sections"] += _reid(doc)["sections"]
        doc = validate_document(merged, can_html=user.has_cap("studio.html"), existing_html=html_by_id(base))
    _snapshot(document, user, note=gettext("Automatic: before an import"), auto=True)
    document.draft = doc
    document.draft_rev += 1
    document.updated_by = user
    document.save(update_fields=["draft", "draft_rev", "updated_by", "updated_at"])
    _audit(user, document, AuditLog.Action.UPDATE, gettext("A layout file was imported into the draft."))
    return document, cleared
