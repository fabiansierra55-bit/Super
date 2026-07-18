"""Domain exceptions used to fail the production cycle safely."""

from __future__ import annotations


class SLPError(RuntimeError):
    """Base class for expected SuperLotto Plus workflow failures."""


class ConfigurationError(SLPError):
    """The runtime configuration violates a production invariant."""


class SourceFetchError(SLPError):
    """A result source could not be fetched or parsed reliably."""


class SourceMismatchError(SLPError):
    """Official and backup winning results do not agree exactly."""


class DrawNotPostedError(SLPError):
    """A draw was requested before its configured Pacific post time."""


class VerificationError(SLPError):
    """A draw lacks the source evidence required for verification."""


class IntegrityError(SLPError):
    """An append-only artifact or hash chain failed validation."""


class ImmutableArtifactError(IntegrityError):
    """An operation attempted to replace or mutate a locked artifact."""


class BundleNotFoundError(SLPError):
    """No active locked prediction bundle exists for the intended draw."""


class AlreadyScoredError(SLPError):
    """A locked bundle already has an immutable scoring artifact."""


class ConstraintError(SLPError, ValueError):
    """A ticket or bundle violates a game or diversity constraint."""


class CalibrationError(SLPError):
    """Training or forward selection could not produce a stable model."""


class SimulationStabilityError(SLPError):
    """Simulation estimates failed to reach the configured tolerance."""


class CyclePreconditionError(SLPError):
    """A strict cycle prerequisite was not satisfied."""
