# Renderer Engineering

## Core Responsibility
Assess current PPTX renderer capabilities, identify layout gaps, and generate extension plans.

## Standards

### Layout Requirements
- Grid-based layout with consistent margins (0.5 inch / 1.27cm)
- Horizontal flow: left-to-right reading order
- Support for: title, content, two-column, chart, image, blank layouts
- Fallback strategy: unsupported layouts degrade to nonblank fallback (never blank slides)

### Typography Constraints
- Minimum font size: 18pt for presentation slides
- Maximum fonts in deck: 3
- Supported: sans-serif preferred (Arial, Helvetica, Microsoft YaHei)
- Title: 20-28pt, Body: 18-20pt

### Color Constraints
- Maximum colors in deck: 5 (primary, secondary, accent, background, text)
- Contrast ratio >= 4.5:1 for text readability
- Support both light and dark themes

### Extension Plan Format
When identifying gaps, output:
```json
{
  "layout": "layout-name",
  "severity": "warning|error",
  "message": "Description of gap",
  "suggested_file": "src/ppt_agent/runtime/pptx.py",
  "function": "_render_layout_name"
}
```

### Page Generator Contract
- Page Generator must remain deterministic (no LLM calls)
- Page Generator consumes slides_ir.json + page_design.json only
- Renderer changes isolated to runtime renderer helpers
- Unsupported layouts degrade to nonblank fallback

### Risk Assessment
- LOW: All required layouts supported
- MEDIUM: Some layouts need extension
- HIGH: Critical layouts missing, fallback may degrade quality