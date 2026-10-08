"""Organization schema fix: find the Next.js layout, check the model's facts against the homepage, insert JSON-LD.

Pure functions: no model and no network.
"""
import json
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

LAYOUT_CANDIDATES = ("app/layout.tsx", "app/layout.jsx", "src/app/layout.tsx", "src/app/layout.jsx")
PROFILE_DOMAINS = ("github.com", "x.com", "twitter.com", "linkedin.com", "youtube.com", "crunchbase.com",
                   "producthunt.com")
HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
HEAD_OPEN = re.compile(r"^[ \t]*<head([ \t]([^>\n]*[^/>\n])?)?>[ \t]*$", re.M)
ANCHOR = re.compile(r"^export (?:const metadata\b|default function)", re.M)
LINK_ATTR = re.compile(r"""(?:href|src)=["']([^"']+)["']""")
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
SCRIPT = ('<script type="application/ld+json" '
          'dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd).replace(/</g, "\\\\u003c") }} />')
MAX_LINKS = 300


@dataclass
class Facts:
    logo: str | None
    same_as: list[str]
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (value, why)


def layout_paths(code_root: str) -> list[str]:
    return [posixpath.join(code_root, p) for p in LAYOUT_CANDIDATES]


def skip_reason(source: str) -> str | None:
    """Why this layout cannot take the fix, or None."""
    if "application/ld+json" in source:
        return "layout already has application/ld+json"
    if "organizationJsonLd" in source:
        return "unsupported layout (organizationJsonLd is already defined)"
    if "\r\n" in source:
        return "unsupported layout (CRLF line endings)"
    anchor = ANCHOR.search(source)
    if not anchor:
        return "unsupported layout (no exported metadata or default function)"
    if not HEAD_OPEN.search(source, anchor.end()):
        return "unsupported layout (no <head> element)"
    return None


def page_links(html: str) -> list[str]:
    """Every href and src on the page, deduplicated in order. This, not the raw HTML, is what the model sees."""
    html_no_comments = HTML_COMMENT.sub("", html)
    seen = []
    for value in LINK_ATTR.findall(html_no_comments):
        if value not in seen and not any(c.isspace() for c in value) and '<' not in value and '>' not in value:
            seen.append(value)
    return seen[:MAX_LINKS]


def page_title(html: str) -> str:
    m = TITLE.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _normalize(site: str, value: str) -> str | None:
    """Normalize a URL: returns None unless it is http(s) with a hostname and no port, else the canonical form."""
    try:
        p = urlsplit(urljoin(site, value))
        if p.scheme not in ("http", "https") or not p.hostname or p.port is not None:
            return None
    except ValueError:  # malformed, e.g. "http://[x" or a non-numeric port
        return None
    return urlunsplit((p.scheme, p.hostname.lower(), p.path.rstrip("/") or "/", p.query, ""))


def check_facts(domain: str, html: str, logo: str | None, same_as: list[dict]) -> Facts:
    """Keep only facts the homepage backs up: a same-site logo, and profile links on known sites that the page links to."""
    site = f"https://{domain}/"
    facts = Facts(logo=None, same_as=[])
    linked = {n for v in page_links(html) if (n := _normalize(site, v))}
    if logo:
        n = _normalize(site, logo)
        if n is None or not n.startswith(f"https://{domain}/"):
            facts.rejected.append((logo, f"logo is not on {domain}"))
        elif n not in linked:
            facts.rejected.append((logo, "not linked from the homepage"))
        else:
            facts.logo = n
    for item in same_as:
        url = item["url"]
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").removeprefix("www.")
        except ValueError:
            facts.rejected.append((url, "not a URL"))
            continue
        n = _normalize(site, url)
        if parts.scheme != "https":
            why = "not https"
        elif host not in PROFILE_DOMAINS or n is None:
            why = "not a known profile site"
        elif host == "linkedin.com" and urlsplit(n).path.startswith("/in/"):
            why = "personal profile"
        elif n not in linked:
            why = "not linked from the homepage"
        else:
            if n not in facts.same_as:
                facts.same_as.append(n)
            continue
        facts.rejected.append((url, why))
    return facts


def org_jsonld(name: str, domain: str, facts: Facts) -> dict:
    data = {"@context": "https://schema.org", "@type": "Organization", "name": name, "url": f"https://{domain}/"}
    if facts.logo:
        data["logo"] = facts.logo
    if facts.same_as:
        data["sameAs"] = facts.same_as
    return data


def _json_for_tsx(data: dict) -> str:
    """JSON-encode, escaping < to avoid closing TSX tags."""
    return json.dumps(data, indent=2).replace("<", "\\u003c")


def insert(source: str, jsonld: dict) -> str:
    """Add the JSON-LD constant above the metadata (or default) export, and its script tag first in <head>."""
    anchor = ANCHOR.search(source)
    const = "const organizationJsonLd = " + _json_for_tsx(jsonld) + ";\n\n"
    source = source[:anchor.start()] + const + source[anchor.start():]
    anchor = ANCHOR.search(source)
    head = HEAD_OPEN.search(source, anchor.end())
    line_start = source.rfind("\n", 0, head.start()) + 1
    line_end = source.find("\n", head.start())
    line_text = source[line_start:line_end]
    head_line_indent = line_text[:len(line_text) - len(line_text.lstrip())]
    script_indent = head_line_indent + "  "
    return source[:line_end] + "\n" + script_indent + SCRIPT + source[line_end:]


MAX_LISTED = 20  # rejected candidates listed in the PR body


def _safe(value: str) -> str:
    """Model-controlled text for a PR body or terminal: no control characters, at most 200 chars, in backticks."""
    clean = "".join(c for c in str(value) if ord(c) >= 32 and ord(c) != 127 and c != "`")
    if len(clean) > 200:
        clean = clean[:200] + "..."
    return f"`{clean}`"


def org_schema_notes(facts: Facts) -> str:
    lines = ["Facts in the Organization schema (name and url come from the brand file):"]
    lines.append(f"- logo: {_safe(facts.logo) if facts.logo else 'none'}")
    lines += [f"- sameAs: {_safe(u)}" for u in facts.same_as] or ["- sameAs: none"]
    if facts.rejected:
        lines.append("\nLeft out, with the reason:")
        lines += [f"- {_safe(value)}: {why}" for value, why in facts.rejected[:MAX_LISTED]]
        if len(facts.rejected) > MAX_LISTED:
            lines.append(f"- and {len(facts.rejected) - MAX_LISTED} more")
    if not facts.same_as:
        lines.append("\norganization_schema gate will still fail: no company profile linked from the homepage")
    return "\n".join(lines)
