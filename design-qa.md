# PPT Agent Workspace Design QA

- source visual truth path: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-workspace-target.png`
- implementation screenshot path: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-workspace-implementation.png`
- mobile screenshot path: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-workspace-mobile.png`
- page-count feedback source: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-page-count-feedback.png`
- corrected 100-page create screenshot: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-100-page-create.png`
- corrected mobile create screenshot: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-100-page-create-mobile.png`
- latest runtime feedback: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-runtime-feedback.png`
- latest form feedback: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-form-feedback.png`
- live preview and custom-page implementation: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-live-preview-and-custom-pages.png`
- unified 1-100 page create form: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-unified-create-1-100.png`
- viewport: desktop `1380 x 751`; mobile `390 x 844`
- state: real long-deck job `02619bd8da5e49449f3b940a0f84771c`, quality gate failed, PPT Master recovery output registered, 30 slides

## Comparison Evidence

- full-view comparison: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-workspace-comparison.png`
- focused progress, preview, and delivery comparison: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-workspace-comparison-focus.png`
- page-count correction comparison: `/Users/jay/Documents/ppt-agent/docs/design/ppt-agent-page-count-comparison.png`
- The final implementation preserves the selected target's project navigation, five-stage progress, four task metrics, real slide storyboard, source coverage, editable chapter allocation, delivery rail, and narrative health hierarchy.
- The implementation intentionally shows the real 30-page recovery state instead of the target mock's idealized 100-page success state.

## Required Fidelity Surfaces

- Fonts and typography: system Chinese sans-serif stack, clear 12/13/15/18/24 px hierarchy, zero letter spacing, no clipped display text. Long task metadata and backend warnings truncate safely.
- Spacing and layout rhythm: 236 px navigation, flexible main canvas, 284 px delivery rail, restrained 6-8 px radii, consistent 18-24 px section spacing. No desktop or mobile horizontal overflow.
- Colors and tokens: white and cool gray surfaces, pale mint selection, teal primary, cobalt/coral/yellow chapter accents. No decorative gradients or monochrome purple/beige treatment.
- Image quality and asset fidelity: preview thumbnails come from finalized PPT Master SVG slides or v2 `PageDesign` HTML rendered from incremental checkpoints through `/api/jobs/{job_id}/preview-slides/{slide_number}`. No placeholder slide art is used.
- Copy and content: interface copy is product-facing Chinese. Technical terms and 77 artifacts remain available but are grouped and collapsed by default.
- Accessibility and states: semantic buttons, labels, headings, alt text, visible focus rings, disabled task actions, empty SVG state, responsive navigation, and mobile single-column layout are present.

## Primary Interactions Tested

- Side navigation scrolls to the preview section.
- Three real SVG previews load for slides 1, 15, and 30 on the recovery job.
- The completed v2 100-page job exposes 100 HTML previews; slides 1, 51, and 100 return successfully.
- The preview manifest supports checkpoint-only jobs, so representative pages appear before the deck is complete.
- Long-deck form topic, audience, page count, and requirements persist across reload; the browser QA draft was cleared afterward.
- Arbitrary page count `73` updates the CTA and survives reload without submitting a generation job.
- Primary delivery CTA resolves to the registered PPTX artifact.
- Artifact details expand and expose 77 registered download links.
- Chapter titles support local editing and persist across reload; the test value was restored afterward.
- Browser console errors checked: none.

## Comparison History

1. Earlier finding: the eight-chapter row forced the center card under the right rail, and nested metric spans stacked vertically.
   Fix: constrained product-section width, corrected direct-child metric selectors, and made the chapter grid responsive.
   Post-fix evidence: `docs/design/ppt-agent-workspace-implementation.png` shows no overlap and horizontal metrics.
2. Earlier finding: source coverage occupied a separate workspace column, making the storyboard materially narrower than the selected target.
   Fix: moved source coverage into the persistent navigation rail and changed the workspace to a main-canvas plus delivery-rail grid.
   Post-fix evidence: `docs/design/ppt-agent-workspace-comparison-focus.png` shows comparable progress, preview, and delivery proportions.
3. Earlier finding: mobile placed the source list before the task and had limited above-the-fold utility.
   Fix: source coverage is hidden in the compact navigation breakpoint while the live task remains first; desktop retains the full source panel.
   Post-fix evidence: `docs/design/ppt-agent-workspace-mobile.png`; `scrollWidth` equals `clientWidth` at 390 px.
4. Earlier finding: the create form only accepted 30 pages and exposed `batch_size` and retry controls that users should not need to understand.
   Fix: replaced the fixed number input with a 30/50/100-page menu, routed 50/100 pages to the existing high-quality long-deck pipeline, removed advanced generation controls, and made strategy, retry, checkpoint, and quality settings automatic.
   Post-fix evidence: `docs/design/ppt-agent-page-count-comparison.png`; selecting 100 updates the CTA, no advanced controls remain, console errors are empty, and the mobile viewport has no horizontal overflow.
5. Latest finding: v2 output had no preview because the endpoint only searched PPT Master `svg_final`; polling used a 2-second interval and stopped permanently after one fetch error; the form reset to an AI product-manager example; page count was still constrained to three presets.
   Fix: added a preview manifest and v2 checkpoint/final-design HTML renderer, changed the preview surface to responsive iframes, added a monotonic local elapsed clock with resilient 1-second polling, persisted the form draft, removed baked-in content, and changed page count to a numeric input.
   Post-fix evidence: `docs/design/ppt-agent-live-preview-and-custom-pages.png`; the real recovery job shows actual slides 1/15/30, the real v2 job exposes all 100 pages through the API, a 73-page draft survives reload, and browser console errors are empty.
6. Latest finding: users still had to choose between a primary long-deck form and a collapsed 1-10-page engineering form, while the product claimed an untested 200-page ceiling.
   Fix: merged both experiences into one 1-100-page create form. Pages 1-10 automatically use the fast pipeline; pages 11-100 use the long-deck pipeline. QA thresholds, retry counts, patch paths, and pipeline selection are no longer user-facing. The v2 planner, CLI help, API validation, and README now share the tested 100-page ceiling.
   Post-fix evidence: `docs/design/ppt-agent-unified-create-1-100.png`; 8 pages switches to fast-mode copy, 37 pages switches to high-quality generation copy, the legacy form is absent, and browser console errors are empty.

## Findings

- No actionable P0, P1, or P2 findings remain.
- P3: the target uses a fuller icon family in navigation and delivery actions. The implementation keeps text controls because the project currently has no icon library and this change adds no dependency.

## Follow-up Polish

- Add a repo-standard icon package only when the dependency policy is intentionally changed.
- Connect the editable chapter draft to a future long-deck planning API before presenting it as generation input; today it is explicitly browser-local.

final result: passed
