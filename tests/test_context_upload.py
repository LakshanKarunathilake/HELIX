"""Context zip upload, hosted folder restriction, size limit, /api/info."""

from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.context_upload import ContextUploadError, extract_context_zip


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return path


def test_extracts_txt_flat(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "A"), ("sub/b.txt", "B"), ("img.png", "x")])
    dest = tmp_path / "out"
    assert extract_context_zip(z, dest) == 2
    assert sorted(p.name for p in dest.iterdir()) == ["a.txt", "b.txt"]


def test_traversal_member_rejects_upload(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "A"), ("../evil.txt", "E")])
    dest = tmp_path / "w" / "out"
    with pytest.raises(ContextUploadError):
        extract_context_zip(z, dest)
    assert not dest.exists()
    assert not (tmp_path / "w").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_backslash_and_absolute_rejected(tmp_path):
    for bad in ("..\\evil.txt", "/abs.txt", "C:\\x.txt"):
        z = _zip(tmp_path / "c.zip", [(bad, "E")])
        with pytest.raises(ContextUploadError):
            extract_context_zip(z, tmp_path / "out")


def test_uncompressed_limit(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "x" * 11)])
    with pytest.raises(ContextUploadError):
        extract_context_zip(z, tmp_path / "out", max_total_bytes=10)


def test_member_limit(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "A"), ("b.txt", "B")])
    with pytest.raises(ContextUploadError):
        extract_context_zip(z, tmp_path / "out", max_members=1)


def test_duplicate_basename_rejected(tmp_path):
    z = _zip(tmp_path / "c.zip", [("x/a.txt", "1"), ("y/a.txt", "2")])
    with pytest.raises(ContextUploadError, match="duplicate file name"):
        extract_context_zip(z, tmp_path / "out")


def test_macosx_and_dotunderscore_skipped(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "A"), ("__MACOSX/a.txt", "junk"),
                                  ("d/._b.txt", "junk"), ("d/", "")])
    dest = tmp_path / "out"
    assert extract_context_zip(z, dest) == 1
    assert [p.name for p in dest.iterdir()] == ["a.txt"]


def test_not_a_zip(tmp_path):
    f = tmp_path / "c.zip"
    f.write_text("nope")
    with pytest.raises(ContextUploadError):
        extract_context_zip(f, tmp_path / "out")


def test_encrypted_rejected(tmp_path):
    z = _zip(tmp_path / "c.zip", [("a.txt", "A")])
    raw = bytearray(z.read_bytes())
    # set the encryption bit in local header and central directory entries
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = raw.find(sig)
        raw[i + off] |= 1
    z.write_bytes(bytes(raw))
    with pytest.raises(ContextUploadError, match="encrypted"):
        extract_context_zip(z, tmp_path / "out")


def test_inflated_size_header_rejected(tmp_path):
    # A lying header is caught by zipfile's own size/CRC checks (the in-code
    # over-read check is only a backstop).
    z = _zip(tmp_path / "c.zip", [("a.txt", "x" * 100)])
    raw = bytearray(z.read_bytes())
    # shrink declared uncompressed size (stored, so csize == size) in both headers
    for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        i = raw.find(sig)
        raw[i + off:i + off + 4] = (5).to_bytes(4, "little")
    z.write_bytes(bytes(raw))
    dest = tmp_path / "out"
    with pytest.raises(ContextUploadError):
        extract_context_zip(z, dest)
    assert not dest.exists()


def test_failure_leaves_existing_dest_intact(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    z = _zip(tmp_path / "c.zip", [("a.txt", "A"), ("../evil.txt", "E")])
    with pytest.raises(ContextUploadError):
        extract_context_zip(z, dest)
    assert (dest / "old.txt").read_text() == "old"
    z2 = _zip(tmp_path / "c2.zip", [("n.txt", "N")])
    assert extract_context_zip(z2, dest) == 1
    assert [p.name for p in dest.iterdir()] == ["n.txt"]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ctx-")] == []


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    import api.main as m
    monkeypatch.setattr(m, "_TMP", tmp_path / "up")
    (tmp_path / "up").mkdir()
    sdir = tmp_path / "sessions" / "s"
    sdir.mkdir()
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung"], "document_id": ["d1"]}).to_csv(inp, index=False)
    cfg = {"session_name": "s", "annotator": "a", "input_path": str(inp),
           "delimiter": ",", "label_column": "entity_text",
           "doc_id_col": "document_id", "kbs": []}
    (sdir / "session.json").write_text(json.dumps(cfg))
    return TestClient(m.app)


def _zbytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in members:
            zf.writestr(n, d)
    return buf.getvalue()


def test_upload_route_sets_context_folder(client, tmp_path):
    r = client.post("/api/sessions/s/context",
                    files={"file": ("c.zip", _zbytes([("d1.txt", "hello lung")]))})
    assert r.status_code == 200 and r.json() == {"files": 1}
    row = client.get("/api/sessions/s/rows/0").json()
    assert row["context_text"] == "hello lung"
    assert list((tmp_path / "up").iterdir()) == []


def test_upload_unknown_session_404(client):
    r = client.post("/api/sessions/nope/context",
                    files={"file": ("c.zip", _zbytes([("d1.txt", "x")]))})
    assert r.status_code == 404


def test_upload_bad_zip_400_and_old_context_kept(client, tmp_path):
    client.post("/api/sessions/s/context",
                files={"file": ("c.zip", _zbytes([("d1.txt", "keep")]))})
    r = client.post("/api/sessions/s/context",
                    files={"file": ("c.zip", _zbytes([("../e.txt", "x")]))})
    assert r.status_code == 400
    assert (tmp_path / "sessions" / "s" / "context" / "d1.txt").read_text() == "keep"
    assert list((tmp_path / "up").iterdir()) == []


def test_upload_over_50mb_is_413(client, tmp_path, monkeypatch):
    import api.main as m
    monkeypatch.setattr(m, "MAX_CONTEXT_UPLOAD", 1000)
    r = client.post("/api/sessions/s/context",
                    files={"file": ("c.zip", b"0" * 5000)})
    assert r.status_code == 413
    assert list((tmp_path / "up").iterdir()) == []


def test_max_upload_constant():
    import api.main as m
    assert m.MAX_CONTEXT_UPLOAD == 50 * 1024 * 1024


def test_hosted_rejects_server_folder(client, monkeypatch):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    body = client.get("/api/sessions/s").json()
    body["context_folder"] = "/etc"
    assert client.put("/api/sessions/s", json=body).status_code == 400


def test_hosted_allows_own_context_folder(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    client.post("/api/sessions/s/context",
                files={"file": ("c.zip", _zbytes([("d1.txt", "x")]))})
    body = client.get("/api/sessions/s").json()
    assert body["context_folder"]
    assert client.put("/api/sessions/s", json=body).status_code == 200


def test_local_allows_server_folder(client, tmp_path):
    body = client.get("/api/sessions/s").json()
    body["context_folder"] = str(tmp_path)
    assert client.put("/api/sessions/s", json=body).status_code == 200


def test_info_endpoint(client, monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    assert client.get("/api/info").json() == {"hosted": False}
    monkeypatch.setenv("HELIX_HOSTED", "1")
    assert client.get("/api/info").json() == {"hosted": True}


# ── fix round 1 ──────────────────────────────────────────────────────────────

def test_read_context_confined(tmp_path):
    from api.main import _read_context
    folder = tmp_path / "ctx"
    folder.mkdir()
    (folder / "abc.txt").write_text("ABC")
    (folder / "ok.txt").write_text("OK")
    (tmp_path / "x").write_text("SECRET")
    (tmp_path / "x.txt").write_text("SECRET")
    assert _read_context(folder, "/etc/passwd") is None
    assert _read_context(folder, "../x") is None
    assert _read_context(folder, "a*") is None
    assert _read_context(folder, "ok") == "OK"


def test_row_context_traversal_not_served(client, tmp_path):
    client.post("/api/sessions/s/context",
                files={"file": ("c.zip", _zbytes([("d1.txt", "fine")]))})
    sdir = tmp_path / "sessions" / "s"
    (sdir / "outside.txt").write_text("SECRET")
    pd.DataFrame({"entity_text": ["lung"], "document_id": ["../outside"]}).to_csv(
        sdir / "input.csv", index=False)
    row = client.get("/api/sessions/s/rows/0").json()
    assert row["context_text"] is None


def test_corrupt_deflate_is_400_and_clean(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("d1.txt", "hello world " * 500)
    raw = bytearray(buf.getvalue())
    for k in range(40, 60):  # corrupt the start of the deflate stream
        raw[k] ^= 0xFF
    r = client.post("/api/sessions/s/context", files={"file": ("c.zip", bytes(raw))})
    assert r.status_code == 400
    sdir = tmp_path / "sessions" / "s"
    assert [p.name for p in sdir.iterdir() if p.name.startswith(".ctx")] == []
    assert list((tmp_path / "up").iterdir()) == []


def test_swap_failure_restores_old_dest(tmp_path, monkeypatch):
    from pathlib import Path
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    z = _zip(tmp_path / "c.zip", [("n.txt", "N")])
    real = Path.rename

    def flaky(self, target):
        if self.name.startswith(".ctx-") and not self.name.startswith(".ctx-old-"):
            raise OSError("boom")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        extract_context_zip(z, dest)
    monkeypatch.undo()
    assert (dest / "old.txt").read_text() == "old"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ctx")] == []


def test_write_oserror_propagates_not_400(tmp_path, monkeypatch):
    z = _zip(tmp_path / "c.zip", [("n.txt", "N")])
    import builtins
    real_open = builtins.open

    def bad_open(path, mode="r", *a, **k):
        if "w" in mode and str(path).endswith("n.txt"):
            raise OSError(28, "No space left on device")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", bad_open)
    with pytest.raises(OSError) as ei:
        extract_context_zip(z, tmp_path / "out")
    assert not isinstance(ei.value, ContextUploadError)


def test_hosted_create_bad_folder_leaves_no_session_dir(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    up = tmp_path / "up" / "in.csv"
    up.write_text("entity_text,document_id\nlung,d1\n")
    r = client.post("/api/sessions", json={
        "session_name": "new", "annotator": "a", "tmp_path": str(up),
        "original_filename": "in.csv", "delimiter": ",", "label_column": "entity_text",
        "context_folder": "/etc"})
    assert r.status_code == 400
    assert not (tmp_path / "sessions" / "new").exists()
    assert up.exists()


def test_corrupt_bzip2_is_400_and_clean(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_BZIP2) as zf:
        zf.writestr("d1.txt", "hello world " * 500)
    raw = bytearray(buf.getvalue())
    i = raw.find(b"BZh")
    for k in range(i + 10, i + 30):
        raw[k] ^= 0xFF
    r = client.post("/api/sessions/s/context", files={"file": ("c.zip", bytes(raw))})
    assert r.status_code == 400
    sdir = tmp_path / "sessions" / "s"
    assert [p.name for p in sdir.iterdir() if p.name.startswith(".ctx")] == []
    assert list((tmp_path / "up").iterdir()) == []
