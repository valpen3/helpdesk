"""Validate the migration graph without requiring PostgreSQL."""

import warnings
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_have_one_head_and_unique_revisions():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    # Alembic only warns about duplicate IDs; treat them as a failure in CI.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        assert len(scripts.get_heads()) == 1
        revisions = list(scripts.walk_revisions())

    assert len([revision for revision in revisions if revision.is_base]) == 1
