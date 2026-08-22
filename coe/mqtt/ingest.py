from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow, TelemetryEvent
from coe.db.models.fjsp import Machine
from coe.db.models.provenance import Instance
from coe.db.session import session_scope


class PayloadError(ValueError):
    pass


class FailurePayload(BaseModel):
    message_id: str
    instance_id: str
    machine_id: str
    event_type: Literal["FAILURE", "MAINTENANCE"]
    occurred_at: int
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    estimated_downtime: int | None = Field(default=None, gt=0)
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("negative occurred_at crashes CP-SAT (spec §6.5)")
        return v


def _union_window(
    session: Session,
    *,
    instance_id: int,
    machine_id: int,
    new_from: int,
    new_until: int | None,
    reason: str,
    telemetry_id: int,
) -> None:
    """Spec §6.5: overlapping/touching intervals union into the smallest covering
    interval, under a per-machine advisory lock."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"downtime:{instance_id}:{machine_id}"},
    )
    rows = session.scalars(
        select(MachineDowntimeWindow).where(
            MachineDowntimeWindow.instance_id == instance_id,
            MachineDowntimeWindow.machine_id == machine_id,
        )
    ).all()

    def touches(r: MachineDowntimeWindow) -> bool:
        r_to = r.downtime_until
        if new_until is None:
            return r_to is None or r_to >= new_from
        if r_to is None:
            return new_until >= r.downtime_from
        return new_from <= r_to and r.downtime_from <= new_until

    overlapping = [r for r in rows if touches(r)]
    if not overlapping:
        session.add(
            MachineDowntimeWindow(
                instance_id=instance_id,
                machine_id=machine_id,
                downtime_from=new_from,
                downtime_until=new_until,
                reason=reason,
                severity=None,
                source_event_ids=[telemetry_id],
            )
        )
        return

    merged_from = min([new_from] + [r.downtime_from for r in overlapping])
    merged_open = any(r.downtime_until is None for r in overlapping) or new_until is None
    merged_until = None if merged_open else max(
        [new_until] + [r.downtime_until for r in overlapping]
    )
    reasons = {r.reason for r in overlapping} | {reason}
    merged_reason = "FAILURE" if "FAILURE" in reasons else next(iter(reasons))
    event_ids: list[int] = [telemetry_id]
    for r in overlapping:
        event_ids.extend(r.source_event_ids or [])
        session.delete(r)

    session.add(
        MachineDowntimeWindow(
            instance_id=instance_id,
            machine_id=machine_id,
            downtime_from=merged_from,
            downtime_until=merged_until,
            reason=merged_reason,
            severity=None,
            source_event_ids=event_ids,
        )
    )


def ingest_telemetry_event(payload_dict: dict) -> tuple[int, bool]:
    """Validate, persist telemetry, union downtime, flip status. Idempotent."""
    try:
        payload = FailurePayload.model_validate(payload_dict)
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
        machine = (
            session.query(Machine)
            .filter(
                Machine.instance_id == inst.id,
                Machine.name == payload.machine_id,
            )
            .one_or_none()
        )
        if machine is None:
            raise PayloadError(
                f"unknown machine '{payload.machine_id}' in '{payload.instance_id}'"
            )

        existing = session.execute(
            select(TelemetryEvent.id).where(
                TelemetryEvent.instance_id == inst.id,
                TelemetryEvent.message_id == payload.message_id,
            )
        ).first()
        if existing is not None:
            return existing[0], False  # duplicate delivery: no state change

        event = TelemetryEvent(
            occurred_at=payload.occurred_at,
            instance_id=inst.id,
            message_id=payload.message_id,
            machine_id=machine.id,
            event_type=payload.event_type,
            received_at=payload.occurred_at,  # convention: see task header
            severity=payload.severity,
            estimated_downtime=payload.estimated_downtime,
            processed_at=payload.occurred_at,
            processing_error=None,
            payload_json=payload_dict,
        )
        session.add(event)
        session.flush()

        if payload.event_type == "FAILURE":
            new_until = (
                payload.occurred_at + payload.estimated_downtime
                if payload.estimated_downtime is not None
                else None
            )
        else:  # MAINTENANCE always carries an explicit estimate in Phase 1
            new_until = payload.occurred_at + (payload.estimated_downtime or 0)

        if new_until is None or new_until > payload.occurred_at:
            _union_window(
                session,
                instance_id=inst.id,
                machine_id=machine.id,
                new_from=payload.occurred_at,
                new_until=new_until,
                reason=payload.event_type,
                telemetry_id=event.id,
            )

        if payload.event_type == "FAILURE":
            machine.status = "FAILED"

        session.flush()
        return event.id, True
