import base64
import json

import httpx
import respx

from fixagent.execute.github import API, GitHub

REPO = "acme/site"


def gh():
    return GitHub("t0k")


def b64(s):
    return base64.b64encode(s.encode()).decode()


@respx.mock
def test_read_file_returns_text_or_none():
    route = respx.get(f"{API}/repos/{REPO}/contents/robots.txt").mock(
        return_value=httpx.Response(200, json={"content": b64("User-agent: *\n"), "sha": "s1"}))
    respx.get(f"{API}/repos/{REPO}/contents/llms.txt").mock(return_value=httpx.Response(404))
    g = gh()
    assert g.read_file(REPO, "robots.txt", "main") == "User-agent: *\n"
    assert g.read_file(REPO, "llms.txt", "main") is None
    assert route.calls[0].request.url.params["ref"] == "main"
    assert route.calls[0].request.headers["authorization"] == "Bearer t0k"


@respx.mock
def test_branch_write_and_pr():
    respx.get(f"{API}/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base1"}}))
    refs = respx.post(f"{API}/repos/{REPO}/git/refs").mock(return_value=httpx.Response(201, json={}))
    respx.get(f"{API}/repos/{REPO}/contents/robots.txt").mock(
        return_value=httpx.Response(200, json={"content": b64("old"), "sha": "s1"}))
    put = respx.put(f"{API}/repos/{REPO}/contents/robots.txt").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{API}/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/site/pull/7"}))
    g = gh()
    g.create_branch(REPO, "main", "fixagent/aeo-1")
    g.write_file(REPO, "fixagent/aeo-1", "robots.txt", "new\n", "Let AI search bots read the site")
    url = g.open_pr(REPO, "main", "fixagent/aeo-1", "AI visibility fixes", "body")
    assert json.loads(refs.calls[0].request.read()) == {"ref": "refs/heads/fixagent/aeo-1", "sha": "base1"}
    sent = json.loads(put.calls[0].request.read())
    assert sent["sha"] == "s1" and sent["branch"] == "fixagent/aeo-1" and base64.b64decode(sent["content"]) == b"new\n"
    assert url == "https://github.com/acme/site/pull/7"


@respx.mock
def test_can_access_is_true_only_for_200():
    respx.get(f"{API}/repos/{REPO}").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{API}/repos/other/repo").mock(return_value=httpx.Response(404))
    assert gh().can_access(REPO) and not gh().can_access("other/repo")


@respx.mock
def test_list_dir_marks_folders_and_treats_missing_as_empty():
    respx.get(f"{API}/repos/{REPO}/contents/web").mock(return_value=httpx.Response(200, json=[
        {"path": "web/app", "type": "dir"}, {"path": "web/package.json", "type": "file"}]))
    respx.get(f"{API}/repos/{REPO}/contents/none").mock(return_value=httpx.Response(404))
    respx.get(f"{API}/repos/{REPO}/contents/web/a.txt").mock(return_value=httpx.Response(200, json={"type": "file"}))
    assert gh().list_dir(REPO, "web", "main") == ["web/app/", "web/package.json"]
    assert gh().list_dir(REPO, "none", "main") == []
    assert gh().list_dir(REPO, "web/a.txt", "main") == []


@respx.mock
def test_pr_state_reports_merged_open_or_closed():
    respx.get(f"{API}/repos/{REPO}/pulls/1").mock(return_value=httpx.Response(200, json={"state": "closed", "merged": True}))
    respx.get(f"{API}/repos/{REPO}/pulls/2").mock(return_value=httpx.Response(200, json={"state": "closed", "merged": False}))
    respx.get(f"{API}/repos/{REPO}/pulls/3").mock(return_value=httpx.Response(200, json={"state": "open", "merged": False}))
    assert [gh().pr_state(REPO, n) for n in (1, 2, 3)] == ["merged", "closed", "open"]
