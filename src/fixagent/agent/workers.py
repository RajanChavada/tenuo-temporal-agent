"""Worker processes for the fix agent. Each role holds only its own secret:

- workflow: the holder key (signs proof of possession for each activity call), no GitHub or model access
- minter:   the issuer key, signs phase warrants
- executor: GitHub tokens, runs the site and repo activities after Tenuo checks the warrant
- llm:      the model API key, runs `llm_turn` after Tenuo checks the warrant
"""
import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions
from tenuo.temporal import EnvKeyResolver, InMemoryPopDedupStore, TenuoPluginConfig, TenuoTemporalPlugin

from fixagent.agent import activities as act
from fixagent.agent.workflow import (EXECUTOR_QUEUE, HOLDER_KEY_ID, LLM_QUEUE, MINTER_QUEUE, WORKFLOW_QUEUE,
                                   FixRunWorkflow)

ROLES = ("workflow", "minter", "executor", "llm")
FREE_TEXT = ("text", "message", "title", "body", "conversation", "proposals")  # never leave the process
log = logging.getLogger(__name__)


def redact(args: dict) -> dict:
    """Arguments with free text (file text, PR text, the model conversation) replaced by its size."""
    return {k: f"<{len(str(v).encode())} bytes>" if k in FREE_TEXT else v for k, v in args.items()}


class _Redacted:
    """A Tenuo decision whose arguments are redacted; every other attribute reads through."""

    def __init__(self, result):
        self._result = result

    def __getattr__(self, name):
        value = getattr(self._result, name)
        return redact(value) if name in ("arguments", "clean_arguments") and isinstance(value, dict) else value


class RedactingCloud:
    """Tenuo Cloud receipts without free text. Falsy without a client, so the plugin neither emits nor creates one."""

    def __init__(self, client=None):
        self.client = client

    def __bool__(self):
        return self.client is not None

    def emit_for_enforcement(self, result, **kwargs):
        try:
            self.client.emit_for_enforcement(_Redacted(result), **kwargs)
        except Exception:  # noqa: BLE001 - a receipt failure never changes a decision
            log.warning("Tenuo Cloud receipt failed", exc_info=True)


def set_holder_key(key) -> None:
    """Expose the holder signing key to Tenuo's EnvKeyResolver, in this process only."""
    os.environ[f"TENUO_KEY_{HOLDER_KEY_ID}"] = base64.b64encode(bytes(key.secret_key_bytes())).decode()


def tenuo_client(client: Client, issuer_public_key, cloud=None, audit=None) -> Client:
    """A copy of `client` with its own Tenuo plugin. Each worker needs its own plugin instance.

    `cloud` is a RedactingCloud; without one Tenuo would create its own client from the environment and send full
    arguments. `audit` receives each decision (TemporalAuditEvent)."""
    plugin = TenuoTemporalPlugin(TenuoPluginConfig(
        key_resolver=EnvKeyResolver(), trusted_roots=[issuer_public_key],
        activity_fns=act.EXECUTOR_ACTIVITIES + act.LLM_ACTIVITIES, pop_dedup_store=InMemoryPopDedupStore(),
        control_plane=cloud or RedactingCloud(), audit_callback=audit,
        redact_args_in_logs=False))  # Tenuo would blank every value; RedactingCloud and redact() drop only free text
    config = client.config()
    config["plugins"] = [plugin]
    return Client(**config)


def worker(role: str, client: Client, issuer_public_key, cloud=None, audit=None) -> Worker:
    """The worker for one role. The minter runs on the plain client: its activity is not warrant-checked."""
    if role == "minter":
        return Worker(client, task_queue=MINTER_QUEUE, activities=act.MINTER_ACTIVITIES,
                      activity_executor=ThreadPoolExecutor(4))
    tc = tenuo_client(client, issuer_public_key, cloud, audit)
    if role == "workflow":
        # beartype's import hook (loaded by fastmcp) breaks sandboxed re-imports, so it passes through
        runner = SandboxedWorkflowRunner(restrictions=SandboxRestrictions.default.with_passthrough_modules("beartype"))
        return Worker(tc, task_queue=WORKFLOW_QUEUE, workflows=[FixRunWorkflow], workflow_runner=runner)
    if role == "executor":
        return Worker(tc, task_queue=EXECUTOR_QUEUE, activities=act.EXECUTOR_ACTIVITIES,
                      activity_executor=ThreadPoolExecutor(8))
    if role == "llm":
        return Worker(tc, task_queue=LLM_QUEUE, activities=act.LLM_ACTIVITIES,
                      activity_executor=ThreadPoolExecutor(2))
    raise ValueError(f"unknown worker role {role!r}, expected one of {', '.join(ROLES)}")
