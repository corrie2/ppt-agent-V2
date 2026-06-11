# Page Generation

## Core Responsibility
Deterministically generate PPTX pages from slides_ir.json and page_design.json. No LLM calls.

## Standards

### Kawasaki 10/20/30 Rule (adapted)
- Optimal slide count: 10-15 for 20-minute presentation
- Slide-to-time ratio: ~2 minutes per slide maximum
- Font minimum: 30pt for pitch decks, 18pt for teaching/reports

### Density Rules (6x6)
- Maximum 6 bullet points per slide
- Maximum 6 words per bullet point
- Total words per slide: < 40 (presentation mode)
- Never exceed 150 words per slide even for documents

### Action Titles
- BAD: "Revenue Analysis" (topic label)
- GOOD: "Revenue grew 15% driven by APAC expansion" (conclusion)
- Title alone should convey the slide's message

### 3-Second Test
- If a slide takes more than 3 seconds to understand, simplify
- Message should be readable from title + first glance

### Layout Mapping Rules
| IR Layout | PPTX Layout | Notes |
|-----------|-------------|-------|
| title | Title Slide | Center-aligned, large font |
| content | Title + Content | Standard bullet layout |
| two-column | Title + 2 Columns | Equal width columns |
| chart | Title + Chart | Chart takes 70% area |
| image | Title + Image | Image with caption |
| blank | Blank | Only for section breaks |

### Fallback Strategy
- Unsupported layout -> closest supported layout
- Missing content -> placeholder with warning
- Never generate blank slides (except intentional section breaks)

### Output Contract
Output must match PptSpec schema:
- slides: list of SlideSpec
- Each slide: title, bullets, layout, notes, image_ref