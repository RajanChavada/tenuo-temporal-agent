"""HTML helpers for the site checks: strip <script>/<style> blocks and tags to get the text a crawler sees."""
import re

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

MAX_HTML = 300_000
_BLOCK_OPEN = re.compile(r"(?i)<(script|style)\b[^<>]*>")


def iter_blocks(html: str):
    """(open tag match, body, end offset) for each closed <script> or <style> block. Content runs to the
    first matching close tag, as in HTML parsing, so a <script> opened inside an unclosed <script> is
    text, not a new block. Linear time:
    once a tag name has no closing tag left, later opens of that name are skipped instead of each
    rescanning to the end of the page (which a lazy regex does, quadratically)."""
    lower = html.lower()
    unclosed = set()
    pos = 0
    while (m := _BLOCK_OPEN.search(html, pos)):
        name = m.group(1).lower()
        close = -1 if name in unclosed else lower.find(f"</{name}>", m.end())
        if close == -1:
            unclosed.add(name)
            pos = m.end()
            continue
        end = close + len(name) + 3
        yield m, html[m.end():close], end
        pos = end


def html_to_text(html: str) -> str:
    html = html[:MAX_HTML]
    parts, pos = [], 0
    for m, _, end in iter_blocks(html):
        parts.append(html[pos:m.start()])
        pos = end
    parts.append(html[pos:])
    # [^<>] (not [^>]) so a run of "<" without ">" can't make each match scan to the end.
    return re.sub(r"\s+", " ", re.sub(r"<[^<>]+>", " ", " ".join(parts))).strip()
