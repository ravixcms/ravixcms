import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.core.testing import MediaTestCase, jpeg_bytes, png_bytes
from apps.media_library.models import Media
from apps.media_library.validators import MAX_FILES_PER_UPLOAD


def up(name="photo.png", content=None, ctype="image/png"):
    return SimpleUploadedFile(name, png_bytes() if content is None else content, content_type=ctype)


class MediaBase(MediaTestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)
        self.author2 = make_user("aut2", Role.AUTHOR)
        self.user = make_user("usr", Role.USER)
        self.upload_url = reverse("media_library:media_upload")
        self.list_url = reverse("media_library:media_list")

    def upload(self, user, files, **extra):
        self.client.force_login(user)
        return self.client.post(self.upload_url, {"files": files, **extra})


class UploadTests(MediaBase):
    def test_single_image_upload_records_metadata(self):
        r = self.upload(self.author, up("My Photo.PNG", png_bytes((40, 30))), alt_text="یک عکس", title="عنوان من")
        media = Media.objects.get()
        self.assertRedirects(r, self.list_url)
        self.assertEqual((media.kind, media.mime_type, media.width, media.height), ("image", "image/png", 40, 30))
        self.assertEqual((media.title, media.alt_text), ("عنوان من", "یک عکس"))
        self.assertEqual(media.original_name, "My Photo.PNG")
        self.assertEqual(media.uploaded_by, self.author)
        self.assertEqual(media.size, len(png_bytes((40, 30))))
        self.assertTrue(os.path.exists(media.file.path))
        self.assertTrue(AuditLog.objects.filter(action="create", object_type="رسانه", actor=self.author).exists())

    def test_stored_name_is_random_and_original_name_never_used_on_disk(self):
        self.upload(self.admin, up("../../etc/passwd.png"))
        media = Media.objects.get()
        self.assertRegex(media.file.name, r"^library/\d{4}/\d{2}/[0-9a-f]{32}\.png$")
        self.assertNotIn("passwd", media.file.name)
        self.assertNotIn("..", media.file.name)
        self.assertEqual(media.original_name, "passwd.png")

    def test_image_without_alt_redirects_to_edit_with_hint(self):
        r = self.upload(self.author, up())
        media = Media.objects.get()
        self.assertRedirects(r, reverse("media_library:media_update", args=[media.pk]))

    def test_multiple_files_upload(self):
        files = [up("a.png"), SimpleUploadedFile("b.pdf", b"%PDF-1.4 x"), SimpleUploadedFile("c.txt", b"hello"),
                 SimpleUploadedFile("d.jpg", jpeg_bytes())]
        self.upload(self.author, files, title="نادیده", alt_text="نادیده")
        self.assertEqual(Media.objects.count(), 4)
        self.assertEqual(set(Media.objects.values_list("kind", flat=True)), {"image", "document"})
        self.assertEqual(Media.objects.exclude(title="").count(), 0)  # عنوان/Alt فقط برای تک‌فایل
        self.assertEqual(Media.objects.exclude(alt_text="").count(), 0)

    def test_all_or_nothing_when_one_file_invalid(self):
        r = self.upload(self.author, [up("good.png"), SimpleUploadedFile("shell.php", b"<?php ?>")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Media.objects.count(), 0)
        self.assertContains(r, "shell.php")
        self.assertContains(r, "بارگذاری انجام نشد")

    def test_dangerous_files_rejected_through_the_view(self):
        # the media folder is shared by the whole test run: compare with what was there before, do not expect it empty
        before = {os.path.join(d, p) for d, _, files in os.walk(self._media_dir) for p in files}
        for name, content in (("a.exe", b"MZ"), ("a.php", b"<?php"), ("a.svg", b"<svg onload=alert(1)>"),
                              ("a.html", b"<script>"), ("x.php.jpg", jpeg_bytes()), ("fake.png", b"MZ\x90\x00" + b"0" * 50)):
            r = self.upload(self.author, SimpleUploadedFile(name, content))
            self.assertEqual(r.status_code, 200, name)
        self.assertEqual(Media.objects.count(), 0)
        after = {os.path.join(d, p) for d, _, files in os.walk(self._media_dir) for p in files}
        self.assertEqual(sorted(after - before), [])

    def test_too_many_files(self):
        files = [SimpleUploadedFile(f"{i}.txt", b"x") for i in range(MAX_FILES_PER_UPLOAD + 1)]
        r = self.upload(self.admin, files)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Media.objects.count(), 0)

    def test_no_file_selected(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.upload_url, {})
        self.assertTrue(r.context["form"].errors.get("files"))

    def test_regular_user_and_anonymous_blocked(self):
        self.assertEqual(self.client.get(self.upload_url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.upload_url).status_code, 403)
        self.assertEqual(self.client.post(self.upload_url, {"files": up()}).status_code, 403)
        self.assertEqual(Media.objects.count(), 0)

    def test_upload_requires_csrf(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(self.upload_url, {"files": up()}).status_code, 403)
        self.assertEqual(Media.objects.count(), 0)


class LibraryTests(MediaBase):
    def setUp(self):
        super().setUp()
        self.a = Media.objects.create(file="x/a.png", original_name="sunset.png", title="غروب", alt_text="دریا", kind="image", uploaded_by=self.author)
        self.b = Media.objects.create(file="x/b.pdf", original_name="report.pdf", title="گزارش", kind="document", uploaded_by=self.author2)
        self.c = Media.objects.create(file="x/c.mp3", original_name="song.mp3", kind="audio", uploaded_by=self.admin)

    def ids(self, user, **params):
        self.client.force_login(user)
        return {m.pk for m in self.client.get(self.list_url, params).context["items"]}

    def test_search_by_title_filename_alt(self):
        self.assertEqual(self.ids(self.admin, q="غروب"), {self.a.pk})
        self.assertEqual(self.ids(self.admin, q="report"), {self.b.pk})
        self.assertEqual(self.ids(self.admin, q="دریا"), {self.a.pk})
        self.assertEqual(self.ids(self.admin, q="یافت-نشد"), set())

    def test_kind_and_mine_filters(self):
        self.assertEqual(self.ids(self.admin, kind="document"), {self.b.pk})
        self.assertEqual(self.ids(self.author, mine="1"), {self.a.pk})
        self.assertEqual(self.ids(self.admin, kind="bogus"), {self.a.pk, self.b.pk, self.c.pk})

    def test_authors_see_whole_library_but_users_are_blocked(self):
        self.assertEqual(self.ids(self.author2), {self.a.pk, self.b.pk, self.c.pk})
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_pagination(self):
        for i in range(30):
            Media.objects.create(file=f"x/{i}.png", original_name=f"n{i}.png", kind="image")
        self.client.force_login(self.admin)
        self.assertEqual(len(self.client.get(self.list_url).context["items"]), 24)
        self.assertEqual(len(self.client.get(self.list_url, {"page": 2}).context["items"]), 9)

    def test_delete_buttons_only_for_manageable_items(self):
        self.client.force_login(self.author)
        r = self.client.get(self.list_url)
        self.assertEqual(r.context["manageable_ids"], {self.a.pk})
        self.assertContains(r, reverse("media_library:media_delete", args=[self.a.pk]))
        self.assertNotContains(r, reverse("media_library:media_delete", args=[self.b.pk]))

    def test_missing_alt_badge_shown(self):
        Media.objects.create(file="x/n.png", original_name="n.png", kind="image")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.list_url), "بدون Alt")


class EditDeleteTests(MediaBase):
    def make(self, owner):
        self.client.force_login(owner)
        self.client.post(self.upload_url, {"files": up(), "alt_text": "اولیه"})
        return Media.objects.latest("id")

    def test_owner_edits_alt_text_and_title(self):
        media = self.make(self.author)
        r = self.client.post(reverse("media_library:media_update", args=[media.pk]), {"title": "جدید", "alt_text": "متن جایگزین جدید"}, follow=True)
        media.refresh_from_db()
        self.assertEqual((media.title, media.alt_text), ("جدید", "متن جایگزین جدید"))
        self.assertContains(r, "ذخیره شد")
        self.assertTrue(AuditLog.objects.filter(action="update", object_type="رسانه").exists())

    def test_alt_text_length_validated(self):
        media = self.make(self.author)
        r = self.client.post(reverse("media_library:media_update", args=[media.pk]), {"title": "", "alt_text": "ا" * 301})
        self.assertTrue(r.context["form"].errors.get("alt_text"))

    def test_other_author_can_view_but_not_edit_or_delete(self):
        media = self.make(self.author)
        self.client.force_login(self.author2)
        r = self.client.get(reverse("media_library:media_update", args=[media.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["can_manage"])
        self.assertNotContains(r, 'name="alt_text"')
        self.assertEqual(self.client.post(reverse("media_library:media_update", args=[media.pk]), {"title": "هک", "alt_text": "هک"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("media_library:media_delete", args=[media.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("media_library:media_delete", args=[media.pk])).status_code, 403)
        media.refresh_from_db()
        self.assertEqual(media.alt_text, "اولیه")
        self.assertTrue(os.path.exists(media.file.path))

    def test_admin_can_manage_anyones_media(self):
        media = self.make(self.author)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("media_library:media_update", args=[media.pk]), {"title": "ت", "alt_text": "م"}).status_code, 302)

    def test_delete_removes_record_and_file_and_logs(self):
        media = self.make(self.author)
        path = media.file.path
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.client.get(reverse("media_library:media_delete", args=[media.pk])).status_code, 200)
        self.assertTrue(Media.objects.filter(pk=media.pk).exists())  # GET چیزی را حذف نمی‌کند
        r = self.client.post(reverse("media_library:media_delete", args=[media.pk]), follow=True)
        self.assertFalse(Media.objects.filter(pk=media.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.assertContains(r, "حذف شد")
        self.assertTrue(AuditLog.objects.filter(action="delete", object_type="رسانه", object_id=str(media.pk)).exists())

    def test_user_deletion_keeps_media(self):
        media = self.make(self.author)
        self.author.delete()
        media.refresh_from_db()
        self.assertIsNone(media.uploaded_by)
