# Course build plan (spec: tasks/course/spec.md)

Style: the `html-pages` skill house style (shared sheets copied unedited into course/assets, `local.css` for shared extras,
quiz.js for quizzes, local Mermaid for sequence diagrams). Verify every page with the skill's `scripts/check.py`.
Constraints from the user: static HTML in the repo; visualisation + quizzes; real evidence; **no mention of agents**;
anonymise the bill screenshot; cost claims honest ("it depends", never a flat 10x); the local stack in stack/ is not changed.

- [x] F1 Foundation: assets copied, local.css, course.js, index.html, module template
- [x] E1 Evidence capture: tools/capture-evidence.py -> course/evidence/ (reports, rows, logs) + tools/shot.mjs screenshots
- [x] S1 Story track: 01 problem+outcome, 02 platform in one picture, 03 evidence it works
- [ ] U1 Using it: 04 sending telemetry, 05 finding your data, 06 alerts
- [ ] R1 Running it: 07 concepts, 08 architecture+setup, 09 how we know it works, 10 what broke, 11 limits+tiers+AWS
- [ ] C1 Cost module content (inside 01 and 11) from cost/comparison.json, anonymised, with assumptions labelled
- [~] Q1 Gates (tools built and negative-tested: check-course.py, smoke-test.mjs, render-check.py; final run pending the other modules): skill check.py on all pages; tools/check-course.py (links, quiz validity, evidence numbers vs source reports); render light/dark/narrow; interactive smoke tests (headless Chrome, no console errors)
- [ ] V1 Independent completion review

## Progress notes
- Modules 1-3 built and verified (checker 0 errors, renders light/dark/narrow, smoke test, quizzes). 4-6 and 7-11 delegated to two subagents with tools/AUTHORING.md.
- Findings so far: Mermaid frames scroll sideways when >2-3 participants (accepted by the skill); charts need a table alternative for phones; the static checker caught a quiz with every right answer in the same position.
