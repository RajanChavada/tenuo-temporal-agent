from tenuo import SigningKey
from typer.testing import CliRunner

from fixagent.cli import app

ISSUER = SigningKey.generate()


def test_keys_init_prints_public_key(monkeypatch):
    monkeypatch.setattr("fixagent.cli.keys.init_issuer", lambda: ISSUER)
    holder = []
    monkeypatch.setattr("fixagent.cli.keys.init_holder", lambda: holder.append(1))
    r = CliRunner().invoke(app, ["keys", "init"])
    assert holder == [1]
    assert r.exit_code == 0 and bytes(ISSUER.public_key.to_bytes()).hex() in r.output


def test_keys_github_stores_token_without_echoing_it(monkeypatch):
    stored = {}
    monkeypatch.setattr("fixagent.cli.keys.set_github_token", lambda repo, token: stored.update({repo: token}))
    r = CliRunner().invoke(app, ["keys", "github", "acme/site"], input="fake_token_secret\n")
    assert r.exit_code == 0 and stored == {"acme/site": "fake_token_secret"}
    assert "fake_token_secret" not in r.output


def test_keys_github_refuses_an_empty_token(monkeypatch):
    calls = []
    monkeypatch.setattr("fixagent.cli.keys.set_github_token", lambda repo, token: calls.append(repo))
    r = CliRunner().invoke(app, ["keys", "github", "acme/site"], input="   \n")
    assert r.exit_code == 1 and "no token entered" in r.output and calls == []
