# COE — Agentic Factory Recovery System

LLM-agent middleware for event-driven FJSP recovery.

## Quick Start

```bash
docker compose up -d
uv run python -m coe.cli import mk01
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli dashboard          # Streamlit cockpit at http://127.0.0.1:8501
```

See [docs/dashboard-demo.md](docs/dashboard-demo.md) for the full 5-minute demo walkthrough.
