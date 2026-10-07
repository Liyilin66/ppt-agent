import pytest
from fastapi.testclient import TestClient
from ppt_agent import api, deck_jobs


def payload(**extra):
    return dict(topic="测试", audience="企业", slide_count=10,
                deck_type="visual_design_v2", user_requirements="测试材料", **extra)


def test_search_request_default_and_confirm_override():
    assert api.CreateLongDeckJobRequest(**payload()).enable_search is False
    assert api.ConfirmDeckPlanRequest(enable_search=True).enable_search is True


def test_search_capability_does_not_leak_key(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_dotenv_file", lambda: [])
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    client = TestClient(api.create_app(data_dir=tmp_path))
    assert client.get("/api/capabilities").json()["search_available"] is False
    monkeypatch.setenv("TAVILY_API_KEY", "private-test-key")
    response = client.get("/api/capabilities")
    assert response.json()["search_available"] is True
    assert "private-test-key" not in response.text


def test_search_without_config_rejected_before_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_dotenv_file", lambda: [])
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    def unexpected():
        raise AssertionError("model must not initialize")
    monkeypatch.setattr(deck_jobs, "create_v2_model_client", unexpected)
    client = TestClient(api.create_app(data_dir=tmp_path))
    for path in ["/api/deck-plans", "/api/long-deck-jobs"]:
        response = client.post(path, json=payload(enable_search=True))
        assert response.status_code == 503
        assert "TAVILY_API_KEY" in response.json()["detail"]


def test_confirmation_ui_contains_disabled_search_switches(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_dotenv_file", lambda: [])
    client = TestClient(api.create_app(data_dir=tmp_path))
    html = client.get("/").text
    assert 'id="enableSearch"' in html
    assert 'id="planEnableSearch"' in html
    assert 'TAVILY_API_KEY' in html
    js = client.get("/static/app.js").text
    assert '/api/capabilities' in js
    assert 'enable_search:' in js


@pytest.mark.parametrize("override, expected", [(None, True), (True, True), (False, False)])
def test_search_survives_plan_and_confirmation_override(tmp_path, monkeypatch, override, expected):
    from test_api import _install_fake_deck_plan_backend, _install_fake_v2_long_deck_backend
    monkeypatch.setattr(api, "load_dotenv_file", lambda: [])
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    captured = {}
    _install_fake_deck_plan_backend(monkeypatch, captured)
    _install_fake_v2_long_deck_backend(monkeypatch, captured)
    provider = object()
    monkeypatch.setattr(deck_jobs, "default_search_provider", lambda: provider)
    original_plan = api.plan_v2_deck
    original_build = deck_jobs.build_v2_deck
    def plan(request, client, **kwargs):
        captured["plan_search_provider"] = kwargs["search_provider"]
        return original_plan(request, client, **kwargs)
    def build(request, client, **kwargs):
        captured["build_search_provider"] = kwargs["search_provider"]
        return original_build(request, client, **kwargs)
    monkeypatch.setattr(api, "plan_v2_deck", plan)
    monkeypatch.setattr(deck_jobs, "build_v2_deck", build)
    client = TestClient(api.create_app(data_dir=tmp_path))
    created = client.post("/api/deck-plans", json=payload(enable_search=True))
    assert created.status_code == 202
    plan_id = created.json()["plan_id"]
    assert captured["plan_request"].enable_search is True
    assert captured["plan_search_provider"] is provider
    assert client.get(f"/api/deck-plans/{plan_id}").json()["request"]["enable_search"] is True
    result = client.post(f"/api/deck-plans/{plan_id}/confirm", json={} if override is None else {"enable_search": override})
    assert result.status_code == 202
    assert captured["request"].enable_search is expected
    assert captured["build_search_provider"] is (provider if expected else None)


def test_confirm_rechecks_search_capability_before_creating_job(tmp_path, monkeypatch):
    from test_api import _install_fake_deck_plan_backend
    monkeypatch.setattr(api, "load_dotenv_file", lambda: [])
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    _install_fake_deck_plan_backend(monkeypatch)
    monkeypatch.setattr(deck_jobs, "default_search_provider", lambda: object())
    client = TestClient(api.create_app(data_dir=tmp_path))
    created = client.post("/api/deck-plans", json=payload(enable_search=True))
    plan_id = created.json()["plan_id"]
    monkeypatch.delenv("TAVILY_API_KEY")
    denied = client.post(f"/api/deck-plans/{plan_id}/confirm")
    assert denied.status_code == 503
    assert "TAVILY_API_KEY" in denied.json()["detail"]
    assert client.get(f"/api/deck-plans/{plan_id}").json()["status"] == "ready"
