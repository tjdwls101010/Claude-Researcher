---
paths:
  - "projects/*/paper/**"
  - "projects/*/literature.md"
---

# The paper

**An empirical number is a registry lookup or it is not in the paper.** Result files carry numbers only as `\result` macros rendered from the registry, so a typo cannot become a value and an unknown id fails the build; a number from the literature is a `\nonresult` whose reason names its source. Typing a value from memory or from a chart, even one you are sure of, is the defect nobody can see once the PDF exists.

**A missing fact is `[MATERIAL GAP]`, not a plausible sentence.** When a method detail, a dataset statistic or a related-work claim is not in the ledgers, the experiment code or a saved source, write `[MATERIAL GAP]` naming what is missing and let `paper verify` block the build. Resolve it from those records where they hold it; go to 성진 when the fact lives in their head or changes a claim. The convincing sentence you could write instead is the one a reviewer catches.

**Hedge exactly as far as the evidence goes.** "Improves" for a confirmatory run with the preregistered test; "we observe" for an exploratory run; "may" where the mechanism is inferred. A main-track reviewer reads a hardened hedge as a claim to attack.

**Search boundaries are recorded before they are claimed.** "No prior work does X" is a claim about the literature. Record the actual search (venues, years, queries) in `literature.md` first, then write the absence relative to that boundary; without a record, the search evidence is missing and the sentence is a gap. A non-arXiv citation passes verification only through a human-verified entry there, because a reference nobody in this project read is the one the interviewer asks about.

**Figures are scripts; method diagrams are editable.** A result figure is produced by a script under `paper/figures/` that reads the registry, so its bytes are traceable to sealed data and `paper verify` refuses anything else. Method diagrams are TikZ or an SVG converted by a script, never a generated image: fixed raster, broken labels, invented numbers, and a disclosure duty at the venue. Design result charts with the `dataviz` skill before writing the script.

**What a main-track reviewer looks at first:** whether the baseline is the one the field would pick, whether the ablation isolates the claimed mechanism, whether seeds and error bars match the size of the claim, whether the limitations name the confound they thought of, and whether the introduction's contribution claims are exactly the supported claims and no more. Write the draft so each has a place they can find.
