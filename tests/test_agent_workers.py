import json
from types import SimpleNamespace as NS

from fixagent.agent import run
from fixagent.agent.workers import RedactingCloud, redact


class FakeCloud:
    def __init__(self):
        self.seen = []

    def emit_for_enforcement(self, result, **kwargs):
        self.seen.append((result.tool, result.arguments))


def test_cloud_receipts_carry_sizes_not_free_text():
    cloud = FakeCloud()
    decision = NS(tool="write_file", allowed=True, arguments={"path": "llms.txt", "text": "secret page", "size": 11})
    RedactingCloud(cloud).emit_for_enforcement(decision)
    assert cloud.seen == [("write_file", {"path": "llms.txt", "text": "<11 bytes>", "size": 11})]


def test_without_a_client_nothing_is_sent():
    assert not RedactingCloud()


def test_audit_log_rows_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "AUDIT_LOG", tmp_path / "audit.jsonl")
    run.audit_event(NS(workflow_id="fixrun-acme", decision="DENY", tool="llm_turn",
                       arguments={"conversation": "x" * 50}, denial_reason="expired"))
    row = json.loads((tmp_path / "audit.jsonl").read_text())
    assert row["job"] == "fixrun-acme" and row["decision"] == "deny" and row["args"] == {"conversation": "<50 bytes>"}


def test_redact_keeps_scope_arguments():
    assert redact({"repo": "a/b", "proposals": "[]", "body": "hi"}) == {"repo": "a/b", "proposals": "<2 bytes>",
                                                                       "body": "<2 bytes>"}
