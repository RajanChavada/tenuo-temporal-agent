import os
import stat
import subprocess

import pytest

from fixagent.execute import keys


@pytest.fixture(autouse=True)
def keychain(monkeypatch):
    """The Keychain tests use FakeKeychain; file-store tests switch this back on."""
    monkeypatch.setattr(keys, "_file_store", lambda: False)


@pytest.fixture
def file_store(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "_file_store", lambda: True)
    monkeypatch.setattr(keys, "KEY_DIR", tmp_path / "keys")
    return tmp_path / "keys"


def test_file_store_round_trips_and_is_private(file_store):
    assert keys.get(keys.GITHUB_SERVICE, "acme/site") is None and not keys.exists(keys.GITHUB_SERVICE, "acme/site")
    keys.put(keys.GITHUB_SERVICE, "acme/site", "token-1")
    keys.put(keys.GITHUB_SERVICE, "acme/site", "token-2")
    assert keys.get(keys.GITHUB_SERVICE, "acme/site") == "token-2" and keys.exists(keys.GITHUB_SERVICE, "acme/site")
    f = file_store / keys.GITHUB_SERVICE / "acme%2Fsite"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600 and stat.S_IMODE(os.stat(file_store).st_mode) == 0o700


def test_file_store_keeps_one_file_per_secret(file_store):
    issuer = keys.init_issuer()
    keys.init_holder()
    assert bytes(keys.issuer_public_key().to_bytes()) == bytes(issuer.public_key.to_bytes())
    assert sorted(p.parent.name for p in file_store.rglob("*") if p.is_file()) == sorted(
        [keys.ISSUER_SERVICE, keys.ISSUER_PUB_SERVICE, keys.HOLDER_SERVICE, keys.HOLDER_PUB_SERVICE])


def test_file_store_is_used_off_macos_or_in_local_mode(monkeypatch):
    monkeypatch.undo()  # drop the autouse pin, test the real switch
    monkeypatch.delenv("FIXAGENT_LOCAL", raising=False)
    monkeypatch.setattr(keys.sys, "platform", "linux")
    assert keys._file_store()
    monkeypatch.setattr(keys.sys, "platform", "darwin")
    assert not keys._file_store()
    monkeypatch.setenv("FIXAGENT_LOCAL", "1")
    assert keys._file_store()


class FakeKeychain:
    """Stands in for the `security` command: stores items in a dict and records every call."""

    def __init__(self):
        self.items, self.calls = {}, []

    def __call__(self, args, input=None):
        self.calls.append(args)
        op, service, account = args[1], args[args.index("-s") + 1], args[args.index("-a") + 1]
        if op == "add-generic-password":
            assert args[-1] == "-w"
            # Secret comes from stdin, first line (entered twice, but we take one).
            secret = input.split("\n")[0] if input else ""
            self.items[(service, account)] = secret
            return subprocess.CompletedProcess(args, 0, "", "")
        if (service, account) not in self.items:
            return subprocess.CompletedProcess(args, 44, "", "The specified item could not be found")
        return subprocess.CompletedProcess(args, 0, self.items[(service, account)] + "\n", "")


def test_init_issuer_creates_once_and_never_replaces():
    kc = FakeKeychain()
    first = keys.init_issuer(kc)
    second = keys.init_issuer(kc)
    assert bytes(first.public_key_bytes()) == bytes(second.public_key_bytes())
    assert [c[1] for c in kc.calls].count("add-generic-password") == 2  # signing key + public key, first run only
    assert bytes(keys.issuer_key(kc).public_key_bytes()) == bytes(first.public_key_bytes())
    assert bytes(keys.issuer_public_key(kc).to_bytes()) == bytes(first.public_key_bytes())


def test_init_issuer_adds_a_missing_public_key_without_touching_the_signing_key():
    kc = FakeKeychain()
    first = keys.init_issuer(kc)
    del kc.items[(keys.ISSUER_PUB_SERVICE, keys.ISSUER_ACCOUNT)]
    signing_before = kc.items[(keys.ISSUER_SERVICE, keys.ISSUER_ACCOUNT)]
    keys.init_issuer(kc)
    assert kc.items[(keys.ISSUER_SERVICE, keys.ISSUER_ACCOUNT)] == signing_before
    assert bytes(keys.issuer_public_key(kc).to_bytes()) == bytes(first.public_key_bytes())


def test_init_holder_creates_once_and_stores_public_half():
    kc = FakeKeychain()
    first = keys.init_holder(kc)
    keys.init_holder(kc)
    assert [c[1] for c in kc.calls].count("add-generic-password") == 2
    assert bytes(keys.holder_key(kc).public_key_bytes()) == bytes(first.public_key_bytes())
    assert bytes(keys.holder_public_key(kc).to_bytes()) == bytes(first.public_key_bytes())


def test_missing_holder_key_says_which_command_to_run():
    with pytest.raises(keys.MissingKey, match="run: fixagent keys init"):
        keys.holder_key(FakeKeychain())


def test_missing_issuer_public_key_says_which_command_to_run():
    with pytest.raises(keys.MissingKey, match="run: fixagent keys init"):
        keys.issuer_public_key(FakeKeychain())


def test_onboarded_check_never_reads_the_secret():
    kc = FakeKeychain()
    keys.set_github_token("acme/site", "fake_token_1", kc)
    kc.calls.clear()
    assert keys.is_onboarded("acme/site", kc) and not keys.is_onboarded("other/repo", kc)
    assert all("-w" not in call for call in kc.calls)


def test_missing_issuer_says_which_command_to_run():
    with pytest.raises(keys.MissingKey, match="run: fixagent keys init"):
        keys.issuer_key(FakeKeychain())


def test_github_token_is_stored_per_repo():
    kc = FakeKeychain()
    keys.set_github_token("acme/site", "fake_token_1", kc)
    assert keys.github_token("acme/site", kc) == "fake_token_1"
    with pytest.raises(keys.MissingKey, match="run: fixagent keys github other/repo"):
        keys.github_token("other/repo", kc)


def test_other_keychain_errors_are_raised():
    def broken(args, input=None):
        return subprocess.CompletedProcess(args, 51, "", "User interaction is not allowed.")
    with pytest.raises(RuntimeError, match="User interaction is not allowed"):
        keys.github_token("acme/site", broken)


def test_secret_never_appears_in_command_arguments():
    kc = FakeKeychain()
    keys.set_github_token("acme/site", "fake_token_hidden", kc)
    assert all("fake_token_hidden" not in a for call in kc.calls for a in call)
    assert keys.github_token("acme/site", kc) == "fake_token_hidden"


def test_security_is_called_by_absolute_path():
    kc = FakeKeychain()
    keys.set_github_token("acme/site", "tok", kc)
    keys.github_token("acme/site", kc)
    keys.is_onboarded("acme/site", kc)
    assert kc.calls and all(call[0] == "/usr/bin/security" for call in kc.calls)


def test_security_runs_without_the_terminal(monkeypatch):
    """With a terminal attached, `security -w` reads the secret from the terminal instead of stdin.

    Running it in a new session (no controlling terminal) makes it read the stdin we pass.
    """
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(keys.subprocess, "run", fake_run)
    keys.set_github_token("acme/site", "fake_token_1")
    assert seen["start_new_session"] is True and seen["input"] == "fake_token_1\nfake_token_1\n"
