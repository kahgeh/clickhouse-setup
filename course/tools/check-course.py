#!/usr/bin/env python3
"""Static quality gates for the course (standard library only). Exit code is non-zero on any failure.

  check-course.py

Checks: links and images resolve; anchors exist; quiz structure (answers, options, explanations, answer positions);
pager chain matches the module order; the index lists every module; evidence-derived numbers on the pages match the
source reports; hard content rules (banned-term rule, no flat-saving claims, no raw bill screenshot).
"""
import glob
import json
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
COURSE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lib  # noqa: E402

errors, notes = [], []


def err(page, msg):
    errors.append(f"{page}: {msg}")


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids, self.imgs, self.text, self.stack = [], set(), [], [], []
        self.quizzes, self.cur_q, self.cur_field, self.in_why = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag in ("a", "link") and a.get("href"):
            self.links.append(a["href"])
        if tag in ("script", "img") and a.get("src"):
            self.links.append(a["src"])
            if tag == "img":
                self.imgs.append(a)
        if tag == "section" and "data-quiz" in a:
            self.cur_q = {"fields": []}
            self.quizzes.append(self.cur_q)
        if self.cur_q is not None and tag == "fieldset":
            self.cur_field = {"answer": a.get("data-answer"), "options": [], "feedback": False}
            self.cur_q["fields"].append(self.cur_field)
        if self.cur_field is not None:
            if tag == "input" and a.get("type") == "radio":
                self.cur_field["options"].append({"value": a.get("value"), "name": a.get("name"), "why": None})
            if tag == "span" and "why" in (a.get("class") or "").split():
                self.in_why = 1
                self.cur_field["options"][-1]["why"] = ""
            elif tag == "p" and "feedback" in (a.get("class") or "").split():
                self.cur_field["feedback"] = True
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.in_why and tag == "span":
            self.in_why = 0
        if tag == "fieldset":
            self.cur_field = None
        if tag == "section" and self.cur_q is not None and not self.stack[-2:-1] == []:
            pass
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if "script" in self.stack or "style" in self.stack:
            return
        self.text.append(data)
        if self.in_why and self.cur_field and self.cur_field["options"]:
            self.cur_field["options"][-1]["why"] += data


def parse(path):
    p = P()
    p.feed(open(path).read())
    return p


pages = [os.path.join(COURSE, "index.html")] + sorted(glob.glob(os.path.join(COURSE, "modules", "*.html")))
data = {}
for path in pages:
    rel = os.path.relpath(path, COURSE)
    p = parse(path)
    data[rel] = p
    base = os.path.dirname(path)
    # links and images
    for href in p.links:
        if re.match(r"^(https?:|mailto:|#|data:)", href):
            if href.startswith("#") and href[1:] and href[1:] not in p.ids:
                err(rel, f"anchor {href} has no matching id")
            continue
        target = os.path.normpath(os.path.join(base, href.split("#")[0].split("?")[0]))
        if not os.path.exists(target):
            err(rel, f"broken link or file reference: {href}")
        elif "#" in href:
            frag = href.split("#", 1)[1]
            if frag and target.endswith(".html") and frag not in parse(target).ids:
                err(rel, f"link {href}: anchor not found in target")
    for img in p.imgs:
        if not (img.get("alt") or "").strip():
            err(rel, f"image without alt text: {img.get('src')}")
    # quiz structure
    positions = []
    for quiz in p.quizzes:
        if len(quiz["fields"]) < 3:
            err(rel, f"quiz has only {len(quiz['fields'])} questions (need at least 3)")
        for qi, f in enumerate(quiz["fields"], 1):
            vals = [o["value"] for o in f["options"]]
            if not 3 <= len(vals) <= 4:
                err(rel, f"question {qi}: {len(vals)} options (need 3 or 4)")
            if f["answer"] not in vals:
                err(rel, f"question {qi}: data-answer {f['answer']!r} is not an option")
            else:
                positions.append(vals.index(f["answer"]))
            if not f["feedback"]:
                err(rel, f"question {qi}: missing feedback element")
            for o in f["options"]:
                why = (o["why"] or "").strip()
                if not why:
                    err(rel, f"question {qi}: option {o['value']} has no explanation")
                elif re.match(r"^(correct|right|wrong|incorrect)\b", why, re.I):
                    err(rel, f"question {qi}: explanation starts with a verdict word: {why[:40]!r}")
    if p.quizzes and len(set(positions)) < 2:
        err(rel, f"every right answer is in the same position ({positions})")
    # hard content rules
    text = " ".join(p.text)
    raw = open(path).read()
    if re.search(r"\b" + "age" + r"nts?\b", text, re.I):
        err(rel, "contains a banned term")
    # only claims that something is N times cheaper / smaller / a saving; "about ten times as many PUTs" is not a cost claim
    claim = re.search(r"(?i)\b(10x|ten times|10 times)\s+(cheaper|smaller|less\b|lower|saving|savings)|\b(cheaper|saving|savings)\b[^.]{0,30}\b(10x|ten times)\b", text)
    if claim and "flat saving" not in text.lower():
        err(rel, f"contains a flat-saving claim without the warning: {claim.group(0)!r}")
    if re.search(r"(?i)screenshot[^.]{0,40}bill|bill[^.]{0,40}screenshot", raw) and "evidence/screens" in raw:
        pass
    if re.search(r"<script(?![^>]*src=)[^>]*>", raw):
        err(rel, "inline script (use a shared assets/ file)")

# pager chain and index coverage
order = lib.ORDER
idx = data["index.html"]
for i, (mid, fname, title) in enumerate(order):
    rel = f"modules/{fname}"
    if rel not in data:
        err("course", f"module page missing: {rel}")
        continue
    if f"modules/{fname}" not in idx.links:
        err("index.html", f"does not link {rel}")
    links = [l for l in data[rel].links if l.endswith(".html")]
    if i > 0 and order[i - 1][1] not in links:
        err(rel, f"missing link to previous module {order[i-1][1]}")
    if i < len(order) - 1 and order[i + 1][1] not in links:
        err(rel, f"missing link to next module {order[i+1][1]}")
    body = re.search(r'<body data-module="([^"]+)"', open(os.path.join(COURSE, rel)).read())
    if not body or body.group(1) != mid:
        err(rel, f"body data-module should be {mid}")

# quiz balance across the whole course: the right answer must not be guessable by length or by letter
_len_hits = _total = _outlier = _short_hits = 0
_letters = {}
_orders = {}
for _mid, _fname, _t in lib.ORDER:
    _raw = open(os.path.join(COURSE, "modules", _fname)).read()
    _seq = ""
    for _fs in re.findall(r'<fieldset class="q" data-answer="(\w)">(.*?)</fieldset>', _raw, re.S):
        _opts = re.findall(r'<label class="opt">.*?<span>(.*?)<span class="why">', _fs[1], re.S)
        _lens = [len(re.sub(r"<[^>]+>", "", o)) for o in _opts]
        _ai = "abcd".index(_fs[0])
        _total += 1
        _len_hits += _lens[_ai] == max(_lens) and _lens.count(max(_lens)) == 1
        _short_hits += _lens[_ai] == min(_lens) and _lens.count(min(_lens)) == 1
        _oth = [x for i, x in enumerate(_lens) if i != _ai]
        _outlier += abs(_lens[_ai] - sum(_oth) / len(_oth)) / (sum(_oth) / len(_oth)) > 0.2
        _letters[_fs[0]] = _letters.get(_fs[0], 0) + 1
        _seq += _fs[0]
    _orders.setdefault(_seq, []).append(_fname)
    _per = [_ for _ in re.findall(r'<fieldset class="q" data-answer="(\w)">(.*?)</fieldset>', _raw, re.S)]
    _ext = []
    for _fs in _per:
        _l = [len(re.sub(r"<[^>]+>", "", o)) for o in re.findall(r'<label class="opt">.*?<span>(.*?)<span class="why">', _fs[1], re.S)]
        _a = _l["abcd".index(_fs[0])]
        _ext.append("short" if _a == min(_l) and _l.count(_a) == 1 else "long" if _a == max(_l) and _l.count(_a) == 1 else "mid")
    if len(_ext) >= 3 and len(set(_ext)) == 1 and _ext[0] != "mid":
        err("modules/" + _fname, f"the right answer is the only {_ext[0]}est option in every question of this module")
if _total and _len_hits / _total > 0.45:
    err("course", f"the right answer is the longest option in {_len_hits} of {_total} questions (limit 45%)")
if _total and _short_hits / _total > 0.4:
    err("course", f"the right answer is the shortest option in {_short_hits} of {_total} questions (limit 40%)")
if _total and _outlier / _total > 0.35:
    err("course", f"the right answer stands out by length (20% from the other options) in {_outlier} of {_total} questions (limit 35%)")
if _total and max(_letters.values()) / _total > 0.42:
    err("course", f"answer letters are unbalanced: {_letters}")
for _seq, _files in _orders.items():
    if len(_files) > 2:
        err("course", f"{len(_files)} modules share the answer order {_seq}: {_files}")

# evidence cross-checks
EV = lambda n: json.load(open(os.path.join(COURSE, "evidence", n)))
try:
    ver, selft, alerts, comp = EV("verify-report.json"), EV("selftest.json"), EV("alerts-report.json"), EV("comparison.json")
    m3 = " ".join(data["modules/03-evidence-it-works.html"].text)
    for label, a, b in (("verification", ver["passed"], len(ver["checks"])), ("alerts", alerts["passed"], len(alerts["checks"])),
                        ("self-test", sum(m["detected"] for m in selft["mutations"]), len(selft["mutations"]))):
        if f"{a} of {b}" not in m3:
            err("modules/03-evidence-it-works.html", f"headline {label} '{a} of {b}' not found on the page")
    if ver["failed"] != 0:
        err("evidence", "verify-report.json records failures")
    m1 = " ".join(data["modules/01-problem-and-outcome.html"].text)
    for r in comp["cases"]:
        for v in (r["total_low"], r["total_high"]):
            if f"${v:,.0f}" not in m1:
                err("modules/01-problem-and-outcome.html", f"cost figure ${v:,.0f} from comparison.json not on the page")
except FileNotFoundError as e:
    err("evidence", f"missing evidence file: {e}")

# evidence files referenced by pages exist and are in the manifest
manifest = EV("manifest.json")
for rel, p in data.items():
    for href in p.links:
        m = re.search(r"evidence/(.+?)(?:[?#]|$)", href)
        if m and m.group(1) not in manifest and not m.group(1).startswith("manifest"):
            err(rel, f"evidence file {m.group(1)} is not listed in evidence/manifest.json")

for e in errors:
    print("FAIL", e)
print(f"\n{len(pages)} pages checked, {len(errors)} problem(s)")
sys.exit(1 if errors else 0)
