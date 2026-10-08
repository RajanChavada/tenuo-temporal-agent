from pathlib import Path

from fixagent.config import load_brand
from fixagent.execute.fixes import allow_search_bots, llms_txt, robots_problems

OWN_GROUP = "User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n"
SHARED = ("# keep this comment\nUser-agent: GPTBot\nUser-agent: ChatGPT-User\nUser-agent: PerplexityBot\nDisallow: /\n\n"
          "User-agent: *\nAllow: /\nDisallow: /private\nSitemap: https://acme.dev/sitemap.xml\n")


def test_nothing_to_do_when_search_bots_are_allowed():
    assert allow_search_bots("User-agent: *\nDisallow: /admin\n") is None
    assert allow_search_bots("") is None


def test_drops_search_bot_group_and_keeps_star_rules():
    after = allow_search_bots(OWN_GROUP)
    assert after == "User-agent: *\nDisallow: /admin\n"
    assert robots_problems(OWN_GROUP, after) == []


def test_keeps_gptbot_block_comment_and_sitemap():
    after = allow_search_bots(SHARED)
    assert "User-agent: GPTBot\nDisallow: /" in after
    assert "ChatGPT-User" not in after and "PerplexityBot" not in after
    assert after.startswith("# keep this comment") and "Sitemap: https://acme.dev/sitemap.xml" in after
    assert robots_problems(SHARED, after) == []


def test_site_that_blocks_every_crawler_is_flagged():
    before = "User-agent: *\nDisallow: /\n"
    assert "OAI-SearchBot is still blocked from /" in robots_problems(before, allow_search_bots(before))


def test_problems_catch_changed_access_and_new_rules():
    before = "User-agent: Googlebot\nDisallow: /drafts\n\nUser-agent: OAI-SearchBot\nDisallow: /\n"
    problems = robots_problems(before, "User-agent: *\nAllow: /\n")
    assert "Googlebot changed on /drafts" in problems
    assert "new Allow or Disallow rules were added" in problems


def test_llms_txt_uses_only_brand_file_facts():
    brand = load_brand("example", Path("brands"))
    assert llms_txt(brand) == ("# Acme\n\n> Acme is an open-source LLM tracing SDK for Python.\n\n"
                               "- Website: https://acme.dev/\n- Category: LLM tracing tools\n")
    brand.claims = ["Acme traces every LLM call with one line of code."]
    assert llms_txt(brand).endswith("\n## Facts\n\n- Acme traces every LLM call with one line of code.\n")


def test_crawl_delay_does_not_end_agent_list():
    before = "User-agent: CCBot\nCrawl-delay: 10\nUser-agent: PerplexityBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert "CCBot changed on /" in robots_problems(before, allow_search_bots(before))


def test_crawl_delay_group_of_other_agent_stays_safe():
    before = "User-agent: Bingbot\nCrawl-delay: 10\n\nUser-agent: OAI-SearchBot\nDisallow: /\n"
    assert robots_problems(before, allow_search_bots(before)) == []


def test_search_bot_sharing_group_with_other_agent():
    before = "User-agent: CCBot\nUser-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    out = allow_search_bots(before)
    assert "User-agent: CCBot\nDisallow: /" in out and "OAI-SearchBot" not in out
    assert robots_problems(before, out) == []
