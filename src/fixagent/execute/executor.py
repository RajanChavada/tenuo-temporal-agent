"""The executor process: the only place that holds a site's GitHub token.

Each tool call passes three checks before any tool code runs: the executor's own scope (repo, base and branch it was
started for), then Tenuo's warrant chain back to the issuer public key (read from the Keychain), then a replay
check. Run as `python -m fixagent.execute.executor`; the CLI starts it and passes config through env vars.
"""
import logging
import os
import re
import sys
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from tenuo import Authorizer
from tenuo.mcp import MCPVerificationResult, MCPVerifier, TenuoMiddleware
from tenuo.nonce import NonceStore

from fixagent.execute import keys
from fixagent.execute.github import GitHub
from fixagent.execute.warrant import _FATAL, PHASE_TTL_SECS, audit_to, cloud_client

log = logging.getLogger(__name__)
FREE_TEXT = ("text", "message", "title", "body")  # kept out of the audit log
SAFE_BRANCH = re.compile(r"fixagent/[A-Za-z0-9][A-Za-z0-9._-]*")


class _SafeCloud:
    """Tenuo Cloud client wrapper: an SDK error or panic is logged and never changes a decision.

    Falsy when there is no client, so MCPVerifier skips emitting instead of creating its own client.
    """

    def __init__(self, client, authorizer):
        self.client = client
        if client:
            try:
                client.bind_authorizer(authorizer)
            except _FATAL:
                raise
            except BaseException:  # noqa: BLE001
                log.warning("Tenuo Cloud bind failed, using local enforcement only", exc_info=True)
                self.client = None

    def __bool__(self):
        return self.client is not None

    def emit_for_enforcement(self, result, chain_result=None, **kwargs):
        try:
            self.client.emit_for_enforcement(result, chain_result=chain_result, **kwargs)
        except _FATAL:
            raise
        except BaseException:  # noqa: BLE001
            log.warning("Tenuo Cloud receipt failed for %s", result.tool, exc_info=True)


def scope_problem(tool: str, args: dict, repo: str, base: str) -> str | None:
    """Why this call is outside the repo, base and branches this executor serves, or None."""
    if args.get("repo") != repo:
        return f"repo {args.get('repo')!r} is not the repo this executor serves"
    if tool in ("create_branch", "open_pr") and args.get("base") != base:
        return f"base {args.get('base')!r} is not the base this executor serves"
    if tool == "write_file":
        n = len(str(args.get("text", "")).encode())
        if n != args.get("size"):
            return f"size {args.get('size')} does not match the text ({n} bytes)"
    for key in ("branch", "head"):
        if key in args and (not SAFE_BRANCH.fullmatch(str(args[key])) or ".." in str(args[key])):
            return f"branch {args[key]!r} is not an allowed fixagent/<name>"
    return None


class _Gate:
    """Scope check, then Tenuo, with one audit-log row per decision. A scope refusal is a denial everywhere."""

    def __init__(self, verifier: MCPVerifier, repo: str, base: str, on_decision, cloud):
        self.verifier, self.repo, self.base, self.on_decision, self.cloud = verifier, repo, base, on_decision, cloud

    def verify(self, tool_name, arguments, meta=None):
        args = arguments or {}
        problem = scope_problem(tool_name, args, self.repo, self.base)
        if problem:
            result = MCPVerificationResult(allowed=False, tool=tool_name, clean_arguments={}, constraints={},
                                           denial_reason=f"refused: {problem}", jsonrpc_error_code=-32001,
                                           error_type="policy_violation")
            if self.cloud:
                self.cloud.emit_for_enforcement(result)
        else:
            result = self.verifier.verify(tool_name, arguments, meta=meta)
        logged = {k: v for k, v in args.items() if k not in FREE_TEXT}
        self.on_decision("allow" if result.allowed else "deny", tool_name, logged, result.denial_reason or "")
        return result


def build_server(issuer_public_key, gh: GitHub, repo: str, base: str, on_decision, cloud=None) -> FastMCP:
    authorizer = Authorizer(trusted_roots=[issuer_public_key])
    safe_cloud = _SafeCloud(cloud, authorizer)
    verifier = MCPVerifier(authorizer=authorizer, require_warrant=True, control_plane=safe_cloud, nonce_store=NonceStore(ttl_seconds=PHASE_TTL_SECS))
    gate = _Gate(verifier, repo, base, on_decision, safe_cloud)
    mcp = FastMCP("fixagent-executor", middleware=[TenuoMiddleware(gate)])

    def github(call, *args):
        try:
            return call(*args)
        except httpx.HTTPError as e:
            detail = ""
            if isinstance(e, httpx.HTTPStatusError):
                try:
                    detail = e.response.json().get("message", "")
                except (ValueError, AttributeError):
                    pass
            raise ToolError(f"github: {e}" + (f" ({detail})" if detail else "")) from e

    @mcp.tool()
    def read_file(repo: str, path: str) -> dict:
        """Read a file from the base branch. `found` is false when it does not exist."""
        text = github(gh.read_file, repo, path, base)
        return {"found": text is not None, "text": text or ""}

    @mcp.tool()
    def create_branch(repo: str, base: str, branch: str) -> dict:
        github(gh.create_branch, repo, base, branch)
        return {"branch": branch}

    @mcp.tool()
    def write_file(repo: str, branch: str, path: str, size: int, text: str, message: str) -> dict:
        github(gh.write_file, repo, branch, path, text, message)
        return {"path": path}

    @mcp.tool()
    def open_pr(repo: str, base: str, head: str, title: str, body: str) -> dict:
        return {"url": github(gh.open_pr, repo, base, head, title, body)}

    return mcp


def main() -> None:
    env = os.environ
    repo = env["FIXAGENT_REPO"]
    try:
        gh = GitHub(keys.github_token(repo))
    except keys.MissingKey as e:
        sys.exit(str(e))
    if not gh.can_access(repo):
        sys.exit(f"token cannot access {repo}")
    try:
        issuer = keys.issuer_public_key()
    except keys.MissingKey as e:
        sys.exit(str(e))
    on_decision = audit_to(Path(env["FIXAGENT_AUDIT_LOG"]), env["FIXAGENT_JOB"])
    build_server(issuer, gh, repo, env["FIXAGENT_BASE"], on_decision, cloud_client()).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
