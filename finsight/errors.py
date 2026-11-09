"""Exception types. Each one tells the orchestrator how to route the failure."""


class FinSightError(Exception):
    """Base class."""


class ConfigurationError(FinSightError):
    """Missing or invalid configuration, e.g. no SEC User-Agent. Not retryable."""


class TransientSourceError(FinSightError):
    """Network or server problem that persisted after the bounded retries."""


class SourceNotFoundError(FinSightError):
    """The source does not exist (HTTP 404, or not present in offline fixtures)."""


class AccessDeniedError(FinSightError):
    """The SEC refused the request (HTTP 403), usually a missing or rejected User-Agent."""


class AmbiguousCompanyError(FinSightError):
    def __init__(self, query: str, candidates: list[dict]):
        self.query = query
        self.candidates = candidates
        super().__init__(f"'{query}' matches {len(candidates)} companies; please choose one")


class CompanyNotFoundError(FinSightError):
    pass


class UnsupportedCompanyError(FinSightError):
    def __init__(self, message: str, profile: str):
        self.profile = profile
        super().__init__(message)


class InvalidTransitionError(FinSightError):
    pass
