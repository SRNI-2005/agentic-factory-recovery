import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance


class SourceParseError(ValueError):
    """Raised when a source file violates its documented format."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_or_create_source_instance(
    session: Session,
    *,
    name: str,
    source_name: str,
    checksum: str | None,
    source_url: str | None = None,
    source_version: str | None = None,
    source_license: str | None = None,
) -> tuple[Instance, bool]:
    """Spec §11: identical re-import is a no-op; changed checksum creates a new instance."""
    existing = session.query(Instance).filter(Instance.name == name).one_or_none()
    if existing is not None:
        if existing.source_checksum == checksum:
            return existing, False
        name = f"{name}@{checksum[:8]}"
        existing = session.query(Instance).filter(Instance.name == name).one_or_none()
        if existing is not None:
            return existing, False
    inst = Instance(
        name=name,
        source_name=source_name,
        source_url=source_url,
        source_version=source_version,
        source_license=source_license,
        retrieved_at=datetime.now(timezone.utc),
        source_checksum=checksum,
    )
    session.add(inst)
    session.flush()
    return inst, True
