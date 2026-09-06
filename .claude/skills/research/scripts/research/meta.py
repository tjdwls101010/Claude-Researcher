"""Closed vocabularies shared by argparse `choices=` and the modules; no third-party import so `--help` and `doctor` work without PyYAML."""

DECIDERS = ("human:seongjin", "claude")
KINDS = ("observation", "hypothesis", "mechanism", "prediction", "alternative", "evidence")
AUTHORS = DECIDERS
CLAIM_STATUSES = ("candidate", "supported", "refuted", "dropped")
STATISTICS = ("mean", "std", "n", "min", "max")
PHASES = ("exploring", "designing", "running", "analyzing", "writing", "reviewing", "submitted")
REVIEW_SCOPES = ("design", "draft")
LANES = ("codex", "claude")
DISPOSITIONS = ("accept", "reject", "test", "human")
