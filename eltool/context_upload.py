"""Safe extraction of an uploaded zip of ``<doc_id>.txt`` context files."""

from __future__ import annotations

import re
import shutil
import lzma
import tempfile
import uuid
import zipfile
import zlib
from pathlib import Path, PurePosixPath


class ContextUploadError(ValueError):
    """The uploaded context archive is unusable or unsafe."""


_ZIP_READ_ERRORS = (zlib.error, EOFError, zipfile.BadZipFile, NotImplementedError,
                    lzma.LZMAError, RuntimeError, ValueError)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _normalise(name: str) -> PurePosixPath:
    n = name.replace("\\", "/")
    if n.startswith("/") or _DRIVE_RE.match(n):
        raise ContextUploadError(f"unsafe path in zip: {name!r}")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ContextUploadError(f"unsafe path in zip: {name!r}")
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _skipped(info: zipfile.ZipInfo, path: PurePosixPath) -> bool:
    if info.is_dir() or info.filename.endswith(("/", "\\")):
        return True
    if "__MACOSX" in path.parts:
        return True
    return path.name.startswith("._")


def extract_context_zip(
    zip_path: Path,
    dest: Path,
    *,
    max_total_bytes: int = 500_000_000,
    max_members: int = 100_000,
) -> int:
    """Extract the ``*.txt`` members of ``zip_path`` flat into ``dest``.

    Everything is validated before anything is written; extraction goes to a
    temp dir beside ``dest`` and replaces it only on success.
    Returns the number of files written.
    """
    dest = Path(dest)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ContextUploadError("not a valid zip file") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise ContextUploadError(f"too many files in zip (limit {max_members})")
        if sum(i.file_size for i in infos) > max_total_bytes:
            raise ContextUploadError(
                f"zip is too large when uncompressed (limit {max_total_bytes} bytes)")

        wanted: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        for info in infos:
            path = _normalise(info.filename)  # traversal check on every member
            if _skipped(info, path):
                continue
            if info.flag_bits & 0x1:
                raise ContextUploadError("encrypted zip files are not supported")
            if not path.name.lower().endswith(".txt"):
                continue
            if path.name in seen:
                raise ContextUploadError(f"duplicate file name in zip: {path.name}")
            seen.add(path.name)
            wanted.append((info, path.name))

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".ctx-", dir=dest.parent))
        try:
            for info, base in wanted:
                written = 0
                try:
                    src = zf.open(info)
                except _ZIP_READ_ERRORS + (OSError,) as e:  # bz2 raises OSError on bad data
                    raise ContextUploadError(f"could not read {base} in the zip") from e
                with src, open(tmp / base, "wb") as out:
                    while True:
                        try:
                            chunk = src.read(65536)
                        except _ZIP_READ_ERRORS + (OSError,) as e:  # bz2 raises OSError on bad data
                            raise ContextUploadError(
                                f"could not read {base} in the zip") from e
                        if not chunk:
                            break
                        written += len(chunk)
                        # Backstop: ZipExtFile already caps reads and checks the CRC.
                        if written > info.file_size:
                            raise ContextUploadError(
                                f"{base} is larger than its declared size")
                        out.write(chunk)  # filesystem errors propagate (not zip errors)
            _swap(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return len(wanted)


def _swap(tmp: Path, dest: Path) -> None:
    """Replace ``dest`` with ``tmp``; restore the old dest if the swap fails."""
    old: Path | None = None
    if dest.exists():
        old = dest.parent / f".ctx-old-{uuid.uuid4().hex}"
        dest.rename(old)
    try:
        tmp.rename(dest)
    except BaseException:
        if old is not None:
            old.rename(dest)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
