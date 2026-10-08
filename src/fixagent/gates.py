import json
import re
from urllib.robotparser import RobotFileParser

import httpx

from fixagent.html import MAX_HTML, UA, html_to_text, iter_blocks

AI_BOTS = ["OAI-SearchBot", "ChatGPT-User", "GPTBot"]
LD_JSON_TYPE = re.compile(r'(?i)\btype=["\']application/ld\+json["\']')


def _get(url: str) -> tuple[int | None, str]:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": UA})
    except (httpx.HTTPError, httpx.InvalidURL):
        return None, ""
    return r.status_code, r.text


def _objects(node):
    if isinstance(node, list):
        for x in node:
            yield from _objects(x)
    elif isinstance(node, dict):
        yield node
        yield from _objects(node.get("@graph", []))


def _has_org_schema(html: str) -> bool:
    for tag, block, _ in iter_blocks(html[:MAX_HTML]):
        if tag.group(1).lower() != "script" or not LD_JSON_TYPE.search(tag.group(0)):
            continue
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for obj in _objects(data):
            types = obj.get("@type", [])
            if isinstance(types, str):
                types = [types]
            elif not isinstance(types, list):
                types = []
            if "Organization" in types and obj.get("sameAs"):
                return True
    return False


def check_gates(domain: str, get=_get) -> list[dict]:
    results = []
    code, robots = get(f"https://{domain}/robots.txt")
    parser = None
    if code is None:
        bot_status, bot_detail = "unknown", "robots.txt unreachable"
    elif code == 404:
        bot_status, bot_detail = "pass", "no robots.txt"
    elif code in (401, 403, 429) or code >= 500:
        bot_status, bot_detail = "unknown", f"robots.txt returned {code}"
    elif robots.lstrip().startswith("<"):
        bot_status, bot_detail = "unknown", "robots.txt is HTML, not robots rules"
    else:
        parser = RobotFileParser()
        parser.parse(robots.splitlines())
        bot_status = bot_detail = None
    for bot in AI_BOTS:
        if parser is not None:
            ok = parser.can_fetch(bot, f"https://{domain}/")
            status, detail = ("pass", "allowed") if ok else ("fail", "disallowed for /")
        else:
            status, detail = bot_status, bot_detail
        results.append({"gate": f"robots:{bot}", "status": status, "detail": detail})
    code, home = get(f"https://{domain}/")
    if code is None or code >= 400:
        results.append({"gate": "renders_without_js", "status": "unknown", "detail": f"homepage returned {code}"})
        results.append({"gate": "organization_schema", "status": "unknown", "detail": f"homepage returned {code}"})
    else:
        n = len(html_to_text(home))
        results.append({"gate": "renders_without_js", "status": "pass" if n >= 500 else "fail",
                        "detail": f"{n} characters of text without JavaScript"})
        org = _has_org_schema(home)
        results.append({"gate": "organization_schema", "status": "pass" if org else "fail",
                        "detail": "Organization with sameAs found" if org else "no Organization schema with sameAs"})
    results.append({"gate": "indexed", "status": "unknown", "detail": f"manual: search Google and Bing for site:{domain}"})
    return results


def crawler_gates_failed(results: list[dict]) -> bool:
    return any(r["status"] == "fail" and (r["gate"].startswith("robots:") or r["gate"] == "renders_without_js")
               for r in results)
