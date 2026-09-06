"""Exceptions that map one-to-one onto the CLI's exit codes (``--help`` prints them).

Separate from ``savepaper.errors`` on purpose: the two tools share code but not
failure classes, and a caller matching on ``SavePaperError`` must not catch a
research gate.
"""

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_NOT_FOUND = 3
EXIT_SUBPROCESS = 5
EXIT_GATE = 6
EXIT_DOCTOR = 7


class ResearchError(Exception):
    exit_code = 1

    def __init__(self, message: str, *, findings: list | None = None, data: dict | None = None):
        super().__init__(message)
        self.findings = findings or []
        self.data = data or {}  # extra keys merged into the CLI's JSON output (doctor's checks, a gate's hashes)


class InputError(ResearchError):
    """A required field is missing or malformed; the message names its path (``options[1].fails_when``)."""

    exit_code = EXIT_INPUT


class NotFoundError(ResearchError):
    exit_code = EXIT_NOT_FOUND


class SubprocessError(ResearchError):
    """A child process (tectonic, codex, claude, the experiment) failed; ``child_exit`` is reported separately."""

    exit_code = EXIT_SUBPROCESS

    def __init__(self, message: str, *, child_exit: int | None = None, findings=None, data=None):
        super().__init__(message, findings=findings, data=data)
        self.child_exit = child_exit


class GateError(ResearchError):
    """Verification failed or a precondition is missing; ``findings`` lists each one with a location."""

    exit_code = EXIT_GATE


class DoctorError(ResearchError):
    exit_code = EXIT_DOCTOR
