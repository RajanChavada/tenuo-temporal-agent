# Design notes

How the fix agent is put together, and why. Terse on purpose.

## 1. Local enforcement is the guardrail

- Every tool call passes a Tenuo warrant check offline, on the worker, whether or not Tenuo Cloud is configured.
- A cloud error, at setup or per call, falls back to local enforcement and is logged as a warning. A receipt failure never changes a decision.
- Every decision, allow or deny, is written to `data/agent-audit.jsonl`.

## 2. One secret per process

- Four Temporal workers, each loading only its own secret (`agent/run.py: configure_role`):
  - `workflow`: the holder key. Signs proof of possession for each activity call. No GitHub or model access.
  - `minter`: the issuer key. Signs phase warrants. Its activity is not warrant-checked; it enforces its own policy.
  - `executor`: GitHub tokens, one per repo. Runs site and repo activities after Tenuo checks the warrant.
  - `llm`: the model API key. Runs `llm_turn` after Tenuo checks the warrant.
- Secrets live in the macOS Keychain (`fixagent keys init`, `fixagent keys github <repo>`).
- `keys.put` passes the secret to `security add-generic-password` on stdin, so it never shows in the process list.
- `tests/test_execute_isolation.py` fails if any module other than the expected ones reads a GitHub token, names the GitHub API, or reads the issuer signing key, and checks no worker role loads another role's secret.

## 3. A fresh, narrow warrant per phase

| Phase | Allowed | TTL |
| --- | --- | --- |
| explore (per model turn) | `run_gates(domain)`, `homepage(domain)`, `list_files` and `read_file` on the base branch under the site roots, `llm_turn` | 15 min |
| build | `build_changes(slug, fixes)` for catalog fix types | 5 min |
| write | `create_branch`, `write_file` on exactly the built paths, `open_pr` into the base branch | 5 min |
| poll | `get_pr_state(repo, number)` for one PR | 5 min |
| verify | `run_gates(domain)` | 35 min |

- A run lasts days (it waits for a human merge), but each warrant lives minutes.
- The minter refuses repos that are not onboarded, bases that start with the agent's branch prefix, fix types outside the catalog, and write paths that are not catalog fix paths.
- Branches the agent may create: `fixagent/<name>` only, no `..`. The executor activities also check this themselves, whatever a warrant says.
- No warrant ever grants merge, push to the base branch, or delete. A person merges.

## 4. Read scope

- The model reads files under `site.root` and `site.code_root`. An empty value means the whole repo.
- Secret-looking paths (`.env*`, `.git`, `.ssh`, `secrets/`, credentials, private keys, `*.pem`, `.npmrc`) are denied either way, in any folder or case.
- `..` segments are denied. Path characters are an allowlist with no `%`, so an encoded `..` or `/` cannot slip past.
- The explore phase is refused if `root` or `code_root` is missing from the request, so a missing root cannot widen reads to the whole repo.

## 5. AI fills, code writes

- The model can only read (gates, homepage links, files) and call `propose_fix(type, facts)` for a catalog fix type.
- `build_changes` writes every byte. The model never writes file text.
- Catalog:
  - `site-files`: unblock AI search bots in `robots.txt` (only the groups that name them) and add `llms.txt` from the brand file. A generated `robots.txt` that changes any other crawler's access is not shipped.
  - `org-schema`: add schema.org Organization JSON-LD to a Next.js `app/layout.(tsx|jsx)`.
- For `org-schema`, the model sees the homepage title and its `href`/`src` list, not raw HTML, as untrusted data.
- Facts must be backed by the page: name and url come from the brand file, the logo must be on the site's domain, each `sameAs` link must be https, on a known profile site and linked from the homepage. Anything else is listed in the PR as left out, with the reason.

## 6. Temporal runs the agent

- `FixRunWorkflow`, one open run per site (workflow id `fixrun-<slug>`).
- Part A, agent loop: the model picks the next tool each turn. Capped at 15 turns, a token budget, and 8 tool calls per turn.
- Part B, fixed steps the model cannot skip: build, open one PR, poll for the merge (every 30 min, up to 14 days), then check the fixed gates on the live site (every 5 min, up to 30 min).
- A denied tool call goes back to the model as an error result and is recorded in the run result. A failed call (timeout, GitHub 5xx) is returned as `failed:`, not counted as a denial.
- A down worker makes the run wait instead of failing: a call no worker picks up within 45 s is withdrawn and scheduled again, with a fresh warrant if the old one is near expiry.
- `fixagent fixrun schedule --every N` starts a run every N days; a start is skipped while the previous run is open.

## 7. What leaves the process

- Audit log and Tenuo Cloud receipts keep repo, paths, branches and sizes, so a denial shows what was tried.
- File text, commit and PR text, proposals and the model conversation are reduced to their byte size.
- Tenuo Cloud is optional (`TENUO_CONNECT_TOKEN` or `TENUO_API_KEY`). Without it, nothing leaves the machine except GitHub and model API calls.

## 8. Any model

- The loop talks to `next_step(system, messages, tools)`.
- `AnthropicLLM` is the first adapter (`claude-opus-5-5` with server-side fallbacks; `FIX_MODEL` overrides).
- `ScriptedLLM` drives tests; `DemoLLM` plays a fixed script that falls for the planted note, for recorded demos with no API spend.

## 9. Older MCP path

- `execute/executor.py` and `execute/minter.py` are an earlier, non-Temporal design: an executor and a minter as separate MCP stdio processes, with `TenuoMiddleware` and a `NonceStore`.
- The Temporal agent reuses their policy (`SAFE_BRANCH`, `FIX_TYPES`, path rules, `_write_caps`). The MCP servers themselves are not started by anything in this repo.

## 10. Limits

- All four workers run as one OS user. Someone with a shell as that user can read every Keychain item. The next step is a separate user or host per role.
- macOS Keychain only (`/usr/bin/security`).
- Workflow tests run on a real local Temporal server, not the time-skipping one, because proof-of-possession signatures use wall-clock time.
