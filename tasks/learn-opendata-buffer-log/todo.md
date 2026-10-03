# Learn OpenData Buffer and Log

Goal: explain and compare Buffer and Log through concrete event examples.

- [x] Read the current product and architecture documentation.
- [x] Verify foundational behavior against current source.
- [x] Lesson 1: distinguish pending delivery from retained keyed history; learner correctly chose Log for replay.
- [x] Lesson 2: follow writes through durability and visibility; learner identified empty Log scan and durable Buffer backlog.
- [ ] Lesson 3: compare reads, ordering, acknowledgements, retries, and retention; check understanding.
- [ ] Lesson 4: choose or combine Buffer and Log for concrete workloads; check understanding.

## Evidence

- https://www.opendata.dev/docs/buffer
- https://www.opendata.dev/docs/buffer/architecture
- https://www.opendata.dev/docs/log
- https://www.opendata.dev/docs/log/storage-design
- https://www.opendata.dev/docs/api-reference/append-records
- https://www.opendata.dev/docs/overview/writing-data
- https://www.opendata.dev/docs/overview/reading-data
- https://github.com/opendata-oss/opendata/blob/main/buffer/src/consumer.rs
- https://github.com/opendata-oss/opendata/blob/main/log/README.md

## Lesson 1 check

Scenario: a processor finishes a batch, Buffer acknowledges and cleans it up, and a new reader wants an earlier session event.
Correct answer: read the retained session Log; Buffer cleanup removes processed batches, whereas Log reads do not consume the history. Replay is limited by Log retention.

## Lesson 2 check

Scenario: Buffer's `await_durable()` succeeded for an event, but the session writer has not appended it to Log.
Learner answer: session scan finds nothing; the event survives in Buffer's backlog. Correct.

## Lesson 3 clarification

The initial crash question was ambiguous: ordinary Buffer `ack()` may leave a batch in the manifest until the 100-ack dequeue or a `flush()`. The learner's inference that it could still be recovered from Buffer is valid. Reframe the failure case using a confirmed durable manifest dequeue and an explicit restart sequence. Current `buffer/src/consumer.rs` verifies `ack()`, `flush()`, and `ack_through()` behavior.

## Review

Teaching is in progress; lesson 1 is complete. The learner found the follow-up about Buffer deletion superfluous: accept demonstrated understanding and avoid redundant checks. Source review confirms Buffer cleanup versus Log key/sequence scans and retention. Buffer has ordered manifest batches, so do not simplify its ordering to wholly unordered. Lesson 2 evidence: `produce()` returns before `await_durable()` confirms durable queue handoff; Log append defaults to `awaitDurable: false`, which acknowledges memory buffering, while `true` waits for durable persistence. A direct writer read can see an entry before it is durable. No software implementation or dependency installation is required.
