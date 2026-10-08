import json
import re

from fixagent.execute.fixes_org import (Facts, check_facts, insert, layout_paths, org_jsonld, org_schema_notes, page_links,
                                      page_title, skip_reason, SCRIPT)

LAYOUT = """import type { Metadata } from "next";
import "./globals.css";

const themeInitScript = `(function() {})();`;

export const metadata: Metadata = {
  title: "Acme",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
"""
HTML = """<html><head><title> Acme -  LLM tracing </title><link rel="icon" href="/favicon.svg"></head><body>
<a href="https://github.com/acme-dev">GitHub</a> <a href="https://x.com/janedoe">Jane</a>
<a href="https://www.linkedin.com/company/acme">LinkedIn</a> <img src="/logo.png"> <a href="/pricing">Pricing</a>
<a href="https://github.com/acme-dev">GitHub again</a></body></html>"""


def test_layout_paths_cover_app_and_src_app():
    assert layout_paths("frontend") == ["frontend/app/layout.tsx", "frontend/app/layout.jsx",
                                        "frontend/src/app/layout.tsx", "frontend/src/app/layout.jsx"]
    assert layout_paths("")[0] == "app/layout.tsx"


def test_skip_reasons():
    assert skip_reason(LAYOUT) is None
    assert skip_reason(LAYOUT.replace("<head>", '<head>\n<script type="application/ld+json" />')) == \
        "layout already has application/ld+json"
    assert skip_reason(LAYOUT.replace("<head>", "").replace("</head>", "")) == "unsupported layout (no <head> element)"
    no_exports = LAYOUT.replace("export const metadata", "const metadata").replace("export default function", "function")
    assert skip_reason(no_exports) == "unsupported layout (no exported metadata or default function)"


def test_page_links_and_title():
    assert page_links(HTML) == ["/favicon.svg", "https://github.com/acme-dev", "https://x.com/janedoe",
                                "https://www.linkedin.com/company/acme", "/logo.png", "/pricing"]
    assert page_title(HTML) == "Acme - LLM tracing"


def test_check_facts_keeps_only_backed_facts():
    facts = check_facts("acme.dev", HTML, "/logo.png", [
        {"url": "https://github.com/acme-dev", "reason": "org"},
        {"url": "https://www.linkedin.com/company/acme", "reason": "org"},
        {"url": "https://github.com/acme-dev", "reason": "duplicate"},
        {"url": "https://facebook.com/acme", "reason": "unknown site"},
        {"url": "https://github.com/not-on-page", "reason": "invented"},
        {"url": "http://x.com/janedoe", "reason": "http"},
    ])
    assert facts.logo == "https://acme.dev/logo.png"
    assert facts.same_as == ["https://github.com/acme-dev", "https://www.linkedin.com/company/acme"]
    assert facts.rejected == [("https://facebook.com/acme", "not a known profile site"),
                              ("https://github.com/not-on-page", "not linked from the homepage"),
                              ("http://x.com/janedoe", "not https")]


def test_logo_not_linked_is_rejected():
    facts = check_facts("acme.dev", HTML, "/unlinked.png", [])
    assert facts.logo is None and facts.rejected == [("/unlinked.png", "not linked from the homepage")]


def test_logo_with_escaped_script_never_appears():
    facts = Facts(None, [], [])
    facts.logo = "/x.png</script><script>alert(1)//"
    data = org_jsonld("Acme", "acme.dev", facts)
    data["logo"] = facts.logo  # simulate model returning the bad value
    out = insert(LAYOUT, data)
    assert "\\u003c/script>" in out and "</script><script>" not in out


def test_prefix_bare_and_comment_urls_are_rejected():
    html_with_comment = """<html><body>
<!-- https://github.com/ -->
<a href="https://github.com/acme-dev">Real link</a>
</body></html>"""
    facts = check_facts("acme.dev", html_with_comment, None, [
        {"url": "https://github.com/acme", "reason": "prefix"},
        {"url": "https://github.com/", "reason": "bare"},
    ])
    assert facts.same_as == []
    assert len(facts.rejected) == 2
    assert facts.rejected[0] == ("https://github.com/acme", "not linked from the homepage")
    assert facts.rejected[1] == ("https://github.com/", "not linked from the homepage")


def test_insert_ignores_comment_before_anchor():
    layout_with_comment = """// <head> comment
const s = "<head>";
export const metadata = { title: "Acme" };

export default function RootLayout() {
  return (
    <html>
      <head>
        {/* real head */}
      </head>
      <body/>
    </html>
  );
}
"""
    data = org_jsonld("Acme", "acme.dev", Facts(None, []))
    out = insert(layout_with_comment, data)
    assert "// <head> comment" in out
    assert 'const s = "<head>";' in out
    assert '<script type="application/ld+json"' in out


def test_skip_reason_ignores_comment_before_anchor():
    layout_with_comment = """// <head> comment
const s = "<head>";
export const metadata = { title: "Acme" };

export default function RootLayout() {
  return (
    <html>
      <head>
        {/* real head */}
      </head>
      <body/>
    </html>
  );
}
"""
    assert skip_reason(layout_with_comment) is None


def test_logo_with_port_is_rejected():
    facts = check_facts("acme.dev", HTML, "https://acme.dev:444/logo.png", [])
    assert facts.logo is None and facts.rejected == [("https://acme.dev:444/logo.png", "logo is not on acme.dev")]


def test_off_site_logo_is_rejected():
    facts = check_facts("acme.dev", HTML, "https://cdn.evil.example/logo.png", [])
    assert facts.logo is None and facts.rejected == [("https://cdn.evil.example/logo.png", "logo is not on acme.dev")]


def test_org_jsonld_uses_brand_name_and_url():
    data = org_jsonld("Acme", "acme.dev", Facts("https://acme.dev/logo.png", ["https://github.com/acme-dev"]))
    assert data == {"@context": "https://schema.org", "@type": "Organization", "name": "Acme",
                    "url": "https://acme.dev/", "logo": "https://acme.dev/logo.png",
                    "sameAs": ["https://github.com/acme-dev"]}
    assert "logo" not in org_jsonld("Acme", "acme.dev", Facts(None, [])) and "sameAs" not in org_jsonld("Acme", "acme.dev", Facts(None, []))


def test_insert_adds_exactly_the_constant_and_the_script():
    data = org_jsonld("Acme", "acme.dev", Facts(None, ["https://github.com/acme-dev"]))
    out = insert(LAYOUT, data)
    escaped_data = json.dumps(data, indent=2).replace("<", "\\u003c")
    assert out.replace(f"const organizationJsonLd = {escaped_data};\n\n", "", 1) \
        .replace("\n        " + SCRIPT, "", 1) == LAYOUT
    assert out.index("const organizationJsonLd") < out.index("export const metadata")
    head = out.index("<head>")
    assert re.match(r"<head>\n        <script type=\"application/ld\+json\"", out[head:])
    assert skip_reason(out) == "layout already has application/ld+json"


def test_comment_embedded_urls_rejected():
    html_with_comment = """<html><body>
<!-- <a href="https://x.com/ghost">Ghost</a> -->
<!-- <img src="/c.png"> -->
<a href="https://github.com/real">Real</a>
<img src="/logo.png">
</body></html>"""
    facts = check_facts("acme.dev", html_with_comment, "/c.png", [
        {"url": "https://x.com/ghost", "reason": "ghost"},
    ])
    assert facts.logo is None
    assert facts.same_as == []
    assert ("/c.png", "not linked from the homepage") in facts.rejected
    assert ("https://x.com/ghost", "not linked from the homepage") in facts.rejected


def test_link_with_carriage_return_skipped():
    html_with_cr = '<a href="https://github.com/acme\r">Link</a>'
    links = page_links(html_with_cr)
    assert links == []


def test_head_not_alone_on_line_rejected():
    layout_head_inline = """export const metadata = { title: "Acme" };

export default function RootLayout() {
  return <html><head></head><body/></html>;
}
"""
    assert skip_reason(layout_head_inline) == "unsupported layout (no <head> element)"


def test_multiline_head_tag_rejected():
    layout_multiline_head = """export const metadata = { title: "Acme" };

export default function RootLayout() {
  return (
    <html>
      <head
        className="x">
        {/* content */}
      </head>
      <body/>
    </html>
  );
}
"""
    assert skip_reason(layout_multiline_head) == "unsupported layout (no <head> element)"


def test_crlf_layouts_skipped():
    crlf_layout = LAYOUT.replace("\n", "\r\n")
    assert skip_reason(crlf_layout) == "unsupported layout (CRLF line endings)"


def test_unclosed_comment_doesnt_leak_links():
    html_unclosed_comment = """<html><body>
<a href="https://github.com/real">Real</a>
<!-- <a href="https://x.com/ghost">
"""
    facts = check_facts("acme.dev", html_unclosed_comment, None, [
        {"url": "https://github.com/real", "reason": "real"},
        {"url": "https://x.com/ghost", "reason": "unclosed"},
    ])
    assert facts.same_as == ["https://github.com/real"]
    assert ("https://x.com/ghost", "not linked from the homepage") in facts.rejected


def test_script_text_escapes_lt_at_runtime():
    assert SCRIPT == ('<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: '
                      'JSON.stringify(organizationJsonLd).replace(/</g, "\\\\u003c") }} />')
    assert '.replace(/</g, "\\\\u003c")' in insert(LAYOUT, org_jsonld("Acme", "acme.dev", Facts(None, [], [])))


def test_linkedin_personal_profile_rejected():
    html = '<a href="https://www.linkedin.com/in/jane">J</a><a href="https://www.linkedin.com/company/acme">C</a>'
    facts = check_facts("acme.dev", html, None, [{"url": "https://www.linkedin.com/in/jane", "reason": "founder"},
                                                 {"url": "https://www.linkedin.com/company/acme", "reason": "co"}])
    assert facts.same_as == ["https://www.linkedin.com/company/acme"]
    assert facts.rejected == [("https://www.linkedin.com/in/jane", "personal profile")]


def test_self_closing_head_is_unsupported_and_attributed_head_inserts():
    for tag in ("<head />", "<head/>", '<head className="x" />'):
        assert skip_reason(LAYOUT.replace("<head>", tag).replace("</head>", "")) == \
            "unsupported layout (no <head> element)"
    layout = LAYOUT.replace("<head>", '<head className="x">')
    assert skip_reason(layout) is None
    assert "application/ld+json" in insert(layout, org_jsonld("Acme", "acme.dev", Facts(None, [], [])))


def test_existing_organization_json_ld_identifier_is_skipped():
    assert skip_reason("const organizationJsonLd = {};\n" + LAYOUT) == \
        "unsupported layout (organizationJsonLd is already defined)"


def test_malformed_urls_are_rejected_not_raised():
    html = HTML.replace("</body>", '<a href="http://[bad">x</a></body>')
    facts = check_facts("acme.com", html, "http://[x", [{"url": "http://[x"}, {"url": "https://github.com:abc/x"},
                                                        {"url": "https://github.com/acme-dev"}])
    assert facts.logo is None and facts.same_as == ["https://github.com/acme-dev"]
    assert [why for _, why in facts.rejected][1:] == ["not a URL", "not a known profile site"]


def test_notes_strip_backticks_and_cap_the_rejected_list():
    facts = Facts(logo=None, same_as=[], rejected=[("x`[link](https://evil)`", "bad")] + [(f"u{i}", "bad") for i in range(30)])
    notes = org_schema_notes(facts)
    assert "`[link]" not in notes and "[link](https://evil)" in notes.replace("`", "") and "- and 11 more" in notes
