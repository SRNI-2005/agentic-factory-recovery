"""DisruptionRecord discriminated union + validation layers 2-3 (§4.1).

Layer 1 (schema) lives in the pydantic models themselves; layer 2 is the
instance cross-check; layer 3 is the DB resource-existence check plus the
narrative-ID guard (defect fix 2026-09-12: the model must never silently
rewrite an unknown explicit identifier — e.g. "MC-999" — into a valid
one; explicit unknown IDs reject, pure natural-language names map).
Layer 4 (time resolution) happens in the translate node before the record
exists.
"""
import re
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
_KIND_IDS = {"MACHINE": "machines", "WORKER": "workers",
             "MATERIAL": "materials"}


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


# Explicit resource-identifier styling: letter prefix + digits, optionally
# separator-joined (M3, MC-999, W10, MAT-001). Natural-language names
# ("machine three", "press 01") deliberately do NOT match this pattern,
# so their fuzzy mapping to a valid ID stays allowed.
_RESOURCE_ID_TOKEN = re.compile(r"\b(?:MAT-\d+|MC-?\d+|M-?\d+|W-?\d+)\b")


def _norm_id(text: str) -> str:
    return re.sub(r"[-_ ]", "", text).upper()


def check_narrative_ids(record: dict, *, narrative: str,
                        valid_ids: dict[str, list[str]]) -> None:
    """Reject silent rewrites of unknown explicit identifiers.

    Layered mapping rule (defect fix 2026-09-12, assurance behind §4.1
    layer 3):
    1. If the narrative names the recorded identifier explicitly, OK.
    2. Narrative with NO identifier-shaped token (pure natural language:
       "machine three", "press 01") may map loosely to any valid ID.
    3. An ID-shaped token (M/W/MAT + digits styling) whose digits match
       exactly one valid identifier of the record's kind (MC-04 -> M4)
       maps there and only there — any other choice rejects.
    4. An ID-shaped token matching nothing (MC-999) always rejects.
    The model is a translation pass, not a rewrite pass.
    """
    kind = record.get("kind")
    field = _RESOURCE_FIELD.get(kind, "")
    chosen = str(record.get(field) or "")
    options = [str(v) for v in (valid_ids.get(
        _KIND_IDS.get(kind, ""), []) or [])]
    tokens = {t.strip() for t in _RESOURCE_ID_TOKEN.findall(narrative)}
    if not tokens:
        return      # pure natural-language reporting: mapping permitted
    norm = lambda s: re.sub(r"[-_ ]", "", s.strip()).upper()  # noqa: E731
    chosen_norm = norm(chosen)
    token_norms = {norm(t) for t in tokens}
    if chosen_norm in token_norms:
        return      # the named identifier is the recorded one
    # Digit-match fallback: MC-04 binds to the unique valid resource
    # whose identifier digits equal the token's digits.
    def _digits(s: str) -> str:
        m = re.search(r"(\d+)$", norm(s).replace("MAT", ""))
        return str(int(m.group(1))) if m else ""
    for t in tokens:
        d = _digits(t)
        if d and sum(1 for v in options if _digits(v) == d) == 1:
            bound = next(v for v in options if _digits(v) == d)
            if chosen_norm != norm(bound):
                raise RecordValidationError(
                    f"identifier token {t!r} binds to {bound!r} "
                    f"(unique digit match); recorded {chosen_norm!r} "
                    f"does not agree — correct the record instead of "
                    f"substituting resources")
            return
    unknown = sorted(t for t in tokens if norm(t) not in {norm(v)
                                                        for v in options})
    raise RecordValidationError(
        f"the report explicitly names identifier(s) {unknown} which do "
        f"not exist in the valid list; never silently rewrite them to "
        f"{chosen_norm!r} (ask the operator to correct the identifier "
        f"or confirm the resource instead)")
