"""Deterministic site fixes. No model in the write path: output depends only on the current file and the brand file."""
from urllib.robotparser import RobotFileParser

# Bots that fetch pages to answer a user's question. GPTBot (training) is left as the owner set it.
SEARCH_BOTS = ["OAI-SearchBot", "ChatGPT-User", "PerplexityBot"]
OTHER_AGENTS = ["*", "Googlebot", "Bingbot", "GPTBot"]


def _parser(text: str) -> RobotFileParser:
    p = RobotFileParser()
    p.parse(text.splitlines())
    return p


def _fields(line: str) -> tuple[str, str]:
    key, _, val = line.split("#", 1)[0].partition(":")
    return key.strip().lower(), val.strip()


def _rules(text: str) -> set[tuple[str, str]]:
    return {f for f in map(_fields, text.splitlines()) if f[0] in ("allow", "disallow")}


def _paths(text: str) -> list[str]:
    return sorted({"/"} | {v.replace("*", "x").rstrip("$") or "/" for _, v in _rules(text) if v.startswith("/")})


def allow_search_bots(robots: str) -> str | None:
    """Drop blocked search bots from the groups that name them, so they follow the same rules as every other crawler."""
    p = _parser(robots)
    blocked = {b.lower() for b in SEARCH_BOTS if not p.can_fetch(b, "/")}
    if not blocked:
        return None
    out, kept, in_agents = [], True, False
    for line in robots.splitlines():
        key, val = _fields(line)
        if key == "user-agent":
            if not in_agents:
                kept = False
            in_agents = True
            if val.lower() in blocked:
                continue
            kept = True
        elif key and key != "sitemap":
            if key in ("allow", "disallow"):
                in_agents = False
            if not kept:
                continue
        out.append(line)
    return "\n".join(out).lstrip("\n") + "\n"


def _agents(text: str) -> list[str]:
    """Every User-agent value in the file, minus the search bots (those are expected to change)."""
    names = {v for k, v in map(_fields, text.splitlines()) if k == "user-agent" and v}
    return sorted(n for n in names if n.lower() not in {b.lower() for b in SEARCH_BOTS})


def robots_problems(before: str, after: str) -> list[str]:
    """Reasons the new robots.txt is unsafe to ship. Empty means safe."""
    old, new = _parser(before), _parser(after)
    problems = [f"{b} is still blocked from /" for b in SEARCH_BOTS if not new.can_fetch(b, "/")]
    watched = OTHER_AGENTS + [a for a in _agents(before) if a.lower() not in {o.lower() for o in OTHER_AGENTS}]
    for path in _paths(before):
        problems += [f"{a} changed on {path}" for a in watched if old.can_fetch(a, path) != new.can_fetch(a, path)]
        problems += [f"{b} differs from * on {path}" for b in SEARCH_BOTS
                     if new.can_fetch(b, path) != new.can_fetch("*", path)]
    if not _rules(after) <= _rules(before):
        problems.append("new Allow or Disallow rules were added")
    return problems


def llms_txt(brand) -> str:
    """llms.txt built only from facts in the brand file."""
    lines = [f"# {brand.name}", "", f"> {brand.one_liner}", "",
             f"- Website: https://{brand.domain}/", f"- Category: {brand.category}"]
    if brand.claims:
        lines += ["", "## Facts", "", *[f"- {c}" for c in brand.claims]]
    return "\n".join(lines) + "\n"
