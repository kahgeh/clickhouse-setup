#!/usr/bin/env python3
"""Writes modules/01-problem-and-outcome.html; the cost chart is generated from evidence/comparison.json."""
import json, os
from lib import bill_history
HERE = os.path.dirname(os.path.abspath(__file__)); C = os.path.dirname(HERE)
cmp_ = json.load(open(os.path.join(C, "evidence", "comparison.json")))
base = json.load(open(os.path.join(C, "evidence", "baseline.json")))

def chart():
    labels = {"A.": "Current AWS setup", "C.": "Platform, self-managed", "D.": "Platform, ClickHouse Cloud"}
    x0, w, maxv = 230, 440, 20000
    sc = lambda v: x0 + v / maxv * w
    out, y = [], 16
    for r in cmp_["cases"]:
        name = r["case"].strip()
        if name.startswith("(C stress"): continue
        key = name[:2]
        if key == "B.":
            lab = "AWS, " + ("50%" if "50%" in name else "80%") + " less scanned"; cls = "d-bar--muted"
        else:
            lab = labels[key]; cls = "d-bar--warn" if key == "A." else ""
        lo, hi = r["total_low"], r["total_high"]
        wd = max(hi - lo, 160) / maxv * w
        cl = ("d-bar " + cls).strip()
        out.append(f'<text class="d-label" x="0" y="{y+18}">{lab}</text>')
        out.append(f'<rect class="{cl}" x="{sc(lo):.0f}" y="{y}" width="{wd:.0f}" height="26" rx="3"/>')
        txt = f"${lo/1000:.1f}k" if hi - lo < 200 else f"${lo/1000:.1f}k to ${hi/1000:.1f}k"
        out.append(f'<text class="d-muted" x="{sc(lo)+wd+8:.0f}" y="{y+19}">{txt}</text>')
        y += 42
    ticks = []
    for v in (0, 5000, 10000, 15000, 20000):
        ticks.append(f'<line class="d-axis" x1="{sc(v):.0f}" y1="{y-6}" x2="{sc(v):.0f}" y2="{y}"/>'
                     f'<text class="d-tick" x="{sc(v)-12:.0f}" y="{y+16}">${v//1000}k</text>')
    return (f'<svg viewBox="0 0 800 {y+28}" role="img" aria-labelledby="f-cost-t f-cost-d">\n'
            '<title id="f-cost-t">Total monthly cost by case</title>\n'
            '<desc id="f-cost-d">Ranges of total monthly cost, including assumed engineer time, for the current AWS setup, the same setup with less scanning, and this platform self-managed or on ClickHouse Cloud.</desc>\n'
            f'<line class="d-axis" x1="{x0}" y1="8" x2="{x0}" y2="{y}"/>\n' + "\n".join(out + ticks) + "\n</svg>")

def case_table():
    rows = []
    for r in cmp_["cases"]:
        name = r["case"].strip()
        rng = lambda a, b: f"${a:,.0f}" if abs(a - b) < 1 else f"${a:,.0f} to ${b:,.0f}"
        sv = "" if name.startswith("A.") else rng(r["saving_vs_current_low"], r["saving_vs_current_high"])
        rows.append(f'<tr><td>{name}</td><td class="num">{rng(r["infra_low"], r["infra_high"])}</td>'
                    f'<td class="num">{rng(r["people_low"], r["people_high"])}</td>'
                    f'<td class="num">{rng(r["total_low"], r["total_high"])}</td><td class="num">{sv}</td></tr>')
    return ('<table class="data">\n<thead><tr><th scope="col">Case (monthly, USD)</th><th scope="col" class="num">Infrastructure</th>'
            '<th scope="col" class="num">Engineer time (assumed)</th><th scope="col" class="num">Total</th><th scope="col" class="num">Saving against current</th></tr></thead>\n<tbody>\n'
            + "\n".join(rows) + '\n</tbody>\n</table>')

items = base["sep_first_25_days"]
rows = "\n".join(f'<tr><td>{k}</td><td class="num">${v:,.0f}</td></tr>' for k, v in items.items())
total = sum(items.values())
scan = items["Logs Insights scanning"]
page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The problem and the outcome</title>
<link rel="stylesheet" href="../assets/tokens.css">
<link rel="stylesheet" href="../assets/theme.css">
<link rel="stylesheet" href="../assets/components.css">
<link rel="stylesheet" href="../assets/local.css">
</head>
<body data-module="m01">
<nav class="site-bar" aria-label="Course"><a class="brand" href="../index.html">Observability course</a></nav>
<main>
  <header>
    <p class="crumbs">Story · module 1 of 3</p>
    <h1>The problem and the outcome</h1>
    <p class="lede">Why reading logs can cost far more than writing them, and what a platform has to deliver to change that without losing data.</p>
  </header>

  <section class="goals" aria-labelledby="goals">
    <h2 id="goals">After this module you can</h2>
    <ul>
      <li>explain why a per-scan price makes search the dominant cost,</li>
      <li>state the three properties the platform is built to deliver, and</li>
      <li>read the cost comparison without mistaking it for a flat saving.</li>
    </ul>
  </section>

  <section aria-labelledby="problem">
    <h2 id="problem">A bill where reading costs more than writing</h2>
    <p>The reference workload for this course is one organisation running on CloudWatch Logs, Managed Prometheus and Grafana. Its log volume is 1.59 TB and 2.78 billion events in 30 days, about 569 bytes per event, and its metrics are 485 thousand active series at 7.6 thousand samples per second. The table shows the first 25 days of September, in US dollars.</p>
    <table class="data">
      <thead><tr><th scope="col">Cost line, first 25 days of September</th><th scope="col" class="num">USD</th></tr></thead>
      <tbody>
{rows}
        <tr class="is-bad"><td><strong>Total</strong> (scanning is {scan/total:.0%} of it)</td><td class="num"><strong>${total:,.0f}</strong></td></tr>
      </tbody>
    </table>
    <p>Writing the logs cost under $800. Searching them cost $12,343, because <dfn>CloudWatch Logs Insights</dfn> charges for every gigabyte a query scans: $0.0067 per GB in the Sydney region on the AWS price list published on 22 September 2026. A query that scans 1 TB costs $6.70, whether it finds one line or a million.</p>
    <aside class="caveat"><p><strong>Sensitive figures.</strong> These amounts come from one organisation's bill. Replace them with your own, or remove this table, before the course is shared outside that organisation.</p></aside>
  </section>

  <section aria-labelledby="outcome">
    <h2 id="outcome">The outcome the platform is built for</h2>
    <p>Three properties define success. The remaining modules show how each one is delivered and how it was tested.</p>
    <ol>
      <li><strong>Reading does not carry a per-scan price.</strong> Searches use capacity you provision, so a heavier search makes the cluster busier and does not raise the bill.</li>
      <li><strong>Telemetry survives failures.</strong> Data waits in a durable queue while a stage is down and is stored once afterwards, because duplicates caused by retries are removed. There are three exceptions: a duplicate created between the application and the collector, whatever the collector holds in memory if it crashes, and an object-store outage longer than the 5-minute retry window (outages up to 30 s were tested, and those from 30 s to 5 minutes were not).</li>
      <li><strong>One place to look.</strong> Logs, metrics and traces share trace identifiers, dashboards and alerts.</li>
    </ol>
  </section>

  <section aria-labelledby="explorer">
    <h2 id="explorer">Try the scan-cost explorer</h2>
    <p>Move the scanned volume and compare a per-scan bill with the compute a column store needs to serve the same searches. The compute figures are assumptions, not measurements: how fast one vCPU scans depends on the query and on the indexes, and you should measure it on your own searches.</p>
    <p>"Data ClickHouse never reads" is the share of what the per-scan price counts that a column store can skip. A per-scan price counts every byte in the searched range. ClickHouse keeps data ordered by time and split by day, so a search for the last hour does not touch older data, and skip indexes on the log body can rule out blocks that cannot match. Fifty per cent is an assumption: measure it on your own searches by comparing the bytes a query reads with the bytes in its time range.</p>
    <div class="sim" id="scan-sim">
      <h3>Per-scan price against provisioned capacity</h3>
      <div class="controls">
        <label class="ctl">Scanned per month (TB)
          <input type="range" id="sc-tb" min="100" max="6000" step="50" value="2384"><output id="sc-tb-o">2,384</output></label>
        <label class="ctl">Data ClickHouse never reads (assumed)
          <input type="range" id="sc-prune" min="0" max="90" step="5" value="50"><output id="sc-prune-o">50%</output></label>
        <label class="ctl">Search speed
          <select id="sc-speed">
            <option value="2">fast: indexed filters, 2 GB/s per vCPU</option>
            <option value="0.5" selected>typical: 0.5 GB/s per vCPU</option>
            <option value="0.1">slow: substring on the body, 0.1 GB/s per vCPU</option>
          </select></label>
      </div>
      <p class="out" id="sc-out" aria-live="polite"></p>
      <p class="readout">Per-scan price: $0.0067 per GB (AWS list, Sydney). vCPU price: $0.0604 per hour (r6g.xlarge, 4 vCPU, on demand). Peak load is assumed to be five times the average.</p>
    </div>
  </section>

  <section aria-labelledby="compare">
    <h2 id="compare">What the comparison says, and what it does not</h2>
    <p>The chart gives the total monthly cost for each case, including engineer time. Engineer time is assumed: 0.1 to 0.25 of an engineer for the optimised AWS setup, 0.5 to 1.0 for the platform self-managed, and 0.25 to 0.5 on ClickHouse Cloud, at $12,000 per engineer-month.</p>
    <figure>
{chart()}
      <figcaption>The current setup runs at $18.5k a month at the September rate. Scanning less on AWS alone removes $4.4k to $10.6k. The platform ranges overlap the optimised AWS cases, so the saving depends on how much engineer time the platform needs. The ClickHouse Cloud rates were read from a pricing page and are unconfirmed.</figcaption>
    </figure>
    <p>The same numbers as a table, with the stress case in which platform infrastructure is twice the high estimate:</p>
    {case_table()}
    {bill_history()}
    <aside class="note"><p><strong>Do not quote a flat saving.</strong> A figure like "ten times cheaper" compares platform infrastructure of about $1k with the whole bill. It leaves out engineer time, migration, traces, and the features a managed service includes. Platform infrastructure estimates are $0.6k to $1.4k a month, and none of it has been load-tested.</p></aside>
  </section>

  <section class="quiz" data-quiz aria-labelledby="quiz-h">
    <h2 id="quiz-h">Check your understanding</h2>

    <fieldset class="q" data-answer="c">
      <legend>Users start running twice as many searches, each scanning the same amount of data. What changes on a per-scan bill and on a provisioned cluster?</legend>
      <label class="opt"><input type="radio" name="q1" value="a">
        <span>Both bills double, because each search reads the same data again and both bills charge for that.<span class="why">Only the per-scan bill follows the scanned volume; a provisioned cluster costs the same whatever it scans, so revisit how each is priced.</span></span></label>
      <label class="opt"><input type="radio" name="q1" value="b">
        <span>Neither bill changes, because the amount of stored data stays the same and so does the cost.<span class="why">The per-scan bill is proportional to bytes scanned, so doubling the searches doubles it.</span></span></label>
      <label class="opt"><input type="radio" name="q1" value="c">
        <span>The per-scan bill doubles; the cluster stays flat until it runs out of capacity.<span class="why">Scan-priced cost is proportional to scanned data, while a cluster only costs more once load needs more vCPU, or searches get slower.</span></span></label>
      <p class="feedback" role="status" aria-live="polite"></p>
    </fieldset>

    <fieldset class="q" data-answer="a">
      <legend>A proposal says the platform makes the bill ten times smaller, comparing about $1k of infrastructure with an $18.5k a month bill. What is the main flaw?</legend>
      <label class="opt"><input type="radio" name="q2" value="a">
        <span>It sets part of the platform's cost against the whole current bill and omits engineer time and the option of scanning less on AWS.<span class="why">Adding assumed engineer time and the optimised AWS case brings the ranges close together, which is why the saving depends on staffing.</span></span></label>
      <label class="opt"><input type="radio" name="q2" value="b">
        <span>It assumes infrastructure is always cheaper than software, so the proposal never needs to compare running costs in detail.<span class="why">That is not a rule, and the flaw is in what the comparison leaves out, not in the category of cost.</span></span></label>
      <label class="opt"><input type="radio" name="q2" value="c">
        <span>The platform cannot hold 2.77 TB of logs at that infrastructure size, so the comparison understates what it needs.<span class="why">Storage of that size fits easily; the issue is the scope of the comparison, not capacity.</span></span></label>
      <p class="feedback" role="status" aria-live="polite"></p>
    </fieldset>

    <fieldset class="q" data-answer="b">
      <legend>Search speed in the explorer is set to "slow" and the scanned volume is raised to 6,000 TB. What is the realistic consequence of provisioning too little capacity?</legend>
      <label class="opt"><input type="radio" name="q3" value="a">
        <span>The bill grows with the scanned volume, exactly as it would on a per-scan price, until the cluster is resized.<span class="why">That is the per-scan model; on a cluster the cost is fixed by the vCPU you run.</span></span></label>
      <label class="opt"><input type="radio" name="q3" value="b">
        <span>Searches slow down or are limited, because capacity caps the cost instead of letting it grow without limit.<span class="why">With provisioned capacity the trade is performance, not price, which is why read limits and quotas matter.</span></span></label>
      <label class="opt"><input type="radio" name="q3" value="c">
        <span>Data is lost, because the searches delete what they scan once the cluster cannot keep up with them.<span class="why">Searches only read; capacity limits affect speed, not stored data.</span></span></label>
      <p class="feedback" role="status" aria-live="polite"></p>
    </fieldset>

    <p class="score" aria-live="polite"></p>
    <p class="gate" hidden>All correct. Continue to module 2, the platform in one picture.</p>
  </section>

  <nav class="pager" aria-label="Module navigation">
    <a href="../index.html">Course home</a>
    <a class="next" href="02-platform-in-one-picture.html">Next: the platform in one picture</a>
  </nav>
</main>
<script src="../assets/quiz.js" defer></script>
<script src="../assets/course.js" defer></script>
<script src="../assets/sim-scan-cost.js" defer></script>
</body>
</html>
'''
open(os.path.join(C, "modules", "01-problem-and-outcome.html"), "w").write(page)
print("wrote module 01")
