# Conversational Interview Visual QA

- Reference: `docs/design/ppt-agent-conversational-interview-reference.png`
- Question state: `docs/design/ppt-agent-conversational-interview-question.png`
- Ready Brief state: `docs/design/ppt-agent-conversational-interview-ready.png`
- Mobile state: `docs/design/ppt-agent-conversational-interview-mobile.png`
- Route: `/`, then select `创建演示`
- Desktop viewport: `1200x743`
- Mobile viewport: `390x844`
- Data state: deterministic mock interview; no real model call

## Interaction parity

- One question per turn.
- Two to four numbered quick options.
- Free-text answer remains available below the options.
- Skip action remains visible.
- Turn count is visible without promising a fixed number of questions.
- A complete request transitions into an editable structured Brief.

## Visual verdict

- Score: 93/100
- Verdict: pass
- Category match: true

The implementation keeps the reference's dark, high-focus question surface while integrating it into the existing light ppt-agent workspace. The additional Live Brief is intentional: it makes the Agent's requirement convergence inspectable before an expensive presentation build starts.

## Reproduction

Start the FastAPI app, open the root route, select `创建演示`, enter a vague presentation request, and capture screenshots after the first clarification and after the Brief becomes ready. The automated run used Playwright CLI with a deterministic mock structured model.
