"""Shared helpers for building course module pages (standard library only).

Usage in a module builder (see build-module02.py):
    from lib import page, quiz, write, evidence
    html = page(slug="02-platform-in-one-picture.html", module="m02", track="Story", number="2 of 3",
                title="...", lede="...", goals=["...", "..."], body="<section>...</section>",
                quiz_html=quiz([...]), prev=("01-problem-and-outcome.html", "The problem and the outcome"),
                next=("03-evidence-it-works.html", "The evidence it works"), scripts=["sim-architecture.js"], mermaid=False)
    write(slug, html)

Rules enforced here so pages pass the html-pages checker: every quiz question gets its feedback element, scripts are shared
files under assets/ (no inline scripts), the stylesheet order is fixed, and the page title is the module title.
"""
import html as _html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
COURSE = os.path.dirname(HERE)
EVID = os.path.join(COURSE, "evidence")

ORDER = [
    ("m01", "01-problem-and-outcome.html", "The problem and the outcome"),
    ("m02", "02-platform-in-one-picture.html", "The platform in one picture"),
    ("m03", "03-evidence-it-works.html", "The evidence it works"),
    ("m04", "04-sending-telemetry.html", "Sending telemetry"),
    ("m05", "05-finding-your-data.html", "Finding your data"),
    ("m06", "06-alerts.html", "Alerts"),
    ("m07", "07-concepts.html", "Concepts"),
    ("m08", "08-architecture-and-setup.html", "Architecture and setup"),
    ("m09", "09-how-we-know-it-works.html", "How we know it works"),
    ("m10", "10-what-broke.html", "What broke and what we learned"),
    ("m11", "11-limits-tiers-and-aws.html", "Limits, tiers and the road to AWS"),
]


def esc(s):
    return _html.escape(s, quote=True)


def evidence(name):
    """Load a JSON or text evidence file captured by tools/capture-evidence.py."""
    path = os.path.join(EVID, name)
    with open(path) as f:
        return json.load(f) if name.endswith(".json") else f.read()


# Where the right answer sits is set here, so that no module follows a pattern a learner could pick up.
# Key: start of the question text; value: the index the right option moves to.
ANSWER_AT = {
    "Why does each signal have its own Buffer queue": 0,
    "Three attempts at the crash scenario show": 1,
    "An ERROR log line shows a trace id and the span id": 1,
    "Two load runs overlapped in time on the same stack": 2,
    "A cumulative counter is exported every 5 seconds": 0,
    "Why does the failure test restart the collector only": 1,
}


def _place(q):
    for start, pos in ANSWER_AT.items():
        if q["q"].startswith(start):
            opts = list(q["options"])
            right = opts.pop(q["answer"])
            opts.insert(pos, right)
            return {**q, "options": opts, "answer": pos}
    return q


def quiz(questions, gate="All correct."):
    """questions: list of dicts {q: legend, options: [(text, why), ...], answer: index of the right option}.
    Put the right answer in different positions across questions. Every option needs a 'why'."""
    out = ['  <section class="quiz" data-quiz aria-labelledby="quiz-h">', '    <h2 id="quiz-h">Check your understanding</h2>']
    letters = "abcdef"
    for i, q in enumerate(questions, 1):
        q = _place(q)
        if len(q["options"]) < 3 or len(q["options"]) > 4:
            raise ValueError("a question needs three or four options")
        out.append(f'    <fieldset class="q" data-answer="{letters[q["answer"]]}">')
        out.append(f'      <legend>{q["q"]}</legend>')
        for j, (text, why) in enumerate(q["options"]):
            out.append(f'      <label class="opt"><input type="radio" name="q{i}" value="{letters[j]}">'
                       f'<span>{text}<span class="why">{why}</span></span></label>')
        out.append('      <p class="feedback" role="status" aria-live="polite"></p>')
        out.append('    </fieldset>')
    out.append('    <p class="score" aria-live="polite"></p>')
    out.append(f'    <p class="gate" hidden>{gate}</p>')
    out.append('  </section>')
    return "\n".join(out)


def page(slug, module, title, track, number, lede, goals, body, quiz_html="", scripts=(), mermaid=False, prev=None, next=None):
    goals_html = "\n".join(f"      <li>{g}</li>" for g in goals)
    pager = ['  <nav class="pager" aria-label="Module navigation">']
    pager.append(f'    <a href="{prev[0]}">Previous: {esc(prev[1])}</a>' if prev else '    <a href="../index.html">Course home</a>')
    pager.append(f'    <a class="next" href="{next[0]}">Next: {esc(next[1])}</a>' if next else '    <a class="next" href="../index.html">Course home</a>')
    pager.append('  </nav>')
    js = []
    if mermaid:
        js += ['<script src="../assets/mermaid.min.js"></script>', '<script src="../assets/mermaid-init.js"></script>']
    if quiz_html:
        js.append('<script src="../assets/quiz.js" defer></script>')
    js.append('<script src="../assets/course.js" defer></script>')
    js += [f'<script src="../assets/{s}" defer></script>' for s in scripts]
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="../assets/tokens.css">
<link rel="stylesheet" href="../assets/theme.css">
<link rel="stylesheet" href="../assets/components.css">
<link rel="stylesheet" href="../assets/local.css">
</head>
<body data-module="{module}">
<nav class="site-bar" aria-label="Course"><a class="brand" href="../index.html">Observability course</a></nav>
<main>
  <header>
    <p class="crumbs">{esc(track)} · module {esc(number)}</p>
    <h1>{esc(title)}</h1>
    <p class="lede">{lede}</p>
  </header>

  <section class="goals" aria-labelledby="goals">
    <h2 id="goals">After this module you can</h2>
    <ul>
{goals_html}
    </ul>
  </section>

{body}

{quiz_html}

{chr(10).join(pager)}
</main>
{chr(10).join(js)}
</body>
</html>
'''


def write(slug, html):
    path = os.path.join(COURSE, "modules", slug)
    with open(path, "w") as f:
        f.write(html)
    print("wrote", os.path.relpath(path, COURSE))


def neighbours(module):
    ids = [m[0] for m in ORDER]
    i = ids.index(module)
    prev = (ORDER[i - 1][1], ORDER[i - 1][2]) if i > 0 else None
    nxt = (ORDER[i + 1][1], ORDER[i + 1][2]) if i < len(ORDER) - 1 else None
    return prev, nxt


def bill_history():
    """Four months of the (anonymised) bill and the cases compared against August, all read from evidence files."""
    base = evidence("baseline.json")
    cmp_ = evidence("comparison.json")
    m = base["monthly_totals"]
    aug = m["Aug"]
    rows = "".join(f'<tr><td>{lab}</td><td class="num">${v:,.0f}</td></tr>' for lab, v in
                   [("June", m["Jun"]), ("July", m["Jul"]), ("August", m["Aug"]), ("September, first 25 days", m["Sep_first_25_days"]),
                    ("September, scaled to 30 days (the baseline used here; the Grafana seat cost is a fixed monthly cost and is not scaled)", cmp_["current_total"])])

    def sv(lo, hi):
        a, b = aug - hi, aug - lo
        f = lambda x: f"${x:,.0f}" if x >= 0 else f"minus ${-x:,.0f}"
        return f"{f(a)} to {f(b)}"
    cases = "".join(f'<tr><td>{esc(r["case"].strip())}</td><td class="num">${r["total_low"]:,.0f} to ${r["total_high"]:,.0f}</td><td class="num">{sv(r["total_low"], r["total_high"])}</td></tr>'
                    for r in cmp_["cases"] if not r["case"].strip().startswith("A."))
    return f'''<h3>How the baseline was chosen</h3>
    <p>The savings on this page are measured against the September run-rate, because it is the latest month and it is the one in which scanning grew. The bill was lower in earlier months, so a saving measured against August is smaller, and for some cases it is negative. A negative figure means the case costs more than August did.</p>
    <table class="data"><thead><tr><th scope="col">Month (USD)</th><th scope="col" class="num">Total</th></tr></thead><tbody>{rows}</tbody></table>
    <table class="data"><thead><tr><th scope="col">Case</th><th scope="col" class="num">Total with assumed engineer time</th><th scope="col" class="num">Saving against August</th></tr></thead><tbody>{cases}</tbody></table>'''
