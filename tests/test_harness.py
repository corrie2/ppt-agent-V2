from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ppt_agent.agent.skill_loader import load_user_skill
from ppt_agent.app.web_service import PptAgentWebService
from ppt_agent.cli.main import app
from ppt_agent.domain.models import PptSpec, SlideSpec
from ppt_agent.runtime.harness.events import append_event
from ppt_agent.runtime.harness.gates import run_quality_gates
from ppt_agent.runtime.harness.manifest import create_manifest, load_manifest, update_stage
from ppt_agent.runtime.harness.runner import HarnessRunner
from ppt_agent.storage.plan_io import read_plan_document


runner = CliRunner()


def test_harness_manifest_tracks_stage_and_resume(tmp_path):
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Harness")

    update_stage(task_dir, manifest, "request", status="passed", output_path=str(task_dir / "input" / "request.json"))
    loaded = load_manifest(task_dir)

    assert loaded.task_id == "task-1"
    assert loaded.stage("request").status == "passed"
    assert loaded.resume["last_passed_stage"] == "request"
    assert loaded.resume["next_stage"] == "source_ingest"


def test_quality_gate_flags_dense_content():
    result = run_quality_gates(
        "content",
        {
            "slides": [
                {
                    "slide_no": 1,
                    "title": "Dense",
                    "message": "Too much",
                    "bullets": ["a", "b", "c", "d"],
                }
            ]
        },
    )

    assert result.status == "needs_rework"
    assert result.issues[0].rule == "max_bullets_per_slide"


def test_task_resume_writes_plan_from_manifest(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        cwd = Path.cwd()
        task_dir = cwd / ".ppt-agent" / "tasks" / "task-1"
        manifest = create_manifest(task_dir, task_id="task-1", topic="Recovered Topic")
        spec = PptSpec(title="Recovered Topic", slides=[SlideSpec(title="One", message="Message")])
        spec_path = task_dir / "build" / "ppt_spec.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        manifest.outputs["ppt_spec"] = "build/ppt_spec.json"
        (task_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        result = runner.invoke(app, ["task", "resume", "task-1", "--out", "resumed.json"])

        assert result.exit_code == 0
        document = read_plan_document(Path("resumed.json"))
        assert document.spec.title == "Recovered Topic"
        assert document.spec.slides[0].title == "One"


def test_web_service_exposes_harness_task_state(tmp_path):
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Harness Web")
    output_path = task_dir / "stages" / "01_request" / "output.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"topic":"Harness Web"}', encoding="utf-8")
    update_stage(task_dir, manifest, "request", status="passed", output_path=str(output_path))
    append_event(task_dir, "request.completed", stage_id="request", payload={"ok": True})

    service = PptAgentWebService()
    session = service.create_session(cwd=str(tmp_path), assistant_enabled=False)
    session_id = session["session_id"]

    tasks = service.harness_tasks(session_id)
    detail = service.harness_task(session_id, "task-1")
    artifacts = service.harness_task_artifacts(session_id, "task-1")
    events = service.harness_task_events(session_id, "task-1")
    state = service.state(session_id)

    assert tasks[0]["task_id"] == "task-1"
    assert state["harness_tasks"][0]["current_stage"] == "request"
    assert detail["manifest"]["topic"] == "Harness Web"
    assert artifacts[0]["kind"] == "output"
    assert events[0]["event"] == "request.completed"


def test_harness_runner_approval_and_preview_flow(tmp_path):
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Harness Runner")
    for stage_id in ("request", "source_ingest", "brief_outline"):
        update_stage(task_dir, manifest, stage_id, status="passed")
    update_stage(task_dir, manifest, "plan_confirm", status="waiting_approval")
    for stage_id in ("content", "design_chart", "slides_ir", "qa", "repair", "page_design", "renderer_engineer"):
        update_stage(task_dir, manifest, stage_id, status="passed")
    spec = PptSpec(title="Harness Runner", slides=[SlideSpec(title="One", message="Message")])
    spec_path = task_dir / "build" / "ppt_spec.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    manifest.outputs["ppt_spec"] = "build/ppt_spec.json"
    update_stage(task_dir, manifest, "page_generator", status="passed", output_path=str(spec_path))
    update_stage(task_dir, manifest, "page_preview", status="pending")
    update_stage(task_dir, manifest, "render_review", status="passed")
    update_stage(task_dir, manifest, "build_confirm", status="waiting_approval")

    waiting = HarnessRunner(task_dir).run_until_blocked()
    assert waiting.action == "waiting_approval"
    assert waiting.stage_id == "plan_confirm"

    after_approval = HarnessRunner(task_dir).approve("plan_confirm", note="ok")
    loaded = load_manifest(task_dir)

    assert after_approval.action == "waiting_approval"
    assert after_approval.stage_id == "build_confirm"
    assert loaded.stage("plan_confirm").status == "approved"
    assert loaded.stage("page_preview").status == "passed"
    assert (task_dir / "previews" / "slide_001.json").exists()


def test_harness_runner_rejects_confirmation(tmp_path):
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Harness Reject")
    update_stage(task_dir, manifest, "plan_confirm", status="waiting_approval")

    action = HarnessRunner(task_dir).reject("plan_confirm", reason="outline mismatch")
    loaded = load_manifest(task_dir)

    assert action.action == "needs_rework"
    assert loaded.status == "rejected"
    assert loaded.stage("plan_confirm").status == "rejected"


def test_harness_runner_regenerates_invalidated_content_downstream(tmp_path):
    config_path = tmp_path / ".ppt-agent" / "agents" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"enabled": False, "agents": {}}, ensure_ascii=False), encoding="utf-8")
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Retry Topic")
    brief_outline_path = task_dir / "stages" / "03_brief_outline" / "output.json"
    brief_outline_path.parent.mkdir(parents=True, exist_ok=True)
    brief_outline = {
        "brief": {"topic": "Retry Topic", "audience": "students", "language": "zh", "page_count": 1},
        "outline": {"agent": "brief_outline", "slides": [{"slide_no": 1, "type": "cover", "title": "Retry Topic", "goal": "open"}]},
    }
    brief_outline_path.write_text(json.dumps(brief_outline, ensure_ascii=False), encoding="utf-8")
    for stage_id in ("request", "source_ingest"):
        update_stage(task_dir, manifest, stage_id, status="passed")
    update_stage(task_dir, manifest, "brief_outline", status="passed", output_path=str(brief_outline_path))
    update_stage(task_dir, manifest, "plan_confirm", status="approved")
    for stage_id in ("content", "design_chart", "slides_ir", "qa", "repair", "page_design", "renderer_engineer", "page_generator", "page_preview", "render_review", "build_confirm"):
        update_stage(task_dir, manifest, stage_id, status="invalidated", invalidated_by="test retry")

    action = HarnessRunner(task_dir).run_until_blocked()
    loaded = load_manifest(task_dir)

    assert action.action == "waiting_approval"
    assert action.stage_id == "build_confirm"
    assert loaded.stage("content").status == "passed"
    assert loaded.stage("page_generator").status == "passed"
    assert loaded.outputs["ppt_spec"].endswith("output.json")
    assert (task_dir / "previews" / "slide_001.json").exists()


def test_web_service_harness_approval_actions(tmp_path):
    task_dir = tmp_path / ".ppt-agent" / "tasks" / "task-1"
    manifest = create_manifest(task_dir, task_id="task-1", topic="Harness Web Approval")
    update_stage(task_dir, manifest, "plan_confirm", status="waiting_approval")

    service = PptAgentWebService()
    session = service.create_session(cwd=str(tmp_path), assistant_enabled=False)
    result = service.harness_task_approve(session["session_id"], "task-1", stage="plan_confirm", note="ok")

    assert result["action"]["task_id"] == "task-1"
    assert result["state"]["harness_tasks"][0]["task_id"] == "task-1"


def test_v2_skill_manifest_loads_extended_fields(tmp_path):
    skill_dir = tmp_path / "academic-paper-deck"
    skill_dir.mkdir()
    (skill_dir / "skill.md").write_text("# academic-paper-deck\n\nRules.", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": "academic-paper-deck",
                "description": "Academic paper deck.",
                "type": "markdown",
                "agent_scope": ["content", "qa"],
                "applies_to": ["paper"],
                "quality_gates": ["citation_required_when_evidence"],
                "artifacts": {"qa_rules": "qa_rules.json"},
                "examples": ["examples/request.json"],
                "version": "2.0.0",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_user_skill(skill_dir)

    assert loaded.enabled
    assert loaded.manifest.applies_to == ["paper"]
    assert loaded.manifest.quality_gates == ["citation_required_when_evidence"]
