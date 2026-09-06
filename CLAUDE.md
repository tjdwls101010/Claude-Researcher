# Claude Researcher

<!-- Harness inventory and design rationale: .claude/harness-spec.md -->

`papers/` holds the knowledge base in two layers. `papers/sources/<arxiv-id>.md` is the checked Markdown conversion of a paper and the only thing to cite as what a paper says; its frontmatter `verified` and `conversion.route` say how far to trust it (no `verified`, or `route: pdf`, means parts are missing). `papers/<arxiv-id>.md` is a Korean study note written for 성진 from that source; read it to learn what 성진 has studied, not as evidence about the paper.

`papers/README.md` is generated from frontmatter by `save_paper.py index`; edit the papers, never the index.

Saving a paper, in any form (URL, id, title), goes through `/save-paper`. Tests live in `tests/<component>/` at the repo root; run them with `python3 -m pytest tests/save-paper tests/research -q`.

## Research (`projects/<slug>/`)

Two boundaries hold in every research session, because the paper has to survive a main-track review and an interview in which 성진 defends each claim without you. **Who decides:** a question goes to 성진 only where the paper's claim changes or their money or time is spent, and it goes through `research.py decide propose` before it is asked, so the recommendation is on file before the answer; everything else you decide and record. **Who authors:** hypotheses, predictions, research questions, experiment-design sketches, contribution claims and conclusions are 성진's first draft, recorded as `by: human:seongjin`; code is yours; methods, related work and results prose you draft from the ledgers. The author field never changes; the content can. Anchoring runs the other way too: no candidate hypothesis from you before 성진 has written theirs.

Evidence for a claim is a saved source under `papers/sources/` with a locator or a registry entry (`<run>/<condition>/<metric>`); a paper citation may also be a non-arXiv reference that a human verified in the project's `literature.md`. Memory, a Korean note, an alt text and a number read off a chart are none of these. `projects/**/experiments/runs/**` and `experiments/registry.json` are written only by `research.py`; Edit, Write and shell redirection there are refused by `.claude/settings.json` (measured 2026-09-06: `mkdir`, `>` and the Write tool all blocked, in bypass mode too), because provenance is a sealed run and nothing else, and a hand edit would exclude the run from the registry anyway.

Research work goes through `/research`; `research.py --help` is the contract for every command and exit code.
