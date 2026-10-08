"""GitHub REST calls for the executor process. Only the executor imports this; Tenuo checks every call before it runs."""
import base64

import httpx

API = "https://api.github.com"


def _ok(r: httpx.Response) -> httpx.Response:
    r.raise_for_status()
    return r


class GitHub:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=API, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def can_access(self, repo: str) -> bool:
        return self.http.get(f"/repos/{repo}").status_code == 200

    def _file(self, repo: str, path: str, ref: str) -> tuple[str, str] | None:
        r = self.http.get(f"/repos/{repo}/contents/{path}", params={"ref": ref})
        if r.status_code == 404:
            return None
        body = _ok(r).json()
        return base64.b64decode(body["content"]).decode(), body["sha"]

    def read_file(self, repo: str, path: str, ref: str) -> str | None:
        f = self._file(repo, path, ref)
        return f[0] if f else None

    def list_dir(self, repo: str, path: str, ref: str) -> list[str]:
        r = self.http.get(f"/repos/{repo}/contents/{path}", params={"ref": ref})
        if r.status_code == 404:
            return []
        body = _ok(r).json()
        return [item["path"] + ("/" if item["type"] == "dir" else "") for item in body] if isinstance(body, list) else []

    def pr_state(self, repo: str, number: int) -> str:
        """open, merged or closed."""
        body = _ok(self.http.get(f"/repos/{repo}/pulls/{number}")).json()
        return "merged" if body.get("merged") else body["state"]

    def create_branch(self, repo: str, base: str, branch: str) -> None:
        sha = _ok(self.http.get(f"/repos/{repo}/git/ref/heads/{base}")).json()["object"]["sha"]
        _ok(self.http.post(f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}))

    def write_file(self, repo: str, branch: str, path: str, text: str, message: str) -> None:
        body = {"message": message, "branch": branch, "content": base64.b64encode(text.encode()).decode()}
        f = self._file(repo, path, branch)
        if f:
            body["sha"] = f[1]
        _ok(self.http.put(f"/repos/{repo}/contents/{path}", json=body))

    def open_pr(self, repo: str, base: str, head: str, title: str, body: str) -> str:
        r = _ok(self.http.post(f"/repos/{repo}/pulls", json={"title": title, "head": head, "base": base, "body": body}))
        return r.json()["html_url"]
