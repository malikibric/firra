"""Token acquisition is scripted; these tests never execute gcloud."""

import subprocess
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from firra.app import _llm
from firra.crypto.kek import MetadataTokenSource
from firra.db.pool import create_pool_for
from firra.llm.tokens import GcloudTokenError, GcloudTokenSource, _run
from tests.conftest import local_settings


def test_cached_token_expires_at_45_minutes() -> None:
    now = [0.0]
    calls = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return f"fake-{len(calls)}\n"

    source = GcloudTokenSource(runner=runner, clock=lambda: now[0])
    assert source.token() == "fake-1"
    now[0] = 2699
    assert source.token() == "fake-1"
    now[0] = 2700
    assert source.token() == "fake-2"
    assert calls == [["gcloud", "auth", "print-access-token"]] * 2
    assert "fake-2" not in repr(source)


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("secret output"),
        subprocess.CalledProcessError(1, "gcloud", output="secret output"),
        subprocess.TimeoutExpired("gcloud", 30, output="secret output"),
    ],
)
def test_command_failure_is_typed_and_does_not_expose_output(failure: Exception) -> None:
    def runner(argv: list[str]) -> str:
        raise failure

    with pytest.raises(GcloudTokenError) as caught:
        GcloudTokenSource(runner=runner).token()
    assert "secret output" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("output", ["", "\n", "two tokens"])
def test_unusable_token_is_rejected(output: str) -> None:
    with pytest.raises(GcloudTokenError):
        GcloudTokenSource(runner=lambda argv: output).token()


def test_default_runner_has_fixed_timeout_and_no_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["gcloud", "auth", "print-access-token"]
        assert kwargs == {"capture_output": True, "text": True, "check": True, "timeout": 30}
        return subprocess.CompletedProcess(argv, 0, stdout="fake")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _run(["gcloud", "auth", "print-access-token"]) == "fake"


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_gcloud_is_rejected_outside_local_test(env: str) -> None:
    with pytest.raises(ValidationError, match="gcloud Vertex tokens"):
        local_settings(env=env, vertex_token_source="gcloud")


@pytest.mark.parametrize("env", ["local", "test", "staging", "prod"])
def test_positive_hard_cap_is_allowed_in_every_environment(env: str) -> None:
    assert (
        local_settings(
            env=env,
            task_auth_mode="oidc",
            task_oidc_audience="fake",
            task_invoker_email="fake",
            llm_hard_cap_usd=Decimal(5),
        ).llm_hard_cap_usd
        == 5
    )
    with pytest.raises(ValidationError):
        local_settings(
            env=env,
            task_auth_mode="oidc",
            task_oidc_audience="fake",
            task_invoker_email="fake",
            llm_hard_cap_usd=Decimal(0),
        )


@pytest.mark.parametrize("source", ["metadata", "gcloud"])
def test_llm_selects_tokens_and_cap_without_acquiring_token(source: str) -> None:
    settings = local_settings(
        vertex_token_source=source,
        vertex_location="eu",
        gcp_project_id="fake",
        llm_hard_cap_usd=Decimal(5),
    )
    with httpx.Client() as http:
        metadata = MetadataTokenSource(http)
        pool = create_pool_for(settings.database_url.get_secret_value())
        llm = _llm(settings, pool, http, metadata)
        assert llm is not None
        assert llm.hard_cap_usd == 5
        tokens = llm.backend.tokens  # type: ignore[attr-defined]
        assert isinstance(tokens, GcloudTokenSource) if source == "gcloud" else tokens is metadata
