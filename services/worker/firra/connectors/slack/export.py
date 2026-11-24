"""Bounded, lazy Slack export reader; members are never extracted to disk.

Docs checked: 2026-10-05,
https://slack.com/help/articles/220556107-How-to-read-Slack-data-exports:
conversation references identify folders; each day is a JSON message array.
"""

import json
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Self
from zipfile import BadZipFile, ZipFile, ZipInfo

MAX_BYTES = 2 * 1024**3
MAX_ENTRIES = 200_000
# A single day/reference is bounded too: never allocate a multi-GiB JSON document.
MAX_MEMBER_BYTES = 64 * 1024**2
DAY = re.compile(r"\d{4}-\d{2}-\d{2}\.json\Z")


class ExportError(ValueError):
    """Safe error class; never carries a path, member name or message content."""


class ExportData:
    def __init__(self, archive: ZipFile, file: BinaryIO) -> None:
        self.archive, self.file = archive, file
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_BYTES:
            raise ExportError("export_too_large")
        names: set[str] = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or entry.filename != entry.orig_filename
                or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                or str(path) in names
            ):
                raise ExportError("unsafe_export")
            if entry.file_size > MAX_MEMBER_BYTES:
                raise ExportError("export_too_large")
            names.add(str(path))
        self.users: dict[str, dict[str, Any]] = {}
        for user in self.array("users.json"):
            if not isinstance(user, dict) or not isinstance(user.get("id"), str):
                raise ExportError("invalid_export")
            if not isinstance(user.get("profile", {}), dict):
                raise ExportError("invalid_export")
            if user["id"] in self.users:
                raise ExportError("invalid_export")
            self.users[user["id"]] = user
        team_id = next(
            (
                u.get("team_id") or u.get("team")
                for u in self.users.values()
                if u.get("team_id") or u.get("team")
            ),
            None,
        )
        if not isinstance(team_id, str) or not team_id:
            raise ExportError("invalid_export")
        self.team_id: str = team_id
        self.channels: dict[str, dict[str, Any]] = {}
        folders: dict[str, dict[str, Any]] = {}
        for filename, kind in (
            ("channels.json", "public"),
            ("groups.json", "private"),
            ("dms.json", "direct"),
            ("mpims.json", "group_direct"),
        ):
            if filename not in names and filename != "channels.json":
                continue
            for raw in self.array(filename):
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    raise ExportError("invalid_export")
                members = raw.get("members", [])
                if not isinstance(members, list) or any(not isinstance(m, str) for m in members):
                    raise ExportError("invalid_export")
                channel = {
                    **raw,
                    "kind": kind,
                    "is_im": kind == "direct",
                    "is_mpim": kind == "group_direct",
                    "is_private": kind == "private",
                }
                if raw["id"] in self.channels:
                    raise ExportError("invalid_export")
                self.channels[raw["id"]] = channel
                for folder in (raw["id"], raw.get("name")):
                    if not isinstance(folder, str) or not folder:
                        continue
                    if folder in folders and folders[folder]["id"] != raw["id"]:
                        raise ExportError("invalid_export")
                    folders[folder] = channel
        self.days: list[tuple[ZipInfo, dict[str, Any] | None]] = []
        for entry in entries:
            parts = PurePosixPath(entry.filename).parts
            if len(parts) == 2 and DAY.fullmatch(parts[1]) and not entry.is_dir():
                self.days.append((entry, folders.get(parts[0])))
        self.days.sort(key=lambda item: item[0].filename)

    def array(self, member: str | ZipInfo) -> list[Any]:
        try:
            with self.archive.open(member) as stream:
                raw = stream.read(MAX_MEMBER_BYTES + 1)
            if len(raw) > MAX_MEMBER_BYTES:
                raise ExportError("export_too_large")
            value = json.loads(raw)
        except ExportError:
            raise
        except (
            BadZipFile,
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
            RecursionError,
            NotImplementedError,
        ):
            raise ExportError("invalid_export") from None
        if not isinstance(value, list):
            raise ExportError("invalid_export")
        return value

    def messages(self) -> Iterator[tuple[dict[str, Any] | None, Any]]:
        for member, channel in self.days:
            for message in self.array(member):
                yield channel, message

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.archive.close()
        self.file.close()


def read_export(path: str | Path) -> ExportData:
    """Keep the checked file descriptor open for lazy reads (no path replacement race)."""
    target = Path(path)
    if not target.is_absolute():
        raise ExportError("invalid_export_path")
    file: BinaryIO | None = None
    archive: ZipFile | None = None
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        file = os.fdopen(fd, "rb")
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ExportError("invalid_export_path")
        archive = ZipFile(file)
        return ExportData(archive, file)
    except ExportError:
        if archive is not None:
            archive.close()
        if file is not None:
            file.close()
        raise
    except (OSError, BadZipFile, ValueError):
        if archive is not None:
            archive.close()
        if file is not None:
            file.close()
        raise ExportError("invalid_export") from None
