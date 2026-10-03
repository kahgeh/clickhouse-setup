#!/usr/bin/env python3
"""Monthly AWS cost model for the OTLP -> OpenData Buffer (S3) -> ClickHouse <- HyperDX platform.

  model.py [--region ap-southeast-2] [--json out.json]

Standard library only. Prices come from prices-<region>.json (fetch_prices.py: AWS Price List bulk files,
on-demand, no Savings Plans / reserved discounts). Every input below is tagged:
  [USER]   given by the user for their workload
  [AWS]    from the AWS price list
  [ASSUME] an assumption you should challenge; the model reports low / base / high where it matters
  [MEAS]   measured on the local stack (synthetic data: NOT representative for compression)
Not modelled: engineer time, data transfer between AZs, NAT/egress, backups, support plans, traces (no volume
given), the cost of your current vendor. See the notes printed at the end.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MONTH_S = 30 * 86400
MONTH_H = 30 * 24

# ------------------------------------------------------------------ workload [USER]
USER = {
    "logs_raw_bytes_month": 1.59e12,
    "logs_events_month": 2.78e9,
    "logs_bytes_per_event": 569,
    "logs_retained_raw_bytes": 2.77e12,  # "stored 2.77 TB": read as raw-equivalent logs held (about 52 days)
    "metrics_samples_month": 20e9,
    "metrics_samples_per_s": 7.6e3,
    "metrics_active_series": 485e3,
}

# ------------------------------------------------------------------ scenarios [ASSUME]
# cost direction: "low" = favourable to this platform, "high" = unfavourable
SCENARIOS = {
    "low": {"log_ch_ratio": 10, "metric_ch_bytes_per_sample": 10, "flush_s": 10, "poll_s": 1.0, "ha": False},
    "base": {"log_ch_ratio": 7, "metric_ch_bytes_per_sample": 16, "flush_s": 5, "poll_s": 1.0, "ha": True},
    "high": {"log_ch_ratio": 4, "metric_ch_bytes_per_sample": 28, "flush_s": 1, "poll_s": 0.25, "ha": True},
}
FIXED = {
    "collectors": 3,                 # [ASSUME] HA gateway replicas, each handles 1/3 of the data
    "signals": 2,                    # logs + metrics (traces: no volume given)
    "buffer_zstd_ratio_logs": 5,     # [ASSUME] OTLP protobuf -> zstd
    "buffer_zstd_ratio_metrics": 4,  # [ASSUME]
    "otlp_bytes_per_log": 650,       # [ASSUME] ~ raw event + OTLP framing
    "otlp_bytes_per_sample": 80,     # [ASSUME] data point + attributes + framing, amortised
    "flush_size_bytes": 4 * 2**20,   # [ASSUME] exporter flush_size_bytes
    "puts_per_flush": 1.25,          # [ASSUME] 1 data object + 1 manifest PUT per 4 batches (manifest_append_batch_size)
    "metrics_retention_months": 1.0,  # [ASSUME] not given by the user
    "ebs_headroom": 1.3,             # [ASSUME] 30% free space
    "gets_per_object": 1.0,          # [ASSUME] each data object read once by the ingestor
}
INSTANCES = {  # [ASSUME] sizing, [AWS] price
    "collector": "c6g.large", "ingestor": "c6g.large", "clickhouse": "r6g.xlarge",
    "keeper": "c6g.large", "hyperdx": "m6g.large",
}


def load_prices(region):
    with open(os.path.join(HERE, f"prices-{region}.json")) as f:
        return json.load(f)


def s3_unit_prices(p):
    r = p["s3"]["requests_standard"]
    tiers = p["s3"]["storage_standard_gb_month_tiers"]["tiers"]
    return {"put": r["S3-API-Tier1"]["usd"], "get": r["S3-API-Tier2"]["usd"], "storage_gb": tiers[0]["usd"]}


def compute(p, scn, flush_s=None):
    u = USER
    f = FIXED
    flush = flush_s if flush_s is not None else scn["flush_s"]
    ec2 = p["ec2"]["instance_hour"]
    gp3 = p["ec2"]["ebs_gb_month"]["gp3"]["usd"]
    s3 = s3_unit_prices(p)
    out = {}

    # ---- Buffer on S3 (transient: objects are garbage-collected after consumption, so storage is negligible)
    bytes_logs = u["logs_events_month"] * f["otlp_bytes_per_log"] / f["buffer_zstd_ratio_logs"]
    bytes_metrics = u["metrics_samples_month"] * f["otlp_bytes_per_sample"] / f["buffer_zstd_ratio_metrics"]
    per_pipeline = {"logs": bytes_logs / f["collectors"], "metrics": bytes_metrics / f["collectors"]}
    puts = 0.0
    objects = 0.0
    for sig, b in per_pipeline.items():
        time_driven = MONTH_S / flush           # one flush per interval while data keeps arriving
        size_driven = b / f["flush_size_bytes"]  # or one per flush_size_bytes
        flushes = max(time_driven, size_driven) * f["collectors"]
        objects += flushes
        puts += flushes * f["puts_per_flush"]
    polls = (MONTH_S / scn["poll_s"]) * f["signals"]  # one manifest GET per poll per ingestor
    gets = objects * f["gets_per_object"] + polls
    out["S3 PUT requests"] = puts * s3["put"]
    out["S3 GET requests (incl. manifest polling)"] = gets * s3["get"]
    out["S3 transient storage"] = (bytes_logs + bytes_metrics) / 1e9 * (10 / 30) * s3["storage_gb"]  # ~10 min GC grace
    out["_detail_puts_month"] = puts
    out["_detail_gets_month"] = gets
    out["_detail_buffer_bytes_month"] = bytes_logs + bytes_metrics

    # ---- ClickHouse storage + compute
    logs_comp = u["logs_retained_raw_bytes"] / scn["log_ch_ratio"]
    metrics_comp = u["metrics_samples_month"] * scn["metric_ch_bytes_per_sample"] * f["metrics_retention_months"]
    stored_gb = (logs_comp + metrics_comp) / 1e9
    replicas = 2 if scn["ha"] else 1
    out["ClickHouse EBS storage (gp3)"] = stored_gb * f["ebs_headroom"] * replicas * gp3
    out["ClickHouse compute"] = replicas * ec2[INSTANCES["clickhouse"]]["usd"] * MONTH_H
    if scn["ha"]:
        out["ClickHouse Keeper x3"] = 3 * ec2[INSTANCES["keeper"]]["usd"] * MONTH_H
    out["_detail_stored_gb"] = stored_gb

    # ---- Pipeline compute
    out["Collectors"] = f["collectors"] * ec2[INSTANCES["collector"]]["usd"] * MONTH_H
    out["Ingestors"] = f["signals"] * ec2[INSTANCES["ingestor"]]["usd"] * MONTH_H
    out["HyperDX + MongoDB"] = ec2[INSTANCES["hyperdx"]]["usd"] * MONTH_H
    return out


def total(d):
    return sum(v for k, v in d.items() if not k.startswith("_"))


def kafka_alternative(p, scn):
    """Replace the S3 Buffer by MSK (what OpenData positions Buffer against). Same ClickHouse/collectors."""
    f = FIXED
    u = USER
    msk = p["msk"]
    broker = msk["broker_hour"]["kafka.m7g.large"]["usd"]
    storage = msk["storage_gb_month"]["APS2-Kafka.Storage.GP2"]["usd"]
    bytes_month = (u["logs_events_month"] * f["otlp_bytes_per_log"] / f["buffer_zstd_ratio_logs"]
                   + u["metrics_samples_month"] * f["otlp_bytes_per_sample"] / f["buffer_zstd_ratio_metrics"])
    retention_days = 3  # [ASSUME]
    replication = 3
    stored_gb = bytes_month / 30 * retention_days * replication / 1e9
    return {"MSK brokers (3 x kafka.m7g.large)": 3 * broker * MONTH_H, "MSK storage (gp2)": stored_gb * storage}


# [CLICKHOUSE PRICING PAGE, fetched 2026-10-03 through a page summariser: VERIFY before quoting]
# Enterprise plan, AWS ap-southeast-2 (Sydney). The Scale plan rate was not returned and may be lower.
CH_CLOUD = {"compute_unit_hour": 0.49046, "unit": "8 GiB RAM + 2 vCPU", "storage_tb_month": 27.50,
            "backup_tb_month": 27.50, "min_replicas_for_over_12GiB": 2,
            "source": "https://clickhouse.com/pricing (Enterprise, ap-southeast-2)"}


def cloud_alternative(p, scn_name="base"):
    """ClickStack on ClickHouse Cloud: managed ClickHouse + HyperDX UI. Collectors stay self-managed.
    A = collectors write straight to ClickHouse Cloud (no Buffer queue, no ingestors).
    B = keep Buffer + ingestors in front of ClickHouse Cloud."""
    scn = SCENARIOS[scn_name]
    f = FIXED
    ec2 = p["ec2"]["instance_hour"]
    stored_tb = compute(p, scn)["_detail_stored_gb"] / 1e3
    storage = stored_tb * CH_CLOUD["storage_tb_month"]
    backup = stored_tb * CH_CLOUD["backup_tb_month"]  # 1 day of backup retention, roughly one extra copy
    collectors = f["collectors"] * ec2[INSTANCES["collector"]]["usd"] * MONTH_H
    out = {}
    for label, units_per_replica in (("small: 2 replicas x 16 GiB", 2), ("base: 2 replicas x 32 GiB", 4), ("large: 2 replicas x 64 GiB", 8)):
        compute_usd = 2 * units_per_replica * CH_CLOUD["compute_unit_hour"] * MONTH_H
        a = compute_usd + storage + backup + collectors
        b = a + f["signals"] * ec2[INSTANCES["ingestor"]]["usd"] * MONTH_H + 31  # + ingestors + ~S3 Buffer (base)
        out[label] = {"compute": compute_usd, "storage+backup": storage + backup, "collectors": collectors,
                      "A_direct": a, "B_with_buffer": b}
    return out


# [ASSUME - MEASURE ON YOUR OWN QUERIES] how much uncompressed log data one vCPU scans per second for
# typical search filters (substring / field filters). Real values vary by 10x with the query and the indexes.
SCAN_GB_PER_S_PER_VCPU = {"fast (indexed / prefix filters)": 2.0, "typical": 0.5, "slow (substring on Body, no index)": 0.1}


def scan_sizing(p, scan_tb_list, peak_factor=5.0, prune=0.5):
    """How many vCPUs the log queries need, given the TB your vendor reports as SCANNED per month.
    prune = fraction of scanned data ClickHouse skips thanks to ORDER BY / partition / skip indexes (a vendor that bills
    per scan usually scans far more than a column store with a time-bounded, ordered primary key needs)."""
    ec2 = p["ec2"]["instance_hour"][INSTANCES["clickhouse"]]["usd"]
    vcpu_hour = ec2 / 4  # r6g.xlarge = 4 vCPU
    rows = []
    for tb in scan_tb_list:
        gb_s_avg = tb * 1000 * (1 - prune) / MONTH_S
        row = {"scan_tb_month": tb, "avg_gb_per_s": gb_s_avg}
        for label, rate in SCAN_GB_PER_S_PER_VCPU.items():
            avg_v = gb_s_avg / rate
            peak_v = avg_v * peak_factor
            row[label] = {"avg_vcpu": avg_v, "peak_vcpu": peak_v, "extra_usd_month_at_peak": peak_v * vcpu_hour * MONTH_H}
        rows.append(row)
    return rows


def money(x):
    return f"${x:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-southeast-2")
    ap.add_argument("--json")
    ap.add_argument("--scan-tb", type=float, nargs="*", help="TB of log data your vendor reports as SCANNED per month (several values allowed)")
    ap.add_argument("--scan-peak", type=float, default=5.0, help="[ASSUME] peak-to-average query load factor")
    ap.add_argument("--scan-prune", type=float, default=0.5, help="[ASSUME] fraction of scanned data ClickHouse avoids via ordering/indexes")
    ap.add_argument("--vendor-low", type=float, help="current monthly bill, low end (USD)")
    ap.add_argument("--vendor-high", type=float, help="current monthly bill, high end (USD)")
    ap.add_argument("--fte-month", type=float, nargs="*", default=[8000, 12000, 16000],
                    help="[ASSUME] fully loaded monthly cost of one platform engineer, USD (several values allowed)")
    args = ap.parse_args()
    p = load_prices(args.region)
    u = USER
    print(f"Region {p['region']} ({p['location']}); AWS price lists published: "
          f"S3 {p['s3']['_meta']['publicationDate'][:10]}, EC2 {p['ec2']['_meta']['publicationDate'][:10]}, "
          f"MSK {p['msk']['_meta']['publicationDate'][:10]}")
    print(f"Workload: logs {u['logs_raw_bytes_month'] / 1e12:.2f} TB / {u['logs_events_month'] / 1e9:.2f} B events per month "
          f"({u['logs_events_month'] / MONTH_S:,.0f} ev/s, {u['logs_bytes_per_event']} B each); "
          f"metrics {u['metrics_samples_month'] / 1e9:.0f} B samples/month ({u['metrics_samples_per_s'] / 1e3:.1f}k/s, "
          f"{u['metrics_active_series'] / 1e3:.0f}k series)\n")
    results = {}
    for name, scn in SCENARIOS.items():
        results[name] = compute(p, scn)
    keys = [k for k in results["base"] if not k.startswith("_")]
    print(f"{'Monthly cost (USD, on-demand)':<46}{'low':>10}{'base':>10}{'high':>10}")
    for k in keys:
        print(f"{k:<46}" + "".join(f"{money(results[s].get(k, 0)):>10}" for s in SCENARIOS))
    print(f"{'TOTAL':<46}" + "".join(f"{money(total(results[s])):>10}" for s in SCENARIOS))
    ingest_tb = (u["logs_raw_bytes_month"]) / 1e12
    print(f"{'  per TB of raw logs ingested':<46}" + "".join(f"{money(total(results[s]) / ingest_tb):>10}" for s in SCENARIOS))
    print("\nScenario assumptions:")
    for s, v in SCENARIOS.items():
        print(f"  {s:<5} log ClickHouse ratio {v['log_ch_ratio']}x, metric {v['metric_ch_bytes_per_sample']} B/sample, "
              f"collector flush {v['flush_s']} s, ingestor poll {v['poll_s']} s, ClickHouse HA {v['ha']}")
    d = results["base"]
    print(f"\nBase detail: {d['_detail_puts_month'] / 1e6:,.1f} M S3 PUTs and {d['_detail_gets_month'] / 1e6:,.1f} M GETs per month; "
          f"ClickHouse holds ~{d['_detail_stored_gb']:,.0f} GB (before headroom/replicas); "
          f"Buffer carries ~{d['_detail_buffer_bytes_month'] / 1e9:,.0f} GB/month (transient).")

    print("\nSensitivity: collector flush interval vs S3 request cost (base scenario otherwise)")
    print(f"  {'flush':>7}{'PUTs/month':>16}{'S3 requests $':>16}{'added latency (~)':>22}")
    sens = []
    for fl in (0.1, 0.5, 1, 5, 10, 30):
        r = compute(p, SCENARIOS["base"], flush_s=fl)
        cost = r["S3 PUT requests"] + r["S3 GET requests (incl. manifest polling)"]
        sens.append({"flush_s": fl, "puts": r["_detail_puts_month"], "s3_request_usd": cost})
        print(f"  {fl:>6}s{r['_detail_puts_month'] / 1e6:>14,.1f} M{money(cost):>16}{'~' + str(fl) + ' s':>22}")
    print("  (our local stack uses 0.1 s for low latency; at this workload that setting is the single biggest S3 cost lever)")

    print("\nBuffer alternative: Kafka (MSK) instead of the S3 Buffer, base scenario")
    kf = kafka_alternative(p, SCENARIOS["base"])
    s3_buffer = results["base"]["S3 PUT requests"] + results["base"]["S3 GET requests (incl. manifest polling)"] + results["base"]["S3 transient storage"]
    for k, v in kf.items():
        print(f"  {k:<40}{money(v):>10}")
    print(f"  {'MSK subtotal':<40}{money(sum(kf.values())):>10}   vs S3 Buffer subtotal {money(s3_buffer)} "
          f"(difference {money(sum(kf.values()) - s3_buffer)}/month)")

    print("\nAlternative: ClickStack on ClickHouse Cloud (managed ClickHouse + HyperDX UI), base data volume")
    print(f"  prices [{CH_CLOUD['source']}]: ${CH_CLOUD['compute_unit_hour']}/unit-hour ({CH_CLOUD['unit']}), ${CH_CLOUD['storage_tb_month']}/TB-month")
    print(f"  {'ClickHouse Cloud sizing':<34}{'compute':>10}{'storage':>10}{'collectors':>12}{'A: direct':>12}{'B: +Buffer':>12}")
    cloud = cloud_alternative(p)
    for k, v in cloud.items():
        print(f"  {k:<34}{money(v['compute']):>10}{money(v['storage+backup']):>10}{money(v['collectors']):>12}{money(v['A_direct']):>12}{money(v['B_with_buffer']):>12}")
    print("  Cloud sizing is a JUDGEMENT (no load test). Removes our ClickHouse, Keeper, HyperDX and MongoDB operations; collectors stay self-managed.")
    scans = args.scan_tb or [100, 500, 2000, 10000]
    shown = "your figure" if args.scan_tb else "illustrative volumes, NOT yours - give me your vendor's scanned TB"
    print(f"\nQuery sizing: vCPUs needed for log searches at scan volumes ({shown}); prune={args.scan_prune:.0%}, peak x{args.scan_peak:g}")
    print(f"  ClickHouse node in the base estimate has 4 vCPU per replica; extra vCPU priced at ${p['ec2']['instance_hour'][INSTANCES['clickhouse']]['usd'] / 4:.3f}/vCPU-hour (r6g)")
    heads = list(SCAN_GB_PER_S_PER_VCPU)
    print(f"  {'scanned TB/mo':>14}{'avg GB/s':>10}" + "".join(f"  {h[:22]:>34}" for h in heads))
    for r in scan_sizing(p, scans, args.scan_peak, args.scan_prune):
        cells = "".join(f"  {r[h]['peak_vcpu']:>8.0f} vCPU peak, +{money(r[h]['extra_usd_month_at_peak']):>7}" for h in heads)
        print(f"  {r['scan_tb_month']:>14,.0f}{r['avg_gb_per_s']:>10.2f}{cells}")
    print("  (peak vCPU needed to serve the load; compare with the 4-8 vCPU already in the base estimate. Throughput per vCPU is an ASSUMPTION.)")
    if args.vendor_low and args.vendor_high:
        vendor = (args.vendor_low, args.vendor_high)
        print(f"\nComparison with your current bill (${vendor[0]:,.0f}-${vendor[1]:,.0f}/month)")
        tot = {k: total(v) for k, v in results.items()}
        print(f"  {'case':<44}{'infra $/mo':>12}{'saving vs $' + format(vendor[0] / 1000, '.0f') + 'k':>14}{'saving vs $' + format(vendor[1] / 1000, '.0f') + 'k':>14}")
        cases = [("base estimate", tot["base"]), ("high estimate", tot["high"]),
                 ("stress: 2 x high (headroom, traces, mis-sized)", 2 * tot["high"]),
                 ("stress: 4 x high (pessimistic)", 4 * tot["high"])]
        for name, c in cases:
            print(f"  {name:<44}{money(c):>12}{money(vendor[0] - c):>14}{money(vendor[1] - c):>14}")
        c = 2 * tot["high"]
        print(f"\n  Break-even in engineer time, against the stress case (infra = {money(c)}/month):")
        for fte in args.fte_month:
            for v in vendor:
                n = (v - c) / fte
                print(f"    one engineer at ${fte:,.0f}/month loaded, vendor ${v:,.0f}: platform may use up to {n:.1f} FTE and still break even")
        print("  (engineer cost is an ASSUMPTION; pass --fte-month with your own figure)")
    print("""
Notes and limits (read before quoting any number):
* On-demand prices only: Savings Plans / reserved instances typically cut compute substantially. Not modelled.
* Compression ratios and metric bytes-per-sample are ASSUMPTIONS. The local measurements (33 B/log, ~9 B/sample) come from
  synthetic, highly repetitive data and must not be used. Replace them by a measurement on a sample of your real data.
* "Stored 2.77 TB" is read as raw-equivalent log volume retained (about 52 days at 1.59 TB/month). If it is the vendor's
  stored size, retention and compression change.
* Traces are excluded (no volume given). Engineer/on-call time, data transfer between AZs, backups, support and HyperDX
  licensing/SSO needs are excluded. People cost is usually larger than infrastructure cost at this scale.
* The comparison with your current vendor needs your actual monthly bill; add it and the saving (or loss) follows.
* The platform proved correctness and resilience on a laptop. Throughput at 1k events/s plus 7.6k samples/s has NOT been
  load-tested here; ClickHouse sizing is a judgement, not a measurement.""")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"region": p["region"], "user": USER, "scenarios": SCENARIOS, "fixed": FIXED,
                       "results": {k: {a: b for a, b in v.items()} for k, v in results.items()},
                       "totals": {k: total(v) for k, v in results.items()},
                       "flush_sensitivity": sens, "kafka_alternative": kf}, fh, indent=2)


if __name__ == "__main__":
    main()
