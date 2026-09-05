---
name: save-paper
description: Save an arXiv paper into the project knowledge base as a faithful Markdown source (papers/sources/<id>.md with figures as PNG, alt text and provenance frontmatter) plus a Korean restructured study note (papers/<id>.md) written by the paper-note agent. Use whenever the user gives an arXiv URL, id, or paper title and wants it saved, ingested, added, archived, converted, or "put in the papers folder" — including Korean requests like 논문 저장, 논문 넣어줘, 이 논문 정리해줘, 노트 만들어줘. Not for searching or discovering papers (use ultra-search), not for reading a paper once without saving it, and not for non-arXiv sources.
allowed-tools: Bash(python3 .claude/skills/save-paper/scripts/save_paper.py *)
---

# save-paper

`papers/` has two layers because they have two different readers. `papers/sources/<id>.md` is the **source layer**: a script's conversion of the arXiv HTML, checked block-by-block against the original, and the only thing you may cite as what the paper says. It is written for you, so it is strict on completeness and indifferent to looks. `papers/<id>.md` is the **note layer**: a Korean restructuring written by the `paper-note` agent for 성진 to study from. Every saved paper gets both (성진's decision, 2026-09-06). The source is always the note's input and its evidence; when they disagree, the source is right and the note has a bug.

## Run

From the project root:

```
python3 .claude/skills/save-paper/scripts/save_paper.py save <arXiv url | id | title>
```

`--help` (and `save --help`) is the definition of every flag and exit code; do not work from memory of them. `doctor` names any missing tool and its install command. When the run ends, relay its last line to 성진 unchanged — `status`, `coverage`, `verified`, `figures`, `losses`, `described`, `cost` and the path are the whole report.

Then write the note (below). Saving without the note is a half-finished job unless 성진 said otherwise.

## What the result means

- **Exit 0, `verified` present** — every paragraph, table cell, caption, footnote and reference of the arXiv HTML is in the Markdown. Cite freely.
- **Exit 6** — the file *was* saved, but without `verified`. `conversion.known_losses` and `conversion.check.missing` say what is not there. Read the file, but before quoting a number or claim from the region that is missing, tell 성진 the source is unverified there. Saving anyway is deliberate: a partial source you can see beats a silent failure you cannot.
- **`conversion.route: pdf`** — arXiv had no HTML, so the text came from the PDF. Math is mostly gone and figures entirely; the file is never verified. Say so when you use it, and tell the note agent.
- **`up-to-date`** — the fingerprint matched; nothing was rewritten. **`new-version-available`** — arXiv has a newer version than the saved one; nothing was rewritten either (see below).
- **Exit 3 with candidates** — a title search matched several papers. This is a decision, not an error.
- **`WARNING: figure alt text skipped`** — no OpenRouter key was found, so the images have empty alt. The figures are still there as PNGs; `describe <id>` fills the text once the key exists.

The check proves the Markdown matches the HTML arXiv served, not that the HTML is the whole paper. A warning about the TeX `\bibitem` count differing is the one signal of the latter; if it appears, mention it.

## Decisions that are the user's

- **Which paper a title meant.** Never pick the first candidate: the search for a famous title returns a dozen "X is all you need" variants, and choosing one silently saves a different paper under a different id. Put the candidates (title, id, year) to 성진 with `AskUserQuestion`; the JSON marks an `exact_title` match, which is a good default to propose, not to assume.
- **Overwriting on a new version.** A note has been written against the saved version, and a new version can change numbers. Ask before re-saving with `--version N` or `--force`, and re-run the note afterwards.

Alt text and the note are *not* decisions any more: both run by default. Turning either off (`--no-describe`, or skipping the note) is something 성진 asks for, not something you save them money on.

## Korean note

1. The source exists; alt text is filled unless the key was missing (the agent then reads the PNGs itself, slower and less precise).
2. Delegate to the `paper-note` agent with both absolute paths: the source `papers/sources/<id>.md` and the target `papers/<id>.md`. The agent's own body carries the rules; do not restate them. If the source came through the PDF route, say so in the delegation so the agent does not invent the missing figures.
3. Several papers saved together get one agent each, launched in parallel — they touch different files and each reports for itself, so nothing is gained by serialising them.
4. The agent ends by running `note-check` and reporting its output. If it reports undecided points, those go to 성진 — the agent cannot ask.
5. Run `save_paper.py index` so `papers/README.md` lists the notes.

## Traps

- Several papers in one go is `batch --ids-file F --jsonl results.jsonl`, not a loop of `save`: the 3-second arXiv rate limit lives inside one process, and a shell loop restarts it every time and gets throttled. `batch` continues past failures and records each paper's outcome as a JSON line.
- The arXiv metadata API (`export.arxiv.org`) throttles hard after a burst from one IP, and a throttled request hangs rather than failing; a `save` that shows no progress for a minute is that, not a bug. Wait a few minutes before retrying; `arxiv.org/html` keeps working meanwhile.
- `papers/README.md` is generated from frontmatter by `index`. A hand edit survives until the next save and then vanishes without a message.
- The OpenRouter key and the vision model are read from the environment, then from `.claude/skills/save-paper/scripts/savepaper/.env` (gitignored; `.env.example` beside it names the two keys), then from a project-root `.env`. Nothing is hard-coded except the fallback model name.
- Legacy ids contain a slash (`hep-th/9901001`); the file is `hep-th_9901001.md`. Pass the id in either form.
