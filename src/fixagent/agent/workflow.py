"""FixRunWorkflow: the durable fix agent.

A. Agent loop: the model picks the next tool each turn; every tool is a Tenuo-checked activity.
B. Fixed steps the model cannot skip: open one PR, wait for the merge, verify the fix is live.
Every phase uses a fresh warrant from the minter.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError, TimeoutType

with workflow.unsafe.imports_passed_through():
    from tenuo import Warrant
    from tenuo.temporal import tenuo_execute_activity

    from fixagent.agent import activities as act
    from fixagent.agent.phases import TTL
    from fixagent.agent.tools import CATALOG, SYSTEM, TOOLS
    from fixagent.gates import AI_BOTS

MINTER_QUEUE, EXECUTOR_QUEUE, LLM_QUEUE, WORKFLOW_QUEUE = "fixagent-minter", "fixagent-executor", "fixagent-llm", "fixagent-workflow"
HOLDER_KEY_ID = "workflow"
GATES_FOR_FIX = {"site-files": tuple(f"robots:{b}" for b in AI_BOTS), "org-schema": ("organization_schema",)}
MAX_RESULT = 8000
MAX_CALLS = 8                  # tool calls handled per model turn; the rest get an error result
MAX_CONVERSATION = 1_000_000  # characters; well under Temporal's 2 MB payload limit
REMINT_MARGIN = 60             # seconds: mint a fresh warrant this close to expiry
ONCE = RetryPolicy(maximum_attempts=1)


@dataclass
class RunInput:
    slug: str
    repo: str
    base: str
    domain: str
    root: str = ""
    code_root: str = ""
    max_turns: int = 15
    token_budget: int = 200_000
    poll_every: int = 1800
    poll_for: int = 14 * 86400
    verify_every: int = 300
    verify_for: int = 1800
    worker_wait: int = 45            # seconds a Tenuo-checked call waits for its worker; under the ~60 s PoP window


@dataclass
class RunResult:
    status: str
    pr_url: str = ""
    proposals: list = field(default_factory=list)
    denied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


@dataclass
class Grant:
    """A phase warrant, plus what is needed to mint it again if it runs out while a worker is down."""
    phase: str
    ctx: dict
    warrant: object
    until: datetime


@workflow.defn
class FixRunWorkflow:
    def __init__(self):
        self.step = "starting"
        self.worker_wait = 45
        self.verify_fixes = []

    @workflow.query
    def current_step(self) -> str:
        return self.step

    async def _warrant(self, phase: str, ctx: dict) -> Grant:
        b64 = await workflow.execute_activity(act.mint_phase, args=[phase, ctx], task_queue=MINTER_QUEUE,
                                              start_to_close_timeout=timedelta(seconds=30), retry_policy=ONCE)
        return Grant(phase, ctx, Warrant.from_base64(b64), workflow.now() + timedelta(seconds=TTL[phase]))

    async def _call(self, fn, args, grant: Grant, queue=EXECUTOR_QUEUE, timeout=60, retry=ONCE):
        """Run a Tenuo-checked activity. The proof of possession is signed when the call is scheduled and is valid for
        about a minute, so a call that no worker picks up in time is withdrawn and scheduled again: a down worker
        makes the run wait instead of turning into a denial."""
        while True:
            if workflow.now() >= grant.until - timedelta(seconds=REMINT_MARGIN):
                fresh = await self._warrant(grant.phase, grant.ctx)
                grant.warrant, grant.until = fresh.warrant, fresh.until
            try:
                return await tenuo_execute_activity(
                    fn, args=args, warrant=grant.warrant, key_id=HOLDER_KEY_ID, task_queue=queue,
                    schedule_to_start_timeout=timedelta(seconds=self.worker_wait),
                    start_to_close_timeout=timedelta(seconds=timeout), retry_policy=retry)
            except ActivityError as e:
                if not (isinstance(e.cause, TimeoutError) and e.cause.type == TimeoutType.SCHEDULE_TO_START):
                    raise
            await workflow.sleep(timedelta(seconds=self.worker_wait))

    def _ctx(self, inp: RunInput, **extra) -> dict:
        return {"slug": inp.slug, "repo": inp.repo, "base": inp.base, "domain": inp.domain, "root": inp.root,
                "code_root": inp.code_root, **extra}

    async def _tool(self, inp: RunInput, call: dict, warrant) -> object:
        name, args = call["name"], call.get("args", {})
        if name == "run_gates":
            return await self._call(act.run_gates, [inp.domain], warrant)
        if name == "homepage_links":
            return await self._call(act.homepage, [inp.domain], warrant)
        if name == "list_files":
            return await self._call(act.list_files, [inp.repo, inp.base, str(args.get("dir", ""))], warrant)
        if name == "read_file":
            return await self._call(act.read_file, [inp.repo, inp.base, str(args.get("path", ""))], warrant)
        raise ValueError(f"unknown tool {name!r}")

    async def _agent(self, inp: RunInput, result: RunResult) -> list[dict]:
        self.step = "agent"
        messages = [{"role": "user", "text": f"Site: https://{inp.domain}/. Improve it with fixes from the catalog."}]
        proposals, tokens = [], 0
        for _ in range(inp.max_turns):
            warrant = await self._warrant("explore", self._ctx(inp))
            conversation = json.dumps({"system": SYSTEM, "messages": messages, "tools": TOOLS})
            if len(conversation) > MAX_CONVERSATION:
                break
            step = await self._call(act.llm_turn, [conversation], warrant, queue=LLM_QUEUE, timeout=600,
                                    retry=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["LLMError"]))
            tokens += step["tokens"]
            messages.append({"role": "assistant", "text": step["text"], "calls": step["calls"], "raw": step["raw"]})
            done = not step["calls"]
            for i, call in enumerate(step["calls"]):
                if i >= MAX_CALLS:
                    out, err = f"not run: at most {MAX_CALLS} tool calls per turn", True
                elif call["name"] == "finish":
                    out, err, done = "ok", False, True
                elif call["name"] == "propose_fix":
                    fix = call.get("args", {})
                    if fix.get("type") in CATALOG:
                        proposals.append({"type": fix["type"], "facts": fix.get("facts", {})})
                        out, err = "recorded", False
                    else:
                        out, err = f"unknown fix type {fix.get('type')!r}", True
                else:
                    try:
                        out, err = json.dumps(await self._tool(inp, call, warrant))[:MAX_RESULT], False
                    except ActivityError as e:
                        cause = e.cause
                        denied = isinstance(cause, ApplicationError) and cause.non_retryable
                        out = f"{'denied' if denied else 'failed'}: {getattr(cause, 'message', None) or cause}"
                        err = True
                        if denied:
                            result.denied.append({"tool": call["name"], "args": call.get("args", {}), "reason": out})
                    except ValueError as e:
                        out, err = str(e), True
                messages.append({"role": "tool", "id": call["id"], "name": call["name"], "result": out,
                                 "is_error": err})
            if done or tokens > inp.token_budget:
                break
        return proposals

    async def _open_pr(self, inp: RunInput, proposals: list[dict], result: RunResult) -> dict | None:
        self.step = "building changes"
        fixes = sorted({p["type"] for p in proposals})
        warrant = await self._warrant("build", self._ctx(inp, fixes=fixes))
        built = await self._call(act.build_changes, [inp.slug, fixes, json.dumps(proposals)], warrant)
        result.skipped = built["skipped"]
        if not built["changes"]:
            return None
        self.step = "opening PR"
        warrant = await self._warrant("write", self._ctx(inp, paths=[c["path"] for c in built["changes"]]))
        branch = "fixagent/agent-" + workflow.now().strftime("%Y%m%d-%H%M%S")
        await self._call(act.create_branch, [inp.repo, inp.base, branch], warrant)
        for c in built["changes"]:
            await self._call(act.write_file, [inp.repo, branch, c["path"], len(c["after"].encode()), c["after"],
                                              c["reason"]], warrant)
        self.verify_fixes = built["verify"]
        body = "\n".join(f"- `{c['path']}`: {c['reason']}" for c in built["changes"])
        if built["notes"]:
            body += "\n\n" + built["notes"]
        return await self._call(act.open_pr, [inp.repo, inp.base, branch, "AI visibility fixes", body], warrant)

    async def _wait_for_merge(self, inp: RunInput, number: int) -> str:
        self.step = "waiting for merge"
        waited = 0
        while waited <= inp.poll_for:
            try:
                warrant = await self._warrant("poll", self._ctx(inp, number=number))
                state = await self._call(act.get_pr_state, [inp.repo, number], warrant,
                                         retry=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                state = None  # GitHub or the minter failed for now: keep waiting, the PR is still open
            if state in ("merged", "closed"):
                return state
            await workflow.sleep(timedelta(seconds=inp.poll_every))
            waited += inp.poll_every
        return "timeout"

    async def _verify(self, inp: RunInput, fixes: list[str]) -> bool:
        self.step = "verifying"
        wanted = {g for f in fixes for g in GATES_FOR_FIX[f]}
        waited = 0
        while waited <= inp.verify_for:
            try:
                warrant = await self._warrant("verify", self._ctx(inp))
                gates = await self._call(act.run_gates, [inp.domain], warrant, retry=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                gates = None  # the check itself failed: try again at the next interval
            if gates is not None and all(g["status"] == "pass" for g in gates if g["gate"] in wanted):
                return True
            await workflow.sleep(timedelta(seconds=inp.verify_every))
            waited += inp.verify_every
        return False

    @workflow.run
    async def run(self, inp: RunInput) -> RunResult:
        result = RunResult(status="")
        self.worker_wait = inp.worker_wait
        try:
            proposals = await self._agent(inp, result)
        except ActivityError as e:
            result.status = f"agent unavailable: {getattr(e.cause, 'message', None) or e.cause}"
            return result
        result.proposals = proposals
        if not proposals:
            result.status = "no changes"
            return result
        pr = await self._open_pr(inp, proposals, result)
        if pr is None:
            result.status = "no changes"
            return result
        result.pr_url = pr["url"]
        state = await self._wait_for_merge(inp, pr["number"])
        if state != "merged":
            result.status = "not merged" if state == "closed" else "no response"
            return result
        if not self.verify_fixes:
            result.status = "merged, no gate to check"
            self.step = "done"
            return result
        live = await self._verify(inp, self.verify_fixes)
        result.status = "verified" if live else "not live"
        self.step = "done"
        return result
