"""Local mode (FIXAGENT_LOCAL=1): a git repo in .fixagent/site stands in for the site's GitHub repo, and
`python -m http.server` on its public/ folder stands in for the live site. Warrants and Tenuo checks are unchanged."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fixagent.gates import _get

HOME = Path(".fixagent")
SITE_DIR, PRS_DIR = HOME / "site", HOME / "prs"
SITE_URL = "http://localhost:8000"
DEMO_SITE = Path("docs/demo/northside-bakery")
AUTHOR = {"GIT_AUTHOR_NAME": "fixagent", "GIT_AUTHOR_EMAIL": "fixagent@localhost",
          "GIT_COMMITTER_NAME": "fixagent", "GIT_COMMITTER_EMAIL": "fixagent@localhost"}


def enabled() -> bool:
    return os.environ.get("FIXAGENT_LOCAL") == "1"


def is_local_repo(repo: str) -> bool:
    """The minter's onboarding check in local mode: no token to store, so any local/ repo counts."""
    return repo.startswith("local/")


def fetch(url: str) -> tuple[int | None, str]:
    """A page of the live site, from the local server. Ceiling: one site per machine; every domain maps to it."""
    return _get(SITE_URL + urlsplit(url).path)


class LocalGit:
    """The GitHub calls the executor makes, on one local repo. Every `repo` name maps to it."""

    def __init__(self, path: Path = SITE_DIR, prs: Path = PRS_DIR):
        self.path, self.prs = path, prs

    def _run(self, *args: str, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["git", "-C", str(self.path), *args], input=input, capture_output=True, text=True,
                                  env={**os.environ, **AUTHOR, **(env or {})})
        except FileNotFoundError:
            raise RuntimeError("local mode needs git on PATH") from None

    def _git(self, *args: str, **kw) -> str:
        r = self._run(*args, **kw)
        if r.returncode:
            raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()}")
        return r.stdout

    def read_file(self, repo: str, path: str, ref: str) -> str | None:
        """None when the file is missing, like GitHub's 404."""
        r = self._run("cat-file", "blob", f"{ref}:{path}")
        return r.stdout if r.returncode == 0 else None

    def list_dir(self, repo: str, path: str, ref: str) -> list[str]:
        d = path.strip("/")
        r = self._run("ls-tree", "-z", ref, "--", *([d + "/"] if d else []))
        rows = [e.split("\t", 1) for e in r.stdout.split("\0") if e] if r.returncode == 0 else []
        return [p + ("/" if meta.split()[1] == "tree" else "") for meta, p in rows]

    def create_branch(self, repo: str, base: str, branch: str) -> None:
        self._git("branch", "--", branch, base)

    def write_file(self, repo: str, branch: str, path: str, text: str, message: str) -> None:
        """Commit one file to `branch` through a scratch index, so the served checkout of main never changes."""
        blob = self._git("hash-object", "-w", "--stdin", input=text).strip()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self._git("read-tree", branch, env=env)
            self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self._git("write-tree", env=env).strip()
        commit = self._git("commit-tree", tree, "-p", branch, "-m", message).strip()
        self._git("update-ref", f"refs/heads/{branch}", commit)

    def open_pr(self, repo: str, base: str, head: str, title: str, body: str) -> str:
        self.prs.mkdir(parents=True, exist_ok=True)
        number = len(list(self.prs.glob("*.json"))) + 1
        (self.prs / f"{number}.json").write_text(json.dumps(
            {"repo": repo, "base": base, "head": head, "title": title, "body": body}))
        return f"local://{repo}/pull/{number}"

    def _pr(self, number: int) -> dict:
        f = self.prs / f"{number}.json"
        if not f.exists():
            raise RuntimeError(f"no local pull request {number}")
        return json.loads(f.read_text())

    def pr_state(self, repo: str, number: int) -> str:
        pr = self._pr(number)
        return "merged" if self._run("merge-base", "--is-ancestor", pr["head"], pr["base"]).returncode == 0 else "open"

    def diff(self, number: int) -> str:
        pr = self._pr(number)
        return self._git("diff", f"{pr['base']}...{pr['head']}")

    def merge(self, number: int) -> None:
        """The reviewer's step on GitHub. The base is checked out, so the merge is live at once."""
        pr = self._pr(number)
        if self.pr_state(pr["repo"], number) == "merged":
            raise RuntimeError(f"pull request {number} is already merged")
        if self._git("symbolic-ref", "--short", "HEAD").strip() != pr["base"]:
            raise RuntimeError(f"check out {pr['base']} in {self.path} first")
        self._git("merge", "-q", "--no-ff", pr["head"], "-m", f"Merge pull request #{number}: {pr['title']}")


def init_site(reset: bool = False, source: Path = DEMO_SITE, path: Path = SITE_DIR, prs: Path = PRS_DIR) -> None:
    """A fresh repo on main holding the demo site's public/ folder, with no pull requests."""
    if path.exists():
        if not reset:
            raise RuntimeError(f"{path} exists, run with --reset to start over")
        shutil.rmtree(path)
        shutil.rmtree(prs, ignore_errors=True)
    shutil.copytree(source / "public", path / "public")
    git = LocalGit(path, prs)
    git._git("init", "-q", "-b", "main")
    git._git("add", "-A")
    git._git("commit", "-q", "-m", "Demo site")
