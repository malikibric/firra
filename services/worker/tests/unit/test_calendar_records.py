from firra.connectors.google.calendar import to_record
from tests.fake_google import event


def test_maps_times_people_and_rescheduling() -> None:
    record = to_record(
        event(
            "e1",
            "2026-09-01T10:00:00+00:00",
            "2026-09-01T10:45:00+00:00",
            ["CEO@acme.com", "room@resource.calendar.google.com"],
            original_start="2026-08-31T10:00:00+00:00",
        )
    )
    assert record is not None
    assert record.organizer.email == "ana@northbeam.agency"
    assert [a.email for a in record.attendees] == [
        "ceo@acme.com",
        "room@resource.calendar.google.com",
    ]
    assert record.rescheduled is True
    assert (record.ends_at - record.starts_at).total_seconds() == 45 * 60


def test_skips_all_day_and_resource_attendees() -> None:
    all_day = event("e2", "x", "y", [])
    all_day["start"], all_day["end"] = {"date": "2026-09-01"}, {"date": "2026-09-02"}
    assert to_record(all_day) is None
    with_room = event("e3", "2026-09-01T10:00:00+00:00", "2026-09-01T11:00:00+00:00", [])
    with_room["attendees"] = [{"email": "room@x.com", "resource": True}, {"email": "a@acme.com"}]
    record = to_record(with_room)
    assert record is not None and [a.email for a in record.attendees] == ["a@acme.com"]
