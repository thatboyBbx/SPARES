"""Provide an isolated database before any test module imports the API."""
import os
import tempfile
from pathlib import Path


db_path = Path(tempfile.gettempdir()) / f"spop-suite-tests-{os.getpid()}.sqlite3"
if db_path.exists():
    db_path.unlink()

os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:5173"]')
