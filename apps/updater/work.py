"""Temporary folders for an uploaded update bundle (shared hosting). The bundle is untrusted: only flat files with plain names are unpacked,
with count and size limits; everything is then verified (SHA-256, signature) by the normal update pipeline."""
import re
import zipfile

from django.utils.translation import gettext

from .errors import UpdateError

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
ALLOWED = re.compile(r"^(index\.json|index\.json\.sig|ravix-(core|theme|plugin)-[A-Za-z0-9._-]+\.zip)$")
MAX_FILES = 12


def new_folder(prefix: str = "upload"):
    from . import pipeline

    return pipeline._work_dir()


def extract_bundle(fileobj, folder, *, max_total: int) -> None:
    with zipfile.ZipFile(fileobj) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos or len(infos) > MAX_FILES:
            raise UpdateError(gettext("This ZIP is not an update bundle (it must contain index.json and the package files)."))
        if sum(i.file_size for i in infos) > max_total:
            raise UpdateError(gettext("The package is larger than the size limit."))
        names = []
        for info in infos:
            name = info.filename
            if "/" in name or "\\" in name or not NAME.match(name) or not ALLOWED.match(name):
                raise UpdateError(gettext("This ZIP is not an update bundle (it must contain index.json and the package files)."))
            names.append(name)
        if "index.json" not in names:
            raise UpdateError(gettext("index.json was not found in the update source folder."))
        for info in infos:
            target = folder / info.filename
            with zf.open(info) as src, open(target, "wb") as out:
                written = 0
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > max_total:
                        raise UpdateError(gettext("The package is larger than the size limit."))
                    out.write(chunk)
