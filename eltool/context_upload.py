"""Safe extraction of an uploaded zip of ``<doc_id>.txt`` context files."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


class ContextUploadError(ValueError):
    """The uploaded context archive is unusable or unsafe."""


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
                with zf.open(info) as src, open(tmp / base, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size:
                            raise ContextUploadError(
                                f"{base} is larger than its declared size")
                        out.write(chunk)
            if dest.exists():
                shutil.rmtree(dest)
            tmp.rename(dest)
        except BaseException as e:
            shutil.rmtree(tmp, ignore_errors=True)
            if isinstance(e, (zipfile.BadZipFile, RuntimeError, OSError)) and not isinstance(
                    e, ContextUploadError):
                raise ContextUploadError("could not read the zip file") from e
            raise
    return len(wanted)
