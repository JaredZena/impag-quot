"""
Root pytest guard: no test may ever reach the production database.

models.py builds its SQLAlchemy engine from DATABASE_URL at import time and runs
Base.metadata.create_all against it, and config.py's load_dotenv() fills
DATABASE_URL from the repo .env -- which points at the PRODUCTION Neon database --
whenever the variable is not already set. pytest imports this file before it
collects any test module, so the assignments below win over .env for every test
in the repo, whichever file runs first or alone (load_dotenv never overrides a
variable that is already set).

A test module may still point DATABASE_URL at its own sqlite file, or (like
tests/test_campaigns.py) at an unroutable *.invalid host with ALEMBIC_RUNNING=1.
pytest_collection_finish re-checks the engine that was actually built and aborts
the run before any test executes if it points anywhere else.
"""

import os
import sys
import tempfile

import pytest

_tmpdir = tempfile.mkdtemp(prefix="impag_quot_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'test.db')}"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

assert os.environ["DATABASE_URL"].startswith("sqlite"), "tests must run on sqlite"
# If models/config were already imported, the engine was built before this guard
# ran -- from whatever DATABASE_URL was set then. Refuse to continue.
assert (
    "models" not in sys.modules and "config" not in sys.modules
), "a project module was imported before conftest.py forced the sqlite DATABASE_URL"


def pytest_collection_finish(session):
    models = sys.modules.get("models")
    if models is None:
        return
    url = models.engine.url
    if url.get_backend_name() == "sqlite":
        return
    # tests/test_campaigns.py's sentinel: *.invalid never resolves and
    # ALEMBIC_RUNNING=1 skips create_all, so this engine can't touch anything.
    if (url.host or "").endswith(".invalid"):
        return
    pytest.exit(
        f"Refusing to run tests: models.engine uses {url.get_backend_name()}, "
        "not sqlite. A test module set DATABASE_URL to a real database.",
        returncode=2,
    )
