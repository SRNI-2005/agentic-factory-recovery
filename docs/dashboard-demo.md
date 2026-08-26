# Dashboard Demo Script (~5 minutes)

## Prerequisites

- Docker running (`docker compose up -d` — TimescaleDB :5432, Mosquitto :1883)
- LLM API key set in `.env` (e.g. `OPENAI_API_KEY` or `GOOGLE_API_KEY`)
- Data imported and scenario built:
  ```bash
  uv run python -m coe.cli import mk01
  uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
  ```

## Terminal Setup

Open **two terminals**:

| Terminal | Purpose |
|----------|---------|
| **Listener** | `uv run python -m coe.cli mqtt listen` — live MQTT event ingestion |
| **Dashboard** | `uv run python -m coe.cli dashboard` — Streamlit cockpit at `http://127.0.0.1:8501` |

## Flow

### 1. Configure Tour (~1 min)

1. Open the dashboard. The sidebar defaults to `factory_demo_01`.
2. Click **Configure** in the navigation.
3. Browse the five tabs: Overview, Machines, Workers, Materials, Jobs.
4. Note the schedule versions and Gantt chart if a baseline exists.

### 2. Workbook Upload (~1 min)

1. In Configure, click **Export Workbook** to download `factory_workbook.xlsx`.
2. Make an intentional edit (e.g. delete a row) to create an invalid file.
3. Click **Upload Workbook** and select the broken file → expect a clear rejection message.
4. Fix the file (restore the row), re-upload → expect a new forked instance to appear in the sidebar.

### 3. Machine Failure / Live Rail (~1 min)

1. Switch to the **Listener** terminal.
2. Inject a machine failure:
   ```bash
   uv run python -m coe.cli mqtt test-failure --machine M3 --at 512
   ```
3. Observe the **Live events** rail appear in the dashboard sidebar with `FAILURE · M3`.

### 4. Chat Recovery / Decision Feed / Diff (~1.5 min)

1. In the dashboard, click **Cockpit**.
2. Type in the chat: `Machine M3 has failed at minute 512. Recover.`
3. Watch the **decision feed** stream progress nodes (translate → ingest → agents → solve → commit).
4. After COMMITTED, the **diff animation** shows before/after schedule changes on the Gantt.
5. The explanation panel shows the LLM's rationale.

### 5. Optional: INFEASIBLE Demonstration (~0.5 min)

1. Inject a scenario that creates a material conflict:
   ```bash
   uv run python -m coe.cli mqtt test-shortage --sku MAT-001 --at 300
   ```
2. In the Cockpit, type: `Material MAT-001 is unavailable. What happens?`
3. If the solver returns INFEASIBLE, the cockpit displays an honest explanation rather than a fake recovery.

## CLI Commands Reference

```bash
uv run python -m coe.cli dashboard              # launch cockpit (default :8501)
uv run python -m coe.cli dashboard --port 9000   # custom port
uv run python -m coe.cli mqtt listen              # live event listener
uv run python -m coe.cli mqtt test-failure        # inject machine failure
uv run python -m coe.cli mqtt test-absence        # inject worker absence
uv run python -m coe.cli mqtt test-shortage       # inject material shortage
uv run python -m coe.cli recover --instance I --narrative "..."  # CLI recovery
uv run python -m coe.cli explain --instance I     # explain active schedule
```
