import json

from typer.testing import CliRunner

from fixagent.agent.demo import SCRIPT, DemoLLM
from fixagent.agent.run import DEMO_TIMINGS, audit_rows, run_input
from fixagent.cli import app, watch_line

from agent_fakes import BRAND


def test_demo_script_restarts_for_every_run():
    llm = DemoLLM(pause=0)
    first = llm.next_step("s", [{"role": "user", "text": "go"}], [])
    again = llm.next_step("s", [{"role": "user", "text": "go"}], [])
    assert first is SCRIPT[0] and again is SCRIPT[0]
    later = [{"role": "user", "text": "go"}] + [{"role": "assistant"}] * len(SCRIPT)
    assert llm.next_step("s", later, []).calls == []


def test_demo_timings_only_with_demo(tmp_path):
    (tmp_path / "acme.yaml").write_text(BRAND)
    assert run_input("acme", tmp_path).poll_every == 1800
    assert run_input("acme", tmp_path, demo=True).poll_every == DEMO_TIMINGS["poll_every"]


def test_audit_rows_follow_one_run(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps({"job": "fixrun-a", "decision": "allow"}) + "\n" + json.dumps({"job": "fixrun-b"}) + "\n")
    rows, offset = audit_rows(log, "fixrun-a", 0)
    assert rows == [{"job": "fixrun-a", "decision": "allow"}]
    with log.open("a") as f:
        f.write(json.dumps({"job": "fixrun-a", "decision": "deny"}) + "\n")
    assert audit_rows(log, "fixrun-a", offset)[0] == [{"job": "fixrun-a", "decision": "deny"}]


def test_watch_lines_say_allowed_or_denied():
    allow = watch_line("allow", {"tool": "read_file", "args": {"repo": "a/b", "ref": "main", "path": "robots.txt"}, "reason": ""})
    deny = watch_line("deny", {"tool": "read_file", "args": {"path": ".env"},
                               "reason": "Constraint 'path' not satisfied: value does not match"})
    assert "ALLOWED" in allow and "path=robots.txt" in allow and "repo=" not in allow
    assert "DENIED" in deny and "path=.env" in deny and "Constraint 'path' not satisfied" in deny
    assert "model turn" in watch_line("allow", {"tool": "llm_turn", "args": {"conversation": "<9 bytes>"}})


def test_demo_flag_is_llm_only():
    r = CliRunner().invoke(app, ["worker", "executor", "--demo"])
    assert r.exit_code != 0 and "llm worker only" in r.output
