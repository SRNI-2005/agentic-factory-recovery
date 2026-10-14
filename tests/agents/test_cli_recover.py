# tests/agents/test_cli_recover.py
"""§10 command surface + §9 pre-flight."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world

    build_g_world(clean_db)


def test_preflight_fails_fast(g_world, monkeypatch):
    """Missing provider/model exits before any graph work (§9)."""
    import coe.cli as cli
    from coe.config import Settings, get_settings

    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom"])
    # pydantic_settings reads .env directly, so monkeypatch the Settings
    # object's lru_cache to return a config without provider/model.
    get_settings.cache_clear()
    monkeypatch.setattr(
        "coe.config.get_settings",
        lambda: Settings(
            database_url=get_settings().database_url,
            llm_provider=None,
            llm_model=None,
        ),
    )
    try:
        with pytest.raises(SystemExit, match="LLM_PROVIDER"):
            cli._run_recover(args)
    finally:
        get_settings.cache_clear()


def test_recover_happy_path(g_world, capsys):
    import coe.cli as cli
    from tests.fixtures.llm.fake_client import FakeLLMClient

    fake = FakeLLMClient(['{"kind": "MACHINE", "instance_id": "g-world", '
                          '"machine_id": "M2", "event_type": "FAILURE", '
                          '"occurred_at": 30, "severity": "HIGH", '
                          '"estimated_downtime": 200, '
                          '"narrative_excerpt": "boom"}',
                          '{"candidates": [], "final": true}',
                          "Rerouted off M2."])
    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom",
        "--at", "30"])
    cli._run_recover(args, client=fake)
    assert "COMMITTED" in capsys.readouterr().out


def test_explain_prints_rationale(g_world, capsys):
    import coe.cli as cli
    from tests.fixtures.llm.fake_client import FakeLLMClient

    # first commit something via recover...
    fake = FakeLLMClient(['{"kind": "MACHINE", "instance_id": "g-world", '
                          '"machine_id": "M2", "event_type": "FAILURE", '
                          '"occurred_at": 30, "severity": "HIGH", '
                          '"estimated_downtime": 200, '
                          '"narrative_excerpt": "boom"}',
                          '{"candidates": [], "final": true}',
                          "Rerouted off M2."])
    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom",
        "--at", "30"])
    cli._run_recover(args, client=fake)

    # ...then explain it with a fresh fake
    expl = FakeLLMClient(["Moved jobs off M2 after its failure."])
    eargs = cli.build_parser().parse_args(
        ["explain", "--instance", "g-world"])
    cli._run_explain(eargs, client=expl)
    assert "Moved jobs off M2" in capsys.readouterr().out
