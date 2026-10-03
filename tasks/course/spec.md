# Course spec: "Cost-efficient observability, proven" (DRAFT FOR REVIEW)

Status: design approved in conversation (audience: all; format: static HTML web course in this repo, with
visualisations and quizzes). This is the written spec. Nothing is built yet.

## 1. Purpose

Teach the **problem, outcome, concepts, architecture, setup and evidence** of the observability platform in
this repository (OTLP -> OpenData Buffer on S3 -> ClickHouse <- HyperDX), so that people can understand why it
exists, use it, run it, and trust it. Evidence is **real**: reports, logs, ClickHouse rows and screenshots
captured from this stack, never invented.

Success looks like: a learner can (a) explain the problem and why object-storage queuing changes the cost
shape, (b) find their service's logs/metrics/traces/alerts, (c) reason about what happens when each stage
fails, and (d) recognise what the tests prove and what they do not.

## 2. Audience and tracks (layered)

| Track | Audience | Modules |
|---|---|---|
| **Story** (about 20 min, everyone starts here) | all | 1 Problem and outcome. 2 The platform in one picture. 3 Evidence it works |
| **Using it** | app developers | 4 Sending telemetry. 5 Finding your data. 6 Alerts |
| **Running it** | platform engineers | 7 Concepts. 8 Architecture and setup. 9 How we know it works. 10 What broke and what we learned. 11 Limits, tiers, road to AWS |

Learners may jump to any module; each module states prerequisites and what it unlocks.

## 3. Module outlines (learning outcomes, key content, interaction, evidence)

1. **Problem and outcome.** Why logs/metrics/traces cost too much and break in outages; what "good" means
   (cost shape, durability under failure, one place to look). Interaction: a cost-shape sketch (queue-on-disk vs
   queue-on-object-storage) with sliders; quiz. Evidence: the user's `cost-efficient-observability-platform.png`
   (if they are happy to include it) and the published benchmark figures, cited.
2. **The platform in one picture.** Interactive architecture diagram; hover a box for what it does and its
   failure behaviour. Quiz: label the data path.
3. **Evidence it works.** Headline numbers from the verification run (31/31 checks, 7/7 tamperings caught, 8/8
   failure scenarios, per-row latency, alerts 11/11) with links into module 9/10 for the detail.
4. **Sending telemetry.** Resource attributes (`service.name`, `deployment.environment.name`), trace context
   over HTTP->gRPC, correlated logs. Interaction: trace explorer on the real `/chain` trace. Quiz.
5. **Finding your data.** Dashboards (availability, latency, per-route SLI), error list, runlog. Interaction:
   SLI calculator (requests -> availability and percentiles). Evidence: real dashboard screenshots.
6. **Alerts.** Thresholds, windows, `>=` vs `>`, once-per-minute evaluation and its late-row blind spot,
   webhook, resolve. Interaction: alert window simulator. Evidence: real fire/resolve timeline and payloads.
7. **Concepts.** OTLP; object-storage queue and manifest compare-and-set; at-least-once delivery; idempotent
   writes; `ReplacingMergeTree`; cumulative vs delta metrics. Interaction: idempotency lab. Quiz.
8. **Architecture and setup.** Components, per-signal manifests, ingestor-per-signal, compose layout,
   configuration, how to run it locally. Hands-on checklist with expected outputs. Evidence: `compose ps`,
   ClickHouse table list.
9. **How we know it works.** Ground truth from the load generator; the check families; **negative testing of
   the tests**; failure injection under load. Interaction: data-flow explorer with fault toggles. Evidence:
   verification report, selftest and chaos output, replay counts.
10. **What broke and what we learned.** War stories with real logs: upstream exporter had no traces; no retry
    (data loss on an S3 outage); MinIO images removed; alert threshold semantics; a verifier that could not
    fail; reused run ids. Each ends with a "what would you have checked?" question.
11. **Limits, tiers and the road to AWS.** Honest limits; environment tiers (prod/non-prod) and the
    filter-vs-separate-storage trade-off; AWS mapping; what is still unverified (real S3 parity).

## 4. Interactive components (specified behaviour)

- **Architecture diagram** (SVG): nodes with tooltips; keyboard-accessible.
- **Data-flow explorer:** animated events through collector -> queue -> ingestor -> ClickHouse; toggles to stop
  the ingestor, ClickHouse or S3; shows queue growth, replay after recovery, dedup of a replayed chunk, and the
  at-least-once edge at the collector. Behaviour is driven by a small state machine documented in the module and
  checked against the real chaos results.
- **Idempotency lab:** insert chunk with token T twice; see it dropped; change token; see it kept; show `FINAL`.
- **Alert window simulator:** failures-per-minute slider; threshold and comparison (`>=` / `>`); once-a-minute
  evaluation; "rows landing after :00 are missed" toggle.
- **Trace explorer:** span waterfall for a real `/chain` and `/chain-fail` trace; click a span to see its logs.
- **SLI calculator:** request mix -> availability, p50/p95/p99.
- **Quizzes:** multiple choice and predict-then-reveal; explanations on every answer; per-module score; final
  check; progress stored in `localStorage` (never required; works if storage is blocked).

## 5. Evidence manifest (generated, not hand-pasted)

A script `course/tools/capture-evidence.py` (stdlib only) regenerates `course/evidence/` from the running stack
and from `stack/verify/out/`: verification, selftest, chaos and alert reports (JSON plus rendered tables);
ClickHouse sample rows and counts; compose status; collector "Dropping data" log excerpt from the pre-fix era is
**quoted from the recorded run logs, labelled with its date and source**, since the bug is fixed and cannot be
reproduced on demand. `course/tools/shot.mjs` (Node built-ins only) captures screenshots: dashboard, error
list, runlog, a trace with its logs, a firing alert, the HyperDX sources page. Every evidence item carries a
caption with source, command and capture time.

## 6. Technical constraints

- Static HTML, CSS and JavaScript. **No frameworks, no CDN, no build step, no npm dependencies.** Works offline
  from disk and when hosted anywhere.
- One shared stylesheet with light and dark themes; responsive down to phone width; keyboard accessible;
  respects `prefers-reduced-motion`; semantic HTML.
- Layout: `course/index.html`, `course/modules/NN-*.html`, `course/assets/{css,js}`, `course/evidence/`,
  `course/tools/`. Quiz content in data files validated by a script.

## 7. Quality gates (before the course is called done)

1. Every internal link, image and evidence file reference resolves (script).
2. Quiz data validates (one correct answer, every option explained).
3. Evidence regenerates from scratch with one command, and the page numbers match the source reports (script
   cross-checks the headline figures against `report.json`).
4. Each interactive component has a smoke test (headless Chrome via `shot.mjs`: loads without console errors,
   key control changes the output).
5. Accessibility spot checks (keyboard path, contrast, alt text on screenshots).
6. Independent completion review (same standard as the platform).

## 8. Out of scope

No hosted publishing step, no LMS/SCORM, no accounts or analytics, no video. The course does not execute
commands against the learner's machine.

## 9. Risks and open questions

- Screenshots are real only if the stack is up and populated; the capture script must run a verification first.
- The cost claim in module 1 must cite sources and must not present benchmark figures as measured here.
- Whether to include the user's cost-platform image; whether the course should be repo-public. (Ask before use.)
- AWS content is documented but unexecuted; modules must say so plainly.
- Size: about 11 modules plus 5 interactives is large; build the Story track end to end first, then the others.
