# Long-input planning experiment

## Goal and scope

Compare source-to-page-script information flow on `feat/evidence-grounding`.
Keep `src/` unchanged. Stop after Brief, Outline and per-page scripts: no page
design, rendering, web research or embeddings. Six arms: A/B/C/D on T3, B/D on
the expanded corpus. One run per arm; no retries to select a better result.

The user clarified that the **USD 3 total cap and reported costs use official
API token prices**, not CCCX's wallet multiplier. Request `gpt-5.6-terra` and
stop if the returned model differs. Official uncached input/output rates are
$2/$12 per million tokens; cached reads $0.20, writes $2.50. Record usage and
actual response model for every request. No API key in artifacts.

## Preregistered design

- A calls production `plan_deck_async` unchanged at the current branch revision.
- B replaces only model-visible source excerpts with the entire parsed corpus.
- C uses the report's six original chapters; a deterministic title/lead BM25
  match assigns one source chapter to each generated outline section. Report
  no-match and ambiguous/merged-section limitations.
- D uses pure BM25 over 1,600-character windows, 300-character overlap, top 4
  chunks. Queries contain outline title, goal and talking points only. The gold
  facts are never used for query construction or retrieval.
- B/C/D share B's full-source Brief and Outline as a controlled prefix. The
  expanded B/D pair shares its own full-source prefix. This holds story choices
  constant for source-allocation comparisons and avoids paying twice for the
  same prefix. Record shared-prefix tokens/cost separately; report both allocated
  actual cost and standalone-equivalent cost. A vs B also changes prefix visibility.
- Default common model settings: temperature 0.4, reasoning_effort none,
  max_output_tokens 4096, concurrency 1, no provider retries. Model parameters
  are experiment-only; production defaults are unchanged.
- Expanded input is T3 plus CNNIC57 and CAC's 2024 informatization report:
  three complete Chinese official reports, approximately 156k raw characters.
  This explicitly replaces the planned two-document expansion to meet the
  target without duplicated padding or mixing languages. B and D receive the
  identical expanded corpus; report actual characters and token estimates.
- All source passages preserve original document IDs and physical PDF pages.
- Run receipts and request packets are immutable experiment artifacts. A failed
  request with unknown usage consumes its reserved worst-case official budget,
  and stops further calls rather than silently charging unmetered retries.

## Implementation / verification plan

- [x] Parse full PDFs, keep page provenance and six chapter boundaries.
- [x] Implement Chinese-bigram/ASCII BM25 and test cross-page/final-chunk retention.
- [ ] Implement shared official-price budget, model-identity guard and packet routing.
- [ ] Test budget refusal before HTTP and cached-token accounting; run original suite.
- [ ] Freeze source hashes, prompt hash and experiment commit before paid calls.
- [ ] Execute six arms while remaining within the shared official-price cap.
- [ ] Find candidate matches for all 15 facts, then verify topic, magnitude, units
      and dates in the scripts. Missing facts are not fabricated facts.
- [ ] Audit new numeric claims against the supplied corpus; separate list/step
      numbering, source-backed values, calculations and explicit assumptions.
- [ ] Save group table, exact JSON field locations and conditional recommendation.

## Limits fixed before results

This is a single-run screening experiment, not a statistical performance claim.
Character length is not token length. Sharing the prefix controls content
selection but may transmit facts through outline talking points: trace those
packets rather than attributing every hit to the chapter excerpt. BM25 is lexical,
not semantic/vector RAG. Chapter alignment and cross-report year conflicts must
be reported. Smaller/full-text cases may not justify adding retrieval.
