from pathlib import Path

from fixagent.config import load_brand
from fixagent.execute.runner import propose, site_paths

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
