#!/usr/bin/env python3
"""Fetch the line items the cost model needs from the official AWS Price List bulk files.

  fetch_prices.py [--region ap-southeast-2] [--out prices-<region>.json]

Standard library only. Downloads the public offer files (S3, MSK, EC2) from
https://pricing.us-east-1.amazonaws.com, extracts a small curated table and records, for every price,
the SKU, the publication date and the source URL. Nothing here is typed in from memory.
The EC2 file is ~480 MB; it is parsed as a stream of members to keep memory bounded.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
LOCATIONS = {  # AWS price-list "location" attribute for each region code
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "us-east-1": "US East (N. Virginia)",
    "eu-west-1": "EU (Ireland)",
}
EC2_TYPES = ["m6g.large", "m6g.xlarge", "m6g.2xlarge", "r6g.large", "r6g.xlarge", "r6g.2xlarge",
             "c6g.large", "c6g.xlarge", "c6g.2xlarge", "m5.large", "m5.xlarge", "r5.xlarge", "r5.2xlarge"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
DEC = json.JSONDecoder()


def download(url, name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=600) as r, open(path + ".part", "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.rename(path + ".part", path)
    return path


def load_small(path):
    with open(path) as f:
        return json.load(f)


def usd(term_block):
    """First on-demand USD price dimension of a term block: (price per unit, unit, description, begin range)."""
    for offer in term_block.values():
        for dim in offer["priceDimensions"].values():
            return {"usd": float(dim["pricePerUnit"]["USD"]), "unit": dim["unit"],
                    "description": dim["description"], "beginRange": dim.get("beginRange"),
                    "endRange": dim.get("endRange")}
    return None


def all_usd(term_block):
    out = []
    for offer in term_block.values():
        for dim in offer["priceDimensions"].values():
            out.append({"usd": float(dim["pricePerUnit"]["USD"]), "unit": dim["unit"],
                        "description": dim["description"], "beginRange": dim.get("beginRange"),
                        "endRange": dim.get("endRange")})
    return out


def s3_prices(location):
    path = download(f"{BASE}/AmazonS3/current/{region_code}/index.json", f"s3-{region_code}.json")
    d = load_small(path)
    prices = {"_meta": {"publicationDate": d["publicationDate"], "source": f"{BASE}/AmazonS3/current/{region_code}/index.json"}}
    for sku, p in d["products"].items():
        a = p.get("attributes", {})
        if a.get("location") != location:
            continue
        terms = d["terms"]["OnDemand"].get(sku)
        if not terms:
            continue
        if a.get("storageClass") == "General Purpose" and a.get("volumeType") == "Standard":
            prices["storage_standard_gb_month_tiers"] = {"sku": sku, "tiers": all_usd(terms)}
        elif p.get("productFamily") == "API Request":
            grp, ut = a.get("group"), a.get("usagetype", "")
            # PUT/COPY/POST/LIST and GET are the plain "...-Requests-Tier1/2" usage types (not "Annotation" ones)
            if grp in ("S3-API-Tier1", "S3-API-Tier2") and ut.endswith(f"-Requests-{grp[-5:]}"):
                prices.setdefault("requests_standard", {})[grp] = {"sku": sku, "usageType": ut, **usd(terms)}
    return prices


def msk_prices(location):
    path = download(f"{BASE}/AmazonMSK/current/{region_code}/index.json", f"msk-{region_code}.json")
    d = load_small(path)
    prices = {"_meta": {"publicationDate": d["publicationDate"], "source": f"{BASE}/AmazonMSK/current/{region_code}/index.json"}}
    for sku, p in d["products"].items():
        a = p.get("attributes", {})
        if a.get("location") != location:
            continue
        terms = d["terms"]["OnDemand"].get(sku)
        if not terms:
            continue
        ut = a.get("usagetype", "")
        m = re.search(r"-Kafka\.(m5|m7g)\.(large|xlarge|2xlarge)$", ut)
        if m:
            prices.setdefault("broker_hour", {})[f"kafka.{m.group(1)}.{m.group(2)}"] = {"sku": sku, "usageType": ut, **usd(terms)}
        elif re.search(r"-Kafka\.Storage", ut):
            prices.setdefault("storage_gb_month", {})[ut] = {"sku": sku, **usd(terms)}
    return prices


def members(text, start):
    """Iterate (key, value_start, value_end) of a JSON object whose '{' is at `start`, without building it."""
    i = start + 1
    n = len(text)
    ws = " \t\r\n"
    while True:
        while text[i] in ws or text[i] == ",":
            i += 1
        if text[i] == "}":
            return
        key, i = DEC.raw_decode(text, i)
        while text[i] in ws or text[i] == ":":
            i += 1
        val, j = DEC.raw_decode(text, i)
        yield key, val
        i = j


def ec2_prices(location):
    path = download(f"{BASE}/AmazonEC2/current/{region_code}/index.json", f"ec2-{region_code}.json")
    print("reading EC2 price list (large)...", file=sys.stderr)
    with open(path) as f:
        text = f.read()
    m = re.search(r'"publicationDate"\s*:\s*"([^"]+)"', text[:2000])
    prices = {"_meta": {"publicationDate": m.group(1) if m else None,
                        "source": f"{BASE}/AmazonEC2/current/{region_code}/index.json"}}
    wanted = {}
    p0 = text.index('"products"')
    start = text.index("{", p0)
    for sku, p in members(text, start):
        a = p.get("attributes", {})
        if a.get("location") != location:
            continue
        fam = p.get("productFamily")
        if (fam == "Compute Instance" and a.get("instanceType") in EC2_TYPES and a.get("operatingSystem") == "Linux"
                and a.get("tenancy") == "Shared" and a.get("preInstalledSw") == "NA" and a.get("capacitystatus") == "Used"):
            wanted[sku] = ("instance", a["instanceType"])
        elif fam == "Storage" and a.get("volumeApiName") in ("gp3", "gp2", "st1", "sc1"):
            wanted[sku] = ("ebs_gb_month", a["volumeApiName"])
        elif fam == "System Operation" and a.get("volumeApiName") == "gp3":
            wanted[sku] = ("ebs_gp3_extra", a.get("usagetype"))
    print(f"matched {len(wanted)} SKUs; reading terms", file=sys.stderr)
    t_terms = text.index('"terms"')  # NOT the first "OnDemand": that string is also a product attribute value
    t0 = text.index('"OnDemand"', t_terms)
    start = text.index("{", t0)
    left = set(wanted)
    for sku, term in members(text, start):
        if sku in wanted:
            kind, key = wanted[sku]
            u = usd(term)
            if u:
                if kind == "instance":
                    prices.setdefault("instance_hour", {})[key] = {"sku": sku, **u}
                elif kind == "ebs_gb_month":
                    prices.setdefault("ebs_gb_month", {})[key] = {"sku": sku, **u}
                else:
                    prices.setdefault("ebs_gp3_extra", {})[key] = {"sku": sku, **u}
            left.discard(sku)
            if not left:
                break
    return prices


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-southeast-2")
    ap.add_argument("--out")
    ap.add_argument("--skip-ec2", action="store_true")
    args = ap.parse_args()
    region_code = args.region
    location = LOCATIONS[region_code]
    out = {"region": region_code, "location": location, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "s3": s3_prices(location), "msk": msk_prices(location)}
    if not args.skip_ec2:
        out["ec2"] = ec2_prices(location)
    path = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), f"prices-{region_code}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {path}")
    print(json.dumps({"s3": list(out["s3"]), "msk": list(out["msk"]), "ec2": list(out.get("ec2", {}))}))
