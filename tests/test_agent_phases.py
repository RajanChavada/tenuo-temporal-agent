import time

import pytest
from tenuo import Authorizer, SigningKey
from tenuo.exceptions import TenuoError

from fixagent.agent.phases import _path_problem, phase_problem, phase_warrant

ISSUER, HOLDER = SigningKey.generate(), SigningKey.generate()
CTX = {"slug": "acme", "repo": "acme/site", "base": "main", "domain": "acme.com", "root": "public", "code_root": "web"}


def allowed(phase, tool, args, **ctx):
    w = phase_warrant(ISSUER, HOLDER.public_key, phase, dict(CTX, **ctx))
    sig = w.sign(HOLDER, tool, args, int(time.time()))
    try:
        Authorizer(trusted_roots=[ISSUER.public_key]).check_chain([w], tool, args, signature=sig)
        return True
    except TenuoError:
        return False


R = {"repo": "acme/site", "ref": "main"}


@pytest.mark.parametrize("phase,tool,args,ctx,ok", [
    ("explore", "run_gates", {"domain": "acme.com"}, {}, True),
    ("explore", "run_gates", {"domain": "evil.com"}, {}, False),
    ("explore", "read_file", dict(R, path="web/app/layout.tsx"), {}, True),
    ("explore", "read_file", dict(R, path="public/robots.txt"), {}, True),
    ("explore", "read_file", dict(R, path="web/.env.local"), {}, False),
    ("explore", "read_file", dict(R, path="web/../.git/config"), {}, False),
    ("explore", "read_file", dict(R, path="api/server.py"), {}, False),
    ("explore", "read_file", dict(R, ref="fixagent/x", path="web/a"), {}, False),
    ("explore", "list_files", dict(R, dir="web/app"), {}, True),
    ("explore", "read_file", dict(R, path="web/app/[slug]/(group)/page.tsx"), {}, True),
    ("explore", "read_file", dict(R, path="website/x"), {}, False),
    ("explore", "read_file", dict(R, path="web/secrets/db.yml"), {}, False),
    ("explore", "read_file", dict(R, path="web/.ENV"), {}, False),
    ("explore", "read_file", dict(R, path="web/SECRET.txt"), {}, False),
    ("explore", "read_file", dict(R, path="web/.env.d/x"), {}, False),
    ("explore", "read_file", dict(R, path="web/id_rsa"), {}, False),
    ("explore", "read_file", dict(R, path="web/.npmrc"), {}, False),
    ("explore", "read_file", dict(R, path="web/%2e%2e/api/x"), {}, False),
    ("explore", "llm_turn", {"conversation": "{}"}, {}, True),
    ("explore", "write_file", {"repo": "acme/site"}, {}, False),
    ("build", "build_changes", {"slug": "acme", "fixes": ["org-schema"], "proposals": "[]"}, {"fixes": ["org-schema"]}, True),
    ("build", "build_changes", {"slug": "other", "fixes": ["org-schema"], "proposals": "[]"}, {"fixes": ["org-schema"]}, False),
    ("build", "build_changes", {"slug": "acme", "fixes": ["shell"], "proposals": "[]"}, {"fixes": ["org-schema"]}, False),
    ("build", "build_changes", {"slug": "acme", "fixes": ["site-files"], "proposals": "[]"}, {"fixes": ["org-schema"]}, False),
    ("poll", "get_pr_state", {"repo": "acme/site", "number": 7}, {"number": 7}, True),
    ("poll", "get_pr_state", {"repo": "acme/site", "number": 8}, {"number": 7}, False),
    ("verify", "run_gates", {"domain": "acme.com"}, {}, True),
    ("verify", "read_file", dict(R, path="web/a"), {}, False),
])
def test_phase_matrix(phase, tool, args, ctx, ok):
    assert allowed(phase, tool, args, **ctx) is ok


def test_whole_repo_readable_when_code_root_is_repo_root():
    assert allowed("explore", "read_file", dict(R, path="package.json"), code_root="")
    assert not allowed("explore", "read_file", dict(R, path="config/secrets.yml"), code_root="")


def test_write_phase_only_proposed_paths():
    w = {"repo": "acme/site", "branch": "fixagent/agent-1", "path": "public/llms.txt", "size": 1, "text": "x", "message": "m"}
    assert allowed("write", "write_file", w, paths=["public/llms.txt"])
    assert not allowed("write", "write_file", dict(w, path=".github/workflows/x.yml"), paths=["public/llms.txt"])


@pytest.mark.parametrize("phase,ctx,onboarded,problem", [
    ("explore", {}, False, "not onboarded"),
    ("explore", {"base": "fixagent/x"}, True, "base"),
    ("build", {"fixes": ["shell"]}, True, "catalog"),
    ("write", {"paths": [".github/workflows/x.yml"]}, True, "catalog fix paths"),
    ("poll", {"number": 0}, True, "pull request number"),
    ("delete", {}, True, "not known"),
])
def test_minter_refuses(phase, ctx, onboarded, problem):
    assert problem in phase_problem(phase, dict(CTX, **ctx), lambda repo: onboarded)


def test_minter_allows_catalog_write():
    assert phase_problem("write", dict(CTX, paths=["public/llms.txt"]), lambda repo: True) is None


def test_explore_without_a_root_is_refused_not_widened():
    ctx = {k: v for k, v in CTX.items() if k != "root"}
    assert "root and code_root" in phase_problem("explore", ctx, lambda repo: True)


@pytest.mark.parametrize("fix,path,bad", [
    ("site-files", "public/robots.txt", False), ("site-files", "public/../robots.txt", True),
    ("site-files", "/etc/robots.txt", True), ("site-files", ".github/workflows/ci.yml", True),
    ("site-files", "app/layout.tsx", True), ("org-schema", "frontend/src/app/layout.jsx", False),
    ("org-schema", "public/robots.txt", True), ("org-schema", "frontend/app/page.tsx", True),
    ("org-schema", "frontend/../app/layout.tsx", True)])
def test_each_fix_type_has_its_own_paths(fix, path, bad):
    assert _path_problem(fix, path) is bad
