from __future__ import annotations


class TmdbError(RuntimeError):
    """A safe TMDB error that never includes credentials or response bodies."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TmdbNotFound(TmdbError):
    def __init__(self) -> None:
        super().__init__("TMDB_NOT_FOUND", "TMDB resource was not found")


class TmdbAuthenticationError(TmdbError):
    def __init__(self) -> None:
        super().__init__("TMDB_AUTHENTICATION_FAILED", "TMDB authentication was rejected")


class TmdbTransientError(TmdbError):
    pass


class IdentityMapConflict(ValueError):
    pass

