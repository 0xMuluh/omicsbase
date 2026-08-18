"""Configuration credentials must never appear in model/log repr output."""

from app.config import Settings


def test_secret_settings_are_excluded_from_repr():
    configured = Settings(
        openai_api_key="sentinel-openai-secret",
        dashscope_api_key="sentinel-dashscope-secret",
        database_url="postgresql://sentinel-db-secret@localhost/db",
        redis_url="redis://sentinel-redis-secret@localhost/0",
        api_key="sentinel-shared-secret",
    )

    rendered = repr(configured)

    assert "sentinel" not in rendered
    assert configured.openai_api_key == "sentinel-openai-secret"
