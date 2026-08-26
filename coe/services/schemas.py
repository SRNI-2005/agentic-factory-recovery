"""API/service response models (single source for FastAPI, spec §3)."""
from pydantic import BaseModel


class InstanceOut(BaseModel):
    name: str
    source_name: str | None = None
    parent: str | None = None


class VersionOut(BaseModel):
    id: int
    version_number: int
    schedule_type: str
    solver_status: str
    makespan: int
    total_tardiness: int
    rolled_back: bool


class GanttOut(BaseModel):
    version: VersionOut
    entries: list[dict]


class MaterialOut(BaseModel):
    sku: str
    initial_stock: int
    reorder_point: int | None
    receipts: list[dict]


class MachineOut(BaseModel):
    name: str
    status: str
    down_since: int | None


class WorkerOut(BaseModel):
    name: str
    role: str | None
    availability: list[tuple[int, int]]
    absent_since: int | None


class JobOut(BaseModel):
    name: str
    family: str | None
    release_time: int
    deadline: int | None
    priority: int
    status: str
    ops: int


class ActionOk(BaseModel):
    ok: bool
    detail: str
