"""Tenuo warrants for one fix job: the job warrant signed by the issuer key, and the narrower phase warrants."""
import json
import logging
import os
import time
from pathlib import Path

from tenuo import Exact, OneOf, Pattern, Range, SigningKey, Warrant, Wildcard

BRANCH_PREFIX = "fixagent/"
MAX_BYTES = 20_000
TTL_SECS = 900
PHASE_TTL_SECS = 300
REGISTER_WAIT_SECS = 10
# tenuo 0.3.1 sends audit events on a 10 s timer and its shutdown() panics, so callers wait this long instead.
CLOUD_SEND_SECS = 11
# Tenuo's Rust core raises pyo3 PanicException, which is a BaseException. Catch it, but let these through.
_FATAL = (KeyboardInterrupt, SystemExit)
log = logging.getLogger(__name__)


class Denied(RuntimeError):
    """The executor refused a tool call under the warrant. The tool did not run."""


def _branch():
    return Pattern(BRANCH_PREFIX + "*")


def _read_cap(builder, repo: str, paths: list[str]):
    return builder.capability("read_file", repo=Exact(repo), path=OneOf(paths))


def _write_caps(builder, repo: str, base: str, paths: list[str]):
    """The 3 write-side tools. Free-text fields are Wildcard: the holder's signature still covers their exact value."""
    return (builder
            .capability("create_branch", repo=Exact(repo), base=Exact(base), branch=_branch())
            .capability("write_file", repo=Exact(repo), branch=_branch(), path=OneOf(paths), size=Range(max=MAX_BYTES),
                        text=Wildcard(), message=Wildcard())
            .capability("open_pr", repo=Exact(repo), base=Exact(base), head=_branch(), title=Wildcard(), body=Wildcard()))


def job_warrant(issuer: SigningKey, holder_public_key, repo: str, base: str, paths: list[str],
                ttl: int = TTL_SECS) -> Warrant:
    """Root warrant for one fix job, signed by the issuer key."""
    if base.startswith(BRANCH_PREFIX):
        raise ValueError(f"base branch {base!r} must not start with {BRANCH_PREFIX!r}")
    builder = _read_cap(Warrant.mint_builder(), repo, paths)
    return _write_caps(builder, repo, base, paths).holder(holder_public_key).ttl(ttl).mint(issuer)


def read_warrant(job: Warrant, job_key: SigningKey, holder_public_key, repo: str, paths: list[str]) -> Warrant:
    """Narrow the job warrant to read_file only."""
    return _read_cap(job.grant_builder(), repo, paths).holder(holder_public_key).ttl(PHASE_TTL_SECS).grant(job_key)


def write_warrant(job: Warrant, job_key: SigningKey, holder_public_key, repo: str, base: str,
                  paths: list[str]) -> Warrant:
    """Narrow the job warrant to the write tools, on only the paths that change."""
    return _write_caps(job.grant_builder(), repo, base, paths).holder(holder_public_key).ttl(PHASE_TTL_SECS).grant(job_key)


def cloud_client(wait_secs: float = REGISTER_WAIT_SECS):
    """Tenuo Cloud client when TENUO_CONNECT_TOKEN or TENUO_API_KEY is set, else None.

    Waits for the background registration, since events sent before it are dropped.
    """
    if not (os.environ.get("TENUO_CONNECT_TOKEN") or os.environ.get("TENUO_API_KEY")):
        return None
    try:
        from tenuo.control_plane import get_or_create
        client = get_or_create()
    except _FATAL:
        raise
    except BaseException:  # noqa: BLE001
        log.warning("Tenuo Cloud unavailable, using local enforcement only", exc_info=True)
        return None
    deadline = time.monotonic() + wait_secs
    while client and not client.authorizer_id and time.monotonic() < deadline:
        time.sleep(0.1)
    if client and not client.authorizer_id:
        log.warning("Tenuo Cloud did not register within %ss; early decisions may not reach it", wait_secs)
    return client


def audit_to(path: Path, job: str):
    def write(decision: str, tool: str, args: dict, reason: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"ts": int(time.time()), "job": job, "decision": decision, "tool": tool,
                                "args": args, "reason": reason}) + "\n")
    return write
