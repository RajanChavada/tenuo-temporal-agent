import json

import pytest

from fixagent.execute import warrant
from fixagent.execute.warrant import audit_to, cloud_client


class FakePanic(BaseException):
    """Stands in for pyo3's PanicException, which Tenuo's Rust core raises and which is not an Exception."""


def test_audit_to_appends_jsonl(tmp_path):
    path = tmp_path / "data" / "audit.jsonl"
    write = audit_to(path, "acme")
    write("allow", "read_file", {"repo": "acme/site", "path": "robots.txt"}, "")
    write("deny", "delete_file", {"repo": "acme/site"}, "not authorized")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["decision"] for r in rows] == ["allow", "deny"]
    assert rows[1]["job"] == "acme" and rows[1]["reason"] == "not authorized"


def test_no_cloud_without_env(monkeypatch):
    monkeypatch.delenv("TENUO_CONNECT_TOKEN", raising=False)
    monkeypatch.delenv("TENUO_API_KEY", raising=False)
    assert cloud_client() is None


def test_cloud_client_failure_returns_none(monkeypatch):
    monkeypatch.setenv("TENUO_API_KEY", "tc_test")
    import tenuo.control_plane
    def boom():
        raise FakePanic("no reactor running")
    monkeypatch.setattr(tenuo.control_plane, "get_or_create", boom)
    assert cloud_client() is None


class SlowRegistration:
    """authorizer_id is None for the first `after` reads, like the SDK while it registers in the background."""
    def __init__(self, after):
        self.reads, self.after = 0, after

    @property
    def authorizer_id(self):
        self.reads += 1
        return "authorizer-1" if self.reads > self.after else None


def test_cloud_client_waits_for_registration(monkeypatch):
    monkeypatch.setenv("TENUO_API_KEY", "tc_test")
    import tenuo.control_plane
    client = SlowRegistration(after=3)
    monkeypatch.setattr(tenuo.control_plane, "get_or_create", lambda: client)
    monkeypatch.setattr(warrant.time, "sleep", lambda s: None)
    assert cloud_client() is client and client.authorizer_id == "authorizer-1"
    assert client.reads >= 4


def test_cloud_client_returns_unregistered_client_after_timeout(monkeypatch, caplog):
    monkeypatch.setenv("TENUO_API_KEY", "tc_test")
    import tenuo.control_plane
    client = SlowRegistration(after=10**9)
    monkeypatch.setattr(tenuo.control_plane, "get_or_create", lambda: client)
    assert cloud_client(wait_secs=0.05) is client
    assert "did not register" in caplog.text
