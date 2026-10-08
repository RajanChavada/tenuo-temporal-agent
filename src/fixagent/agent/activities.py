"""Temporal activities for the fix agent. Tenuo's Temporal plugin checks a warrant before each one runs, except
`mint_phase`, which runs on the minter worker and enforces the minter's own policy.

Activity parameter names are part of the warrant contract (see phases.py); keep them in sync.
Dependencies are set once per worker process through `configure(...)`, so tests can swap in fakes.
"""
import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from fixagent.config import load_brand
from fixagent.execute import keys
from fixagent.execute.executor import SAFE_BRANCH
from fixagent.execute.fixes_org import (check_facts, insert, layout_paths, org_jsonld, org_schema_notes, page_links,
                                      page_title, skip_reason)
from fixagent.execute.runner import propose, site_paths
from fixagent.gates import _get as fetch_page, check_gates

MAX_TEXT = 20_000
MAX_TITLE = 200


@dataclass
class Deps:
    github: object = None          # repo -> GitHub client (executor worker only, set by run.configure_role)
    fetch: object = fetch_page     # url -> (status, html)
    llm: object = None             # has next_step(system, messages, tools)
    brands_root: Path = Path("brands")
    issuer: object = None          # SigningKey (minter worker only)
    holder_public_key: object = None
    is_onboarded: object = keys.is_onboarded
    extra: dict = field(default_factory=dict)


DEPS = Deps()


def configure(deps: Deps) -> None:
    global DEPS
    DEPS = deps


def _refuse(message: str) -> ApplicationError:
    return ApplicationError(message, type="Refused", non_retryable=True)


# --- minter worker -----------------------------------------------------------------------------------------------
@activity.defn
def mint_phase(phase: str, ctx: dict) -> str:
    from fixagent.agent.phases import phase_problem, phase_warrant
    problem = phase_problem(phase, ctx, DEPS.is_onboarded)
    if problem:
        raise _refuse(f"refused: {problem}")
    return phase_warrant(DEPS.issuer, DEPS.holder_public_key, phase, ctx).to_base64()


# --- executor worker ---------------------------------------------------------------------------------------------
@activity.defn
def run_gates(domain: str) -> list[dict]:
    return check_gates(domain, get=DEPS.fetch)


@activity.defn
def homepage(domain: str) -> dict:
    status, html = DEPS.fetch(f"https://{domain}/")
    return {"status": status, "title": page_title(html or "")[:MAX_TITLE], "links": page_links(html or "")}


@activity.defn
def list_files(repo: str, ref: str, dir: str) -> list[str]:
    return DEPS.github(repo).list_dir(repo, dir, ref)


@activity.defn
def read_file(repo: str, ref: str, path: str) -> dict:
    text = DEPS.github(repo).read_file(repo, path, ref)
    return {"found": text is not None, "text": (text or "")[:MAX_TEXT], "truncated": len(text or "") > MAX_TEXT}


def _facts_by_fix(proposals: str) -> dict:
    """Facts per fix type from the model's proposals. Malformed entries are dropped, never raised on."""
    try:
        items = json.loads(proposals)
    except ValueError:
        return {}
    if not isinstance(items, list):
        return {}
    return {p["type"]: p.get("facts") if isinstance(p.get("facts"), dict) else {}
            for p in items if isinstance(p, dict) and isinstance(p.get("type"), str)}


@activity.defn
def build_changes(slug: str, fixes: list[str], proposals: str) -> dict:
    """Build the file changes for the proposed catalog fixes. Code writes every byte; the model only chose facts.

    `verify` lists the fixes whose gates can show them live after the merge."""
    brand = load_brand(slug, DEPS.brands_root)
    site = brand.site
    repo, base = site["repo"], site.get("base", "main")
    gh = DEPS.github(repo)
    facts_by_fix = _facts_by_fix(proposals)
    changes, notes, skipped, verify = [], [], [], []
    if "site-files" in fixes:
        found, skips = propose(brand, site, lambda p: gh.read_file(repo, p, base))
        changes += [{"path": c.path, "after": c.after, "reason": c.reason} for c in found]
        skipped += skips
        verify += ["site-files"] if found else []
    if "org-schema" in fixes:
        paths = layout_paths(site.get("code_root", ""))
        layout = next(((p, t) for p in paths if (t := gh.read_file(repo, p, base)) is not None), None)
        reason = skip_reason(layout[1]) if layout else f"unsupported layout (none of {', '.join(paths)})"
        if reason:
            skipped.append(f"org-schema: {reason}")
        else:
            status, html = DEPS.fetch(f"https://{brand.domain}/")
            facts_in = facts_by_fix.get("org-schema", {})
            logo = facts_in.get("logo") if isinstance(facts_in.get("logo"), str) else None
            same_as = facts_in.get("same_as") if isinstance(facts_in.get("same_as"), list) else []
            same_as = [i for i in same_as if isinstance(i, dict) and isinstance(i.get("url"), str)]
            facts = check_facts(brand.domain, html or "", logo, same_as)
            changes.append({"path": layout[0], "after": insert(layout[1], org_jsonld(brand.name, brand.domain, facts)),
                            "reason": "Add Organization schema (JSON-LD) so AI assistants can identify the company"})
            notes.append(org_schema_notes(facts))
            verify += ["org-schema"] if facts.same_as else []  # the gate needs sameAs, so without it there is nothing to check
    return {"changes": changes, "notes": "\n\n".join(notes), "skipped": skipped, "verify": verify}


def _branch_ok(branch: str) -> None:
    if not SAFE_BRANCH.fullmatch(branch) or ".." in branch:
        raise _refuse(f"refused: branch {branch!r} is not an allowed fixagent/<name>")


@activity.defn
def create_branch(repo: str, base: str, branch: str) -> str:
    _branch_ok(branch)
    DEPS.github(repo).create_branch(repo, base, branch)
    return branch


@activity.defn
def write_file(repo: str, branch: str, path: str, size: int, text: str, message: str) -> str:
    _branch_ok(branch)
    if len(text.encode()) != size:
        raise _refuse(f"refused: size {size} does not match the text ({len(text.encode())} bytes)")
    DEPS.github(repo).write_file(repo, branch, path, text, message)
    return path


@activity.defn
def open_pr(repo: str, base: str, head: str, title: str, body: str) -> dict:
    _branch_ok(head)
    url = DEPS.github(repo).open_pr(repo, base, head, title, body)
    return {"url": url, "number": int(url.rstrip("/").rsplit("/", 1)[1])}


@activity.defn
def get_pr_state(repo: str, number: int) -> str:
    return DEPS.github(repo).pr_state(repo, number)


# --- llm worker --------------------------------------------------------------------------------------------------
@activity.defn
def llm_turn(conversation: str) -> dict:
    """One model turn. `conversation` is JSON {system, messages, tools}: Tenuo cannot constrain nested arguments."""
    from fixagent.agent.llm import LLMError
    data = json.loads(conversation)
    try:
        step = DEPS.llm.next_step(data["system"], data["messages"], data["tools"])
    except LLMError as e:
        raise ApplicationError(str(e), type="LLMError") from e
    return {"text": step.text, "calls": step.calls, "raw": step.raw, "tokens": step.tokens}


EXECUTOR_ACTIVITIES = [run_gates, homepage, list_files, read_file, build_changes, create_branch, write_file, open_pr,
                       get_pr_state]
LLM_ACTIVITIES = [llm_turn]
MINTER_ACTIVITIES = [mint_phase]
