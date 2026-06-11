# Visual Quality Evaluation

## Core Responsibility
Evaluate PPT visual quality using industry-standard frameworks.

## Duarte Design Principles

### 1. Transform
- Presenter is the hero's guide, audience is the hero
- Every slide must serve the audience's needs

### 2. Transmit
- Ideas are currency of persuasion
- Every slide must advance the argument

### 3. Exhibit
- Show don't tell
- Visuals over bullet points when possible

### 4. Integrate
- Design supports content, never decorates
- No clip art or decorative elements

### 5. Rehearse
- Good design enables good delivery

## Tufte Data-Ink Ratio (for charts)
- Maximize data-ink ratio (remove non-essential ink)
- Avoid chart junk (decorative elements that don't convey data)
- Use small multiples for comparison
- Label directly (no legend if possible)

## Visual Checklist

### Typography
- [ ] Maximum 3 fonts in entire deck
- [ ] Font size >= 18pt (presentation mode)
- [ ] Consistent font usage across slides
- [ ] Title font distinct from body font

### Color
- [ ] Maximum 5 colors in entire deck
- [ ] Contrast ratio >= 4.5:1 for text
- [ ] Consistent color usage (primary, secondary, accent)
- [ ] Color-blind friendly (avoid red-green only)

### Layout
- [ ] Grid-based alignment
- [ ] Consistent margins (0.5 inch)
- [ ] White space >= 30% of slide area
- [ ] Elements aligned to each other

### Images
- [ ] High resolution (>= 500px wide)
- [ ] Relevant to content
- [ ] Consistent style (all photos or all illustrations)
- [ ] Proper aspect ratio (no stretching)

### Consistency
- [ ] Same layout for same content type
- [ ] Same bullet style throughout
- [ ] Same chart style throughout
- [ ] Same transition style throughout

## Scoring
Score 1-5 for each dimension, weighted average for final score:
- Typography: 20%
- Color: 20%
- Layout: 25%
- Images: 15%
- Consistency: 20%

## Output Format
```json
{
  "agent": "visual_quality_evaluator",
  "ok": true/false,
  "score": 4.2,
  "summary": "Overall assessment",
  "slide_scores": [...],
  "issues": [...],
  "metrics": {...}
}
```