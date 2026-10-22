"""
Admin-editable settings, one JSON document per feature (models.AppSetting).

Each feature owns a pydantic model with a default for every field (e.g.
services/apoyos.ApoyosSettings). read() merges the stored document over those
defaults and drops anything the model no longer knows, so a missing row, a
partial row or a row written by an older version always reads back as a
complete, valid configuration. write() validates the merged result before
storing it, so the admin can never save a document the feature can't read.
"""

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from models import AppSetting

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def read(db: Session, key: str, model: type[T]) -> T:
    row = db.get(AppSetting, key)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    known = {k: v for k, v in stored.items() if k in model.model_fields}
    try:
        return model.model_validate(known)
    except ValidationError:
        # A field that no longer validates (a removed enum value, a tightened
        # bound) falls back to its default; the rest of the document stays.
        logger.warning(
            "settings %s: stored document invalid, using defaults per field", key
        )
        good: dict = {}
        for name, value in known.items():
            try:
                model.model_validate({**good, name: value})
                good[name] = value
            except ValidationError:
                continue
        return model.model_validate(good)


def write(db: Session, key: str, model: type[T], patch: dict, user: str | None) -> T:
    """Merge `patch` over the current settings, validate, store and commit.
    Raises pydantic.ValidationError when the result is invalid."""
    current = read(db, key, model).model_dump(mode="json")
    merged = model.model_validate({**current, **patch})
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = merged.model_dump(mode="json")
    row.updated_by = (user or "")[:255] or None
    db.commit()
    return merged


def updated_info(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    if row is None:
        return {"updated_at": None, "updated_by": None}
    return {
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }
