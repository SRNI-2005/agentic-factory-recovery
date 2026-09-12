"""DegradedLLMClient — deterministic stand-in behind the LLMClient protocol.

Simulate-page "LLM narration" toggle, OFF (default): the recovery graph's
three LLM touchpoints (translate §4.1, strategy §3.3/§4.3, explain §4.5)
are answered locally instead of by a provider, so a scripted day runs the
REAL pipeline — translate → ingest → investigate → strategy → compile →
solve → gate → commit → verify → explain — with zero network calls. The
solver alone re-plans ("auto-fix").

Node detection keys off stable substrings of each node's system prompt
(coe/agents/nodes/translate.py::_SYSTEM_PROMPT, strategy.py, explain.py);
an unknown ask raises loudly rather than guessing. The translate path
mirrors the layered mapping rules of coe/agents/records.py
(check_narrative_ids): explicit identifier tokens bind by trailing digits
only (MC-04 → M4), and an unknown token emits
{"error": "unknown resource <token>"} so the run terminates
TRANSLATION_FAILED exactly as the live model's refusal would (§3.3 retry
then fail; zero DB mutation).
"""
import json
import re

from coe.agents.records import resource_id_tokens

# Stable substrings of the three node system prompts (§3.3 narrow LLM
# boundary: exactly these three nodes ever call complete()).
_TRANSLATE_MARK = "You translate factory disruption reports"
_STRATEGY_MARK = "You are a factory recovery strategist"
_EXPLAIN_MARK = "You explain factory schedule changes"

_EXPLAIN_TEXT = ("Auto-recovery committed. (LLM disabled for this run — "
                 "deterministic re-plan only.)")

_SEVERITY_KEYWORDS = [
    ("CRITICAL", ("critical", "catastrophic", "total shutdown",
                  "completely down")),
    ("HIGH", ("seized", "major", "fire", "urgent", "serious")),
    ("LOW", ("minor", "slight", "small", "brief")),
]

_WORKER_RETURN_KEYWORDS = ("back", "returns", "returned",
                           "available again", "recovered")
_WORKER_ABSENT_KEYWORDS = ("absent", "sick", "call", "out sick",
                           "unavailable", "no-show", "off")
_SHORTAGE_KEYWORDS = ("empty", "shortage", "short", "stuck", "out of",
                      "depleted", "exhausted")
_RESTOCK_KEYWORDS = ("restock", "delivery", "arrived", "refill", "received")


def _normalize(text: str) -> str:
    return re.sub(r"[-_ ]", "", text.strip()).upper()


def _digits(text: str) -> str:
    m = re.search(r"(\d+)$", _normalize(text).replace("MAT", ""))
    return str(int(m.group(1))) if m else ""


def _bind_token(token: str, options: list[str]) -> str | None:
    """check_narrative_ids digit-binding: unique trailing-digit match."""
    d = _digits(token)
    if not d:
        return None
    matches = [v for v in options if _digits(v) == d]
    return matches[0] if len(matches) == 1 else None


def _extract_section(user: str, header: str) -> list[str]:
    for line in user.splitlines():
        if line.startswith(header):
            return [x.strip() for x in
                    line[len(header):].split(",") if x.strip()]
    return []


def _report_text(user: str) -> str:
    idx = user.find("Report:")
    return user[idx + len("Report:"):].strip() if idx >= 0 else ""


def _parse_clock(user: str) -> int:
    m = re.search(r"Reference clock: minute (\d+)", user)
    return int(m.group(1)) if m else 0


def _parse_instance(user: str) -> str:
    m = re.search(r"Target instance: (.+)", user)
    return m.group(1).strip() if m else ""


def _severity(report: str) -> str:
    low = report.lower()
    for level, words in _SEVERITY_KEYWORDS:
        if any(w in low for w in words):
            return level
    return "MEDIUM"


def _classify(report: str) -> tuple[str, str, str]:
    """(kind, event_type, resource-list key) from report keywords.

    Falls back to MACHINE FAILURE — the day-simulator narratives are
    overwhelmingly machine-down reports.
    """
    low = report.lower()
    if "worker" in low or re.search(r"\bW-?\d+\b", report):
        if any(w in low for w in _WORKER_RETURN_KEYWORDS):
            return "WORKER", "WORKER_RETURN", "workers"
        if any(w in low for w in _WORKER_ABSENT_KEYWORDS):
            return "WORKER", "WORKER_ABSENT", "workers"
    if "material" in low or "sku" in low or re.search(r"\bMAT-\d+\b", report):
        if any(w in low for w in _RESTOCK_KEYWORDS):
            return "MATERIAL", "MATERIAL_RESTOCK", "materials"
        if any(w in low for w in _SHORTAGE_KEYWORDS):
            return "MATERIAL", "MATERIAL_SHORTAGE", "materials"
    return "MACHINE", "FAILURE", "machines"


class DegradedLLMClient:
    """Pure, network-free LLMClient (spec §9 boundary preserved)."""

    def complete(self, *, system: str, user: str) -> str:
        if _TRANSLATE_MARK in system:
            return self._translate(user)
        if _STRATEGY_MARK in system:
            # §3.3 sanctioned no-strategy degradation.
            return json.dumps({"candidates": [], "final": True})
        if _EXPLAIN_MARK in system:
            return _EXPLAIN_TEXT
        raise RuntimeError("degraded client cannot answer unknown node")

    def _translate(self, user: str) -> str:
        report = _report_text(user)
        instance = _parse_instance(user)
        clock = _parse_clock(user)
        ids = {
            "machines": _extract_section(user, "Valid machine IDs: "),
            "workers": _extract_section(user, "Valid worker IDs: "),
            "materials": _extract_section(user, "Valid material SKUs: "),
        }
        kind, event_type, id_key = _classify(report)
        tokens = resource_id_tokens(report)
        if tokens:
            bound = None
            for t in sorted(tokens):
                hit = _bind_token(t, ids.get(id_key, []))
                if hit is not None:
                    bound = hit
                    break
            if bound is None:
                # Unknown explicit identifier: reject verbatim; translate
                # retries then aborts TRANSLATION_FAILED (§4.1 layer 3).
                return json.dumps({"error":
                                   f"unknown resource {sorted(tokens)[0]}"})
            chosen = bound
        else:
            options = ids.get(id_key, [])
            if not options:
                return json.dumps({"error":
                                   f"unknown resource {report[:40]!r}"})
            chosen = options[0]

        record: dict = {
            "kind": kind, "instance_id": instance,
            "occurred_at": clock, "severity": _severity(report),
            "narrative_excerpt": report,
        }
        if kind == "MACHINE":
            record["machine_id"] = chosen
            record["event_type"] = event_type
        elif kind == "WORKER":
            record["worker_id"] = chosen
            record["event_type"] = event_type
        else:
            record["material_sku"] = chosen
            record["event_type"] = event_type
        return json.dumps(record)
