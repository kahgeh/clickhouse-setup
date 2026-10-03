# Lessons

## Teaching: avoid redundant comprehension checks

- User feedback: the Buffer-deletion follow-up question felt superfluous after they correctly identified Log as the source for replay.
- Do not demand explanations of obvious consequences merely to satisfy a teaching checklist. Accept demonstrated understanding and move on.
- Use checks only when they expose a meaningful misconception or require a useful prediction; omit redundant parts when the learner has already answered the substantive question.

## Teaching: name the durable acknowledgement boundary

- Do not use an unqualified Buffer `ack()` as if it immediately removes a batch from the durable queue. It advances local progress and only dequeues every 100 acknowledgements; `flush()` or `ack_through()` persists a dequeue explicitly.
- In crash questions, state whether the manifest dequeue succeeded and what restart sequence the new consumer receives. Otherwise a learner can correctly infer either recovery or loss from different valid timings.
- When a question is ambiguous, acknowledge the ambiguity, verify the current code, and credit the learner's valid interpretation before re-framing it.

## Verify documentation claims against the artifact before designing on them

- The OpenData docs said the exporter supports logs, metrics and traces; the shipped exporter registered no traces at all, and the MinIO images the tutorial relied on had been deleted. Both were only found by running the thing.
- Before committing to a design that depends on a vendor claim, run the artifact (`components`, `validate`, a smoke test) and note any gap in the plan immediately. Say plainly when an earlier statement turns out wrong (the "worked example" test file was dead code).

## A verifier that has never failed proves nothing

- After building a verification suite, tamper with the data it checks (delete, flip, duplicate, re-parent, inflate) and require the right check to fail. Doing this exposed three verifier bugs: early exit that hid other checks, a crash masked by a stale report, and run-id reuse that silently tripled every count.
- Make runs refuse unsafe preconditions (reused run ids) instead of producing plausible wrong numbers.

## Fault-inject the data path early

- The first S3-outage test found real data loss (no retry in the upstream exporter). Failure scenarios belong in the first end-to-end milestone, not at the end.

## When a tool is unavailable, look for another route before declaring a gap

- The Chrome extension never connected, so "dashboards never seen in a browser" was disclosed for hours. Headless Chrome plus the DevTools Protocol over Node's built-in WebSocket needed no new dependency and took minutes, and it immediately exposed a real dashboard flaw (empty buckets drawn as 0% availability).

## Shell and tooling gotchas

- zsh does not word-split unquoted variables: `$N` holding `--network x` is passed as one argument. Write flags inline or use arrays.
- Prefer one explicit, unique id per test run; cumulative counters need before/after deltas.
- When the user asks to review the goal and verification before starting, write them down and stop; do not continue with exploratory commands.

## Lessons from the trace_all round
- A build cache can hide a source change (buildah reported every step cached after the macro edit). After editing code that goes into an image, check the image's created time against the source, or build with --no-cache.
- A negative-test mutation that picks "LIMIT 1" of a broad filter becomes flaky when the data mix grows. Pin each mutation to the exact thing it targets.
- A check that compares three counts in three separate queries races live streams (cumulative metrics keep exporting). Pin all of them to one queue position.
- When a quiz gate only measures one bias (longest), fixing it can create the opposite (shortest). Measure both ends and per module.
- Numbers typed into page text go stale when the data changes (17% vs 15.6%). Compute them from the evidence file.
