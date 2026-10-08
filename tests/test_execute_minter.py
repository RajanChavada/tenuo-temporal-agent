import pytest
from tenuo import SigningKey

from fixagent.execute.minter import policy_problem

REPO, BASE, PATHS = "acme/site", "main", ["public/robots.txt", "public/llms.txt"]
HOLDER = bytes(SigningKey.generate().public_key.to_bytes()).hex()


def problem(repo=REPO, base=BASE, paths=PATHS, holder=HOLDER, fix="site-files"):
    return policy_problem(repo, base, paths, holder, lambda r: r == "acme/site", fix)


def test_allows_catalog_paths_on_an_onboarded_repo():
    assert problem() is None


@pytest.mark.parametrize("repo,base,paths,why", [
    ("other/repo", BASE, PATHS, "other/repo is not onboarded, run: fixagent keys github other/repo"),
    (REPO, "fixagent/main", PATHS, "base 'fixagent/main' is not allowed"),
    (REPO, "main/../x", PATHS, "base 'main/../x' is not allowed"),
    (REPO, BASE, ["public/../robots.txt"], "path 'public/../robots.txt' is not allowed"),
    (REPO, BASE, ["/etc/robots.txt"], "path '/etc/robots.txt' is not allowed"),
    (REPO, BASE, [".github/workflows/ci.yml"], "path '.github/workflows/ci.yml' is not allowed"),
    (REPO, BASE, [], "no paths requested"),
])
def test_refuses_requests_outside_policy(repo, base, paths, why):
    assert problem(repo, base, paths).startswith(why)


def test_refuses_a_malformed_holder():
    assert problem(holder="not-a-key") == "holder must be a 64-character hex public key"


@pytest.mark.parametrize("paths", [["app/layout.tsx"], ["frontend/src/app/layout.jsx", "frontend/app/layout.tsx"]])
def test_org_schema_may_touch_layout_files(paths):
    assert problem(paths=paths, fix="org-schema") is None


@pytest.mark.parametrize("fix,paths,why", [
    ("org-schema", ["public/robots.txt"], "path 'public/robots.txt' is not allowed for org-schema"),
    ("org-schema", ["frontend/app/page.tsx"], "path 'frontend/app/page.tsx' is not allowed for org-schema"),
    ("org-schema", ["frontend/../app/layout.tsx"], "path 'frontend/../app/layout.tsx' is not allowed for org-schema"),
    ("site-files", ["app/layout.tsx"], "path 'app/layout.tsx' is not allowed for site-files"),
    ("rewrite-everything", ["app/layout.tsx"], "fix 'rewrite-everything' is not a known fix type"),
])
def test_each_fix_type_has_its_own_paths(fix, paths, why):
    assert problem(paths=paths, fix=fix) == why
