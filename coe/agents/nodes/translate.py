"""Translate + ingest node bodies (AI Role 1, spec §4.1).

The ONLY LLM usage here is narrative -> DisruptionRecord. Every candidate
output passes the pydantic union + DB validators before entering state;
validator errors feed back into the prompt for up to llm_max_retries
retries (§3.3), then the run aborts TRANSLATION_FAILED with zero DB
mutation. Ingestion is a SEPARATE graph node (run_ingest): it writes the
validated record through the Phase 1 ingestion function under the wire
message_id (MQTT) or a content-derived cli- id (CLI), so identical
narratives are idempotent (§4.1 tail, criterion 13).

Deviation from plan code (documented per repo convention): when neither
an explicit clock nor instance telemetry exists yet (fresh scenario,
CLI run without --at), run_translate falls back to minute 0 instead of
the strict §10 loud failure — the prompt clock only anchors RELATIVE
expressions and validated records carry absolute occurred_at values;
once run_ingest writes the telemetry row, later nodes resolve a real
clock normally.
"""
import hashlib
import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from coe.agents.records import (
    RecordValidationError,
    parse_disruption_record,
    validate_record_fields,
)
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine
from coe.mqtt.ingest import PayloadError, ingest_telemetry_event
from coe.solver.payload_builder import resolve_reference_clock

_SYSTEM_PROMPT = """You translate factory disruption reports into ONE \
structured JSON record. Output ONLY the JSON object, no prose, no code \
fences. Schema (discriminated on "kind"):
{"kind":"MACHINE","instance_id":str,"machine_id":str,\
"event_type":"FAILURE"|"MAINTENANCE","occurred_at":int>=0,\
"severity":"LOW"|"MEDIUM"|"HIGH"|"CRITICAL",\
"estimated_downtime":int|null,"narrative_excerpt":str}
{"kind":"WORKER","instance_id":str,"worker_id":str,\
"event_type":"WORKER_ABSENT"|"WORKER_RETURN","occurred_at":int>=0,\
"severity":...,"estimated_absence":int|null,"narrative_excerpt":str}
{"kind":"MATERIAL","instance_id":str,"material_sku":str,\
"event_type":"MATERIAL_SHORTAGE"|"MATERIAL_RESTOCK","occurred_at":int>=0,\
"severity":...,"narrative_excerpt":str}   (no duration field allowed)
Rules: exactly ONE disruption per record — if the report describes \
several simultaneous disruptions, output {"error":"multiple disruptions"} \
and nothing else. Resolve relative times ("two hours ago") against the \
reference clock given in the prompt; occurred_at is an absolute minute. \
Use the exact machine/worker/SKU identifiers as they appear in the report."""


class TranslationFailed(RuntimeError):
    def __init__(self, narrative: str, error: str) -> None:
        super().__init__(error)
        self.narrative = narrative
        self.error = error


def build_translate_messages(narrative: str, instance_name: str,
                             clock: int) -> tuple[str, str]:
    user = (
        f"Target instance: {instance_name}\n"
        f"Reference clock: minute {clock}\n"
        f"Report:\n{narrative}")
    return _SYSTEM_PROMPT, user


def _extract_json(text: str) -> dict:
    """Tolerate code fences; otherwise parse directly."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in LLM response: {text[:200]!r}")
    return json.loads(stripped[start:end + 1])


def cli_message_id(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return "cli-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def record_to_wire_payload(record: dict, *, message_id: str) -> dict:
    payload = {
        "message_id": message_id,
        "resource_kind": record["kind"],
        "instance_id": record["instance_id"],
        "event_type": record["event_type"],
        "occurred_at": record["occurred_at"],
        "severity": record.get("severity"),
        "reason": record.get("narrative_excerpt"),
    }
    if record["kind"] == "MACHINE":
        payload["machine_id"] = record["machine_id"]
        if record.get("estimated_downtime") is not None:
            payload["estimated_downtime"] = record["estimated_downtime"]
    elif record["kind"] == "WORKER":
        payload["worker_id"] = record["worker_id"]
        if record.get("estimated_absence") is not None:
            payload["estimated_absence"] = record["estimated_absence"]
    else:
        payload["material_sku"] = record["material_sku"]
    return payload


def _instance_row(session, name):
    from coe.db.models.provenance import Instance

    row = (session.query(Instance)
           .filter(Instance.name == name).one_or_none())
    if row is None:
        raise ValueError(f"unknown instance {name!r}")
    return row


def _resolve_clock(session, instance_id: int, at: int | None) -> int:
    """Explicit --at wins; else latest telemetry; else 0 (fresh scenario)."""
    if at is not None:
        return at
    try:
        return resolve_reference_clock(session, instance_id, None)
    except ValueError:
        return 0


def run_translate(state: RecoveryState, *, client,
                  max_retries: int | None = None) -> RecoveryState:
    settings = get_settings()
    retries = (settings.llm_max_retries if max_retries is None
               else max_retries)
    with Session(make_engine()) as session:
        inst_row = _instance_row(session, state.instance_name)
        clock = _resolve_clock(session, inst_row.id, state.reference_clock)
        feedback = ""
        for attempt in range(1 + retries):
            system, user = build_translate_messages(
                state.narrative, state.instance_name, clock)
            raw = client.complete(system=system, user=user + feedback)
            try:
                data = _extract_json(raw)
                if "error" in data and len(data) == 1:
                    raise RecordValidationError(
                        f"translator refused: {data['error']}")
                record = parse_disruption_record(data).model_dump()
                record = validate_record_fields(
                    record, session=session,
                    instance_name=state.instance_name)
            except (ValueError, ValidationError, RecordValidationError) \
                    as exc:
                feedback = (f"\n\nYour previous output was rejected: "
                            f"{exc}. Fix it and respond again.")
                continue
            break
        else:
            raise TranslationFailed(state.narrative,
                                    feedback.strip() or "unknown error")

    # No writes here — ingestion belongs to the `ingest` node (§3.1).
    return state.model_copy(update={
        "disruption_record": record,
        "reference_clock": clock,
    })


def run_ingest(state: RecoveryState) -> RecoveryState:
    """`ingest` node body for BOTH entry points (§3.1, §4.1 tail).

    MQTT runs carry the wire message_id; CLI runs derive cli-{hash} from
    the validated record so identical narratives are idempotent
    (criterion 13). Duplicate deliveries are suppressed inside the shared
    Phase 1 ingestion function.
    """
    mid = state.source_message_id or cli_message_id(
        state.disruption_record)
    wire = record_to_wire_payload(state.disruption_record, message_id=mid)
    try:
        ingest_telemetry_event(wire)
    except PayloadError as exc:   # defensive: pre-validated, but be loud
        raise TranslationFailed(state.narrative, f"ingest rejected: {exc}") \
            from exc
    return state
