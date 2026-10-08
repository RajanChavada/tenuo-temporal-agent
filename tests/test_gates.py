from fixagent.gates import check_gates, crawler_gates_failed

HOME_OK = "<html><body>" + "<p>Real words about the product.</p>" * 40 + \
    '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","sameAs":["https://x.com/acme"]}</script></body></html>'
HOME_JS = '<html><body><div id="root"></div><script>app()</script></body></html>'

def fake(robots, home):
    def get(url):
        return robots if url.endswith("/robots.txt") else home
    return get

def by_gate(results):
    return {r["gate"]: r["status"] for r in results}

def test_all_pass_when_robots_allows_and_page_has_text_and_schema():
    r = by_gate(check_gates("acme.dev", fake((200, "User-agent: *\nAllow: /\n"), (200, HOME_OK))))
    assert r == {"robots:OAI-SearchBot": "pass", "robots:ChatGPT-User": "pass", "robots:GPTBot": "pass",
                 "renders_without_js": "pass", "organization_schema": "pass", "indexed": "unknown"}

def test_blocked_bot_and_js_only_page_fail():
    robots = (200, "User-agent: GPTBot\nDisallow: /\n")
    results = check_gates("acme.dev", fake(robots, (200, HOME_JS)))
    r = by_gate(results)
    assert r["robots:GPTBot"] == "fail" and r["robots:OAI-SearchBot"] == "pass"
    assert r["renders_without_js"] == "fail" and r["organization_schema"] == "fail"
    assert crawler_gates_failed(results)

def test_missing_robots_is_pass_and_unreachable_is_unknown():
    assert by_gate(check_gates("acme.dev", fake((404, ""), (200, HOME_OK))))["robots:GPTBot"] == "pass"
    r = by_gate(check_gates("acme.dev", fake((None, ""), (None, ""))))
    assert r["robots:GPTBot"] == "unknown" and r["renders_without_js"] == "unknown"
    assert not crawler_gates_failed(check_gates("acme.dev", fake((None, ""), (None, ""))))

def test_schema_inside_graph_counts():
    home = HOME_OK.split('<script type="application/ld+json">')[0] + \
        '<script type="application/ld+json">{"@graph":[{"@type":["Organization","Brand"],"sameAs":"https://github.com/acme"}]}</script>'
    assert by_gate(check_gates("acme.dev", fake((404, ""), (200, home))))["organization_schema"] == "pass"

def test_robots_403_is_unknown_not_allow_all():
    results = check_gates("acme.dev", fake((403, ""), (200, HOME_OK)))
    r = by_gate(results)
    assert r["robots:OAI-SearchBot"] == "unknown" and r["robots:ChatGPT-User"] == "unknown" and r["robots:GPTBot"] == "unknown"
    assert not crawler_gates_failed(results)

def test_robots_503_is_unknown_not_allow_all():
    results = check_gates("acme.dev", fake((503, ""), (200, HOME_OK)))
    r = by_gate(results)
    assert r["robots:OAI-SearchBot"] == "unknown" and r["robots:ChatGPT-User"] == "unknown" and r["robots:GPTBot"] == "unknown"
    assert not crawler_gates_failed(results)

def test_robots_html_body_is_unknown_not_allow_all():
    results = check_gates("acme.dev", fake((200, "<html><body>not found</body></html>"), (200, HOME_OK)))
    r = by_gate(results)
    assert r["robots:OAI-SearchBot"] == "unknown" and r["robots:ChatGPT-User"] == "unknown" and r["robots:GPTBot"] == "unknown"
    assert not crawler_gates_failed(results)


def test_get_returns_none_on_invalid_url(monkeypatch):
    import httpx

    from fixagent import gates

    def boom(*a, **kw):
        raise httpx.InvalidURL("bad url")

    monkeypatch.setattr(gates.httpx, "get", boom)
    assert gates._get("https://exa mple.com/") == (None, "")


def test_non_string_type_is_not_a_crash():
    home = '<script type="application/ld+json">{"@type": 5, "sameAs": ["x"]}</script>' \
        '<script type="application/ld+json">{"@type": {"a": 1}, "sameAs": ["x"]}</script>'
    assert by_gate(check_gates("acme.dev", fake((404, ""), (200, home))))["organization_schema"] == "fail"

def test_many_unclosed_ld_json_tags_finish_fast():
    import time
    home = '<script type="application/ld+json">{"@type": "Thing"}' * 4000
    start = time.perf_counter()
    check_gates("acme.dev", fake((404, ""), (200, home)))
    assert time.perf_counter() - start < 0.5

def test_schema_after_first_300kb_is_ignored():
    org = '<script type="application/ld+json">{"@type":"Organization","sameAs":["https://x.com/a"]}</script>'
    home = "<p>" + "x" * 300_000 + "</p>" + org
    assert by_gate(check_gates("acme.dev", fake((404, ""), (200, home))))["organization_schema"] == "fail"


def test_schema_inside_an_unclosed_earlier_script_is_not_seen_like_browsers():
    # HTML parses <script> content as raw text up to the first </script>, so a JSON-LD block that sits
    # after an unclosed <script> is part of that script's text in browsers and in Google's parser too.
    home = ("<html><body>" + "<p>Real words about the product.</p>" * 40
            + '<script>var a = 1;<script type="application/ld+json">'
            + '{"@type":"Organization","sameAs":["https://x.com/acme"]}</script></body></html>')
    assert by_gate(check_gates("acme.dev", fake((404, ""), (200, home))))["organization_schema"] == "fail"
