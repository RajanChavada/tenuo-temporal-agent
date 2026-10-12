import pytest

from fixagent import local
from fixagent.local import LocalGit, init_site


@pytest.fixture
def site(tmp_path):
    src = tmp_path / "src"
    (src / "public").mkdir(parents=True)
    (src / "public" / "robots.txt").write_text("User-agent: OAI-SearchBot\nDisallow: /\n")
    (src / "public" / "index.html").write_text("<h1>Bakery</h1>")
    init_site(source=src, path=tmp_path / "site", prs=tmp_path / "prs")
    return tmp_path / "site", LocalGit(tmp_path / "site", tmp_path / "prs")


def test_reads_and_lists_like_github(site):
    _, git = site
    assert git.read_file("local/x", "public/robots.txt", "main").startswith("User-agent")
    assert git.read_file("local/x", "public/.env", "main") is None
    assert git.list_dir("local/x", "", "main") == ["public/"]
    assert git.list_dir("local/x", "public", "main") == ["public/index.html", "public/robots.txt"]
    assert git.list_dir("local/x", "nope", "main") == []


def test_pull_request_goes_live_only_when_merged(site):
    path, git = site
    git.create_branch("local/x", "main", "fixagent/a")
    git.write_file("local/x", "fixagent/a", "public/robots.txt", "User-agent: *\nAllow: /\n", "Unblock bots")
    git.write_file("local/x", "fixagent/a", "public/llms.txt", "# Bakery\n", "Add llms.txt")
    url = git.open_pr("local/x", "main", "fixagent/a", "AI visibility fixes", "body")
    assert url == "local://local/x/pull/1" and git.pr_state("local/x", 1) == "open"
    assert "Disallow" in (path / "public" / "robots.txt").read_text()  # served tree untouched before the merge
    assert "+Allow: /" in git.diff(1) and "public/llms.txt" in git.diff(1)
    git.merge(1)
    assert git.pr_state("local/x", 1) == "merged" and (path / "public" / "llms.txt").exists()
    with pytest.raises(RuntimeError, match="already merged"):
        git.merge(1)
    with pytest.raises(RuntimeError, match="no local pull request 2"):
        git.pr_state("local/x", 2)


def test_init_site_refuses_to_overwrite_without_reset(site, tmp_path):
    with pytest.raises(RuntimeError, match="--reset"):
        init_site(source=tmp_path / "src", path=tmp_path / "site", prs=tmp_path / "prs")
    init_site(reset=True, source=tmp_path / "src", path=tmp_path / "site", prs=tmp_path / "prs")


def test_fetch_reads_the_local_server(monkeypatch):
    seen = []
    monkeypatch.setattr(local, "_get", lambda url: seen.append(url) or (200, "ok"))
    assert local.fetch("https://northside.test/robots.txt") == (200, "ok")
    assert seen == ["http://localhost:8000/robots.txt"]
    assert local.is_local_repo("local/northside-bakery") and not local.is_local_repo("acme/site")
