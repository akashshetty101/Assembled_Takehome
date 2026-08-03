import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_sane():
    settings = Settings()
    assert settings.tick_interval_sec > 0
    assert settings.replay_speed > 0
    assert settings.scheduler_impl == "scan"
    assert settings.db_path
    assert settings.schema_path
    assert settings.seeds_path


def test_env_var_overrides_db_path(monkeypatch):
    monkeypatch.setenv("APP_DB_PATH", "/tmp/custom.db")
    settings = Settings()
    assert settings.db_path == "/tmp/custom.db"


def test_scheduler_impl_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(scheduler_impl="round_robin")


@pytest.mark.parametrize("bad_value", [0, -1])
def test_tick_interval_must_be_positive(bad_value):
    with pytest.raises(ValidationError):
        Settings(tick_interval_sec=bad_value)


@pytest.mark.parametrize("bad_value", [0, -1.0])
def test_replay_speed_must_be_positive(bad_value):
    with pytest.raises(ValidationError):
        Settings(replay_speed=bad_value)


def test_settings_is_frozen_instance_reusable():
    a = Settings()
    b = Settings()
    assert a.db_path == b.db_path
