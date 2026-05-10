# Megido72 LLM Translation Workflow

Chinese documentation: [README.zh-CN.md](README.zh-CN.md)

This repository is not meant to publish a complete localization patch. Its purpose is to share an LLM-assisted translation workflow for long-form projects such as visual novels, story-heavy games, and large script archives, especially the proofreading command we used with Claude Code / Codex to find and fix the most common, most dangerous failures in AI first drafts.

I used to be an amateur Japanese-to-Chinese translation hobbyist. I participated in the Simplified Chinese translation work for the Steam versions of *STEINS;GATE 0* and *STEINS;GATE ELITE*, and I also led a fan-made Simplified Chinese patch project for *CHAOS;CHILD*. With the rapid progress of LLMs, I have realized that many novel/game translation projects that used to require a large amount of human labor can now be drafted quickly by AI, then reviewed by humans and stronger models. This repository is a record of that experiment and a workflow I want to share.

The research target is *Megido72*, a Japanese mobile game that has already ended service but has an offline version. It has rich, high-quality story content, making it a good stress test for AI-assisted game translation. I chose it for two reasons:

- Its text encryption and asset structure are relatively simple, so the project does not spend most of its time on unpacking and reverse engineering.
- It is extremely hard as an AI translation target: huge text volume, many characters, locations, organizations, skills, and a painful amount of katakana names and terms.

If AI can produce a basically playable translation for a project like this, then the workflow should be useful for projects with less text and fewer terms. Of course, AI translation will make mistakes. Terminology consistency will also fail unless the glossary is maintained manually and updated as the project progresses.

This is the main motivation of the repository: AI first drafts are fast, but they absolutely make mistakes, so “how to proofread AI translation” has to be treated as its own problem. Even with a glossary and carefully written prompts, AI still produces these failures:

- Glossary amnesia (it is still a probability model, not a database lookup tool; even if the prompt says the term, it will forget from time to time).
- Japanese leftovers (laziness, or simply leaving the source text untouched during long batch translation).
- Over-transliteration (Japanese sometimes writes complete sentences in katakana, and models can easily transliterate a sentence that should have been translated by meaning).
- Leaked reasoning (explanations, apologies, or other meta text appear in the translation).
- Translation bloat / AI drooling nonsense (hallucination plus rambling; a simple sentence becomes a long paragraph, strange language appears, or nearby context contaminates the output into repetition).

The proofreading workflow in this repository is designed to locate these problems systematically.

## Example Screenshots

These screenshots show the patched offline version running with translated UI, skill text, character profile text, and story dialogue:

| Home / lobby text | Skill and battle text |
|---|---|
| ![Translated home screen](assets/screenshots/1.png) | ![Translated skill text](assets/screenshots/2.png) |

| Character profile | Story dialogue |
|---|---|
| ![Translated character profile](assets/screenshots/3.png) | ![Translated story dialogue](assets/screenshots/4.png) |

A typical translation project can be roughly divided into four stages:

```text
1. First-pass translation
2. Proofreading
3. Polishing
4. Repacking and debugging
```

For this project, the first pass was produced with [AiNiee](https://github.com/NEKOparapa/AiNiee), calling Gemini 3.1 Pro and Gemini 3 Flash. Pro was too expensive for the whole project, so the workflow switched to Flash partway through. Proofreading is usually the highest-skill step in a human translation workflow; here it was mainly handled with Claude Code Opus 4.6 and the proofreading command included in this repository. There was no dedicated full polishing pass. Repacking and debugging were mainly handled by Codex. In practice, proofreading and repacking/debugging can also be handled by the same agent.

This repository keeps only reusable tools, the proofreading command, the clean Japanese unique text file, and the final glossary. It does not include original game assets, APKs, unpacked databases, large translated files, signing keys, or generated outputs.

## Layout

```text
tools/
  extract_japanese_text.py       # unpack MVGL databases and extract Japanese text
  split_translated_texts.py      # split line-aligned translated text back by source table
  pack_translated_mvgl.py        # write translations into SQLite databases and rebuild MVGL archives
  patch_apk_assets.py            # replace rebuilt MVGL files inside an APK
  sign_apk_with_signaturetools.py # call SignatureTools' embedded signer

.claude/commands/
  proofread-translation.md       # proofreading command for Claude Code / Codex

glossary/
  megido72_terms_ainee.json       # AiNiee-compatible shared glossary

data/
  japanese_text_unique.txt        # clean, deduplicated, line-aligned Japanese text
```

## 1. Extract Text

Put the game's MVGL files in the repository root. At minimum, the workflow expects:

```text
GKDB_offline.android.mvgl
GKDB_offline_episode.android.mvgl
GKDB_offline_win.android.mvgl
```

Run:

```powershell
python .\tools\extract_japanese_text.py .\GKDB_offline.android.mvgl .\GKDB_offline_episode.android.mvgl .\GKDB_offline_win.android.mvgl -o .\extracted_japanese_text
```

The script unpacks MVGL archives, exports SQLite databases, and generates the text files, TSV files, and manifest needed by the split/repack pipeline.

This repository also includes a pre-extracted clean Japanese unique text file:

```text
data/japanese_text_unique.txt
```

If you only want to reuse the translation workflow, you can use this file directly as the Japanese source text for AiNiee and the proofreading command.

If your MVGL files are elsewhere, pass full paths:

```powershell
python .\tools\extract_japanese_text.py D:\path\GKDB_offline.android.mvgl D:\path\GKDB_offline_episode.android.mvgl D:\path\GKDB_offline_win.android.mvgl -o .\extracted_japanese_text
```

## 2. Glossary And Reference Terms

Before translation, prepare a glossary for character names, place names, item names, organization names, skill names, and other proper nouns. This repository provides an AiNiee-compatible glossary:

```text
glossary/megido72_terms_ainee.json
```

Example:

```json
[
  {
    "src": "メギド",
    "dst": "梅基多",
    "info": "Core term"
  },
  {
    "src": "ヴァイガルド",
    "dst": "维加尔德",
    "info": "Location"
  }
]
```

For a project as large as Megido72, with so many characters, references, and recurring terms, it is almost impossible to build a complete glossary using AI alone. If quality matters, the glossary should be maintained manually throughout translation and proofreading.

## 3. First-Pass Translation With AiNiee

We used [AiNiee](https://github.com/NEKOparapa/AiNiee) for the first-pass machine translation. This project used Gemini 3.1 Pro and Gemini 3 Flash; Pro generally gives better results but is expensive, while Flash is more suitable for large-volume first drafts.

Recommended flow:

```text
1. Extract the Japanese unique text with this repository's scripts.
2. Import the glossary into AiNiee.
3. Use AiNiee to produce bilingual text or a line-aligned translated text file.
4. Make sure the final translated file has exactly the same line count as the Japanese unique text file.
```

I do not recommend using AiNiee's built-in AI auto-proofreading feature. In testing, it often polluted the translation: swapping source and target text, introducing extra explanations, breaking correct terminology, or making line alignment uncontrollable. After the first pass, move to Claude Code or Codex for dedicated proofreading.

## 4. Proofreading With Claude Code / Codex

The repository includes a proofreading command:

```text
.claude/commands/proofread-translation.md
```

This command is the part of the repository I most want to share. Put it under `.claude/commands/` in your project, and Claude Code / Codex can review translations with a fixed standard. Two modes are recommended:

```text
montecarlo; game; Japanese->Simplified Chinese; japanese_text_unique.txt; chinese_text_unique.txt; glossary/megido72_terms_ainee.json
split 500; game; Japanese->Simplified Chinese; japanese_text_unique.txt; chinese_text_unique.txt; glossary/megido72_terms_ainee.json
```

The proofreading pass focuses on:

- Wrong meaning, reversed meaning, wrong subject, or wrong speaker.
- Glossary conflicts, especially character names, place names, skill names, and proper nouns.
- Japanese leftovers, source/target swaps, missing lines, and alignment errors.
- Over-transliteration. Names can be transliterated, but ordinary sentences must be translated naturally by meaning; full katakana sentences need special attention.
- Translation bloat, verbosity, hallucination, or nonsense.
- AI contamination such as explanations, apologies, notes in parentheses, or adjacent-line leakage.
- Text that is too long and overflows the in-game text box.
- Broken rich-text or control syntax, such as splitting `{font_megid}` or `{font_end}` across line breaks.

The two modes serve different purposes:

- `montecarlo` is for risk discovery in very large files. It classifies high-risk regions, samples randomly, and repeats until sampled issues converge. It does not guarantee every line is correct, but it quickly reveals polluted batches, alignment failures, glossary collapse, and untranslated regions.
- `split N` is for actual correction. It reviews fixed-size chunks line by line and is better suited for writing fixes back into the translated text.

The HIGH-level issues in the proofreading command cover major AI translation failures: wrong meaning, wrong subject, wrong glossary term, untranslated text, omission, unsupported additions, leaked AI meta text, gender/pronoun errors, machine-translation hallucination, and abnormal bloat. In practice, `montecarlo` is useful for finding the disease, and `split N` is useful for surgery.

More concretely, the workflow locates problems with several signals:

- Compare Japanese source and translated text to check line count, blank lines, likely misalignment, and source/target swaps.
- Detect remaining Japanese characters in translated lines.
- Check fixed terminology against the glossary.
- Use length ratio and display width to find abnormal bloat; these lines are often hallucinated, over-expanded, or likely to overflow text boxes.
- Protect rich-text tags so automatic wrapping does not tear control syntax such as `{font_megid}` apart.
- Use `montecarlo` sampling to discover polluted regions first, then use `split N` for chunk-level repair. This turns proofreading from “read everything by feeling” into “locate risk first, then fix it in focused passes.”

`pack_translated_mvgl.py` includes generic text cleanup and wrapping logic:

- Flatten meaningless whitespace.
- Preserve and protect rich-text tags.
- Rewrap text based on the original Japanese line breaks and safe width for each text area.
- Limit story text line count to avoid overflowing the main story textbox.
- Support local override files for a small number of manually shortened story lines, instead of hardcoding project-specific fixes into the script.

If some story lines need manual shortening, create a local JSON file:

```json
{
  "365544": "Shortened translated story text"
}
```

Pass it while repacking:

```powershell
python .\tools\pack_translated_mvgl.py --story-overrides .\local_story_overrides.json
```

TSV is also supported. It should include `rowid` plus one of `text`, `translated_text`, or `cn`.

## 5. Repack Databases And MVGL

After proofreading, prepare the line-aligned translated text. The default path is:

```text
已翻译完成/chinese_text_unique.txt
```

Then run the one-command repack pipeline:

```powershell
python .\tools\pack_translated_mvgl.py
```

Common explicit arguments:

```powershell
python .\tools\pack_translated_mvgl.py `
  --fortrans-root .\Fortransjp `
  --chinese-unique .\已翻译完成\chinese_text_unique.txt `
  --db-source .\extracted_japanese_text\databases `
  --output-root .\packed_translated
```

Output:

```text
packed_translated/mvgl/
  GKDB_offline.android.mvgl
  GKDB_offline_episode.android.mvgl
```

Intermediate databases and report:

```text
packed_translated/databases/
packed_translated/pack_report.json
```

If you only want to split the translation back into txt/tsv files corresponding to each source table, run:

```powershell
python .\tools\split_translated_texts.py --fortrans-root .\Fortransjp --chinese-unique .\已翻译完成\chinese_text_unique.txt
```

## 6. Patch APK And Sign

Prepare the original APK, for example:

```text
apk/com_dena_a12021245_v2.0.1.apk
```

Patch the rebuilt MVGL files into the APK:

```powershell
python .\tools\patch_apk_assets.py --apk .\apk\com_dena_a12021245_v2.0.1.apk --replacements-dir .\packed_translated\mvgl --output .\patched_apk\com_dena_a12021245_v2.0.1_cn_unsigned.apk
```

This script uses Python's standard library to replace resources inside the APK zip, updates the CRC entries in `assets/offlinechecksumcache`, and removes old signature entries. The result is an unsigned APK, so it must be signed before installation.

### Option A: SignatureTools On Windows

If your machine does not have system-level Java configured, use [SignatureTools](https://github.com/DeMonJavaSpace/SignatureTools). This is the signing tool used in this workflow. We provide a Python wrapper that calls SignatureTools' bundled `zipalign.exe` and embedded signing launcher.

Example:

```powershell
python .\tools\sign_apk_with_signaturetools.py `
  --tool-root D:\path\to\SignatureTools `
  --input-apk .\patched_apk\com_dena_a12021245_v2.0.1_cn_unsigned.apk `
  --output-apk .\patched_apk\com_dena_a12021245_v2.0.1_cn_signed.apk `
  --keystore D:\path\to\your_key.jks `
  --key-alias your_alias `
  --store-password your_store_password `
  --key-password your_key_password
```

The script runs `zipalign`, signs the APK through SignatureTools, and then runs verification. Install the generated signed APK.

### Option B: Java / Android SDK Tools

If Java or the Android SDK is already installed, you can also use:

- [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer)
- Android SDK `apksigner`

These options require the corresponding `java` or SDK commands to be available on your machine.

## 7. Install The Repacked APK

If you want to reuse the game data that is already downloaded on your device or emulator, follow this order. Back up the game data directory first.

1. Confirm that the game data directory exists and contains the downloaded game files:

```text
Android/data/com.dena.a12021245/
```

2. Copy the rebuilt MVGL files into that directory and overwrite the files with the same names. Back up the originals before overwriting.

```text
GKDB_offline.android.mvgl
GKDB_offline_episode.android.mvgl
```

3. Temporarily rename `com.dena.a12021245` to any other name, for example:

```text
com.dena.a12021245_backup
```

4. Uninstall the original `メギド72`.

5. Install the repacked and signed `メギド72` APK.

6. Before launching the game for the first time, rename the data directory back to:

```text
com.dena.a12021245
```

7. Launch the game.

## Important Notes

- This repository does not distribute original game assets or APKs. Megido72 players are expected to have their own files.
- The translated text file must have exactly the same number of lines as the extracted Japanese unique file.
- For true commercial-quality output, human proofreading is still required. The workflow and proofreading command are meant to greatly improve efficiency, not to remove human judgment.
