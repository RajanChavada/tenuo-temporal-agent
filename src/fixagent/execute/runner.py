"""Propose fixes for a brand's site repo, and apply them as one pull request."""
import posixpath
from dataclasses import dataclass
from datetime import datetime, timezone

from fixagent.execute.fixes import allow_search_bots, llms_txt, robots_problems
from fixagent.execute.warrant import BRANCH_PREFIX

FILES = ("robots.txt", "llms.txt")


@dataclass
class Change:
    path: str
    before: str | None
    after: str
    reason: str


def site_paths(site: dict) -> dict[str, str]:
    return {name: posixpath.join(site.get("root", ""), name) for name in FILES}


def propose(brand, site: dict, read) -> tuple[list[Change], list[str]]:
    """read(path) returns the file text or None. Returns the changes and a note for each fix skipped."""
    paths = site_paths(site)
    changes, skipped = [], []
    robots = read(paths["robots.txt"])
    if robots is None:
        skipped.append("robots.txt: none found, crawlers allowed")
    elif (after := allow_search_bots(robots)) is not None:
        problems = robots_problems(robots, after)
        if problems:
            skipped.append("robots.txt: " + "; ".join(problems))
        else:
            changes.append(Change(paths["robots.txt"], robots, after, "Let AI search bots read the site"))
    if read(paths["llms.txt"]) is None:
        changes.append(Change(paths["llms.txt"], None, llms_txt(brand), "Add llms.txt with facts from the brand file"))
    else:
        skipped.append("llms.txt: already exists, not overwritten")
    return changes, skipped


def open_fix_pr(changes: list[Change], gh, site: dict, notes: str = "") -> str:
    repo, base = site["repo"], site.get("base", "main")
    branch = BRANCH_PREFIX + "aeo-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    gh.create_branch(repo, base, branch)
    for c in changes:
        gh.write_file(repo, branch, c.path, c.after, c.reason)
    body = "\n".join(f"- `{c.path}`: {c.reason}" for c in changes) + (f"\n\n{notes}" if notes else "")
    return gh.open_pr(repo, base, branch, "AI visibility fixes", body)
