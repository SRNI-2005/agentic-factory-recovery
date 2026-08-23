from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    TelemetryEvent,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import Machine
from coe.db.models.materials import Material
from coe.db.models.provenance import Instance
from coe.db.models.workers import Worker
from coe.db.session import session_scope

Kind = Literal["MACHINE", "WORKER", "MATERIAL"]
EVENT_TYPES: dict[str, frozenset[str]] = {
    "MACHINE": frozenset({"FAILURE", "MAINTENANCE"}),
    "WORKER": frozenset({"WORKER_ABSENT", "WORKER_RETURN"}),
    "MATERIAL": frozenset({"MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"}),
}


class PayloadError(ValueError):
    pass


class ResourceEventPayload(BaseModel):
    """Flat wire format shared by all three resource kinds.

    Legacy compatibility: `resource_kind` may be omitted when `machine_id`
    is present (pre-amendment machine payloads) — inferred as MACHINE.
    """

    model_config = {"extra": "forbid"}

    message_id: str
    instance_id: str
    resource_kind: Kind | None = None
    machine_id: str | None = None
    worker_id: str | None = None
    material_sku: str | None = None
    event_type: str
    occurred_at: int
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    estimated_downtime: int | None = Field(default=None, gt=0)
    estimated_absence: int | None = Field(default=None, gt=0)
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("negative occurred_at crashes CP-SAT (spec §6.5)")
        return v

    @model_validator(mode="after")
    def _resolve(self) -> "ResourceEventPayload":
        refs = {
            "MACHINE": self.machine_id,
            "WORKER": self.worker_id,
            "MATERIAL": self.material_sku,
        }
        present = {k for k, v in refs.items() if v is not None}
        if self.resource_kind is None:
            if present == {"MACHINE"}:
                self.resource_kind = "MACHINE"  # legacy inference
            else:
                raise ValueError(
                    "resource_kind required (legacy inference only applies "
                    "to machine payloads)"
                )
        if len(present) != 1 or self.resource_kind not in present:
            raise ValueError(
                "payload must carry exactly the one resource reference "
                "matching its resource_kind"
            )
        kind = self.resource_kind
        if self.event_type not in EVENT_TYPES[kind]:
            raise ValueError(
                f"{self.event_type!r} is not a valid {kind} event_type"
            )
        if kind != "MACHINE" and self.estimated_downtime is not None:
            raise ValueError("estimated_downtime is MACHINE-only")
        if (
            kind != "WORKER" or self.event_type != "WORKER_ABSENT"
        ) and self.estimated_absence is not None:
            raise ValueError("estimated_absence is WORKER_ABSENT-only")
        return self


def _merge_intervals(
    session: Session,
    *,
    model,
    instance_id: int,
    res_attr: str,
    res_id: int,
    lock_key: str,
    new_from: int,
    new_until: int | None,
    reason: str,
    telemetry_id: int,
    from_attr: str,
    until_attr: str,
) -> None:
    """Shared union logic (spec §6.5 + Amendment): overlapping OR touching
    intervals merge into the smallest covering interval under an advisory lock."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
    col = getattr(model, res_attr)
    rows = session.scalars(
        select(model).where(model.instance_id == instance_id, col == res_id)
    ).all()

    def touches(r) -> bool:
        r_to = getattr(r, until_attr)
        if new_until is None:
            return r_to is None or r_to >= new_from
        if r_to is None:
            return new_until >= getattr(r, from_attr)
        return new_from <= r_to and getattr(r, from_attr) <= new_until

    overlapping = [r for r in rows if touches(r)]
    if not overlapping:
        session.add(model(
            instance_id=instance_id, **{res_attr: res_id},
            **{from_attr: new_from, until_attr: new_until},
            reason=reason, severity=None, source_event_ids=[telemetry_id],
        ))
        return

    merged_from = min([new_from] + [getattr(r, from_attr) for r in overlapping])
    merged_open = any(getattr(r, until_attr) is None for r in overlapping) or new_until is None
    merged_until = None if merged_open else max(
        [new_until] + [getattr(r, until_attr) for r in overlapping]
    )
    reasons = {getattr(r, "reason") for r in overlapping} | {reason}
    merged_reason = "FAILURE" if "FAILURE" in reasons else next(iter(reasons))
    event_ids: list[int] = [telemetry_id]
    for r in overlapping:
        event_ids.extend(getattr(r, "source_event_ids") or [])
        session.delete(r)

    session.add(model(
        instance_id=instance_id, **{res_attr: res_id},
        **{from_attr: merged_from, until_attr: merged_until},
        reason=merged_reason, severity=None, source_event_ids=event_ids,
    ))


def ingest_telemetry_event(payload_dict: dict) -> tuple[int, bool]:
    try:
        payload = ResourceEventPayload.model_validate(payload_dict)
    except Exception as exc:
        raise PayloadError(str(exc)) from exc

    with session_scope() as session:
        inst = (
            session.query(Instance)
            .filter(Instance.name == payload.instance_id)
            .one_or_none()
        )
        if inst is None:
            raise PayloadError(f"unknown instance '{payload.instance_id}'")

        existing = session.execute(
            select(TelemetryEvent.id).where(
                TelemetryEvent.instance_id == inst.id,
                TelemetryEvent.message_id == payload.message_id,
            )
        ).first()
        if existing is not None:
            return existing[0], False  # duplicate delivery: no state change

        kind = payload.resource_kind
        machine_id = worker_id = material_id = None
        duration_until = None

        if kind == "MACHINE":
            row = (
                session.query(Machine)
                .filter(Machine.instance_id == inst.id,
                        Machine.name == payload.machine_id)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown machine '{payload.machine_id}'")
            machine_id = row.id
            if payload.event_type == "FAILURE":
                duration_until = (
                    payload.occurred_at + payload.estimated_downtime
                    if payload.estimated_downtime is not None else None
                )
            else:
                duration_until = payload.occurred_at + (
                    payload.estimated_downtime or 0
                )
        elif kind == "WORKER":
            row = (
                session.query(Worker)
                .filter(Worker.instance_id == inst.id,
                        Worker.name == payload.worker_id)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown worker '{payload.worker_id}'")
            worker_id = row.id
            duration_until = (
                payload.occurred_at + payload.estimated_absence
                if payload.estimated_absence is not None else None
            ) if payload.event_type == "WORKER_ABSENT" else 0
        else:  # MATERIAL — telemetry only; resolve for the FK
            row = (
                session.query(Material)
                .filter(Material.instance_id == inst.id,
                        Material.sku == payload.material_sku)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown material '{payload.material_sku}'")
            material_id = row.id

        event = TelemetryEvent(
            occurred_at=payload.occurred_at,
            instance_id=inst.id,
            message_id=payload.message_id,
            machine_id=machine_id,
            worker_id=worker_id,
            material_id=material_id,
            resource_kind=kind,
            event_type=payload.event_type,
            received_at=payload.occurred_at,  # convention: see Phase 1 plan
            severity=payload.severity,
            estimated_downtime=payload.estimated_downtime,
            processed_at=payload.occurred_at,
            processing_error=None,
            payload_json=payload_dict,
        )
        session.add(event)
        session.flush()

        if kind == "MACHINE":
            if duration_until is None or duration_until > payload.occurred_at:
                _merge_intervals(
                    session, model=MachineDowntimeWindow,
                    instance_id=inst.id, res_attr="machine_id",
                    res_id=machine_id,
                    lock_key=f"downtime:{inst.id}:{machine_id}",
                    new_from=payload.occurred_at, new_until=duration_until,
                    reason=payload.event_type, telemetry_id=event.id,
                    from_attr="downtime_from", until_attr="downtime_until",
                )
            if payload.event_type == "FAILURE":
                row.status = "FAILED"
        elif kind == "WORKER":
            if payload.event_type == "WORKER_ABSENT":
                _merge_intervals(
                    session, model=WorkerAbsenceWindow,
                    instance_id=inst.id, res_attr="worker_id",
                    res_id=worker_id,
                    lock_key=f"absence:{inst.id}:{worker_id}",
                    new_from=payload.occurred_at, new_until=duration_until,
                    reason="WORKER_ABSENT", telemetry_id=event.id,
                    from_attr="absence_from", until_attr="absence_until",
                )
                row.status = "UNAVAILABLE"
            else:  # WORKER_RETURN closes open absences, restores status
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"absence:{inst.id}:{worker_id}"},
                )
                open_rows = session.scalars(
                    select(WorkerAbsenceWindow).where(
                        WorkerAbsenceWindow.instance_id == inst.id,
                        WorkerAbsenceWindow.worker_id == worker_id,
                        WorkerAbsenceWindow.absence_until.is_(None),
                    )
                ).all()
                for r in open_rows:
                    r.absence_until = max(r.absence_from + 1, payload.occurred_at)
                row.status = "AVAILABLE"

        session.flush()
        return event.id, True
