#!/usr/bin/env python3
"""Three-case monthly cost comparison on the user's real bill (30-day normalised, USD).

  compare.py [--region ap-southeast-2] [--fte-month 12000] [--json comparison.json]

Cases: (A) current AWS setup from the bill; (B) the same AWS setup with query discipline (less data scanned);
(C) this platform self-managed; (D) this platform with ClickHouse Cloud. Platform infrastructure comes from model.py.
People cost is an ASSUMPTION (--fte-month, FTE fractions below). See model.py for all other assumptions and limits.
"""
import argparse
import json
import os

import model

HERE = os.path.dirname(os.path.abspath(__file__))


def baseline_30d():
    b = json.load(open(os.path.join(HERE, "baseline.json")))
    scale = 30 / b["days_in_period"]
    items = {k: v * (1 if k in b["fixed_items"] else scale) for k, v in b["sep_first_25_days"].items()}
    return b, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-southeast-2")
    ap.add_argument("--fte-month", type=float, default=12000, help="[ASSUME] fully loaded monthly cost of one engineer, USD")
    ap.add_argument("--json")
    args = ap.parse_args()
    p = model.load_prices(args.region)
    b, items = baseline_30d()
    scan_key = "Logs Insights scanning"
    current = sum(items.values())
    rest = current - items[scan_key]
    aug = b["monthly_totals"]["Aug"]

    cases = []  # (name, infra_low, infra_high, fte_low, fte_high, note)
    cases.append(("A. Current AWS setup (Sep rate, 30-day)", current, current, 0, 0, "from the bill; Aug actual was ${:,.0f}".format(aug)))
    for cut, label in ((0.5, "50%"), (0.8, "80%")):
        v = rest + items[scan_key] * (1 - cut)
        cases.append((f"B. AWS with query discipline ({label} less scanned)", v, v, 0.1, 0.25, "ASSUMED scan reduction; some engineer time"))
    res = {n: model.compute(p, s) for n, s in model.SCENARIOS.items()}
    tot = {n: model.total(v) for n, v in res.items()}
    cases.append(("C. This platform, self-managed", tot["low"], tot["high"], 0.5, 1.0, "AWS on-demand; ClickHouse + HyperDX run by you"))
    cases.append(("   (C stress: 2 x high infra)", 2 * tot["high"], 2 * tot["high"], 0.5, 1.0, "headroom, traces, mis-sizing"))
    cloud = model.cloud_alternative(p)
    lo = min(v["B_with_buffer"] for v in cloud.values())
    hi = max(v["B_with_buffer"] for v in cloud.values())
    cases.append(("D. This platform, ClickHouse Cloud", lo, hi, 0.25, 0.5, "Cloud rates UNVERIFIED (Enterprise page); Buffer kept"))

    print(f"Baseline: September bill normalised to 30 days = ${current:,.0f}; of which log-query scanning ${items[scan_key]:,.0f} "
          f"({items[scan_key] / current:.0%}); everything else ${rest:,.0f}.")
    s = b["scan_price_usd_per_gb"]
    eff = items[scan_key] / (b["scanned_tb_30_days"] * 1000)
    print(f"Scan price (AWS list, Sydney): ${s['logs_insights_sydney']}/GB Logs Insights, ${s['log_analytics_sydney']}/GB log analytics; "
          f"the bill implies an effective ${eff:.4f}/GB over {b['scanned_tb_30_days']:,} TB.\n")
    print(f"{'Case':<48}{'infra $/mo':>16}{'people $/mo':>16}{'total $/mo':>18}{'vs A':>14}")
    rows = []
    for name, ilo, ihi, flo, fhi, note in cases:
        plo, phi = flo * args.fte_month, fhi * args.fte_month
        tlo, thi = ilo + plo, ihi + phi
        saving = (current - thi, current - tlo)
        rows.append({"case": name, "infra_low": ilo, "infra_high": ihi, "people_low": plo, "people_high": phi,
                     "total_low": tlo, "total_high": thi, "saving_vs_current_low": saving[0], "saving_vs_current_high": saving[1], "note": note})
        rng = lambda a, c: model.money(a) if abs(a - c) < 1 else f"{model.money(a)}-{model.money(c)}"
        print(f"{name:<48}{rng(ilo, ihi):>16}{rng(plo, phi):>16}{rng(tlo, thi):>18}"
              f"{('-' if name.startswith('A.') else rng(*saving)):>14}")
    print(f"\n(people = FTE fraction x ${args.fte_month:,.0f}/month, ASSUMED: A 0, B 0.1-0.25, C 0.5-1.0, D 0.25-0.5 FTE)")
    print("Reading it: B shows what disciplined querying alone could save on AWS; C and D add a new platform to run.")
    print("Not included anywhere: migration effort, parallel running, traces, features you use that HyperDX lacks (SSO, RBAC, long-term")
    print("archive, alert routing), and any load test: the platform's capacity at this volume is a judgement, not a measurement.")
    if args.json:
        json.dump({"baseline_30d": items, "current_total": current, "cases": rows, "fte_month": args.fte_month}, open(args.json, "w"), indent=2)


if __name__ == "__main__":
    main()
