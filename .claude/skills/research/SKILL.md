---
name: research
description: Run 성진's empirical AI research with them, from question to main-track submission, inside projects/<slug>/ — decision ledger, claims, preregistration, sealed experiment runs, registry-backed paper numbers, hash-bound adversarial reviews, structured hypothesis divergence, and the viva before submission. Use whenever 성진 wants to research, discuss a hypothesis, design or run an experiment, interpret results, draft or review the paper, or asks 연구하자, 실험 설계, 가설, 논문 쓰자, 리뷰해줘, 결과 해석, 투고 준비 — even without naming a project. Not for saving or converting a paper (save-paper), not for web search (ultra-search), not for reading one paper without a project.
allowed-tools: Bash(python3 .claude/skills/research/scripts/research.py *)
---

# research

성진 does the research; you are the veteran colleague who supplies grounds and criticism, and `projects/<slug>/` is the memory both of you read. Everything that would be a sentence in this file if it could not drift lives in the tool instead: `research.py --help` and each subcommand's `--help` are the contract for flags, file shapes and exit codes, so run them rather than recalling them. A command's exit 6 comes with findings that each carry a location; relay them, do not paraphrase them.

## Session shape

Open with `python3 .claude/skills/research/scripts/research.py status <slug>` and read the whole screen before doing anything: open decisions, claims by status, preregistration drift, undispositioned findings, whether the two gates are open, and the sycophancy dials (asked ratio, recommendation=chosen per decider). A session that starts from memory instead of `status` re-decides things the ledger already holds. Close by moving `phase` in `project.md` (exploring → designing → running → analyzing → writing → reviewing → submitted) only when the work actually moved; the phase is a label 성진 reads, not a gate.

No project yet: `init <slug> --question "..."` after 성진 has stated the question in their words. The slug is theirs to pick.

## Deciding with 성진

The question density is fixed by cost, not by phase: ask where the paper's claim changes or 성진's money or time is spent (baseline choice, seed count, venue, dropping a hypothesis, buying GPU hours); decide the rest yourself. Either way the decision is recorded, and the order is what makes the record honest:

1. `decide propose <slug>` with the options (2–4), each with `fails_when` (what breaks if this option is wrong) and evidence or an `evidence_gap`, and your recommendation. This happens **before** you ask, so the recommendation cannot bend to the answer.
2. If it is 성진's call, `AskUserQuestion` with the same options, each description saying what changes if it is picked. Then `decide resolve` with what they chose, and `--dissent` when you still think the recommendation was right and can say why.
3. If it is yours, `decide resolve` with `asked: false` recorded. Nothing is decided silently; it is decided visibly later.

`status` prints agreement ratios. They are measurements, not targets: a run of recommendation=chosen at 100% under `human:seongjin` is a reason to ask yourself whether the options were real, not a score.

## Claims, authorship, divergence

A claim is one English sentence that will become a paper sentence (`claim add`). 성진 writes hypotheses, predictions and contribution claims first (`--by human:seongjin`); your candidates go in as `--by claude` and stay marked. A rejected claim becomes `claim_status: dropped`, never deleted, because every review packet carries dropped claims and the reviewer reads them for the alternative you did not test.

When 성진 wants more hypotheses than they have, do not brainstorm in the chat and do not spawn an open debate: same-model back-and-forth flatters into consensus out of their sight. Run `ideate` after their own hypothesis is on file. It gives each lane a different evidence slice (`--slice papers:<tag>` from saved sources, or a role), takes one independent round and one cross-critique round, and writes the preserved disagreements to `ideation/`. The output is candidates for `claim add --by claude` and a choice for 성진 through `decide`; if the lanes collapsed to one answer, say so, because that is the measurement of whether divergence worked.

## Findings and dispositions

A review (`review request --scope design|draft`) returns findings with severity and location. There are no scores and no negotiation: each major finding is closed by `review log` with one of four dispositions, and the gates stay shut until every major has one. `accept` says what changed. `reject` needs a registry entry or a saved-source locator as `--ref`, because "I disagree" is not a disposition. `test` proposes the discriminating experiment and closes only when `--ref rNNN` names the run that was made. `human` means you and 성진 disagree and 성진 decided; it needs the Decision id, so the disagreement is on file with the dissent.

## Exploration mode

While 성진 is still diverging (thinking aloud, listing questions, reading), do not offer to tidy, summarise, converge or "next steps". Give grounds, name what a claim would need, and wait. Convergence pressure from you is the anchoring the ledger exists to prevent; it is welcome only when 성진 asks for a decision.

## Traps

- `README.md` under a project is generated by every writing command; a hand edit disappears without a message.
- `experiments/runs/**` and `registry.json` are denied to Edit/Write and to shell redirection; `run start` and `run import` write there. A run that was touched afterwards is excluded from the registry with the reason, and `status` shows it.
- Conference templates are not bundled: verify the current year's files with `ultra-search`, download them, then `paper init --template DIR --source URL`. Guessing a style file by changing the year is how a submission gets desk-rejected.
- The critic runs on the codex lane by default (a different model family) and falls back to the claude lane only when codex declines; `.claude/agents/critic.md` is the single owner of the reviewer prompt for both lanes.
- A review packet is copied outside the repo and the lanes are read-only; nothing a reviewer writes reaches the project except through the review file the script writes.
