# Stack Revert Decision: React+FastAPI → Streamlit

**Status:** Decided (user-approved 2026-08-26)
**Supersedes:** `2026-08-26-react-fastapi-cockpit-design.md` (render+transport layers only)
**Reinstates:** `2026-08-25-dashboard-streamlit-cockpit-design.md`

## Evidence

Main orchestration session (`ses_fc274a2f2ffe7P4o4BrxXuRKpr`) hit repeated provider
refusals from 12:39–12:41 and beyond:

```
AI_APICallError: Error from provider (Console):
Upstream request failed: Endpoint is unavailable.
```

- Main session had accumulated **~106k tokens** after orchestrating S1–S5 + F6–F8
  (implementer/reviewer/checkpoint subagents, retry loops, DB-wipe incident recovery).
- Every request — including plain-text "continue" — was refused once past ~105k.
- Playwright checkpoint **subagent** sessions stayed at ~24k tokens and succeeded
  (first FAIL was a legitimate backend-data issue; retry PASSED 168/168 bars).
- Backend-only phases (P1–P3, S1–S5) never hit the wall: their verification loop is
  pytest-output-shaped (compact, linear). The frontend loop added browser-in-the-loop
  verification: DOM snapshots of a 168-bar Gantt, console dumps, screenshots,
  FAIL→diagnose→re-solve→retry cycles.

Root cause: **browser-in-the-loop verification economics**, not React/Playwright per se.

## Ruling

1. Revert to the Streamlit cockpit (spec of 2026-08-25 reinstated). Its verification
   model needs no browser at all: pytest + `streamlit.testing.v1.AppTest`; spec §8
   mandated "no browser e2e automation" from the start.
2. **Keep** the shared service layer `coe/services/*` (zero FastAPI imports — pure
   functions), `coe/parsers/workbook.py`, `tests/services/`. Streamlit pages call
   these in-process. The R1 "backend spine" work survives verbatim.
3. **Remove** `frontend/` (React render layer; preserved in history at `058620d`) and
   the FastAPI transport layer (`coe/api/`, `tests/api/`, deps fastapi/uvicorn/httpx/
   python-multipart). Removal commits: `3810031`, `258e899`.
4. **Playwright MCP disabled** in `opencode.json`. If a visual check is ever needed,
   a human runs it manually outside the agent loop; it is never load-bearing.

## Testing posture going forward

- Layer 1: `tests/services/` (existing, 52 tests) — business logic.
- Layer 2: AppTest page smoke tests per page (render vs `clean_db`+`demo_scenario`).
- Layer 3: one AppTest full-journey test (select instance → view schedule → action →
  recovery → new version visible).
- Visual cosmetics: manual demo script, once per milestone, by the human.

## Session hygiene (applies regardless of stack)

- One milestone per session; fresh sessions over compaction marathons.
- Heavy verification runs in fresh subagents with text-only report-back.
- Never read images/screenshots into an orchestration context.
