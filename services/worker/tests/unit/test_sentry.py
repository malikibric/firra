from firra.sentry import scrub_event


def test_scrub_removes_bodies_cookies_and_user() -> None:
    event = {
        "request": {
            "data": {"body": "hello"},
            "cookies": "sid=1",
            "query_string": "email=a@b.c",
            "headers": {"Authorization": "Bearer x", "Content-Type": "application/json"},
        },
        "user": {"email": "a@b.c"},
    }
    scrubbed = scrub_event(event, None)
    assert scrubbed["request"] == {"headers": {"Content-Type": "application/json"}}
    assert "user" not in scrubbed
