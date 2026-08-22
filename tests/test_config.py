from coe.config import Settings


def test_defaults_match_spec():
    s = Settings(_env_file=None)  # ignore any local .env
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.mqtt_port == 1883
    assert s.default_seed == 42
    assert s.telemetry_chunk_interval_minutes == 10080
