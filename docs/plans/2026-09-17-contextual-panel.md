# Contextual navigation and actions

## Outcome
The floating mobile panel exposes relevant actions on every main screen while preserving Archive, Gallery and Studies as stable destinations. Gallery journeys preserve view/filter/card and return origin. Share actions name their actual target.

## Decisions
- Preserve existing tokens, type and tactile appearance.
- Compact action row over stable mobile destinations; desktop retains sidebar with equivalent reachable capabilities.
- Archive: New design, Inbox count, Capture. Project: Back, New design scoped to project, Share project / secondary Capture to Inbox.
- Design: contextual Back, Add to design, More (Share design, Studio, view options if appropriate).
- Gallery: focused design Open/Share; view/filter controls accessible from panel; ring share explicitly chooses/names a project, never defaults silently to the first. Empty gallery offers a design-selection route for adding final/editorial imagery.
- Inbox: Sort next only when nonempty, Capture, Back. Studio hub: New study, Back to design, All studies. Global Studies gives a clear path to choosing a design for new work.
- Sheets/media viewer own their controls; underlying panel must not compete. Hide panel while keyboard would obstruct editing; safe-area and content clearance.
- Label actions, maintain tap targets, support keyboard focus and Escape dismissal.
- Persist Gallery browsing state in URL or an appropriately bounded store. Record route origin explicitly, with safe project fallback for direct design links. Restore scroll as well as card/filter/mode.
- Keep design capture context throughout nested studio routes.

## Contracts and ownership
One implementation worker owns frontend source changes and its browser proof. Root prepares independent browser fixtures outside the checkout, reviews and integrates tests afterward; no simultaneous checkout-mutating work.

## Acceptance
1. Phone dock changes actions correctly across Archive, project, design, Gallery, Inbox and Studio; Studies is reachable.
2. Gallery filter/card/mode survives opening and returning from a design, including browser back; direct design entry has a safe parent fallback.
3. Share payload matches the named design/project; no first-project implicit collection share.
4. Empty/loading/overlay states offer no invalid or obscured actions; capture remains scoped in nested design routes.
5. 390px and short/narrow phone layouts, plus desktop, are usable and overflow-free. Fresh-context browser checks and lint/build pass.

## Out of scope
Backend collection-sharing model, batch editing, generation changes, production data mutations, deployment and pushes.

## Verified result

Implemented locally on 2026-09-17. Fable was not exposed by the session registry; implementation used inherited Astra, with Dienda supervision and Deez browser evidence.

- Contextual actions and stable mobile destinations work; desktop gets equivalent contextual controls beside its sidebar.
- Gallery preserves filter, focused media and mode across routes. Origin-aware back returns to Gallery; direct design links fall back to the project. Browser POP scroll restoration stays owned by React Router, preventing stale explicit-return state from overriding it.
- Stack shares its focused design; Ring explicitly chooses a project. Share scope resets atomically when opening another target.
- App-level capture context survives nested Studio routes and resets when leaving the design.
- Sheets own keyboard focus/Escape, hide the panel, preserve busy saves, and keep new-design autofocus. Long labels are ellipsized without losing accessible names.

Validation: pnpm lint, pnpm build and all five checked-in browser scripts passed. Responsive matrix: 7 routes × 3 viewports (320×568, 390×844, 1440×1000). Additional interactions cover scoped create/share requests, Inbox sort/read-back, nested capture, empty states, busy save guards, repeated Gallery scroll, long labels, keyboard focus and signed-out redirect. Fresh browser contexts and intercepted multi-project API fixtures were used. Worker additionally exercised the study composer and overlays. No production account/API/data was used.

Reproduction: frontend/tests/browser/README.md. Final local screenshots/request evidence: /tmp/deez/atelier-context-panel/final-evidence. Worker report: /tmp/deez/atelier-context-panel/worker-report.md. Static review: /tmp/deez/atelier-context-panel/review.md; focus findings were already fixed when independently reproduced, and POP scroll ownership was made explicit and rechecked.

Limits: verification covers the actual frontend with fixture API responses, not live backend integration or a physical mobile keyboard. No backend/collection-share model changes, push or deployment.
