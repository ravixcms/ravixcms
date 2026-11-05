#!/usr/bin/env python3
"""Offline signer for a Ravix release folder (the output of ``manage.py ravix_release … --no-sign``).

It runs on the machine that holds the private key: the server (key in /root/ravix-keys/) or a Windows PC (key on a USB stick), and needs only Python 3.9+
and the ``cryptography`` package — not Django, not the Ravix code, not the site's database:

    pip install cryptography
    python ravix_sign.py dist\\ --key-file E:\\keys\\release-2026.key          (Windows)
    python scripts/ravix_sign.py dist/ --key-file /root/ravix-keys/release-2026.key

For every package listed in index.json it checks that the file's SHA-256 is the one the index says, signs "kind, slug, version, sha256" with Ed25519, and
signs index.json itself (index.json.sig). Packages that already carry a signature are left alone unless --resign is given. The private key is read
from the file and never printed, copied or written anywhere. ``--check`` verifies a signed folder against a public key without needing any private key.
"""
import argparse
import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

PREFIX = "ravin-update-v1"  # the historical protocol prefixes; they are part of the signed message and must never change
INDEX_PREFIX = "ravin-index-v1"


def b64(value: str, length: int) -> bytes:
    raw = base64.b64decode(value.strip(), validate=True)
    if len(raw) != length:
        raise ValueError
    return raw


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


def message(kind: str, slug: str, version: str, sha256: str) -> bytes:
    return "\n".join([PREFIX, kind, slug or "-", version, sha256]).encode()


def index_message(sha256: str) -> bytes:
    return "\n".join([INDEX_PREFIX, sha256]).encode()


def read_seed(path: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    p = Path(path)
    if os.name == "posix" and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"The private key file ({p}) must have permission 600 (chmod 600).")
    try:
        return Ed25519PrivateKey.from_private_bytes(b64(p.read_text(encoding="utf-8"), 32))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"The key file could not be read as a Ravix signing key: {type(exc).__name__}")


def entries(doc: dict):
    """(kind, slug, entry) for every package of index.json."""
    for entry in doc.get("core", []):
        yield "core", "", entry
    for slug, items in (doc.get("themes") or {}).items():
        for entry in items:
            yield "theme", slug, entry


def sign_folder(folder: Path, key, resign: bool) -> tuple[int, str]:
    public = key.public_key().public_bytes_raw()
    kid = key_id(public)
    index_path = folder / "index.json"
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    signed = 0
    for kind, slug, entry in entries(doc):
        name = Path(str(entry.get("download_url", ""))).name
        package = folder / name
        if not name or not package.is_file():
            raise SystemExit(f"The package file for {kind} {entry.get('version')} is missing: {name!r}")
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        if sha != entry.get("sha256"):
            raise SystemExit(f"{name}: its SHA-256 ({sha[:16]}…) is not the one in index.json — the file changed after the index was built. Nothing was signed.")
        if entry.get("signature") and not resign:
            continue
        entry["signature"] = base64.b64encode(key.sign(message(kind, slug, str(entry["version"]), sha))).decode()
        entry["signature_key_id"] = kid
        signed += 1
    data = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True).encode()
    index_path.write_bytes(data)
    sig = base64.b64encode(key.sign(index_message(hashlib.sha256(data).hexdigest()))).decode()
    (folder / "index.json.sig").write_text(json.dumps({"signature": sig, "key_id": kid}), encoding="utf-8")
    return signed, kid


def check_folder(folder: Path, public_b64: str) -> list[str]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public = Ed25519PublicKey.from_public_bytes(b64(public_b64, 32))
    problems = []
    data = (folder / "index.json").read_bytes()
    try:
        sig = json.loads((folder / "index.json.sig").read_text(encoding="utf-8"))["signature"]
        public.verify(base64.b64decode(sig), index_message(hashlib.sha256(data).hexdigest()))
    except (OSError, ValueError, KeyError, InvalidSignature):
        problems.append("index.json: the signature is missing or does not match this key")
    for kind, slug, entry in entries(json.loads(data)):
        name = Path(str(entry.get("download_url", ""))).name
        try:
            sha = hashlib.sha256((folder / name).read_bytes()).hexdigest()
            if sha != entry.get("sha256"):
                raise ValueError
            public.verify(base64.b64decode(entry["signature"]), message(kind, slug, str(entry["version"]), sha))
        except (OSError, ValueError, KeyError, InvalidSignature):
            problems.append(f"{name}: the file or its signature is wrong")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Sign a Ravix release folder offline.")
    ap.add_argument("folder", help="the folder with index.json and the packages (dist/)")
    ap.add_argument("--key-file", help="the private key file (a file with permission 600; on Windows a file on the USB stick)")
    ap.add_argument("--resign", action="store_true", help="sign packages that already have a signature too")
    ap.add_argument("--check", metavar="PUBLIC_KEY", help="only verify the folder against this public key (base64)")
    args = ap.parse_args()
    folder = Path(args.folder)
    if not (folder / "index.json").is_file():
        raise SystemExit(f"{folder}: index.json was not found (build it first with: manage.py ravix_release core --no-sign --out {folder})")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise SystemExit("This tool needs the cryptography package: pip install cryptography")
    if args.check:
        problems = check_folder(folder, args.check)
        print("\n".join(problems) if problems else "OK: every signature in this folder is valid for that key")
        return 1 if problems else 0
    if not args.key_file:
        raise SystemExit("--key-file is required (or use --check PUBLIC_KEY to verify)")
    signed, kid = sign_folder(folder, read_seed(args.key_file), args.resign)
    print(f"signed {signed} package(s) and index.json with key {kid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
