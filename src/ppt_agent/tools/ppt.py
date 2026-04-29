from __future__ import annotations

from pathlib import Path

from ppt_agent.domain.models import PptSpec
from ppt_agent.runtime.pptx import build_pptx
from ppt_agent.tools.base import Tool


build_pptx_tool = Tool(
    name="build_pptx",
    description="Build a PPTX artifact from a validated PptSpec.",
    handler=lambda spec, output_path: build_pptx(PptSpec.model_validate(spec), Path(output_path)),
)
