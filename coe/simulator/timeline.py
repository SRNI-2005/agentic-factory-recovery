"""Timeline loader for the day simulator (spec §3/§5).

A timeline is a deterministic permutation-free event list. Schema errors
fail loudly at load, never mid-walk.
"""
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from coe.config import get_settings


class TimelineError(ValueError):
    pass


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int = Field(ge=0)
    agentic: bool = False


class MachineEvent(_Base):
    kind: Literal["MACHINE"]
    event_type: Literal["FAILURE", "MAINTENANCE"]
    machine_id: str
    estimated_downtime: int | None = Field(default=None, gt=0)

    @property
    def resource_kind(self) -> str:
        return self.kind


class WorkerEvent(_Base):
    kind: Literal["WORKER"]
    event_type: Literal["WORKER_ABSENT", "WORKER_RETURN"]
    worker_id: str
    duration: int | None = Field(default=None, gt=0)

    @property
    def resource_kind(self) -> str:
        return self.kind


class MaterialEvent(_Base):
    kind: Literal["MATERIAL"]
    event_type: Literal["MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"]
    sku: str
    quantity: int | None = Field(default=None, gt=0)

    @property
    def resource_kind(self) -> str:
        return self.kind


class NarrativeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int = Field(ge=0)
    kind: Literal["NARRATIVE"]
    text: str
    at: int | None = Field(default=None, ge=0)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None


_TimelineEventUnion = Annotated[
    Union[MachineEvent, WorkerEvent, MaterialEvent, NarrativeEvent],
    Field(discriminator="kind"),
]

_event_kind_map = {
    "MACHINE": MachineEvent,
    "WORKER": WorkerEvent,
    "MATERIAL": MaterialEvent,
    "NARRATIVE": NarrativeEvent,
}


class Timeline(BaseModel):
    """Authored script. `seed` feeds downstream determinism consumers; the
    engine passes llm clients explicitly (tests) or the settings provider."""
    model_config = ConfigDict(extra="forbid")
    name: str
    seed: int = 42
    horizon_days: int = Field(default=1, ge=1)
    events: list[_TimelineEventUnion]


class _TimelineEventFactory:
    def __call__(cls, **kwargs):
        kind = kwargs.get("kind")
        event_cls = _event_kind_map.get(kind)
        if event_cls is None:
            raise ValueError(f"Unknown event kind: {kind!r}")
        return event_cls(**kwargs)


TimelineEvent = _TimelineEventFactory()

adapter: TypeAdapter = TypeAdapter(Timeline)


def load_timeline(path: str) -> Timeline:
    with open(path) as fh:
        tl = adapter.validate_python(json.load(fh))
    max_days = get_settings().simulate_max_horizon_days
    if tl.horizon_days > max_days:
        raise TimelineError(
            f"horizon_days {tl.horizon_days} exceeds "
            f"SIMULATE_MAX_HORIZON_DAYS={max_days}")
    prev = None
    for e in tl.events:
        if prev is not None and e.t < prev:
            raise TimelineError(
                f"events must be monotonic in t ({prev} then {e.t})")
        prev = e.t
    return tl
