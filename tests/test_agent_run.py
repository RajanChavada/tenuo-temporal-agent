import pytest
from typer.testing import CliRunner

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
