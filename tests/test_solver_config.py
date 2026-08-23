from coe.config import Settings


def test_solver_defaults_match_spec():
    s = Settings()
    assert s.solver_time_limit_seconds == 60
    assert s.solver_alpha_weight == 1.0
    assert s.solver_beta_weight == 1.0
    assert s.solver_normalize_objectives is True
    assert s.solver_random_seed == 42
    assert s.solver_num_search_workers == 1


def test_env_override(monkeypatch):
    monkeypatch.setenv("SOLVER_ALPHA_WEIGHT", "2.5")
    monkeypatch.setenv("SOLVER_TIME_LIMIT_SECONDS", "120")
    s = Settings()
    assert s.solver_alpha_weight == 2.5
    assert s.solver_time_limit_seconds == 120
