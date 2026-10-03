#!/usr/bin/env python3
"""Alert verification: do the HyperDX alerts stay quiet on healthy traffic, fire on failures,
deliver their webhook, and resolve afterwards?

  alerts.py [--quiet-wait 150] [--fire-timeout 300] [--resolve-timeout 360]

Alerts under test (created by hyperdx/provision.py, both "above 4 per minute", 1m interval, webhook):
  * dashboard-tile alert on 'Errors (5xx)'  (web server spans with Error status)
  * saved-search alert on 'Error list: error logs'
Phases, each asserted:
  0 baseline   both alerts reach state OK (earlier test traffic ages out of the 1-minute window)
  1 quiet      healthy traffic (no errors): nothing delivered, both stay OK
  2 fire       a burst of failures: both alerts reach ALERT and a webhook delivery per alert arrives
  3 resolve    traffic stops: both return to OK and a further (resolve) delivery arrives
HyperDX evaluates alerts about once a minute, so this takes several minutes. Exit code non-zero on failure.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify  # noqa: E402

SINK = os.environ.get("SINK_URL", "http://localhost:8089")
TILE_ALERT = "Alert: 5xx errors above 4 per minute (dashboard tile)"
LOG_ALERT = "Alert: ERROR logs above 4 per minute (saved search)"
MATCH = {TILE_ALERT: None, LOG_ALERT: None}  # keys: alert names (they appear in each notification's title)
BURST = "error=40,exception=10,chain-fail=10"
EXPECT_5XX = 60       # web requests that fail with 5xx in the burst (error + exception + chain-fail)
EXPECT_ERR_LOGS = 70  # ERROR log lines in the burst (error 40 + exception 10 + chain-fail 10 x 2 services)


def sink(method="GET"):
    req = urllib.request.Request(f"{SINK}/received", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def alert_states(state):
    for attempt in range(6):
        req = urllib.request.Request(f"{state['api']}/api/v2/alerts", headers={"Authorization": f"Bearer {state['accessKey']}"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=30))["data"]
            return {a["name"]: a["state"] for a in data if a["name"] in MATCH}
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(min(int(e.headers.get("Retry-After", "30")), 65) + 1)


def deliveries(alert_name, state=None):
    """Parsed webhook deliveries for one alert (matched on the alert name in the title), oldest first."""
    out = []
    for d in sink():
        try:
            body = json.loads(d["body"])
        except ValueError:
            continue
        if alert_name in body.get("title", "") and (state is None or body.get("state") == state):
            out.append(body)
    return out


def reported_count(delivery):
    """The number HyperDX reports in a firing notification: '60 exceeds 4' / '70 lines found' (wording depends on the comparison: '>=' says 'meets or exceeds')."""
    import re
    m = re.search(r"(\d+)\s+(?:meets or exceeds|exceeds|lines found)", delivery.get("body", ""))
    return int(m.group(1)) if m else None


def wait_until(pred, timeout, every=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return time.time() - t0
        time.sleep(every)
    return None


def wait_for_safe_second(lo=5, hi=40):
    """HyperDX evaluates each minute once, at second :00, over the minute just ended and without any
    allowance for ingest delay. Start a burst early in a minute so every row has landed before then."""
    while not lo <= time.gmtime().tm_sec <= hi:
        time.sleep(1)


def seconds_to_next_evaluation(extra=20):
    return 60 - time.gmtime().tm_sec + extra


def load(run_id, counts):
    return verify.run_loadgen(run_id, counts, 200, os.path.join(HERE, "out", run_id))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet-wait", type=int, default=150)
    ap.add_argument("--fire-timeout", type=int, default=300)
    ap.add_argument("--resolve-timeout", type=int, default=360)
    ap.add_argument("--skip-fire", action="store_true", help="stop after the quiet and boundary phases")
    args = ap.parse_args()
    rep = verify.Report()
    tag = time.strftime("%H%M%S")

    state_path = os.path.join(os.path.dirname(HERE), "hyperdx", "state.json")
    if not os.path.exists(state_path):
        raise SystemExit("run stack/hyperdx/provision.py first")
    state = json.load(open(state_path))
    if not all(n in state.get("alerts", {}) for n in MATCH):
        raise SystemExit("alerts are not provisioned: re-run stack/hyperdx/provision.py")
    sink("DELETE")

    # ---- 0 baseline: earlier traffic must age out of the 1-minute window
    print("== phase 0: waiting for both alerts to be OK (old failures ageing out)")
    ok_after = wait_until(lambda: set(alert_states(state).values()) == {"OK"}, 420)
    rep.check("AL0", "baseline: both alerts reach state OK", {k: "OK" for k in MATCH}, alert_states(state),
              ok=ok_after is not None)
    sink("DELETE")  # ignore any resolve notification from before this test

    # ---- 1 quiet: healthy traffic only
    print(f"== phase 1: healthy traffic, then {args.quiet_wait}s of observation")
    load(f"alerts-{tag}-quiet", "ok=40,slow=5,chain=15")
    time.sleep(args.quiet_wait)
    states = alert_states(state)
    rep.check("AL1a", "healthy traffic: both alerts remain OK", {k: "OK" for k in MATCH}, states)
    rep.check("AL1b", "healthy traffic: no webhook delivered", 0, len(sink()))

    # ---- 1c boundary: exactly the threshold (4 failures) is NOT "more than 4". HyperDX's `above` is >=,
    # `above_exclusive` is >; this probe is what tells them apart (the quiet phase sends no failures).
    print("== phase 1c: boundary probe, exactly 4 failures in one window must NOT fire")
    wait_for_safe_second()
    load(f"alerts-{tag}-boundary", "error=4")
    time.sleep(seconds_to_next_evaluation() + 60)  # the window closes, is evaluated, plus one spare cycle
    rep.check("AL1c", "exactly 4 failures (== threshold): both alerts OK when read about a minute later (a snapshot: a >= alert would already have resolved; AL1d is the discriminating check)", {k: "OK" for k in MATCH}, alert_states(state))
    rep.check("AL1d", "exactly 4 failures: no webhook delivered", 0, len(sink()))
    if args.skip_fire:
        outdir = os.path.join(HERE, "out", f"alerts-{tag}")
        os.makedirs(outdir, exist_ok=True)
        return verify.finish(rep, f"alerts-{tag}", outdir, {}, None)

    # ---- 2 fire: web 5xx = error 40 + exception 10 + chain-fail 10 = 60 spans; ERROR logs = 70
    print("== phase 2: burst of failures; waiting for both alerts to fire")
    t_fire = time.time()
    wait_for_safe_second()
    load(f"alerts-{tag}-fire", BURST)
    both = lambda: all(s == "ALERT" for s in alert_states(state).values()) and len(alert_states(state)) == 2
    fired_after = wait_until(both, args.fire_timeout)
    states = alert_states(state)
    rep.check("AL2a", "failure burst: both alerts reach state ALERT", {k: "ALERT" for k in MATCH}, states,
              ok=fired_after is not None)
    if fired_after is not None:
        print(f"   both alerts in ALERT {time.time() - t_fire:.0f}s after the burst started")
    arrived = wait_until(lambda: all(deliveries(n, "ALERT") for n in MATCH), 120, every=5)
    got = {n: len(deliveries(n, "ALERT")) for n in MATCH}
    rep.check("AL2b", "failure burst: a firing (ALERT) webhook delivery arrived for each alert",
              {n: ">= 1" for n in MATCH}, got, ok=arrived is not None)
    # The delivered value must be tied to what was actually sent, not just "something fired".
    expected = {TILE_ALERT: EXPECT_5XX, LOG_ALERT: EXPECT_ERR_LOGS}
    seen = {n: [reported_count(d) for d in deliveries(n, "ALERT")] for n in MATCH}
    rep.check("AL2c", "the value in each firing notification is above the threshold and no larger than what was sent "
              "(equal when the burst fits one 1-minute window)",
              {n: f"4 < n <= {expected[n]}" for n in MATCH}, seen,
              ok=all(seen[n] and all(c is not None and 4 < c <= expected[n] for c in seen[n]) for n in MATCH))
    rep.check("AL2d", "the counts in the firing notifications add up to exactly what was sent",
              expected, {n: sum(c for c in seen[n] if c) for n in MATCH},
              ok=all(sum(c for c in seen[n] if c) == expected[n] for n in MATCH))
    for n in MATCH:
        d = deliveries(n, "ALERT")
        if d:
            print(f"   firing delivery for '{n}': {d[0]['body'][:140]!r}  link={d[0].get('link', '')[:80]}")

    # ---- 3 resolve: no traffic; the window empties and the alerts clear
    print("== phase 3: traffic stopped; waiting for both alerts to resolve")
    t_stop = time.time()
    back = wait_until(lambda: set(alert_states(state).values()) == {"OK"}, args.resolve_timeout)
    rep.check("AL3a", "after the failures stop: both alerts return to OK", {k: "OK" for k in MATCH}, alert_states(state),
              ok=back is not None)
    if back is not None:
        print(f"   both alerts OK again {time.time() - t_stop:.0f}s after the burst ended")
    resolved = wait_until(lambda: all(deliveries(n, "OK") for n in MATCH), 120, every=5)
    got = {n: len(deliveries(n, "OK")) for n in MATCH}
    rep.check("AL3b", "a resolve (OK) webhook delivery arrived for each alert", {n: ">= 1" for n in MATCH}, got,
              ok=resolved is not None)

    run_id = f"alerts-{tag}"
    outdir = os.path.join(HERE, "out", run_id)
    os.makedirs(outdir, exist_ok=True)
    return verify.finish(rep, run_id, outdir, {}, None)


if __name__ == "__main__":
    sys.exit(main())
