"""Smoke tests that the Alembic migration chain applies cleanly end to end,
independent of the create_all() fallback the rest of the test suite uses."""
import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _config_for(db_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def test_upgrade_head_applies_cleanly_on_a_fresh_database() -> None:
    db_path = Path(tempfile.gettempdir()) / f"spop-migrations-fresh-{os.getpid()}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    try:
        command.upgrade(_config_for(db_path), "head")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_downgrade_to_base_and_back_to_head_round_trips() -> None:
    db_path = Path(tempfile.gettempdir()) / f"spop-migrations-roundtrip-{os.getpid()}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    try:
        config = _config_for(db_path)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        if db_path.exists():
            db_path.unlink()
