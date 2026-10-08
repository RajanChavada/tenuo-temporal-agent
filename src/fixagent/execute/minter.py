"""The minter process: the only place that holds the issuer key.

It signs a job warrant only for onboarded repos (a GitHub token stored with `fixagent keys github`), only for robots.txt
and llms.txt, and only bound to the job public key the caller sends. Run as `python -m fixagent.execute.minter`.
"""
import re

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from tenuo import PublicKey

from fixagent.execute import keys
from fixagent.execute.warrant import BRANCH_PREFIX, job_warrant

FILES = ("robots.txt", "llms.txt")
FIX_TYPES = ("site-files", "org-schema")
LAYOUT = re.compile(r"(?:[A-Za-z0-9._-]+/)*(?:src/)?app/layout\.(?:tsx|jsx)")
SAFE_BASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
HOLDER = re.compile(r"[0-9a-f]{64}")


def _path_problem(fix: str, path: str) -> bool:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts:
        return True
    if fix == "org-schema":
        return not LAYOUT.fullmatch(path)
    return parts[-1] not in FILES


def policy_problem(repo: str, base: str, paths: list[str], holder: str, is_onboarded,
                   fix: str = "site-files") -> str | None:
    """Why the minter must not sign this request, or None when it may."""
    if fix not in FIX_TYPES:
        return f"fix {fix!r} is not a known fix type"
    if not is_onboarded(repo):
        return f"{repo} is not onboarded, run: fixagent keys github {repo}"
    if not SAFE_BASE.fullmatch(base) or ".." in base or base.startswith(BRANCH_PREFIX):
        return f"base {base!r} is not allowed"
    if not paths:
        return "no paths requested"
    for p in paths:
        if _path_problem(fix, p):
            return f"path {p!r} is not allowed for {fix}"
    if not HOLDER.fullmatch(holder):
        return "holder must be a 64-character hex public key"
    return None


def build_minter(load_issuer_key, is_onboarded) -> FastMCP:
    mcp = FastMCP("fixagent-minter")

    @mcp.tool()
    def mint_job(repo: str, base: str, paths: list[str], holder: str, fix: str = "site-files") -> dict:
        """Sign a 15-minute job warrant for one onboarded repo, bound to the caller's job public key."""
        problem = policy_problem(repo, base, paths, holder, is_onboarded, fix)
        if problem:
            raise ToolError(f"refused: {problem}")
        try:
            issuer = load_issuer_key()
        except keys.MissingKey as e:
            raise ToolError(f"refused: {e}") from e
        warrant = job_warrant(issuer, PublicKey.from_bytes(bytes.fromhex(holder)), repo, base, paths)
        return {"warrant": warrant.to_base64()}

    return mcp


def main() -> None:
    build_minter(keys.issuer_key, keys.is_onboarded).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
