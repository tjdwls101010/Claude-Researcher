# Claude Researcher

<!-- Harness inventory and design rationale: .claude/harness-spec.md -->

`papers/` holds the knowledge base in two layers. `papers/sources/<arxiv-id>.md` is the checked Markdown conversion of a paper and the only thing to cite as what a paper says; its frontmatter `verified` and `conversion.route` say how far to trust it (no `verified`, or `route: pdf`, means parts are missing). `papers/<arxiv-id>.md` is a Korean study note written for 성진 from that source; read it to learn what 성진 has studied, not as evidence about the paper.

`papers/README.md` is generated from frontmatter by `save_paper.py index`; edit the papers, never the index.

Saving a paper, in any form (URL, id, title), goes through `/save-paper`. Tests live in `tests/<component>/` at the repo root; run them with `python3 -m pytest tests/save-paper -q`.
