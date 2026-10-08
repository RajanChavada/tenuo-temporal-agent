import json

import pytest
from temporalio.exceptions import ApplicationError
from tenuo import SigningKey, Warrant

from fixagent.agent import activities as act
from fixagent.agent.llm import LLMError, ScriptedLLM, Step

from agent_fakes import BRAND, BLOCKED, HTML, LAYOUT, FakeGitHub


@pytest.fixture
def gh(tmp_path):
    (tmp_path / "acme.yaml").write_text(BRAND)
    gh = FakeGitHub({"web/app/layout.tsx": LAYOUT, "public/robots.txt": BLOCKED})
    act.configure(act.Deps(github=lambda repo: gh, fetch=lambda url: (200, HTML), brands_root=tmp_path,
                           llm=ScriptedLLM([Step(text="hi", tokens=3)]), issuer=SigningKey.generate(),
                           holder_public_key=SigningKey.generate().public_key, is_onboarded=lambda repo: True))
    yield gh
    act.configure(act.Deps())


def test_build_changes_code_writes_both_fixes(gh):
    proposals = json.dumps([{"type": "org-schema", "facts": {"logo": None, "same_as": [
        {"url": "https://github.com/acme-dev", "reason": "company"}, {"url": "https://x.com/evil", "reason": "?"}]}},
                            {"type": "site-files", "facts": {}}])
    out = act.build_changes("acme", ["org-schema", "site-files"], proposals)
    paths = [c["path"] for c in out["changes"]]
    assert "web/app/layout.tsx" in paths and "public/robots.txt" in paths
    layout = next(c["after"] for c in out["changes"] if c["path"] == "web/app/layout.tsx")
    assert "https://github.com/acme-dev" in layout and "x.com/evil" not in layout
    assert "x.com/evil" in out["notes"]


@pytest.mark.parametrize("proposals", [
    "not json", "{}", "5", "[1]", '[{"facts": {}}]',
    '[{"type": "org-schema", "facts": "x"}]',
    '[{"type": "org-schema", "facts": {"logo": 5, "same_as": [{"reason": "no url"}, "x", {"url": 3}]}}]',
    '[{"type": "org-schema", "facts": {"same_as": "https://github.com/acme-dev"}}]',
])
def test_build_changes_drops_malformed_facts(gh, proposals):
    out = act.build_changes("acme", ["org-schema"], proposals)
    layout = out["changes"][0]["after"]
    assert '"sameAs"' not in layout and "acme.com" in layout


def test_homepage_title_is_capped(gh):
    act.DEPS.fetch = lambda url: (200, "<html><head><title>" + "x" * 5000 + "</title></head></html>")
    assert len(act.homepage("acme.com")["title"]) == act.MAX_TITLE


def test_build_changes_skips_layout_with_schema(gh):
    gh.files["web/app/layout.tsx"] = LAYOUT.replace("<head>", '<head><script type="application/ld+json" />')
    out = act.build_changes("acme", ["org-schema"], "[]")
    assert out["changes"] == [] and "org-schema" in out["skipped"][0]


def test_read_file_truncates(gh):
    gh.files["web/big.txt"] = "x" * (act.MAX_TEXT + 5)
    out = act.read_file("acme/site", "main", "web/big.txt")
    assert out["found"] and out["truncated"] and len(out["text"]) == act.MAX_TEXT
    assert act.read_file("acme/site", "main", "web/none") == {"found": False, "text": "", "truncated": False}


def test_homepage_returns_links_not_html(gh):
    out = act.homepage("acme.com")
    assert out["title"] == "Acme" and "https://github.com/acme-dev" in out["links"] and "<a" not in json.dumps(out)


@pytest.mark.parametrize("branch", ["main", "feature/x", "fixagent/../main"])
def test_write_refuses_non_fixagent_branch(gh, branch):
    with pytest.raises(ApplicationError, match="refused"):
        act.write_file("acme/site", branch, "public/llms.txt", 1, "x", "m")
    assert gh.writes == []


def test_write_refuses_size_mismatch(gh):
    with pytest.raises(ApplicationError, match="size"):
        act.write_file("acme/site", "fixagent/agent-1", "public/llms.txt", 99, "x", "m")


def test_open_pr_returns_number(gh):
    assert act.open_pr("acme/site", "main", "fixagent/agent-1", "t", "b") == {
        "url": "https://github.com/acme/site/pull/12", "number": 12}


def test_mint_phase_signs_or_refuses(gh):
    ctx = {"slug": "acme", "repo": "acme/site", "base": "main", "domain": "acme.com", "number": 12}
    assert Warrant.from_base64(act.mint_phase("poll", ctx))
    with pytest.raises(ApplicationError, match="refused"):
        act.mint_phase("poll", dict(ctx, number=0))


def test_llm_turn_round_trips_json(gh):
    out = act.llm_turn(json.dumps({"system": "s", "messages": [], "tools": []}))
    assert out == {"text": "hi", "calls": [], "raw": None, "tokens": 3}


def test_llm_turn_error_is_typed(gh):
    class Broken:
        def next_step(self, *a):
            raise LLMError("no credit")
    act.DEPS.llm = Broken()
    with pytest.raises(ApplicationError) as e:
        act.llm_turn(json.dumps({"system": "s", "messages": [], "tools": []}))
    assert e.value.type == "LLMError"
