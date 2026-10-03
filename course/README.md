# Observability course

A static web course (HTML, CSS, JavaScript; no build step, no frameworks, no CDN) about the logs, metrics and traces platform in this repository.
Open `index.html` in a browser. Start with the Story track (modules 1 to 3), then the Using it track (4 to 6) or the Running it track (7 to 11).

## What is in it

- `index.html`, `modules/01..11-*.html`: the pages. Each has learning goals, an interactive element, evidence, and a self-scoring quiz.
- `assets/`: the shared house style (`tokens.css`, `theme.css`, `components.css`, `quiz.js`, Mermaid) copied unedited from the `html-pages` skill,
  plus `local.css`, `course.js` and one `sim-*.js` per interactive.
- `evidence/`: real results captured from the running stack, with `manifest.json` recording the source, command and capture time of every item.
  `evidence/payloads/` holds the payload at each hop for one request. The screenshots are in `evidence/screens/`.
- `tools/`: everything that builds, captures and checks the course.

## Regenerate the evidence (stack must be running)

    python3 stack/verify/verify.py                                    # a fresh passing verification run
    python3 course/tools/capture-evidence.py --screens --war-stories --chaos-replays
    python3 course/tools/capture-payloads.py                          # needs course/tools/bin/peek (see collector/exporter/opendataexporter/cmd/peek)
    for n in 01 02 03; do python3 course/tools/build-module$n.py; done # modules 4-11 have the same kind of builder

## Quality gates

    python3 course/tools/check-course.py        # links, images, quiz structure, pager chain, numbers match the source reports, content rules
    node course/tools/smoke-test.mjs            # headless Chrome: no console errors, quizzes score, controls respond, diagrams render
    NARROW=1 node course/tools/smoke-test.mjs   # the same at 500 px wide, also fails on sideways scrolling
    python3 ~/.claude/skills/html-pages/scripts/check.py course/modules/NN-*.html   # the house-style checker, per page
    python3 course/tools/render-check.py modules/NN-*.html                           # light, dark and narrow PNGs in /tmp/course-render
    node course/tools/test-sim-*.js             # logic of the interactives (pure functions)

The checkers were negative-tested: the smoke test and the course checker both fail on deliberately broken pages.

## Honest limits

The course shows results from one laptop. It has not been run on real AWS, at production volume, or on real x86 hardware.
The cost figures are a model with stated assumptions. The figures from the reference bill are sensitive: remove or replace them before sharing outside the organisation.
`tools/AUTHORING.md` is the guide for adding or changing a module.
