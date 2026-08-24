from coe.config import Settings


def test_solver_defaults_match_spec():
    s = Settings()
    assert s.solver_time_limit_seconds == 60
    assert s.solver_alpha_weight == 1.0
    assert s.solver_beta_weight == 1.0
    assert s.solver_normalize_objectives is True
    assert s.solver_random_seed == 42
    assert s.solver_num_search_workers == 8


def test_env_override(monkeypatch):
    monkeypatch.setenv("SOLVER_ALPHA_WEIGHT", "2.5")
    monkeypatch.setenv("SOLVER_TIME_LIMIT_SECONDS", "120")
    s = Settings()
    assert s.solver_alpha_weight == 2.5
    assert s.solver_time_limit_seconds == 120


def test_settings_isolation(monkeypatch):
    for k in ("SOLVER_ALPHA_WEIGHT", "SOLVER_BETA_WEIGHT",
              "SOLVER_TIME_LIMIT_SECONDS", "SOLVER_RANDOM_SEED",
              "SOLVER_NUM_SEARCH_WORKERS", "SOLVER_NORMALIZE_OBJECTIVES"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert (s.solver_alpha_weight, s.solver_beta_weight,
            s.solver_random_seed, s.solver_num_search_workers,
            s.solver_normalize_objectives) == (1.0, 1.0, 42, 8, True)
