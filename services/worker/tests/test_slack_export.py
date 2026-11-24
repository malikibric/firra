"""Synthetic archives only: lazy reads and hostile ZIP boundaries."""

import json
import stat
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from firra.connectors.slack import export


def archive(path: Path, extra: dict[str, object] | None = None) -> Path:
    values: dict[str, object] = {
        "users.json": [{"id": "UOWNER", "team": "TOWN"}],
        "channels.json": [{"id": "CPUBLIC", "name": "synthetic"}],
        "groups.json": [{"id": "GPRIVATE", "name": "private"}],
        "dms.json": [{"id": "DCLIENT", "members": ["UOWNER", "UCLIENT"]}],
        "mpims.json": [{"id": "GDM", "members": ["UOWNER", "UCLIENT", "UOTHER"]}],
        "synthetic/2026-10-05.json": [{"type": "message", "ts": "1791158400.000001"}],
        "DCLIENT/2026-10-05.json": [],
    }
    values.update(extra or {})
    with ZipFile(path, "w") as zipfile:
        for name, value in values.items():
            zipfile.writestr(name, json.dumps(value))
    return path


def test_references_kinds_and_lazy_days(tmp_path: Path) -> None:
    path = archive(tmp_path / "export.zip", {"synthetic/2026-10-06.json": "invalid-array"})
    with export.read_export(path) as data:
        assert data.team_id == "TOWN" and data.users["UOWNER"]["id"] == "UOWNER"
        assert {c["kind"] for c in data.channels.values()} == {
            "public",
            "private",
            "direct",
            "group_direct",
        }
        messages = data.messages()
        channel, message = next(messages)
        assert channel is not None and channel["id"] == "CPUBLIC"
        assert message["ts"] == "1791158400.000001"
        with pytest.raises(export.ExportError, match="invalid_export"):
            next(messages)
    assert data.file.closed and data.archive.fp is None


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "folder/../outside", "C:\\file", "a\\b"]
)
def test_traversal_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(export.ExportError, match="unsafe_export"):
        export.read_export(archive(tmp_path / "export.zip", {name: []}))
    assert not (tmp_path / "outside").exists()


def test_symlink_and_duplicate_members_refused(tmp_path: Path) -> None:
    path = archive(tmp_path / "export.zip")
    with ZipFile(path, "a") as zipfile:
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipfile.writestr(link, "users.json")
    with pytest.raises(export.ExportError, match="unsafe_export"):
        export.read_export(path)
    path = archive(path)
    with ZipFile(path, "a") as zipfile, pytest.warns(UserWarning):
        zipfile.writestr("users.json", "[]")
    with pytest.raises(export.ExportError, match="unsafe_export"):
        export.read_export(path)


@pytest.mark.parametrize("limit", ["MAX_BYTES", "MAX_ENTRIES", "MAX_MEMBER_BYTES"])
def test_limits_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    path = archive(tmp_path / "export.zip")
    monkeypatch.setattr(export, limit, 1)
    with pytest.raises(export.ExportError, match="export_too_large"):
        export.read_export(path)


def test_bad_files_safe_errors_and_no_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "private-path.zip"
    path.write_text("SYNTHETIC_PRIVATE_CONTENT")
    link = tmp_path / "link.zip"
    link.symlink_to(path)
    for target in (path, link, tmp_path, tmp_path / "missing.zip", Path("relative.zip")):
        with pytest.raises(export.ExportError) as caught:
            export.read_export(target)
        assert "private-path" not in str(caught.value)
        assert "SYNTHETIC_PRIVATE_CONTENT" not in str(caught.value)


def test_invalid_references_and_unknown_day_are_not_silently_ingested(tmp_path: Path) -> None:
    path = archive(tmp_path / "export.zip", {"unknown/2026-10-05.json": ["bad-message"]})
    with export.read_export(path) as data:
        assert (None, "bad-message") in list(data.messages())
    for users in ([], [{"id": "UOWNER"}], ["not-an-object"]):
        with pytest.raises(export.ExportError, match="invalid_export"):
            export.read_export(archive(path, {"users.json": users}))
