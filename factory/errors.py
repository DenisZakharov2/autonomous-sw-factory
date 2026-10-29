"""Small exception hierarchy with user-facing messages."""


class FactoryError(Exception):
    """Base error for expected, user-facing failures."""

    exit_code = 1


class ConfigError(FactoryError):
    """Invalid or inconsistent configuration."""


class RunError(FactoryError):
    """Invalid run id, state or lifecycle transition."""


class JournalError(FactoryError):
    """Journal corruption or a rejected event."""


class VerificationError(FactoryError):
    """Deterministic verification failed."""


class QuarantineError(FactoryError):
    """A skill could not be scanned or proven safe."""
