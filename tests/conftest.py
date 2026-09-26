import pytest


@pytest.fixture(autouse=True)
def disable_external_language_calls(monkeypatch):
    monkeypatch.setenv("LANGUAGE_ENRICHMENT_ENABLED", "0")
