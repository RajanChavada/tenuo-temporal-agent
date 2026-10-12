# Tenuo + Temporal: lessons

Things learned wiring Tenuo 0.3.1 (`tenuo[temporal]`) into Temporal workers. Each item points to the code that handles it.

## Proof of possession and timing

- **PoP is valid about 60 s from when the call is scheduled.** The verifier uses 30 s windows and accepts ±5 of them (±60 s, per `tenuo/temporal/_constants.py`). A call that sits in the task queue longer than that is denied, so a worker outage turns into a denial.
  - Fix: `schedule_to_start_timeout=45s`, catch the `SCHEDULE_TO_START` timeout, sleep, and schedule again with a fresh PoP. Re-mint the warrant first if it is within 60 s of expiry. See `FixRunWorkflow._call` in `agent/workflow.py`.
- **Tests need a real local Temporal server.** PoP signatures carry wall-clock timestamps, so the time-skipping test server breaks them. `tests/test_agent_workflow.py` uses `WorkflowEnvironment.start_local()` with timings shortened to seconds.
- **Replay protection.** On Temporal the plugin takes a `pop_dedup_store` (`InMemoryPopDedupStore` in `agent/workers.py`).

## Warrants and arguments

- **Activity parameter names must match warrant capability argument names exactly.** Tenuo denies unknown or missing arguments. `agent/phases.py` and `agent/activities.py` say so at the top; keep them in sync.
- **Tenuo cannot constrain nested arguments.** `llm_turn` takes the whole conversation as one JSON string (`conversation`), granted as `Wildcard()`.
- **Free text is `Wildcard`, but still signed.** File text, commit messages and PR text are unconstrained in the warrant, but the holder's PoP signature covers their exact values. A separate `size` argument is `Range(max=...)`, and the activity checks it against the text. See `_write_caps` in `execute/warrant.py`.
- **Path constraints are regex, composed with `All` and `Not`.** See `readable()` in `agent/phases.py`:
  - `Regex(prefix)`: under the allowed roots only.
  - `Not(Regex(SECRET))`: `.env*`, `.git`, `.ssh`, secrets, credentials, private keys, in any folder or case.
  - `Not(Regex(PARENT))`: no `..` segment.
  - `SAFE_CHARS` has no `%`, so a percent-encoded `..` or `/` cannot pass the prefix check.
- **The workflow never sees a signing key.** `tenuo_execute_activity(fn, warrant=..., key_id="workflow")` resolves the holder key through `EnvKeyResolver`, which reads `TENUO_KEY_<key_id>` from the environment. `set_holder_key` sets it in the workflow worker process only.

## The plugin

- **One `TenuoTemporalPlugin` instance per worker.** `tenuo_client()` in `agent/workers.py` copies the Temporal client config and attaches a new plugin for each worker.
- **Without a `control_plane`, the plugin creates its own Tenuo Cloud client from the environment and sends full arguments.** Pass a wrapper instead. `RedactingCloud` replaces free text with its byte size, and is falsy when there is no real client, so the plugin neither emits nor creates one.
- **`redact_args_in_logs` defaults to blanking every value.** That hides the repo, path and branch you need to understand a denial. This repo sets `redact_args_in_logs=False` and redacts only free text itself (`redact()` and `RedactingCloud`).
- **The minter runs on a plain client.** Its activity signs warrants and is not warrant-checked, so it gets no plugin.

## Tenuo Cloud client (0.3.1)

- **Registration takes about 0.4 s, and events sent before it are dropped.** `cloud_client()` in `execute/warrant.py` waits up to 10 s for `authorizer_id`.
- **Audit events go out on a 10 s timer, and `shutdown()` panics with "no reactor running".** A short-lived process must wait about 11 s before exiting instead of calling `shutdown()`.
- **The panic is a pyo3 `PanicException`, which is a `BaseException`.** `except Exception` does not catch it. Cloud calls catch `BaseException` but re-raise `KeyboardInterrupt` and `SystemExit` (`_FATAL`).
