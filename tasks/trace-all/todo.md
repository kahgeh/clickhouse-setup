# trace_all: automatic function-level spans in the test app

Request (user): use a `#[trace_all]` attribute macro in the test app, with the same components (web axum + backend tonic), and introduce fictitious functions so traces show function-level depth.

## Design
- New workspace crate `app/trace_macros` (proc-macro). `#[trace_all]` on an `impl` block or an inline `mod` adds `#[tracing::instrument(skip_all)]` to every fn that lacks one. `#[trace_all(skip(name, ...))]` leaves named fns alone.
- Dependencies: syn (full, visit-mut), quote, proc-macro2. Already in Cargo.lock transitively; auditor approval required before they become direct deps (auditor running).
- New route `GET /checkout` in web, plus a new gRPC call in backend, with fictitious call trees (cart, pricing, inventory, payment authorisation, ...), each in an impl/mod annotated with `#[trace_all]`. Existing routes and their span counts stay unchanged so the existing verification evidence remains valid.
- loadgen: add `checkout` to the request mix and to ground truth with its expected span count (computed from the call tree, asserted by a unit test).
- Verification: new check family for the checkout trace (one trace, expected span names, parent/child links, depth), run with the existing verify.py; existing 31 checks must still pass.

## Steps
- [x] S1 supply-chain auditor approves syn/quote/proc-macro2 pins
- [x] S2 write trace_macros with unit tests (expansion on impl and mod, skip list, existing #[instrument] untouched, async fn)
- [x] S3 fictitious functions in web and backend using #[trace_all]; new route
- [x] S4 loadgen + ground truth + verify checks for the checkout trace
- [x] S5 cargo fmt, clippy, tests; rebuild images; run verify.py, selftest.py, chaos smoke
- [x] S6 look at the waterfall in HyperDX (screenshot), add it to the course module 4/5 with source
- [ ] S7 completion reviewer

## Risks
- Every request that exists in ground truth changes span counts; a new route avoids that.
- Span volume: function-level spans raise span count per request; note the effect on the cost model inputs (events per request).
- Attribute on non-inline `mod x;` is unstable; use impl blocks and inline mods only.

## Review (before the completion reviewer)
- trace_macros: 6 integration tests; workspace fmt, clippy -D warnings and tests pass.
- /checkout: 22 spans (web 11, backend 11), 2 logs; existing routes unchanged (1,1,1,1,4,3 spans).
- Loop: price_items is one span with items=5 and slowest_line_ms recorded; no per-pass histogram (not built).
- Run-id propagation: telemetry::RunIdPropagator span processor gives function spans their parent's test_run_id.
- verify.py: 32/32 (new B9 checkout tree check; A3b now sums per-route counters; A4m pinned to one queue position because cumulative series keep exporting). selftest 3/3 repeated runs pass. chaos: 7 scenarios + token probe pass.
- Course: module 4 new section with captured tree and HyperDX waterfall; evidence recaptured (32 checks); module 5 and 9 include /checkout.
- Honest limits: .NET/Java zero-code instrumentation described, not run; function spans raise span volume (22 vs 4 per request) and the cost model inputs are unchanged; no metric histogram for loop passes.
