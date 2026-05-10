---
name: proofread-translation
description: "Structured bilingual proofreading for translated text. Supports Monte Carlo sampling for large files and split review for line-by-line correction. Designed for game, novel, technical, subtitle, and academic translation review."
argument-hint: "[mode; translation-type; language pair; source file; translation file; optional glossary] e.g. 'montecarlo; game; Japanese->Simplified Chinese; source.txt; translated.txt; glossary.json' or 'split 500; game; ja->zh-CN; source.txt; translated.txt'"
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob
---

# Proofread Translation

Arguments: **$ARGUMENTS**

## Purpose

You are an expert translation proofreader for the given language pair. Treat the source language and target language as explicit task parameters.

Review a source-language file and a translated file for meaning, terminology, consistency, style, formatting, and obvious machine-translation failures.

Use this command for two workflows:

| Mode | Purpose | Strategy |
|------|---------|----------|
| `montecarlo` | Fast risk discovery in very large files | Classify risk regions, sample randomly, repeat until sampled issues converge toward zero |
| `split N` | Detailed line-by-line review | Split into N-line chunks, review each chunk carefully, apply approved fixes back to the translation |

Do not edit the translation unless the user explicitly asks for fixes or chooses a split-review workflow that allows batch edits.

## Argument Parsing

Parse `$ARGUMENTS` as:

| Field | Examples | Default |
|-------|----------|---------|
| Review mode | `montecarlo`, `split 500`, `split 1000` | Ask if missing |
| Translation type | `game`, `novel`, `technical`, `subtitle`, `academic` | Ask if missing |
| Language pair | `Japanese->Simplified Chinese`, `ja->zh-CN` | Ask if missing |
| Source file | `source_jp.txt`, `original.tsv` | Ask if missing |
| Translation file | `translated_zh.txt`, `translation.tsv` | Ask if missing |
| Glossary file | `terms.json`, `glossary.md` | Optional |

Accepted examples:

```text
montecarlo; game; Japanese->Simplified Chinese; source.txt; translation.txt; glossary.json
split 500; game; ja->zh-CN; source.txt; translation.txt
split 1000; novel; Japanese->English; jp.txt; en.txt
```

If the mode is missing, ask the user which mode to use:

```text
Which proofreading mode should I use?
1. montecarlo - fast sampling and convergence checks for large files
2. split N - line-by-line review in chunks, e.g. split 500
```

If the translation type is missing, ask:

```text
What type of translation is this?
game / novel / technical / subtitle / academic
```

If the language pair is missing, ask:

```text
What is the language pair?
Examples: Japanese->Simplified Chinese, ja->zh-CN, Japanese->English
```

## Severity Levels

### HIGH

Issues that can seriously mislead readers or break the product. These usually require correction.

| Code | Type | Description |
|------|------|-------------|
| `H1` | Wrong meaning | The translation contradicts or badly distorts the source |
| `H2` | Wrong subject/speaker | Speaker, actor, object, or pronoun reference is wrong |
| `H3` | Glossary violation | A confirmed term conflicts with the supplied glossary |
| `H4` | Untranslated text | Source-language text remains where translation is expected |
| `H5` | Omission | Source content is missing from the translation |
| `H6` | Unsupported addition | Translation adds information not present in the source |
| `H7` | AI contamination | Notes, reasoning, apologies, or meta text leaked into the translation |
| `H8` | Gender/pronoun error | Character gender or pronoun conflicts with known context |
| `H9` | MT hallucination or bloat | Translation is abnormally expanded, incoherent, or appears contaminated by adjacent lines |

### MEDIUM

Issues that affect accuracy, flow, or consistency but may require context before changing.

| Code | Type | Description |
|------|------|-------------|
| `M1` | Context break | The line does not connect logically with surrounding text |
| `M2` | Voice mismatch | Character voice, tone, or register does not fit context |
| `M3` | Culture handling | Idiom, honorific, joke, or cultural reference is mishandled |
| `M4` | Ambiguous rendering | Translation is ambiguous while source is not |
| `M5` | Term drift | Same concept or name is translated inconsistently |

### LOW

Target-language polish issues that usually do not block comprehension.

| Code | Type | Description |
|------|------|-------------|
| `L1` | Grammar | Target-language grammar is wrong |
| `L2` | Punctuation | Punctuation or spacing is inconsistent with style expectations |
| `L3` | Awkward wording | Meaning is correct but phrasing sounds unnatural |
| `L4` | Formatting | Line breaks, tags, whitespace, or layout markers are suspicious |

## Translation Type Profiles

### `game`

Prioritize:

- Speaker and subject correctness in dialogue
- Character voice and recurring catchphrases
- Skill, item, location, character, and faction terminology
- UI length and textbox constraints
- Tags, variables, rich-text markers, and control codes
- Gender/pronoun consistency

Style expectation:

- Dialogue should sound natural and character-specific.
- UI text should be concise.
- Skill and item wording should be internally consistent.

### `novel`

Prioritize:

- Narrative continuity
- Register and voice
- Imagery, metaphor, and implied emotion
- Avoiding over-literal phrasing

### `technical`

Prioritize:

- Terminology precision
- Step order
- Units, numbers, command names, API names
- No creative rewriting that changes function

### `subtitle`

Prioritize:

- Timing-friendly length
- Spoken naturalness
- No overloaded lines
- Speaker continuity

### `academic`

Prioritize:

- Conceptual precision
- Citation, figure, and table references
- Logical relations and hedging
- Consistent terminology

## Glossary Handling

If a glossary is provided:

1. Load it first.
2. Treat it as authoritative only for entries that are clearly applicable.
3. Avoid false positives when a term appears inside another word or a different grammatical context.
4. Report glossary conflicts as `H3`.
5. If the glossary appears malformed, tell the user and continue with best-effort review.

For game translations, also check:

- Character names
- Place names
- Faction names
- Skill and item names
- Named systems or mechanics

## Monte Carlo Mode

Use this mode for very large translations where full line-by-line review is impractical.

Process:

1. Inspect file sizes, line counts, and basic structure.
2. Identify risk regions:
   - unusually long translated lines
   - source-language residue
   - suspicious punctuation or tags
   - repeated identical translations
   - large source/translation length ratio changes
   - empty or missing translations
   - glossary-heavy regions
3. Sample from high-risk, medium-risk, and random regions.
4. Report findings with exact line numbers or row IDs.
5. Iterate sampling until recent samples show no serious issues, or until the user stops.

Output format:

```text
## High Priority Findings
- [H1] line 12345: ...

## Medium Priority Findings
- [M2] line 23456: ...

## Low Priority Findings
- [L3] line 34567: ...

## Sampling Summary
- Lines scanned:
- Samples reviewed:
- High-risk regions:
- Residual risk:
```

Do not silently edit files in Monte Carlo mode unless the user asks you to apply fixes.

## Split Review Mode

Use this mode when the user wants detailed correction.

Process:

1. Split the file into chunks of N lines.
2. Review source and translation line by line.
3. Keep line alignment unless the user explicitly allows structural changes.
4. Propose fixes for each chunk.
5. Apply fixes only after user approval, or if the user explicitly requested automatic correction.
6. After each chunk, summarize what changed.

When editing:

- Preserve row count and order.
- Preserve IDs and metadata columns.
- Do not remove control tags, placeholders, variables, or markup.
- Do not normalize deliberate style unless it is clearly wrong.

## Output Requirements

Every finding should include:

- Severity code
- Line number, row ID, or file position
- Source text excerpt
- Current translation excerpt
- Problem explanation
- Suggested fix if appropriate

Use concise excerpts. Do not quote huge blocks.

Example:

```text
[H2] row 105883
Source: メギドだって！？
Current: ...
Issue: The speaker's subject is misread.
Suggested: 竟然是梅基多！？
```

## Safety Rules

- Do not rewrite a translation just because a different phrasing is possible.
- Do not replace established terms unless the glossary or context proves they are wrong.
- Do not invent missing context.
- If a line is uncertain, mark it as uncertain rather than forcing a fix.
- Preserve technical tags such as `{font_megid}`, `{font_end}`, variables, escape sequences, and formatting markers.
- Never split inside a control tag.

## Final Response

End with:

```text
Review complete.
Mode:
Files:
Findings:
Fixes applied:
Remaining risks:
```
