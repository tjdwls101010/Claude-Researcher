---
paths:
  - "projects/*/paper/**"
---

# The paper

**A number is a registry lookup or it is not in the paper.** In result files every number is `\result{<run>/<condition>/<metric>}{mean|std|n|min|max}{digits}`; `paper results` renders the macros from `registry.json` and an unknown id is a LaTeX error, so a typo cannot become a value. A number from the literature is `\nonresult{literal}{\cite{key} and locator}`, and `paper verify` refuses a reason that names no bib key, saved source or decision. Typing a value from memory or from a chart, even a value you are sure of, is the defect that cannot be seen once the PDF exists.

**A missing fact is `[MATERIAL GAP]`, not a plausible sentence.** When a method detail, a dataset statistic or a related-work claim is not in the ledgers or in a saved source, write `[MATERIAL GAP]` with what is missing and let `paper verify` block the build until 성진 supplies it. The convincing sentence you could write instead is the one a reviewer catches and the one that costs the paper its credibility.

**Hedge exactly as far as the evidence goes.** "Improves" for a confirmatory run with the preregistered test; "we observe" for an exploratory run; "may" where the mechanism is inferred. Overstating is what anti-hedging rules in other harnesses were written to produce, and a main-track reviewer reads a hardened hedge as a claim to attack.

**Search boundaries are stated, not implied.** "No prior work does X" is a claim about the literature and needs `literature.md`'s search log behind it; without it, write "within the venues and years we searched (list), we found no work that X". Non-arXiv citations pass `paper verify` only with a human-verified entry there, because a reference nobody in this project read is a reference the interviewer will ask about.

**Figures are scripts, methods diagrams are editable.** A result figure is a `figures/<name>.py` that reads the registry through `research.registry.load` and writes `$RESEARCH_FIGURE_OUT`; `paper figures` records script, registry and output hashes and `paper verify` refuses an `\includegraphics` that is not in that manifest or whose bytes changed. Method diagrams are TikZ or an SVG converted by a script, never a generated image (fixed raster, broken labels, invented numbers, and a disclosure duty at the venue). Design the charts with the `dataviz` skill before writing the script.

**What a main-track reviewer looks at first:** whether the baseline is the one the field would pick, whether the ablation isolates the claimed mechanism, whether the seeds and error bars match the size of the claim, whether the limitations section names the confound they thought of, and whether the contribution claims in the introduction are exactly the supported claims and no more. Write the draft so each of those has a place they can find.
