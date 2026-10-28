from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext, gettext_lazy

from apps.content.models import Page, Post
from apps.core.models import TimeStampedModel

from .schema import empty_document


class StudioDocument(TimeStampedModel):
    """The Studio layout of one page or post. The draft is always here; the live layout is a pointer to one ``StudioVersion``."""

    page = models.OneToOneField(Page, null=True, blank=True, on_delete=models.CASCADE, related_name="studio", verbose_name=gettext_lazy("Page"))
    post = models.OneToOneField(Post, null=True, blank=True, on_delete=models.CASCADE, related_name="studio", verbose_name=gettext_lazy("Post"))
    draft = models.JSONField(gettext_lazy("Layout draft"), default=empty_document)
    # Optimistic lock: every save adds one; a save made with a stale number is refused (409) instead of overwriting someone else's draft.
    draft_rev = models.PositiveIntegerField(gettext_lazy("Revision number"), default=1)
    published_version = models.ForeignKey("StudioVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                          verbose_name=gettext_lazy("Published version"))
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   verbose_name=gettext_lazy("Last editor"))
    # Soft edit lock (shown as a warning, never enforced): who opened the editor last, and when.
    editing_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    editing_at = models.DateTimeField(null=True, blank=True)
    legacy_design_id = models.PositiveIntegerField(null=True, blank=True, unique=True)  # the old page builder row this was imported from

    class Meta:
        verbose_name = gettext_lazy("Studio layout")
        verbose_name_plural = gettext_lazy("Studio layouts")
        constraints = [
            models.CheckConstraint(name="studio_document_one_target",
                                   condition=(Q(page__isnull=False, post__isnull=True) | Q(page__isnull=True, post__isnull=False))),
        ]

    @property
    def target(self):
        return self.page if self.page_id else self.post

    @property
    def kind(self) -> str:
        return "page" if self.page_id else "post"

    def __str__(self):
        return gettext("Layout of “%(title)s”") % {"title": self.target.title if self.target else "?"}


class StudioVersion(models.Model):
    """An immutable snapshot of a layout (only its review state and, for the live one, the rendered cache change)."""

    class State(models.TextChoices):
        SAVED = "saved", gettext_lazy("Saved")
        PENDING = "pending", gettext_lazy("Awaiting approval")
        APPROVED = "approved", gettext_lazy("Approved / published")
        REJECTED = "rejected", gettext_lazy("Rejected")

    document = models.ForeignKey(StudioDocument, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField(gettext_lazy("Version number"))
    schema_version = models.PositiveSmallIntegerField(default=2)
    content = models.JSONField(gettext_lazy("Document"))
    state = models.CharField(gettext_lazy("Status"), max_length=10, choices=State.choices, default=State.SAVED, db_index=True)
    is_auto = models.BooleanField(gettext_lazy("System autosave"), default=False)
    note = models.CharField(gettext_lazy("Note"), max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=gettext_lazy("Creator"))
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=gettext_lazy("Reviewer"))
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(gettext_lazy("Reviewer note"), max_length=200, blank=True)

    # The rendered cache of the live version (empty for every other version): what visitors get without any rendering work.
    html = models.TextField(blank=True)
    css = models.TextField(blank=True)
    css_hash = models.CharField(max_length=40, blank=True, db_index=True)
    widgets_used = models.JSONField(default=list, blank=True)
    scripts = models.JSONField(default=list, blank=True)
    frame_hosts = models.JSONField(default=list, blank=True)
    template = models.CharField(max_length=20, default="full_width")
    show_title = models.BooleanField(default=True)
    renderer_version = models.PositiveIntegerField(default=0)
    rendered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Layout version")
        verbose_name_plural = gettext_lazy("Layout versions")
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["document", "number"], name="uniq_studio_version_number")]

    def __str__(self):
        return f"{self.document_id}#{self.number}"


class StudioMediaUse(models.Model):
    """Which media files the live version shows, so replacing or deleting a file re-renders the pages that use it."""

    version = models.ForeignKey(StudioVersion, on_delete=models.CASCADE, related_name="media_uses")
    media_id = models.PositiveBigIntegerField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "media_id"], name="uniq_studio_media_use")]
