import json

from typer.testing import CliRunner

from ppt_agent.cli.main import app


runner = CliRunner()


def test_run_plan_out_writes_plan_file(tmp_path):
    plan_path = tmp_path / "review-plan.json"
    pptx_path = tmp_path / "deck.pptx"

    result = runner.invoke(
        app,
        [
            "run",
            "Plan Out Smoke",
            "--plan-out",
            str(plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0
    assert plan_path.exists()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["request"]["topic"] == "Plan Out Smoke"
    assert payload["title"] == "Plan Out Smoke"
    assert payload["outline"]
    assert payload["slides"]
    assert payload["transitions"] == ["plan", "asset_plan", "asset_resolve"]
    assert any(slide["visual_type"] for slide in payload["slides"])
    assert "wrote review file" in result.output
    assert pptx_path.exists()


def test_run_rejection_keeps_plan_file_without_pptx(tmp_path):
    plan_path = tmp_path / "rejected-plan.json"
    pptx_path = tmp_path / "rejected.pptx"

    result = runner.invoke(
        app,
        [
            "run",
            "Rejected Plan",
            "--plan-out",
            str(plan_path),
            "--out",
            str(pptx_path),
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert plan_path.exists()
    assert not pptx_path.exists()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["request"]["topic"] == "Rejected Plan"
    assert payload["approved"] is False
    assert payload["transitions"] == ["plan", "asset_plan", "asset_resolve"]
    assert "rejected, build skipped" in result.output


def test_run_from_plan_auto_approve_builds_pptx(tmp_path):
    plan_path = tmp_path / "valid-plan.json"
    pptx_path = tmp_path / "from-plan.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request": {"topic": "Edited Plan", "audience": "leadership"},
                "title": "Edited Plan",
                "theme": None,
                "outline": ["Opening", "Decision"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context", "Goal"], "speaker_notes": ""},
                    {"title": "Decision", "bullets": ["Recommendation", "Next step"], "speaker_notes": ""},
                    {"title": "Risks", "bullets": ["Dependency", "Mitigation"], "speaker_notes": ""},
                ],
                "mode": "execute",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            "Ignored Topic",
            "--from-plan",
            str(plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0
    assert pptx_path.exists()
    assert "loaded review file" in result.output
    assert "using plan from file; ignoring provided topic" in result.output
    assert "Title: Edited Plan" in result.output


def test_run_from_plan_invalid_fails_without_pptx(tmp_path):
    plan_path = tmp_path / "invalid-plan.json"
    pptx_path = tmp_path / "invalid.pptx"
    plan_path.write_text('{"title": "Broken", "slide_specs": "not slides"}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "Invalid Plan",
            "--from-plan",
            str(plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 1
    assert not pptx_path.exists()
    assert "invalid plan schema" in result.output


def test_plan_spec_and_run_plan_out_share_same_schema(tmp_path):
    plan_spec_path = tmp_path / "plan-spec.json"
    run_plan_path = tmp_path / "run-plan.json"
    pptx_path = tmp_path / "schema-check.pptx"

    plan_result = runner.invoke(app, ["plan", "Unified Schema", "--spec", str(plan_spec_path)])
    run_result = runner.invoke(
        app,
        [
            "run",
            "Unified Schema",
            "--plan-out",
            str(run_plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert plan_result.exit_code == 0
    assert run_result.exit_code == 0

    plan_payload = json.loads(plan_spec_path.read_text(encoding="utf-8"))
    run_payload = json.loads(run_plan_path.read_text(encoding="utf-8"))

    assert set(plan_payload) == set(run_payload)
    assert plan_payload["schema_version"] == run_payload["schema_version"] == 2
    assert plan_payload["title"] == run_payload["title"] == "Unified Schema"
    assert plan_payload["slides"] == run_payload["slides"]
    assert plan_payload["outline"] == run_payload["outline"]
    assert isinstance(plan_payload["request"], dict)
    assert isinstance(run_payload["request"], dict)


def test_run_from_plan_legacy_slide_specs_is_still_supported(tmp_path):
    plan_path = tmp_path / "legacy-plan.json"
    pptx_path = tmp_path / "legacy-plan.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "request": {"topic": "Legacy Plan", "audience": "leadership"},
                "title": "Legacy Plan",
                "theme": None,
                "outline": ["Opening", "Decision"],
                "slide_specs": [
                    {"title": "Opening", "bullets": ["Context", "Goal"], "speaker_notes": ""},
                    {"title": "Decision", "bullets": ["Recommendation", "Next step"], "speaker_notes": ""},
                    {"title": "Risks", "bullets": ["Dependency", "Mitigation"], "speaker_notes": ""},
                ],
                "mode": "execute",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            "Ignored Topic",
            "--from-plan",
            str(plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0
    assert pptx_path.exists()
    assert "legacy compatibility plan file" in result.output
    assert "Recommendation: run `ppt-agent migrate-plan" in result.output


def test_run_from_plan_without_topic_builds_pptx(tmp_path):
    plan_path = tmp_path / "no-topic-plan.json"
    pptx_path = tmp_path / "no-topic.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request": {"topic": "Plan File Topic", "audience": "leadership"},
                "title": "Plan File Topic",
                "theme": None,
                "outline": ["Opening", "Decision"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context", "Goal"], "speaker_notes": ""},
                    {"title": "Decision", "bullets": ["Recommendation", "Next step"], "speaker_notes": ""},
                    {"title": "Risks", "bullets": ["Dependency", "Mitigation"], "speaker_notes": ""},
                ],
                "mode": "execute",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--from-plan",
            str(plan_path),
            "--out",
            str(pptx_path),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0
    assert pptx_path.exists()
    assert "loaded review file" in result.output


def test_validate_versioned_plan_succeeds(tmp_path):
    plan_path = tmp_path / "versioned-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request": {"topic": "Validated Plan", "audience": "leadership"},
                "title": "Validated Plan",
                "theme": None,
                "outline": ["Opening", "Decision"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": "", "visual_type": "hero_image"},
                    {"title": "Decision", "bullets": ["Recommendation"], "speaker_notes": "", "visual_type": "comparison_table"},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path)])

    assert result.exit_code == 0
    assert "Schema Version: 2" in result.output
    assert "Format: formal schema" in result.output
    assert "Source Type: versioned" in result.output
    assert "Slides: 2" in result.output


def test_validate_legacy_slide_specs_succeeds(tmp_path):
    plan_path = tmp_path / "legacy-slide-specs.json"
    plan_path.write_text(
        json.dumps(
            {
                "request": {"topic": "Legacy Validate", "audience": "leadership"},
                "title": "Legacy Validate",
                "theme": None,
                "outline": ["Opening"],
                "slide_specs": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path)])

    assert result.exit_code == 0
    assert "Schema Version: none" in result.output
    assert "Format: legacy compatibility" in result.output
    assert "Source Type: legacy_slide_specs" in result.output
    assert "Warning: legacy compatibility format" in result.output
    assert "Recommendation: run `ppt-agent migrate-plan" in result.output


def test_validate_invalid_file_fails(tmp_path):
    plan_path = tmp_path / "invalid-validate.json"
    plan_path.write_text('{"title": "Broken", "slides": "bad"}', encoding="utf-8")

    result = runner.invoke(app, ["validate", str(plan_path)])

    assert result.exit_code == 1
    assert "invalid plan schema" in result.output


def test_validate_future_schema_version_fails(tmp_path):
    plan_path = tmp_path / "future-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "request": {"topic": "Future Plan", "audience": "leadership"},
                "title": "Future Plan",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": "", "visual_type": "hero_image"},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path)])

    assert result.exit_code == 1
    assert "Format: unsupported schema version" in result.output
    assert "unsupported future schema version" in result.output


def test_validate_future_schema_version_fails_before_current_schema_parse(tmp_path):
    plan_path = tmp_path / "future-plan-new-shape.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "request": {"topic": "Future Plan", "audience": "leadership"},
                "deck": {"title": "Future Plan", "pages": []},
                "slides": "future schema no longer uses current slide list",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path), "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["format"] == "unsupported schema version"
    assert payload["schema_version"] == 3
    assert payload["errors"] == ["unsupported future schema version"]


def test_validate_json_success_outputs_valid_json(tmp_path):
    plan_path = tmp_path / "json-success-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request": {"topic": "JSON Success", "audience": "leadership"},
                "title": "JSON Success",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": "", "visual_type": "hero_image"},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path), "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["format"] == "formal schema"
    assert payload["schema_version"] == 2
    assert payload["source_type"] == "versioned"
    assert payload["slides_count"] == 1
    assert payload["title"] == "JSON Success"
    assert payload["errors"] == []
    assert payload["warnings"] == []


def test_validate_json_failure_outputs_valid_json(tmp_path):
    plan_path = tmp_path / "json-failure-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "next",
                "request": {"topic": "JSON Failure", "audience": "leadership"},
                "title": "JSON Failure",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path), "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["format"] == "invalid schema"
    assert payload["schema_version"] is None
    assert payload["source_type"] == "versioned"
    assert payload["errors"] == ["invalid schema_version"]


def test_build_future_schema_version_fails(tmp_path):
    plan_path = tmp_path / "future-build.json"
    pptx_path = tmp_path / "future-build.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "request": {"topic": "Future Build", "audience": "leadership"},
                "title": "Future Build",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["build", str(plan_path), "--out", str(pptx_path)])

    assert result.exit_code == 1
    assert not pptx_path.exists()
    assert "unsupported future schema version: 3, current supported version is 2" in result.output


def test_run_from_plan_future_schema_version_fails(tmp_path):
    plan_path = tmp_path / "future-run.json"
    pptx_path = tmp_path / "future-run.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "request": {"topic": "Future Run", "audience": "leadership"},
                "title": "Future Run",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--from-plan", str(plan_path), "--out", str(pptx_path)])

    assert result.exit_code == 1
    assert not pptx_path.exists()
    assert "unsupported future schema version: 3, current supported version is 2" in result.output


def test_build_legacy_plan_still_runs(tmp_path):
    plan_path = tmp_path / "legacy-build.json"
    pptx_path = tmp_path / "legacy-build.pptx"
    plan_path.write_text(
        json.dumps(
            {
                "request": {"topic": "Legacy Build", "audience": "leadership"},
                "title": "Legacy Build",
                "theme": None,
                "outline": ["Opening"],
                "slide_specs": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["build", str(plan_path), "--out", str(pptx_path)])

    assert result.exit_code == 0
    assert pptx_path.exists()
    assert "legacy compatibility plan file" in result.output
    assert "Recommendation: run `ppt-agent migrate-plan" in result.output


def test_validate_json_legacy_includes_migration_recommendation(tmp_path):
    plan_path = tmp_path / "legacy-json-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "request": {"topic": "Legacy JSON", "audience": "leadership"},
                "title": "Legacy JSON",
                "theme": None,
                "outline": ["Opening"],
                "slide_specs": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(plan_path), "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["format"] == "legacy compatibility"
    assert any("Recommendation: run `ppt-agent migrate-plan" in warning for warning in payload["warnings"])


def test_migrate_plan_legacy_slide_specs_to_formal_schema(tmp_path):
    input_path = tmp_path / "legacy-slide-specs.json"
    output_path = tmp_path / "migrated-slide-specs.json"
    input_path.write_text(
        json.dumps(
            {
                "request": {"topic": "Legacy Migrate", "audience": "leadership"},
                "title": "Legacy Migrate",
                "theme": None,
                "outline": ["Opening"],
                "slide_specs": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["migrate-plan", str(input_path), "--out", str(output_path)])

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert "slides" in payload
    assert "slide_specs" not in payload
    assert payload["slides"][0]["title"] == "Opening"
    assert "Source Type: legacy_slide_specs" in result.output


def test_migrate_plan_bare_pptspec_to_formal_schema(tmp_path):
    input_path = tmp_path / "bare-pptspec.json"
    output_path = tmp_path / "migrated-bare.json"
    input_path.write_text(
        json.dumps(
            {
                "title": "Bare Spec",
                "audience": "leadership",
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["migrate-plan", str(input_path), "--out", str(output_path)])

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["title"] == "Bare Spec"
    assert payload["request"]["topic"] == "Bare Spec"
    assert payload["slides"][0]["title"] == "Opening"
    assert "Source Type: bare_pptspec" in result.output


def test_migrate_plan_formal_schema_still_writes_formal_schema(tmp_path):
    input_path = tmp_path / "formal-v1.json"
    output_path = tmp_path / "formal-v1-out.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request": {"topic": "Formal Plan", "audience": "leadership"},
                "title": "Formal Plan",
                "theme": None,
                "outline": ["Opening"],
                "slides": [
                    {"title": "Opening", "bullets": ["Context"], "speaker_notes": ""},
                ],
                "mode": "plan",
                "approved": False,
                "transitions": ["plan"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["migrate-plan", str(input_path), "--out", str(output_path)])

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["title"] == "Formal Plan"
    assert "Already current schema, normalized output written" in result.output


def test_migrate_plan_invalid_file_fails_without_output(tmp_path):
    input_path = tmp_path / "invalid-migrate.json"
    output_path = tmp_path / "invalid-out.json"
    input_path.write_text('{"title": "Broken", "slides": "bad"}', encoding="utf-8")

    result = runner.invoke(app, ["migrate-plan", str(input_path), "--out", str(output_path)])

    assert result.exit_code == 1
    assert not output_path.exists()
    assert "invalid plan schema" in result.output
