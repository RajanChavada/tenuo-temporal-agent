"""Only the executor worker role may hold a GitHub token or talk to the GitHub API.

`agent/run.py` loads secrets per Temporal worker role: the executor role gets GitHub tokens, the minter role the
issuer key. Each role runs in its own process.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "fixagent"


def _sources():
    return {p.relative_to(SRC).as_posix(): p.read_text() for p in SRC.rglob("*.py")}


def test_no_module_reads_a_github_token_from_the_environment():
    assert [name for name, text in _sources().items() if "GITHUB_TOKEN" in text] == []


def test_only_the_github_module_names_the_github_api():
    assert [name for name, text in _sources().items() if "api.github.com" in text] == ["execute/github.py"]


def test_only_the_executor_imports_the_github_client():
    users = [name for name, text in _sources().items() if "fixagent.execute.github" in text]
    assert sorted(users) == ["agent/run.py"]


def test_only_the_executor_reads_a_github_token_from_the_keychain():
    users = [n for n, t in _sources().items() if re.search(r"(?<!set_)github_token\(", t) and n != "execute/keys.py"]
    assert sorted(users) == ["agent/run.py"]


def test_only_the_minter_reads_the_issuer_signing_key():
    users = [n for n, t in _sources().items() if re.search(r"\bissuer_key\b", t) and n != "execute/keys.py"]
    assert sorted(users) == ["agent/run.py"]


def test_agent_secrets_load_only_for_their_worker_role():
    run = _sources()["agent/run.py"]
    body = run[run.index("def configure_role"):run.index("async def run_worker")]
    roles = re.split(r"\n    (?:el)?if role == ", body)[1:]
    by_role = {r.split(":")[0].strip('"'): r for r in roles}
    assert "holder_key()" in by_role["workflow"] and "github_token" not in by_role["workflow"]
    assert "issuer_key()" in by_role["minter"] and "github_token" not in by_role["minter"]
    assert "github_token" in by_role["executor"] and "issuer_key" not in by_role["executor"]
    assert "github_token" not in by_role["llm"] and "issuer_key" not in by_role["llm"]
