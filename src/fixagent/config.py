from dataclasses import dataclass, field
from pathlib import Path
import yaml

@dataclass
class Competitor:
    name: str
    domain: str
    aliases: list[str] = field(default_factory=list)

@dataclass
class Brand:
    slug: str
    name: str
    domain: str
    aliases: list[str]
    one_liner: str
    category: str
    competitors: list[Competitor]
    sender_disclosure: str = ""
    sender_name: str = ""
    claims: list[str] = field(default_factory=list)
    site: dict = field(default_factory=dict)

    def all_names(self) -> list[Competitor]:
        return [Competitor(self.name, self.domain, self.aliases), *self.competitors]

def load_brand(slug: str, root: Path = Path("brands")) -> Brand:
    d = yaml.safe_load((root / f"{slug}.yaml").read_text())
    comps = [Competitor(c["name"], c["domain"], c.get("aliases", [])) for c in d.get("competitors", [])]
    return Brand(d["slug"], d["name"], d["domain"], d.get("aliases", []), d["one_liner"], d["category"], comps,
                 d.get("sender_disclosure", ""), d.get("sender_name", ""), d.get("claims", []), d.get("site") or {})
