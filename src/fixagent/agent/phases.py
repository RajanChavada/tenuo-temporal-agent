"""Warrants for each phase of a fix run, signed by the minter. Each phase gets a fresh, narrow, short-lived warrant.

Activity argument names here must match the activity function parameters exactly: Tenuo denies unknown or missing
arguments.
"""
import re

from tenuo import All, Exact, Not, Range, Regex, SigningKey, Subset, Warrant, Wildcard

from fixagent.execute.minter import FIX_TYPES, SAFE_BASE, _path_problem
from fixagent.execute.warrant import BRANCH_PREFIX, _write_caps

PHASES = ("explore", "build", "write", "poll", "verify")
TTL = {"explore": 900, "build": 300, "write": 300, "poll": 300, "verify": 2100}
SECRET = (r"(?i)(^|/)(\.env[^/]*|\.git|\.ssh|secrets?|credentials?|[^/]*(secret|credential|private)[^/]*|"
          r"id_(rsa|dsa|ecdsa|ed25519)[^/]*|\.npmrc|\.netrc|\.pypirc|[^/]*\.(pem|key|p12|pfx))(/|$)")
PARENT = r"(^|/)\.\.(/|$)"
SAFE_CHARS = r"[A-Za-z0-9._@+()\[\]/-]"  # no %, so encoded `..` or `/` cannot slip past the checks


def readable(roots: list[str]):
    """Paths the agent may list or read: under the site roots, never secrets, never `..`."""
    roots = [r.strip("/") for r in roots]
    prefix = rf"^{SAFE_CHARS}+$" if "" in roots else r"^(" + "|".join(re.escape(r) for r in roots) + rf")(/{SAFE_CHARS}*)?$"
    return All([Regex(prefix), Not(Regex(SECRET)), Not(Regex(PARENT))])


def phase_problem(phase: str, ctx: dict, is_onboarded) -> str | None:
    """Why the minter must not sign this phase warrant, or None when it may."""
    if phase not in PHASES:
        return f"phase {phase!r} is not known"
    if phase == "explore" and not ("root" in ctx and "code_root" in ctx):
        return "explore needs root and code_root"  # a missing root must not widen reads to the whole repo
    repo = ctx.get("repo", "")
    if not is_onboarded(repo):
        return f"{repo} is not onboarded, run: fixagent keys github {repo}"
    base = ctx.get("base", "")
    if not SAFE_BASE.fullmatch(base) or ".." in base or base.startswith(BRANCH_PREFIX):
        return f"base {base!r} is not allowed"
    if phase == "build" and not set(ctx.get("fixes", [])) <= set(FIX_TYPES):
        return "fixes must come from the catalog"
    if phase == "write":
        paths = ctx.get("paths", [])
        if not paths or any(all(_path_problem(f, p) for f in FIX_TYPES) for p in paths):
            return "write paths must be catalog fix paths"
    if phase == "poll" and not (isinstance(ctx.get("number"), int) and ctx["number"] > 0):
        return "poll needs a pull request number"
    return None


def phase_warrant(issuer: SigningKey, holder_public_key, phase: str, ctx: dict) -> Warrant:
    b = Warrant.mint_builder()
    repo, base, domain = ctx.get("repo"), ctx.get("base"), ctx.get("domain")
    if phase == "explore":
        paths = readable([ctx.get("code_root", ""), ctx.get("root", "")])
        b = (b.capability("run_gates", domain=Exact(domain))
              .capability("homepage", domain=Exact(domain))
              .capability("list_files", repo=Exact(repo), ref=Exact(base), dir=paths)
              .capability("read_file", repo=Exact(repo), ref=Exact(base), path=paths)
              .capability("llm_turn", conversation=Wildcard()))
    elif phase == "build":
        b = b.capability("build_changes", slug=Exact(ctx["slug"]), fixes=Subset(list(ctx["fixes"])), proposals=Wildcard())
    elif phase == "write":
        b = _write_caps(b, repo, base, ctx["paths"])
    elif phase == "poll":
        b = b.capability("get_pr_state", repo=Exact(repo), number=Range(min=ctx["number"], max=ctx["number"]))
    elif phase == "verify":
        b = b.capability("run_gates", domain=Exact(domain))
    return b.holder(holder_public_key).ttl(TTL[phase]).mint(issuer)
