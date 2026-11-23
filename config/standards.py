"""Rule provenance, not hard-coded assertions of electrical compliance.

Design values must come from the project's verified Playbook. No universal
mounting height, spacing, demand factor or safety clearance is assumed here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardReference:
    identifier: str
    source: str
    verified: bool = False

    def as_dict(self) -> dict:
        return {"identifier": self.identifier, "source": self.source, "verified": self.verified}
