import json
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.cli.main import app


runner = CliRunner()


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title": "AI GTM Plan",
                                "audience": "sales leadership",
                                "slides": [
                                    {
                                        "title": "Context",
                                        "bullets": ["Why now", "Market pressure"],
                                        "speaker_notes": "Frame the urgency.",
                                    },
                                    {
                                        "title": "Strategy",
                                        "bullets": ["Positioning", "Execution"],
                                        "speaker_notes": "",
                                    },
                                    {
                                        "title": "Next Steps",
                                        "bullets": ["Owners", "Metrics"],
                                        "speaker_notes": "",
                                    },
                                ],
                            }
                        )
                    }
                }
            ]
        }


def test_llm_configure_and_set_key_persist_locally():
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["llm", "configure", "--provider", "deepseek", "--model", "deepseek-chat"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["llm", "set-key", "deepseek", "--api-key", "sk-test"])
        assert result.exit_code == 0

        config = json.loads(Path(".ppt-agent/llm/config.json").read_text(encoding="utf-8"))
        key = Path(".ppt-agent/llm/keys/deepseek.key").read_text(encoding="utf-8")

        assert config == {"provider": "deepseek", "model": "deepseek-chat"}
        assert key == "sk-test"


def test_plan_uses_saved_llm_configuration(monkeypatch):
    captured: dict = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("ppt_agent.llm.planner.httpx.post", fake_post)

    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "configure", "--provider", "deepseek", "--model", "deepseek-chat"])
        runner.invoke(app, ["llm", "set-key", "deepseek", "--api-key", "sk-deepseek"])

        result = runner.invoke(app, ["plan", "AI GTM Plan", "--spec", "plan.json"])

        assert result.exit_code == 0
        payload = json.loads(Path("plan.json").read_text(encoding="utf-8"))
        assert payload["schema_version"] == 2
        assert payload["title"] == "AI GTM Plan"
        assert payload["slides"][0]["title"] == "Context"
        assert "visual_type" in payload["slides"][0]
        assert captured["url"] == "https://api.deepseek.com/chat/completions"
        assert captured["json"]["model"] == "deepseek-chat"
        assert captured["headers"]["Authorization"] == "Bearer sk-deepseek"


def test_run_provider_and_model_override_use_selected_provider(monkeypatch):
    captured: dict = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("ppt_agent.llm.planner.httpx.post", fake_post)

    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "set-key", "kimi", "--api-key", "sk-kimi"])

        result = runner.invoke(
            app,
            [
                "run",
                "Partner Enablement",
                "--provider",
                "kimi",
                "--model",
                "moonshot-v1-8k",
                "--auto-approve",
                "--out",
                "deck.pptx",
            ],
        )

        assert result.exit_code == 0
        assert Path("deck.pptx").exists()
        assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
        assert captured["json"]["model"] == "moonshot-v1-8k"


def test_llm_test_uses_default_configuration(monkeypatch):
    captured: dict = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("ppt_agent.llm.planner.httpx.post", fake_post)

    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "configure", "--provider", "deepseek", "--model", "deepseek-chat"])
        runner.invoke(app, ["llm", "set-key", "deepseek", "--api-key", "sk-test"])

        result = runner.invoke(app, ["llm", "test"])

        assert result.exit_code == 0
        assert "Provider: deepseek" in result.output
        assert "Model: deepseek-chat" in result.output
        assert "Key Status: present" in result.output
        assert "Connection OK: yes" in result.output
        assert captured["url"] == "https://api.deepseek.com/chat/completions"
        assert captured["json"]["model"] == "deepseek-chat"


def test_llm_test_rejects_provider_model_mismatch():
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["llm", "test", "--provider", "deepseek", "--model", "moonshot-v1-8k"])

        assert result.exit_code != 0
        assert "unsupported model for deepseek" in result.output


def test_llm_test_fails_when_key_missing():
    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "configure", "--provider", "deepseek", "--model", "deepseek-chat"])

        result = runner.invoke(app, ["llm", "test"])

        assert result.exit_code == 1
        assert "missing API key for provider deepseek" in result.output


def test_llm_test_api_failure_returns_nonzero(monkeypatch):
    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        raise __import__("httpx").HTTPStatusError(
            "500 Server Error",
            request=__import__("httpx").Request("POST", url),
            response=__import__("httpx").Response(500, request=__import__("httpx").Request("POST", url)),
        )

    monkeypatch.setattr("ppt_agent.llm.planner.httpx.post", fake_post)

    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "configure", "--provider", "kimi", "--model", "moonshot-v1-8k"])
        runner.invoke(app, ["llm", "set-key", "kimi", "--api-key", "sk-kimi"])

        result = runner.invoke(app, ["llm", "test"])

        assert result.exit_code == 1
        assert "connection test failed" in result.output


def test_llm_test_with_explicit_provider_and_model(monkeypatch):
    captured: dict = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("ppt_agent.llm.planner.httpx.post", fake_post)

    with runner.isolated_filesystem():
        runner.invoke(app, ["llm", "set-key", "kimi", "--api-key", "sk-kimi"])

        result = runner.invoke(app, ["llm", "test", "--provider", "kimi", "--model", "moonshot-v1-8k"])

        assert result.exit_code == 0
        assert "Provider: kimi" in result.output
        assert "Model: moonshot-v1-8k" in result.output
        assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
