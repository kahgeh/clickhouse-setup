# Reset Makefile

Request: a Makefile to reset the data (Buffer, logs, metrics, traces) and then the setup (dashboards, alerts, saved searches) of the local stack.

- [x] reset-data: stop pipeline, empty bucket, truncate five tables, clear webhook sink list, restart
- [x] reset-setup: drop HyperDX/Mongo store, start, provision.py (idempotent, waits for HyperDX itself)
- [x] reset (both, with confirmation; YES=1 skips), status, help; .NOTPARALLEL; PROJECT variable
- [x] pairs (user request): start/stop, setup (start + provision)/teardown (down --volumes, state.json, IMAGES=1 adds --rmi all), provision
- [x] README section in stack/README.md
- [x] Proved: abort on "n" and on YES=0; real `make reset YES=1` twice; tables 0 rows; verify.py 32/32 after each (after-reset-105251, after-reset2-105747)

Review 1 (not ready): -j skipped the prompt, YES=0 counted as yes, volume rm error ignored, project name duplicated, webhook list not cleared. All fixed.
Not done: amd64 override file is not part of COMPOSE; TABLES is a fixed list.

Review 2 (not ready): reset-data on a stack stopped with make stop failed after emptying the bucket (ClickHouse not ready). Fixed with `up -d --wait`; status now `ps -a`; per-target confirmation text; README reworked.
Proved: stop 11 -> 0 containers, start 0 -> 11; teardown left no containers/volumes/network/state.json; setup from nothing then verify 32/32 (after-teardown-232720).
Left to the user: `reset-setup` could be renamed `reset-hyperdx` to avoid overlapping with `setup`; amd64 override not in COMPOSE.

Review 3 (not ready): after stop + reset the webhook sink stayed down (not in PIPELINE), so alerts would post to nothing. Fixed: webhook-sink is in PIPELINE; the clear-webhook-sink target is gone (a restart empties its in-memory list). Header lists status; README paragraph rewrapped.
Proved (review 4, thumbs up): make stop then make reset YES=1 left all 11 services up, sink list empty, tables 0 rows, verify 32/32; prompt guard checked on a copy with side effects stubbed.
