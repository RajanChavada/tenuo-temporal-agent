"""Secrets for the fix agent: in the macOS Keychain through the `security` command, or in 0600 files in local mode and on other systems."""
import base64
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from tenuo import PublicKey, SigningKey

ISSUER_SERVICE = "fixagent-tenuo-issuer"
ISSUER_ACCOUNT = "default"
ISSUER_PUB_SERVICE = "fixagent-tenuo-issuer-pub"  # public half, readable without the signing key
HOLDER_SERVICE = "fixagent-tenuo-holder"  # the Temporal workflow worker's holder key
HOLDER_PUB_SERVICE = "fixagent-tenuo-holder-pub"
GITHUB_SERVICE = "fixagent-github"
SECURITY = "/usr/bin/security"  # absolute, so a PATH entry cannot substitute the Keychain tool
NOT_FOUND = 44  # `security` exit code when no item matches
KEY_DIR = Path(".fixagent/keys")  # file store: one 0600 file per secret, so each role still reads only its own


def _file_store() -> bool:
    return os.environ.get("FIXAGENT_LOCAL") == "1" or sys.platform != "darwin"


def _key_file(service: str, account: str) -> Path:
    return KEY_DIR / service / quote(account, safe="")  # repo names contain "/"


class MissingKey(RuntimeError):
    """A secret the command needs is not set. The message says which command sets it."""


def _run(args: list[str], input: str | None = None) -> subprocess.CompletedProcess:
    # New session: with a terminal attached, `security -w` would prompt on the terminal and ignore stdin.
    return subprocess.run(args, input=input, capture_output=True, text=True, start_new_session=True)


def get(service: str, account: str, run=_run) -> str | None:
    if _file_store():
        f = _key_file(service, account)
        return f.read_text().strip() if f.exists() else None
    r = run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"])
    if r.returncode == NOT_FOUND:
        return None
    if r.returncode != 0:
        raise RuntimeError(f"Keychain read failed for {service}/{account}: {r.stderr.strip()}")
    return r.stdout.strip()


def exists(service: str, account: str, run=_run) -> bool:
    """True when the item exists. Never asks for the secret (no `-w`)."""
    if _file_store():
        return _key_file(service, account).exists()
    r = run([SECURITY, "find-generic-password", "-s", service, "-a", account])
    if r.returncode == NOT_FOUND:
        return False
    if r.returncode != 0:
        raise RuntimeError(f"Keychain lookup failed for {service}/{account}: {r.stderr.strip()}")
    return True


def put(service: str, account: str, secret: str, run=_run) -> None:
    if _file_store():
        f = _key_file(service, account)
        f.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        with os.fdopen(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(secret)
        return
    # The secret goes on stdin, entered twice as `security` asks, so it never shows in the process list.
    r = run([SECURITY, "add-generic-password", "-s", service, "-a", account, "-U", "-w"], input=f"{secret}\n{secret}\n")
    if r.returncode != 0:
        raise RuntimeError(f"Keychain write failed for {service}/{account}: {r.stderr.strip()}")


def issuer_key(run=_run) -> SigningKey:
    raw = get(ISSUER_SERVICE, ISSUER_ACCOUNT, run)
    if raw is None:
        raise MissingKey("run: fixagent keys init")
    return SigningKey.from_bytes(base64.b64decode(raw))


def issuer_public_key(run=_run) -> PublicKey:
    raw = get(ISSUER_PUB_SERVICE, ISSUER_ACCOUNT, run)
    if raw is None:
        raise MissingKey("run: fixagent keys init")
    return PublicKey.from_bytes(bytes.fromhex(raw))


def init_issuer(run=_run) -> SigningKey:
    """Return the issuer key, creating it on first use, and make sure its public half is stored.

    Never replaces an existing signing key.
    """
    try:
        key = issuer_key(run)
    except MissingKey:
        key = SigningKey.generate()
        put(ISSUER_SERVICE, ISSUER_ACCOUNT, base64.b64encode(bytes(key.secret_key_bytes())).decode(), run)
    public_hex = bytes(key.public_key.to_bytes()).hex()
    if get(ISSUER_PUB_SERVICE, ISSUER_ACCOUNT, run) != public_hex:
        put(ISSUER_PUB_SERVICE, ISSUER_ACCOUNT, public_hex, run)
    return key


def holder_key(run=_run) -> SigningKey:
    raw = get(HOLDER_SERVICE, ISSUER_ACCOUNT, run)
    if raw is None:
        raise MissingKey("run: fixagent keys init")
    return SigningKey.from_bytes(base64.b64decode(raw))


def holder_public_key(run=_run) -> PublicKey:
    raw = get(HOLDER_PUB_SERVICE, ISSUER_ACCOUNT, run)
    if raw is None:
        raise MissingKey("run: fixagent keys init")
    return PublicKey.from_bytes(bytes.fromhex(raw))


def init_holder(run=_run) -> SigningKey:
    """The workflow worker's holder key, created on first use and never replaced; its public half is stored too."""
    try:
        key = holder_key(run)
    except MissingKey:
        key = SigningKey.generate()
        put(HOLDER_SERVICE, ISSUER_ACCOUNT, base64.b64encode(bytes(key.secret_key_bytes())).decode(), run)
    public_hex = bytes(key.public_key.to_bytes()).hex()
    if get(HOLDER_PUB_SERVICE, ISSUER_ACCOUNT, run) != public_hex:
        put(HOLDER_PUB_SERVICE, ISSUER_ACCOUNT, public_hex, run)
    return key


def github_token(repo: str, run=_run) -> str:
    token = get(GITHUB_SERVICE, repo, run)
    if token is None:
        raise MissingKey(f"run: fixagent keys github {repo}")
    return token


def is_onboarded(repo: str, run=_run) -> bool:
    """A repo is onboarded once `fixagent keys github <repo>` stored its token."""
    return exists(GITHUB_SERVICE, repo, run)


def set_github_token(repo: str, token: str, run=_run) -> None:
    put(GITHUB_SERVICE, repo, token, run)
