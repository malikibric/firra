from pathlib import Path

from firra.local.bootstrap import local_timezone


def test_timezone_comes_from_the_localtime_link(tmp_path: Path) -> None:
    link = tmp_path / "localtime"
    link.symlink_to("/var/db/timezone/zoneinfo/Europe/Sarajevo")
    assert local_timezone(link) == "Europe/Sarajevo"
    other = tmp_path / "other"
    other.symlink_to("/usr/share/zoneinfo.default/America/New_York")
    assert local_timezone(other) == "UTC"  # no zoneinfo/ segment: do not guess


def test_unreadable_localtime_is_utc(tmp_path: Path) -> None:
    assert local_timezone(tmp_path / "missing") == "UTC"
    regular = tmp_path / "regular"
    regular.write_text("not a link")
    assert local_timezone(regular) == "UTC"
