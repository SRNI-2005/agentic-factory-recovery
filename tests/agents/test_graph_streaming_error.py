"""Tests for Critical #2: streaming recovery exception handling.

Non-TranslationFailed exceptions during streaming must produce a terminal
error outcome (not escape and leave result=None in the cockpit).
"""
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

TRANSLATE_OK = ('{"kind": "MACHINE", "instance_id": "g-world", '
                '"machine_id": "M2", "event_type": "FAILURE", '
                '"occurred_at": 30, "severity": "HIGH", '
                '"estimated_downtime": 200, "narrative_excerpt": "boom"}')


@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world
    build_g_world(clean_db)


class TestStreamingExceptionHandling:

    def test_non_translation_exception_records_error_run(self, g_world,
                                                         monkeypatch):
        """A RuntimeError mid-stream must still yield a terminal outcome
        with a run recorded — not escape without recording."""
        import coe.agents.graph as graph_mod
        from coe.agents.graph import execute_recovery_streaming

        class BoomApp:
            def stream(self, initial, stream_mode=None):
                yield {"translate": None}
                raise RuntimeError("LLM client exploded")

        monkeypatch.setattr(graph_mod, "build_graph",
                            lambda *a, **k: BoomApp())
        items = list(execute_recovery_streaming(
            "g-world", trigger="CLI", narrative="boom",
            reference_clock=30, client=object(), lock_wait=5))
        # Must have yielded a terminal dict (not nothing)
        final = [i for i in items if "status" in i]
        assert len(final) == 1
        assert final[0]["status"] == "STREAMING_ERROR"
        assert "LLM client exploded" in str(final[0].get("state", "")), \
            f"unexpected state: {final[0].get('state')}"
        # Run must be recorded
        from sqlalchemy import text
        from coe.db.session import make_engine
        engine = make_engine()
        with engine.begin() as c:
            n = c.execute(text(
                "SELECT count(*) FROM recovery_runs WHERE status="
                "'STREAMING_ERROR'")).scalar_one()
        assert n == 1

    def test_streaming_error_has_run_id(self, g_world, monkeypatch):
        """Terminal error outcome must include a run_id for cockpit parity."""
        import coe.agents.graph as graph_mod
        from coe.agents.graph import execute_recovery_streaming

        class BoomApp:
            def stream(self, initial, stream_mode=None):
                yield {"ingest": None}
                raise ValueError("kaboom")

        monkeypatch.setattr(graph_mod, "build_graph",
                            lambda *a, **k: BoomApp())
        items = list(execute_recovery_streaming(
            "g-world", trigger="CLI", narrative="test",
            reference_clock=30, client=object(), lock_wait=5))
        final = [i for i in items if "status" in i]
        assert len(final) == 1
        assert "run_id" in final[0]
        assert final[0]["run_id"] is not None

    def test_streaming_error_yields_node_before_crash(self, g_world,
                                                      monkeypatch):
        """Nodes streamed before the crash must still appear in output."""
        import coe.agents.graph as graph_mod
        from coe.agents.graph import execute_recovery_streaming

        class BoomApp:
            def stream(self, initial, stream_mode=None):
                yield {"translate": None}
                yield {"ingest": None}
                raise OSError("network down")

        monkeypatch.setattr(graph_mod, "build_graph",
                            lambda *a, **k: BoomApp())
        items = list(execute_recovery_streaming(
            "g-world", trigger="CLI", narrative="test",
            reference_clock=30, client=object(), lock_wait=5))
        nodes = [i["node"] for i in items if "node" in i]
        assert "translate" in nodes
        assert "ingest" in nodes
        final = [i for i in items if "status" in i]
        assert len(final) == 1
        assert final[0]["status"] == "STREAMING_ERROR"
