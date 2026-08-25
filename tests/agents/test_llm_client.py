"""§3.3/§9: narrow client interface, provider factory, fake injection."""
import pytest

from coe.config import Settings


def _settings(provider, model):
    return Settings(llm_provider=provider, llm_model=model, _env_file=None)


def test_factory_unknown_provider_rejected():
    from coe.agents.llm_client import LLMConfigError, make_llm_client
    with pytest.raises(LLMConfigError):
        make_llm_client(_settings("carrier-pigeon", "x"))


def test_factory_openai_adapter(monkeypatch):
    # Constructor must stay offline: dummy key satisfies validation only.
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    from coe.agents.llm_client import make_llm_client
    c = make_llm_client(_settings("openai", "gpt-4o-mini"))
    assert hasattr(c, "complete")


def test_factory_gemini_adapter(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    from coe.agents.llm_client import make_llm_client
    c = make_llm_client(_settings("gemini", "gemini-2.0-flash"))
    assert hasattr(c, "complete")


def test_fake_client_pops_in_order():
    from tests.fixtures.llm.fake_client import FakeLLMClient
    f = FakeLLMClient(["first", "second"])
    assert f.complete(system="s", user="u") == "first"
    assert f.complete(system="s", user="u") == "second"
    with pytest.raises(AssertionError):
        f.complete(system="s", user="u")


def test_fake_client_routes_on_substring():
    from tests.fixtures.llm.fake_client import FakeLLMClient
    f = FakeLLMClient({"MC-04": '{"kind":"MACHINE"}',
                       "W-03": '{"kind":"WORKER"}'})
    assert f.complete(system="s", user="narrative about W-03 sick") \
        == '{"kind":"WORKER"}'
    assert f.complete(system="s", user="gearbox MC-04 seized") \
        == '{"kind":"MACHINE"}'
    # .calls records (user, system) — what was sent, per the §11 contract.
    assert f.calls == [
        ("narrative about W-03 sick", "s"),
        ("gearbox MC-04 seized", "s"),
    ]
