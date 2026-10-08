"""Start, inspect and schedule fix runs, and run the worker processes. Used by `fixagent fixrun` and `fixagent worker`."""
import asyncio
import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError,
                               ScheduleDescription, ScheduleIntervalSpec, ScheduleOverlapPolicy, SchedulePolicy,
                               ScheduleSpec, WorkflowExecutionStatus)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from fixagent.agent import activities as act
from fixagent.agent.workers import RedactingCloud, redact, set_holder_key, worker
from fixagent.agent.workflow import WORKFLOW_QUEUE, FixRunWorkflow, RunInput
from fixagent.config import load_brand
from fixagent.execute import keys
from fixagent.execute.fixes_org import _safe
from fixagent.execute.warrant import audit_to, cloud_client

ADDRESS = "localhost:7233"
RUN_TIMEOUT = timedelta(days=60)  # covers 14 days of merge polling with room to spare


def run_id(slug: str) -> str:
    return f"fixrun-{slug}"


DEMO_TIMINGS = {"poll_every": 15, "verify_every": 15, "verify_for": 600}  # seconds; a deploy takes 1 to 3 minutes


def run_input(slug: str, brands_root: Path = Path("brands"), demo: bool = False) -> RunInput:
    brand = load_brand(slug, brands_root)
    site = brand.site
    if not site:
        raise ValueError(f"brands/{slug}.yaml has no site: section")
    inp = RunInput(slug=slug, repo=site["repo"], base=site.get("base", "main"), domain=brand.domain,
                   root=site.get("root", ""), code_root=site.get("code_root", ""))
    return replace(inp, **DEMO_TIMINGS) if demo else inp


async def connect() -> Client:
    return await Client.connect(os.environ.get("TEMPORAL_ADDRESS", ADDRESS))


async def _schedule_of(client: Client, slug: str) -> ScheduleDescription | None:
    try:
        return await client.get_schedule_handle(run_id(slug)).describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


async def start(client: Client, inp: RunInput) -> str:
    """Start one run. The fixed workflow id allows one open run per site. Temporal gives scheduled runs their own
    ids, so a site with a schedule is left to the schedule, whose overlap policy skips while a run is open."""
    if await _schedule_of(client, inp.slug):
        return "refused, the site has a schedule"
    try:
        await client.start_workflow(FixRunWorkflow.run, inp, id=run_id(inp.slug), task_queue=WORKFLOW_QUEUE,
                                    execution_timeout=RUN_TIMEOUT, id_conflict_policy=WorkflowIDConflictPolicy.FAIL)
    except WorkflowAlreadyStartedError:
        return "already open"
    return "started"


async def status(client: Client, slug: str) -> str:
    workflow_id = run_id(slug)
    scheduled = await _schedule_of(client, slug)
    if scheduled:
        if not scheduled.info.recent_actions:
            return "scheduled, no run yet"
        workflow_id = scheduled.info.recent_actions[-1].action.workflow_id
    handle = client.get_workflow_handle_for(FixRunWorkflow.run, workflow_id)
    try:
        desc = await handle.describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return f"no run for {slug}"
        raise
    if desc.status == WorkflowExecutionStatus.RUNNING:
        return f"running: {await handle.query(FixRunWorkflow.current_step)}"
    if desc.status == WorkflowExecutionStatus.COMPLETED:
        r = await handle.result()
        lines = [f"finished: {r.status}"] + ([f"pr: {r.pr_url}"] if r.pr_url else [])
        lines += [f"denied: {d['tool']} {_safe(json.dumps(d['args']))}" for d in r.denied]
        lines += [f"skipped: {s}" for s in r.skipped]
        return "\n".join(lines)
    return desc.status.name.lower()


async def schedule(client: Client, inp: RunInput, every_days: int) -> str:
    """Start a run every `every_days` days. A start is skipped while the previous scheduled run is still open."""
    try:
        if (await client.get_workflow_handle(run_id(inp.slug)).describe()).status == WorkflowExecutionStatus.RUNNING:
            return "refused, a run is open"
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    try:
        await client.create_schedule(run_id(inp.slug), Schedule(
            action=ScheduleActionStartWorkflow(FixRunWorkflow.run, inp, id=run_id(inp.slug), task_queue=WORKFLOW_QUEUE,
                                               execution_timeout=RUN_TIMEOUT),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=every_days))]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)))
    except ScheduleAlreadyRunningError:
        return "already scheduled"
    return "scheduled"


def configure_role(role: str, demo: bool = False) -> None:
    """Load only the secrets this role needs. The others never enter the process."""
    if role == "workflow":
        set_holder_key(keys.holder_key())
    elif role == "minter":
        act.configure(act.Deps(issuer=keys.issuer_key(), holder_public_key=keys.holder_public_key()))
    elif role == "executor":
        from fixagent.execute.github import GitHub
        act.configure(act.Deps(github=lambda repo: GitHub(keys.github_token(repo))))
    elif role == "llm":
        from fixagent.agent.demo import DemoLLM
        from fixagent.agent.llm import AnthropicLLM
        act.configure(act.Deps(llm=DemoLLM() if demo else AnthropicLLM()))


AUDIT_LOG = Path("data/agent-audit.jsonl")


def audit_event(event) -> None:
    """One JSONL row per Tenuo decision on this worker, free text redacted."""
    audit_to(AUDIT_LOG, event.workflow_id)(event.decision.lower(), event.tool, redact(event.arguments),
                                           event.denial_reason or "")


async def run_worker(role: str, demo: bool = False) -> None:
    issuer_public_key = keys.issuer_public_key()
    configure_role(role, demo)
    cloud = RedactingCloud(cloud_client()) if role != "minter" else None
    audit = audit_event if role in ("executor", "llm") else None
    await worker(role, await connect(), issuer_public_key, cloud, audit).run()


def audit_rows(path: Path, workflow_id: str, offset: int) -> tuple[list[dict], int]:
    """Audit rows for one run written after `offset` bytes, and the new offset."""
    if not path.exists():
        return [], offset
    with path.open() as f:
        f.seek(offset)
        text = f.read()
        offset = f.tell()
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [r for r in rows if r.get("job") == workflow_id], offset


async def watch(client: Client, slug: str, every: float = 1.0):
    """Follow a run live: yields ("step", text), ("allow" or "deny", row) and finally ("done", status text)."""
    workflow_id = run_id(slug)
    handle = client.get_workflow_handle_for(FixRunWorkflow.run, workflow_id)
    offset = AUDIT_LOG.stat().st_size if AUDIT_LOG.exists() else 0
    step = None
    while True:
        rows, offset = audit_rows(AUDIT_LOG, workflow_id, offset)
        for r in rows:
            yield ("allow" if r["decision"] == "allow" else "deny"), r
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            rows, offset = audit_rows(AUDIT_LOG, workflow_id, offset)
            for r in rows:
                yield ("allow" if r["decision"] == "allow" else "deny"), r
            yield "done", await status(client, slug)
            return
        current = await handle.query(FixRunWorkflow.current_step)
        if current != step:
            step = current
            yield "step", step
        await asyncio.sleep(every)


async def dev_server(db_file: str) -> None:
    """A local Temporal server on localhost:7233 with its UI on http://localhost:8233, kept until Ctrl-C."""
    async with await WorkflowEnvironment.start_local(port=7233, ui=True, ui_port=8233,
                                                     dev_server_database_filename=db_file):
        await asyncio.Event().wait()
