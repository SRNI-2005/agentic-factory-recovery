"""Shared typed state threaded through every graph node (spec §3.2).

Validated LLM outputs enter state as plain dicts (post-.model_dump()) to
keep the langgraph state serializable; pydantic validation happens at the
node boundaries, satisfying §3.3's "validator before state" rule.
Extra bookkeeping fields beyond §3.2 (reference_clock, source_message_id,
round_count, material_reactive, trigger, run_id) are implementation seams
used by routing/back-edges/dedup — documented here once.
"""
from typing import Literal

from pydantic import BaseModel, Field


class RecoveryState(BaseModel):
    instance_name: str
    trigger: Literal["CLI", "MQTT"] = "CLI"
    run_id: int | None = None                 # recovery_runs row (§7)
    source_message_id: str | None = None      # MQTT dedup key (§3.4)
    narrative: str = ""
    reference_clock: int | None = None        # §4.1 layer 4
    disruption_record: dict | None = None
    db_facts: dict = Field(default_factory=dict)
    strategy_candidates: list[dict] = Field(default_factory=list)
    round_verdicts: list[dict] = Field(default_factory=list)
    round_count: int = 0                      # shared §3.1 budget counter
    material_reactive: bool = False           # set by compile/solve back-edges
    strategy_final: bool = False              # §4.3 step 1 declaration
    committed_version_id: int | None = None   # §7 final_status_version_id
    material_reactive_passes: int = 0         # single intervention pass guard
    solve_infeasible_material: bool = False   # set by solve node (back-edge 2)
    compiled_payload: dict | None = None
    solution: dict | None = None
    gate_result: dict | None = None
    verify_result: dict | None = None
    explanation: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
