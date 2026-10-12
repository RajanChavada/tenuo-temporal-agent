import asyncio
from pathlib import Path

import typer
from dotenv import load_dotenv

from fixagent import local
from fixagent.config import load_brand
from fixagent.execute import keys
from fixagent.gates import _get as live_get, check_gates

load_dotenv()
app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Tenuo-guarded fix agent on Temporal."""


@app.command()
def gates(slug: str) -> None:
    """Check whether AI crawlers can reach and read the brand's site."""
    for r in check_gates(load_brand(slug).domain, get=local.fetch if local.enabled() else live_get):
        typer.echo(f"{r['status']:<8} {r['gate']:<22} {r['detail']}")


keys_app = typer.Typer(no_args_is_help=True)
app.add_typer(keys_app, name="keys")


@keys_app.command("init")
def keys_init() -> None:
    """Create the issuer and holder keys in the macOS Keychain, or in files in local mode (first run only) and print the issuer public key."""
    key = keys.init_issuer()
    keys.init_holder()
    typer.echo(f"Issuer public key: {bytes(key.public_key.to_bytes()).hex()}")


@keys_app.command("github")
def keys_github(repo: str) -> None:
    """Store a GitHub token for one repo in the macOS Keychain, or in files in local mode. Only the executor worker reads it."""
    token = typer.prompt(f"GitHub token for {repo}", hide_input=True)
    if not token.strip():
        typer.echo("no token entered")
        raise typer.Exit(1)
    keys.set_github_token(repo, token.strip())
    typer.echo(f"Stored the token for {repo}.")


demo_app = typer.Typer(no_args_is_help=True)
app.add_typer(demo_app, name="demo", help="Local mode: the demo site as a local git repo, no GitHub or hosting.")


@demo_app.command("init")
def demo_init(reset: bool = typer.Option(False, "--reset", help="Start over: delete the site repo and its pull requests")) -> None:
    """Create the keys and the demo site repo in .fixagent/. Needs FIXAGENT_LOCAL=1."""
    if not local.enabled():
        typer.echo("stopped: set FIXAGENT_LOCAL=1 (cp .env.example .env)")
        raise typer.Exit(1)
    keys.init_issuer()
    keys.init_holder()
    _stopped_on_error(lambda: local.init_site(reset))
    typer.echo(f"Site repo ready in {local.SITE_DIR}. Next: uv run honcho start")


pr_app = typer.Typer(no_args_is_help=True)
app.add_typer(pr_app, name="pr", help="Local mode: review and merge the agent's pull requests.")


@pr_app.command("merge")
def pr_merge(number: int) -> None:
    """Show a pull request's diff and merge it into main. A person merges: no warrant lets the agent."""
    git = local.LocalGit()
    typer.echo(_stopped_on_error(lambda: git.diff(number)))
    if not typer.confirm(f"Merge pull request {number}?"):
        raise typer.Exit(1)
    _stopped_on_error(lambda: git.merge(number))
    typer.echo(f"Merged. {local.SITE_URL} serves it now.")


fixrun_app = typer.Typer()
app.add_typer(fixrun_app, name="fixrun", help="Durable fix agent runs on Temporal.")


@app.command()
def temporal(db_file: str = typer.Option(".temporal/dev.db", help="Where the local server keeps its history")) -> None:
    """Run a local Temporal server (UI on http://localhost:8233) until Ctrl-C."""
    from fixagent.agent.run import dev_server
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(dev_server(db_file))


@app.command()
def worker(role: str = typer.Argument(..., help="workflow, minter, executor or llm"),
           demo: bool = typer.Option(False, "--demo", help="llm only: play the scripted demo agent, no model API")) -> None:
    """Run one fix-agent worker. Each role loads only its own secret."""
    from fixagent.agent.run import run_worker
    from fixagent.agent.workers import ROLES
    if role not in ROLES:
        raise typer.BadParameter(f"expected one of {', '.join(ROLES)}")
    if demo and role != "llm":
        raise typer.BadParameter("--demo applies to the llm worker only")
    _stopped_on_error(lambda: asyncio.run(run_worker(role, demo)))


@fixrun_app.command("start")
def fixrun_start(slug: str,
                 demo: bool = typer.Option(False, "--demo", help="Check for the merge and the live fix every 15 s")) -> None:
    """Start the fix agent for one site: explore, open a PR, wait for the merge, verify."""
    from fixagent.agent import run
    result = _temporal(lambda c: run.start(c, run.run_input(slug, demo=demo)))
    typer.echo(f"{result}: {run.run_id(slug)} (watch it at http://localhost:8233)")


@fixrun_app.command("status")
def fixrun_status(slug: str) -> None:
    """Show where a site's fix run is, or how it ended."""
    from fixagent.agent import run
    typer.echo(_temporal(lambda c: run.status(c, slug)))


@fixrun_app.command("watch")
def fixrun_watch(slug: str) -> None:
    """Follow a run live: each step, and every Tenuo decision in green or red."""
    from fixagent.agent import run

    async def follow(client):
        async for kind, data in run.watch(client, slug):
            typer.echo(watch_line(kind, data))
    _temporal(follow)


WATCH_HIDDEN = ("repo", "ref", "base", "conversation", "text", "message", "title", "body", "proposals")  # same every call, or free text


def watch_line(kind: str, data) -> str:
    if kind == "step":
        return typer.style(f"\n> {data}", bold=True)
    if kind == "done":
        return typer.style(f"\n{data}", bold=True)
    args = "  ".join(f"{k}={v}" for k, v in data["args"].items() if k not in WATCH_HIDDEN)
    if data["tool"] == "llm_turn":
        args = "model turn"
    if kind == "allow":
        return typer.style("  ALLOWED ", fg="green", bold=True) + f" {data['tool']:<14} {args}"
    reason = (data.get("reason") or "").split(":")[0][:60]
    return (typer.style("  DENIED  ", fg="red", bold=True) + f" {data['tool']:<14} {args}"
            + typer.style(f"   {reason}", fg="red"))


@fixrun_app.command("schedule")
def fixrun_schedule(slug: str, every: int = typer.Option(7, "--every", min=1, help="Days between runs")) -> None:
    """Start a fix run every N days. A start is skipped while the previous run is open."""
    from fixagent.agent import run
    result = _temporal(lambda c: run.schedule(c, run.run_input(slug), every))
    typer.echo(f"{result}: {run.run_id(slug)} every {every} days")


def _stopped_on_error(fn):
    """Operator errors (Temporal not running, unknown slug, missing key) print one line instead of a traceback."""
    from temporalio.service import RPCError
    try:
        return fn()
    except (RuntimeError, RPCError, FileNotFoundError, ValueError) as e:
        typer.echo(f"stopped: {e}")
        raise typer.Exit(1)


def _temporal(fn):
    from fixagent.agent.run import connect

    async def call():
        return await fn(await connect())
    return _stopped_on_error(lambda: asyncio.run(call()))
