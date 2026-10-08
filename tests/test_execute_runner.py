from pathlib import Path

from fixagent.config import load_brand
from fixagent.execute.runner import open_fix_pr, propose, site_paths

BLOCKED = "User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n"


def brand():
    return load_brand("example", Path("brands"))


def test_site_paths_respect_root():
    assert site_paths({}) == {"robots.txt": "robots.txt", "llms.txt": "llms.txt"}
    assert site_paths({"root": "public"}) == {"robots.txt": "public/robots.txt", "llms.txt": "public/llms.txt"}


def test_propose_fixes_robots_and_adds_llms():
    changes, skipped = propose(brand(), {}, {"robots.txt": BLOCKED}.get)
    assert [c.path for c in changes] == ["robots.txt", "llms.txt"]
    assert changes[0].after == "User-agent: *\nDisallow: /admin\n"
    assert changes[1].before is None and changes[1].after.startswith("# Acme\n")
    assert skipped == []


def test_propose_skips_unsafe_robots_and_existing_llms():
    files = {"robots.txt": "User-agent: *\nDisallow: /\n", "llms.txt": "# Acme\n"}
    changes, skipped = propose(brand(), {}, files.get)
    assert changes == []
    assert skipped[0].startswith("robots.txt: OAI-SearchBot is still blocked from /")
    assert skipped[1] == "llms.txt: already exists, not overwritten"


def test_propose_notes_missing_robots():
    changes, skipped = propose(brand(), {"root": "public"}, {}.get)
    assert [c.path for c in changes] == ["public/llms.txt"]
    assert skipped == ["robots.txt: none found, crawlers allowed"]


class FakeGitHub:
    def __init__(self):
        self.calls = []

    def create_branch(self, repo, base, branch):
        self.calls.append(("branch", repo, base, branch))

    def write_file(self, repo, branch, path, text, message):
        self.calls.append(("write", path, branch))

    def open_pr(self, repo, base, head, title, body):
        self.calls.append(("pr", base, head, body))
        return "https://github.com/acme/site/pull/1"


def test_open_fix_pr_writes_every_change_on_one_fixagent_branch():
    changes, _ = propose(brand(), {}, {"robots.txt": BLOCKED}.get)
    gh = FakeGitHub()
    url = open_fix_pr(changes, gh, {"repo": "acme/site"})
    branch = gh.calls[0][3]
    assert gh.calls[0][:3] == ("branch", "acme/site", "main") and branch.startswith("fixagent/aeo-")
    assert [c[0] for c in gh.calls] == ["branch", "write", "write", "pr"]
    assert gh.calls[1][2] == gh.calls[2][2] == gh.calls[3][2] == branch
    assert "- `robots.txt`: Let AI search bots read the site" in gh.calls[3][3]
    assert url == "https://github.com/acme/site/pull/1"
