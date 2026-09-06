---
paths:
  - "projects/*/experiments/**"
  - "projects/*/claims/**"
  - "projects/*/prereg/**"
---

# Experiments, claims and preregistration

**Design follows the claim, not a default.** Seeds, baselines and factors are set by what the claim needs to be believed at a main-track venue: five seeds is `run start`'s default, not a rule, and a claim about a 0.3-point gain needs more seeds than one about a 10-point gain, while a claim about a mechanism needs an ablation rather than more seeds. When the design departs from the default, the reason is a Decision with the claim it serves in `evidence`, so a reviewer (and the viva) can trace why.

**The cheapest discriminating pilot comes before the expensive run.** Before scaling anything, name the pilot whose outcome would kill the hypothesis fastest, run it as exploratory, and only then preregister and run the confirmatory version. An exploratory run's numbers are exploratory forever: `paper verify` warns when one is cited as a result, and no `\result` macro turns it into evidence.

**Freeze before you look.** `prereg freeze` snapshots the hypotheses, predictions and the analysis plan; a confirmatory run needs it and a design review of it, and `prereg check` reports drift. Changing a hypothesis after seeing the numbers is HARKing whether or not it was intended, so the honest move after a surprise is a new hypothesis (`claim add`, marked as post hoc in its description), a new freeze, and a new run, not an edit.

**Registry warnings are resolved before interpretation.** Identical per-seed values across conditions, identical means, missing conditions, substituted seeds and non-finite values are the shapes a bug takes when it looks like a result; `registry --strict` refuses to proceed while one stands. A warning that turns out to be legitimate is explained in a Decision, not silenced.

**The sealed run is the only provenance.** `results.json` is written by the experiment into `$RESEARCH_RUN_DIR`, values as decimal strings so rounding is explicit and done once in the paper macro. A run edited after sealing, a hand-made `run.json`, or a manifest that does not match its files is excluded with the reason; the fix is to run again, never to make the files agree.
