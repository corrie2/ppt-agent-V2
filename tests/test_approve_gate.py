from ppt_agent.domain.models import AgentMode, AgentState, DeckIntent
from ppt_agent.graph.agent import create_agent_graph


def test_rejected_approval_stops_before_build(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    output_path = tmp_path / "rejected.pptx"
    graph = create_agent_graph()
    state = AgentState(
        intent=DeckIntent(topic="Approval Gate", output_path=str(output_path)),
        mode=AgentMode.EXECUTE,
    )

    result = graph.invoke(state.model_dump(mode="json"))

    assert result["approved"] is False
    assert "asset_plan" in result["transitions"]
    assert "asset_resolve" in result["transitions"]
    assert "approve" in result["transitions"]
    assert "rejected" in result["transitions"]
    assert "build" not in result["transitions"]
    assert result.get("artifact") is None
    assert not output_path.exists()


def test_auto_approve_continues_to_build_and_qa(tmp_path):
    output_path = tmp_path / "approved.pptx"
    graph = create_agent_graph()
    state = AgentState(
        intent=DeckIntent(topic="Auto Approval", output_path=str(output_path)),
        mode=AgentMode.EXECUTE,
        approved=True,
    )

    result = graph.invoke(state.model_dump(mode="json"))

    assert result["approved"] is True
    assert result["transitions"] == ["plan", "asset_plan", "asset_resolve", "approve", "build", "qa"]
    assert result["artifact"]["path"] == str(output_path)
    assert output_path.exists()
