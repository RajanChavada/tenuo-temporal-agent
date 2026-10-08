from pathlib import Path
import yaml
from fixagent.config import load_brand

def test_load_brand_example():
    b = load_brand("example", Path("brands"))
    assert b.name == "Acme" and b.domain == "acme.dev"
    assert [c.name for c in b.competitors] == ["Beacon", "Lumen"]
    assert [c.name for c in b.all_names()] == ["Acme", "Beacon", "Lumen"]
    assert b.all_names()[0].aliases == ["Acme Tracing"]

def test_load_brand_sender_disclosure_defaults_empty():
    assert load_brand("example", Path("brands")).sender_disclosure == ""

def test_load_brand_reads_sender_disclosure_when_present(tmp_path):
    data = {"slug": "acme", "name": "Acme", "domain": "acme.dev", "aliases": [], "one_liner": "x",
            "category": "y", "competitors": [], "sender_disclosure": "Sent by Acme's founder."}
    (tmp_path / "acme.yaml").write_text(yaml.safe_dump(data))
    b = load_brand("acme", tmp_path)
    assert b.sender_disclosure == "Sent by Acme's founder."

def test_load_brand_reads_sender_name_and_claims(tmp_path):
    data = {"slug": "acme", "name": "Acme", "domain": "acme.dev", "aliases": [], "one_liner": "x", "category": "y",
            "competitors": [], "sender_name": "Sam", "claims": ["None of the listed tools estimate cost before shipping."]}
    (tmp_path / "acme.yaml").write_text(yaml.safe_dump(data))
    b = load_brand("acme", tmp_path)
    assert b.sender_name == "Sam" and b.claims == ["None of the listed tools estimate cost before shipping."]
    assert load_brand("example", Path("brands")).claims == []
