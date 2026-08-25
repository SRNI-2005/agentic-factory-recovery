"""Phase 3 §9 configuration defaults + fail-fast pre-flight."""
import pytest

from coe.config import Settings


def test_defaults():
    s = Settings(llm_provider="openai", llm_model="gpt-4o-mini",
                 _env_file=None)
    assert s.llm_temperature == 0.0
    assert s.strategy_max_rounds == 3
    assert s.llm_max_retries == 2
    assert s.benchmark_translation_accuracy == 0.90
    assert s.recovery_lock_wait_seconds == 600


def test_provider_and_model_have_no_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider is None
    assert s.llm_model is None


def test_preflight_fails_loud_when_unset():
    from coe.agents.llm_client import LLMConfigError, require_llm_config
    with pytest.raises(LLMConfigError):
        require_llm_config(Settings(_env_file=None))


def test_preflight_passes_when_set():
    from coe.agents.llm_client import require_llm_config
    require_llm_config(Settings(llm_provider="openai", llm_model="x",
                                _env_file=None))
