# Render Review

## Core Responsibility
Review generated PPTX for rendering quality, detect issues, and generate review report.

## Quality Scoring Framework (6 Dimensions)

### 1. Clarity (Weight: 25%)
- 5: Message crystal clear from title alone in < 3 seconds
- 4: Message clear with minimal scanning
- 3: Message understandable with effort
- 2: Message buried in text, hard to find
- 1: No discernible message

### 2. Design (Weight: 20%)
- 5: Professional, consistent, excellent whitespace
- 4: Clean, mostly consistent, good spacing
- 3: Adequate, some inconsistency
- 2: Cluttered, inconsistent, poor spacing
- 1: Default templates, unreadable, chaotic

### 3. Visual Hierarchy (Weight: 20%)
- 5: Eye flows naturally; size/color/grouping perfect
- 4: Good hierarchy with minor issues
- 3: Adequate but could be improved
- 2: Weak hierarchy, hard to find key points
- 1: No visual hierarchy

### 4. Data Presentation (Weight: 15%)
- 5: Appropriate chart type, annotated, sourced, clear
- 4: Good chart with minor issues
- 3: Acceptable but could be clearer
- 2: Wrong chart type or hard to read
- 1: Misleading or unreadable data

### 5. Content Density (Weight: 10%)
- 5: Perfect density (< 40 words presentation, < 100 words doc)
- 4: Slightly dense but acceptable
- 3: Noticeably dense
- 2: Too dense or too sparse
- 1: Unreadable density

### 6. Consistency (Weight: 10%)
- 5: Perfect formatting, fonts, colors, layouts across deck
- 4: Minor inconsistencies
- 3: Some noticeable variations
- 2: Frequent formatting changes
- 1: No consistency at all

## Hard Rules (Auto-fail)
- Font size < 18pt -> ERROR
- More than 7 bullets per slide -> ERROR
- More than 30 words per bullet -> ERROR
- Blank slides (unintentional) -> ERROR
- Missing slide title -> ERROR
- Low-res images (< 200px) -> WARNING

## Squint Test
- Squint at the slide - can you still tell the point?
- If not, the slide is too cluttered

## Report Format
```json
{
  "agent": "render_review",
  "ok": true/false,
  "score": 4.2,
  "issues": [
    {
      "id": "issue-001",
      "severity": "error|warning",
      "slide_no": 3,
      "message": "Description",
      "suggested_fix": "Fix suggestion"
    }
  ],
  "slide_scores": [...]
}
```