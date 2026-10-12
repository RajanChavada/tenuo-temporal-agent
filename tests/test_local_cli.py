from typer.testing import CliRunner

from fixagent import local
from fixagent.cli import app


def test_demo_init_needs_local_mode(monkeypatch):
    monkeypatch.delenv("FIXAGENT_LOCAL", raising=False)
    r = CliRunner().invoke(app, ["demo", "init"])
    assert r.exit_code == 1 and "FIXAGENT_LOCAL=1" in r.output


def test_demo_init_makes_keys_and_site(monkeypatch):
    monkeypatch.setenv("FIXAGENT_LOCAL", "1")
    done = []
    monkeypatch.setattr("fixagent.cli.keys.init_issuer", lambda: done.append("issuer"))
    monkeypatch.setattr("fixagent.cli.keys.init_holder", lambda: done.append("holder"))
    monkeypatch.setattr(local, "init_site", lambda reset: done.append(("site", reset)))
    r = CliRunner().invoke(app, ["demo", "init", "--reset"])
    assert r.exit_code == 0 and done == ["issuer", "holder", ("site", True)]


class FakeGit:
    def __init__(self, *a):
        self.merged = []

    def diff(self, n):
        return "+Allow: /\n"

    def merge(self, n):
        self.merged.append(n)


def test_pr_merge_shows_the_diff_and_asks(monkeypatch):
    git = FakeGit()
    monkeypatch.setattr(local, "LocalGit", lambda: git)
    r = CliRunner().invoke(app, ["pr", "merge", "1"], input="n\n")
    assert "+Allow: /" in r.output and git.merged == [] and r.exit_code == 1
    r = CliRunner().invoke(app, ["pr", "merge", "1"], input="y\n")
    assert r.exit_code == 0 and git.merged == [1]


def test_pr_merge_unknown_number_is_one_line(monkeypatch, tmp_path):
    real = local.LocalGit
    monkeypatch.setattr(local, "LocalGit", lambda: real(tmp_path, tmp_path / "prs"))
    r = CliRunner().invoke(app, ["pr", "merge", "7"])
    assert r.exit_code == 1 and r.output.strip() == "stopped: no local pull request 7"
