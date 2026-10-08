import time

import pytest
from tenuo import Authorizer, Exact, OneOf, SigningKey, Warrant
from tenuo.exceptions import TenuoError

from fixagent.execute.warrant import job_warrant, read_warrant, write_warrant

REPO, BASE, PATHS = "acme/site", "main", ["robots.txt", "llms.txt"]
WRITE = {"repo": REPO, "branch": "fixagent/x", "path": "llms.txt", "size": 3, "text": "abc", "message": "m"}


@pytest.fixture
def chain():
    issuer, job_key, read_key, write_key = (SigningKey.generate() for _ in range(4))
    job = job_warrant(issuer, job_key.public_key, REPO, BASE, PATHS)
    return {"issuer": issuer, "job": job, "job_key": job_key, "read_key": read_key, "write_key": write_key,
            "read": read_warrant(job, job_key, read_key.public_key, REPO, PATHS),
            "write": write_warrant(job, job_key, write_key.public_key, REPO, BASE, ["llms.txt"])}


def check(issuer, warrants, key, tool, args):
    sig = warrants[-1].sign(key, tool, args, int(time.time()))
    Authorizer(trusted_roots=[issuer.public_key]).check_chain(warrants, tool, args, signature=sig)


def test_read_warrant_can_only_read(chain):
    check(chain["issuer"], [chain["job"], chain["read"]], chain["read_key"], "read_file", {"repo": REPO, "path": "robots.txt"})
    with pytest.raises(TenuoError):
        check(chain["issuer"], [chain["job"], chain["read"]], chain["read_key"], "write_file", WRITE)


def test_write_warrant_covers_only_changed_paths(chain):
    check(chain["issuer"], [chain["job"], chain["write"]], chain["write_key"], "write_file", WRITE)
    with pytest.raises(TenuoError):
        check(chain["issuer"], [chain["job"], chain["write"]], chain["write_key"], "write_file", dict(WRITE, path="robots.txt"))
    with pytest.raises(TenuoError):
        check(chain["issuer"], [chain["job"], chain["write"]], chain["write_key"], "read_file", {"repo": REPO, "path": "llms.txt"})


def test_signature_covers_free_text(chain):
    sig = chain["write"].sign(chain["write_key"], "write_file", WRITE, int(time.time()))
    with pytest.raises(TenuoError):
        Authorizer(trusted_roots=[chain["issuer"].public_key]).check_chain(
            [chain["job"], chain["write"]], "write_file", dict(WRITE, text="xyz"), signature=sig)


def test_child_cannot_widen_the_job(chain):
    with pytest.raises(TenuoError):
        write_warrant(chain["job"], chain["job_key"], chain["write_key"].public_key, REPO, BASE, ["index.html"])


def test_chain_must_start_at_the_trusted_issuer(chain):
    rogue = SigningKey.generate()
    fake = Warrant.mint_builder().capability("read_file", repo=Exact(REPO), path=OneOf(PATHS)) \
        .holder(rogue.public_key).ttl(60).mint(rogue)
    with pytest.raises(TenuoError):
        check(chain["issuer"], [fake], rogue, "read_file", {"repo": REPO, "path": "robots.txt"})


def test_phase_key_must_match_holder(chain):
    with pytest.raises(TenuoError):
        check(chain["issuer"], [chain["job"], chain["read"]], chain["write_key"], "read_file", {"repo": REPO, "path": "robots.txt"})


def test_job_refuses_base_under_branch_prefix():
    with pytest.raises(ValueError):
        job_warrant(SigningKey.generate(), SigningKey.generate().public_key, REPO, "fixagent/main", PATHS)
