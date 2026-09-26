"""Phase 11 safety checks that do not require live infrastructure."""

from app.core.config import Settings
from app.main import readiness_checks


def test_production_configuration_rejects_development_defaults() -> None:
    settings = Settings(environment="production")

    try:
        settings.assert_deployable()
    except RuntimeError as error:
        assert "Unsafe production configuration" in str(error)
    else:
        raise AssertionError("production defaults must be rejected")


def test_readiness_returns_a_status_for_every_dependency() -> None:
    checks = readiness_checks()

    assert set(checks) == {"database", "redis", "migrations"}
    assert all(check["status"] in {"ok", "unavailable"} for check in checks.values())
