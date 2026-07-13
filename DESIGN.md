# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-11
- Primary product surfaces: conversational requirements interview, editable Live Brief, unified Web presentation workspace, task progress, storyboard preview, presentation history, delivery/artifact management
- Evidence reviewed: `src/ppt_agent/api.py`, `docs/design/`, `docs/readme/`, `README.md`

## Brand
- Personality: calm, capable, technical, product-focused
- Trust signals: visible progress, explicit quality status, persisted history, inspectable artifacts, direct download paths
- Avoid: marketing-style hero sections, decorative card walls, hidden technical failure states, internal-only labels without user meaning

## Product goals
- Goals: help users clarify vague presentation ideas, confirm an actionable Brief, create, monitor, recover, reopen, and download editable presentations from one local workspace
- Non-goals: multi-tenant collaboration, cloud asset management, billing, in-browser slide editing
- Success signals: users can find a prior request and its final PPTX without searching the filesystem

## Personas and jobs
- Primary personas: AI product managers, students, technical presenters, portfolio builders
- User jobs: create a deck, understand progress, inspect quality, resume failures, reopen history, download editable output
- Key contexts of use: local desktop browser, long-running 30-100 page generation, repeated experiments with multiple prompts

## Information architecture
- Primary navigation: project workspace, conversational creation, presentation history, preview, technical details
- Core routes/screens: `/`, `/api/presentation-interviews`, `/api/presentations`, `/api/jobs/{job_id}`, artifact downloads
- Content hierarchy: current task first, preview and creation second, searchable history, technical details last

## Design principles
- Show real state: labels and actions must come from persisted job/artifact data.
- Ask only what matters: one adaptive clarification per turn, with quick choices and free text.
- Keep primary workflows shallow: history rows expose open and download actions directly.
- Preserve failed work: failed and quality-gate-blocked jobs remain visible for diagnosis and recovery.
- Tradeoffs: dense operational clarity is preferred over decorative presentation.

## Visual language
- Color: white and light neutral surfaces, teal for primary actions, cobalt for links, coral/yellow for warnings
- Typography: system sans-serif with compact dashboard-scale headings
- Spacing/layout rhythm: 8px base rhythm, 16-24px section spacing
- Shape/radius/elevation: 6-8px radius, borders before shadows, no nested decorative cards
- Motion: short native scrolling and state transitions; respect reduced-motion browser behavior
- Imagery/iconography: generated slide previews are the primary visual assets; controls use clear text commands

## Components
- Existing components to reuse: side navigation, product sections, status labels, action buttons, artifact links
- New/changed components: conversation stream, focused question panel, numbered option row, composer, Live Brief, history toolbar, history record row, status badge, direct PPTX action
- Variants and states: interview empty, thinking, clarifying, ready, manual Brief, loading, empty history, succeeded, running, quality warning, failed, cancelled
- Token/component ownership: CSS variables and components remain in the local FastAPI workspace until a frontend extraction is justified

## Accessibility
- Target standard: practical WCAG 2.1 AA behavior
- Keyboard/focus behavior: all filters, buttons, links, and navigation controls remain keyboard accessible with visible focus
- Contrast/readability: status meaning is conveyed through text as well as color
- Screen-reader semantics: labels for search/filter controls, `aria-live` for history refreshes
- Reduced motion and sensory considerations: no essential information depends on animation

## Responsive behavior
- Supported breakpoints/devices: desktop, tablet, and mobile from 320px
- Layout adaptations: history controls and rows collapse to one column below 680px
- Touch/hover differences: actions retain 36-40px minimum height and do not depend on hover

## Interaction states
- Loading: Agent composer reports analysis in progress; history summary reports SQLite loading
- Empty: explains that the first created presentation will appear automatically
- Error: history read/open errors stay inside the history surface
- Success: interview transitions to an editable Brief; final PPTX download appears directly in the history record row
- Disabled: refresh and generation actions disable only while their own operation is active
- Offline/slow network: the local API remains the source of truth; polling and history refresh can retry independently

## Content voice
- Tone: concise, direct, reassuring without hiding failure
- Terminology: use “演示”, “任务”, “可编辑 PPTX”, and “质量门禁”; reserve schema and batch terminology for technical details
- Microcopy rules: describe the current state and the next available action, not implementation internals

## Implementation constraints
- Framework/styling system: FastAPI-served HTML/CSS/vanilla JavaScript with strict Pydantic structured interview output
- Design-token constraints: extend existing CSS variables; no new frontend dependency
- Performance constraints: history is paginated and capped at 100 rows per request
- Compatibility constraints: existing SQLite databases and old jobs must remain readable
- Test/screenshot expectations: API regression tests plus desktop/mobile browser smoke checks for new UI surfaces

## Open questions
- [ ] Decide whether later versions should support deleting or archiving history; deletion is intentionally excluded from the first release because it affects local artifacts.
- [ ] Decide whether file upload/search metadata should join the same presentation request snapshot when those Web features ship.
