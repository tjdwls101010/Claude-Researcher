"""Exceptions that map one-to-one onto the CLI's exit codes.

The codes are the interface the skill body points at (``--help`` prints them),
so every failure the pipeline can name raises one of these rather than a bare
exception; the CLI translates ``exit_code`` and prints ``str(exc)``.
"""

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RESOLVE = 3
EXIT_FETCH = 4
EXIT_CONVERT = 5
EXIT_UNVERIFIED = 6
EXIT_DOCTOR = 7
EXIT_NOTE = 8  # source saved, but the Korean note is missing or structurally broken


class SavePaperError(Exception):
    exit_code = 1


class ResolveError(SavePaperError):
    exit_code = EXIT_RESOLVE


class NotFound(ResolveError):
    pass


class AmbiguousRef(ResolveError):
    """A title search returned more than one candidate.

    Never resolved automatically: picking the first hit would silently save a
    different paper. The CLI prints ``candidates`` as JSON and exits 3 so the
    calling Claude can ask the user.
    """

    def __init__(self, query, candidates):
        super().__init__(f"{len(candidates)} candidates for title {query!r}; pick one by arXiv id")
        self.query = query
        self.candidates = candidates


class FetchError(SavePaperError):
    exit_code = EXIT_FETCH


class ConvertError(SavePaperError):
    exit_code = EXIT_CONVERT


class DoctorError(SavePaperError):
    exit_code = EXIT_DOCTOR
