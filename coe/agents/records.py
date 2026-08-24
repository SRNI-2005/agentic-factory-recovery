"""DisruptionRecord discriminated union + validation layers 2-3 (§4.1).

Layer 1 (schema) lives in the pydantic models themselves; layer 2 is the
instance cross-check; layer 3 is the DB resource-existence check. Layer 4
(time resolution) happens in the translate node before the record exists.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class RecordValidationError(ValueError):
    """Feeds verbatim back into the LLM prompt for retry (§4.1 layer 3)."""


class _BaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str
    occurred_at: int = Field(ge=0)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    narrative_excerpt: str


class MachineRecord(_BaseRecord):
    kind: Literal["MACHINE"]
    machine_id: str
    event_type: Literal["FAILURE", "MAINTENANCE"]
    estimated_downtime: int | None = Field(default=None, gt=0)


class WorkerRecord(_BaseRecord):
    kind: Literal["WORKER"]
    worker_id: str
    event_type: Literal["WORKER_ABSENT", "WORKER_RETURN"]
    estimated_absence: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _absence_only_on_absent(self) -> "WorkerRecord":
        # Mirrors Phase 1 wire rule: estimated_absence is WORKER_ABSENT-only
        # (coe/mqtt/ingest.py). Keeping the union aligned means every record
        # that passes here also survives write-through ingestion.
        if self.event_type != "WORKER_ABSENT" \
                and self.estimated_absence is not None:
            raise ValueError("estimated_absence is WORKER_ABSENT-only")
        return self


class MaterialRecord(_BaseRecord):
    kind: Literal["MATERIAL"]
    material_sku: str
    event_type: Literal["MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"]


DisruptionRecord = Annotated[
    Union[MachineRecord, WorkerRecord, MaterialRecord],
    Field(discriminator="kind"),
]

_record_adapter: TypeAdapter = TypeAdapter(DisruptionRecord)


def parse_disruption_record(data: dict):
    """Layer 1. Raises pydantic ValidationError with a per-field message."""
    return _record_adapter.validate_python(data)


_RESOURCE_MODEL = {"MACHINE": "Machine", "WORKER": "Worker",
                   "MATERIAL": "Material"}
_RESOURCE_FIELD = {"MACHINE": "machine_id", "WORKER": "worker_id",
                   "MATERIAL": "material_sku"}


def validate_record_fields(data: dict, *, session, instance_name: str) -> dict:
    """Layers 2+3. Returns data unchanged; raises RecordValidationError."""
    if data.get("instance_id") != instance_name:
        raise RecordValidationError(
            f"record.instance_id {data.get('instance_id')!r} does not match "
            f"the target instance {instance_name!r} (CLI value is "
            "authoritative, §4.1 layer 2)")
    kind = data.get("kind")
    ref = data.get(_RESOURCE_FIELD.get(kind, ""), ...)
    if ref is ...:
        raise RecordValidationError(f"missing resource field for {kind!r}")
    if kind == "MACHINE":
        from coe.db.models.fjsp import Machine

        hit = session.query(Machine.id).filter(
            Machine.instance_id == _inst_id(session, instance_name),
            Machine.name == ref).one_or_none()
    elif kind == "WORKER":
        from coe.db.models.workers import Worker

        hit = session.query(Worker.id).filter(
            Worker.instance_id == _inst_id(session, instance_name),
            Worker.name == ref).one_or_none()
    elif kind == "MATERIAL":
        from coe.db.models.materials import Material

        hit = session.query(Material.id).filter(
            Material.instance_id == _inst_id(session, instance_name),
            Material.sku == ref).one_or_none()
    else:
        raise RecordValidationError(f"unknown kind {kind!r}")
    if hit is None:
        raise RecordValidationError(
            f"{_RESOURCE_MODEL[kind]} {ref!r} does not exist within "
            f"instance {instance_name!r} (§4.1 layer 3)")
    return data


def _inst_id(session, instance_name: str) -> int:
    from coe.db.models.provenance import Instance

    row = (session.query(Instance.id)
           .filter(Instance.name == instance_name).one_or_none())
    if row is None:
        raise RecordValidationError(f"unknown instance {instance_name!r}")
    return row[0]
