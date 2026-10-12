import asyncio
import logging
import sys

import pytest
from temporalio.exceptions import ApplicationError
from typer.testing import CliRunner

from fixagent import local
from fixagent.agent import activities as act
from fixagent.agent import run as run_mod
from fixagent.agent.run import run_id, run_input
from fixagent.cli import app

from agent_fakes import BRAND


def test_run_input_reads_the_brand_site(tmp_path):
    (tmp_path / "acme.yaml").write_text(BRAND)
    inp = run_input("acme", brands_root=tmp_path)
    assert (inp.repo, inp.base, inp.domain, inp.root, inp.code_root) == ("acme/site", "main", "acme.com", "public", "web")
    assert run_id("acme") == "fixrun-acme"


def test_run_input_needs_a_site(tmp_path):
    (tmp_path / "acme.yaml").write_text(BRAND.split("site:")[0])
    with pytest.raises(ValueError, match="no site"):
        run_input("acme", tmp_path)


def test_worker_rejects_unknown_role():
    r = CliRunner().invoke(app, ["worker", "shell"])
    assert r.exit_code != 0 and "expected one of" in r.output


def test_fixrun_reports_operator_errors_in_one_line(monkeypatch):
    async def down():
        raise RuntimeError("Failed client connect: connection refused")
    monkeypatch.setattr("fixagent.agent.run.connect", down)
    r = CliRunner().invoke(app, ["fixrun", "status", "acme"])
    assert r.exit_code == 1 and r.output.strip() == "stopped: Failed client connect: connection refused"


def test_fixrun_start_unknown_slug_is_one_line(monkeypatch):
    async def client():
        return object()
    monkeypatch.setattr("fixagent.agent.run.connect", client)
    r = CliRunner().invoke(app, ["fixrun", "start", "no-such-brand"])
    assert r.exit_code == 1 and r.output.startswith("stopped: ")


def test_local_mode_swaps_github_fetch_and_onboarding(monkeypatch):
    monkeypatch.setenv("FIXAGENT_LOCAL", "1")
    monkeypatch.setattr(run_mod.keys, "issuer_key", lambda: "issuer")
    monkeypatch.setattr(run_mod.keys, "holder_public_key", lambda: "holder")
    run_mod.configure_role("executor")
    assert isinstance(act.DEPS.github("local/northside-bakery"), local.LocalGit) and act.DEPS.fetch is local.fetch
    run_mod.configure_role("minter")
    assert act.DEPS.is_onboarded is local.is_local_repo


def test_connect_retries_while_temporal_starts(monkeypatch):
    calls = []

    async def flaky(address):
        calls.append(address)
        if len(calls) < 3:
            raise RuntimeError("Failed client connect")
        return "client"
    monkeypatch.setattr(run_mod.Client, "connect", flaky)
    monkeypatch.setattr(run_mod, "RETRY_SECS", 0)
    assert asyncio.run(run_mod.connect(wait=5)) == "client" and len(calls) == 3


def _record(err: Exception) -> logging.LogRecord:
    try:
        raise err
    except Exception:
        return logging.LogRecord("temporalio.activity", logging.WARNING, __file__, 1, "Completing activity as failed",
                                 None, sys.exc_info())


def test_quiet_denials_strips_only_tenuo_denials():
    from tenuo.exceptions import ToolNotAuthorized
    from tenuo.temporal.exceptions import _build_non_retryable_application_error
    denial = _build_non_retryable_application_error(ToolNotAuthorized("write_file"))
    denied, bug = _record(denial), _record(ApplicationError("boom"))
    assert denial.__cause__ is not None
    for r in (denied, bug):
        assert run_mod.QuietDenials().filter(r)
    assert denied.exc_info is None
    assert bug.exc_info is not None
