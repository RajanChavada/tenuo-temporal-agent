"""Propose fixes for a brand's site repo."""
import posixpath
from dataclasses import dataclass

from fixagent.execute.fixes import allow_search_bots, llms_txt, robots_problems

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
