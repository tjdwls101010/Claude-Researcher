---
paths:
  - "projects/*/experiments/**"
  - "projects/*/claims/**"
  - "projects/*/prereg/**"
---

# Experiments, claims and preregistration

**Design follows the claim, not a default.** Seeds, baselines and factors are justified from the effect size the claim asserts, the variance the pilot showed and the planned test, whether or not the count matches the tool's default; a claim about a mechanism needs an ablation that isolates it rather than more seeds. The justification is a Decision naming the claim it serves, so a reviewer and the viva can trace why.

**The cheapest discriminating evidence comes before the expensive run.** Before scaling anything, name what would kill the hypothesis fastest and get it: a pilot run, or existing evidence that already answers it, or a recorded reason why a pilot would not reduce the uncertainty enough to pay for itself. Exploratory results stay evidence for a qualified observation; what they cannot become is confirmatory evidence for a hypothesis formed from them.

**Freeze before you look.** A confirmatory run needs the hypotheses and the analysis plan frozen first and a design review of that freeze. Changing a hypothesis after seeing the numbers is HARKing whether or not it was intended, so the honest move after a surprise is a new hypothesis marked as post hoc, a new freeze and a new run, not an edit.

**Registry warnings are investigated before interpretation.** Identical per-seed values across conditions, identical means, missing conditions, substituted seeds and non-finite values are the shapes a bug takes when it looks like a result. A warning that turns out to be legitimate is explained in a Decision for the reader; that explanation does not clear `registry --strict`, which stays red until the data no longer trips it.

**The sealed run is the only provenance.** A run edited after sealing, a hand-made run record, or an imported directory that disagrees with its manifest is excluded with the reason. The fix is to run again; making the files agree with each other destroys the one thing the hash was for.
