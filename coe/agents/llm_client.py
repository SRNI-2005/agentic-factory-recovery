class LLMConfigError(RuntimeError):
    """Missing LLM_PROVIDER/LLM_MODEL — setup error, never mid-run (§9)."""


def require_llm_config(settings) -> None:
    if not settings.llm_provider or not settings.llm_model:
        raise LLMConfigError(
            "set LLM_PROVIDER and LLM_MODEL before running recoveries "
            "(pre-flight check, spec §9)")
