# Vocabulary

The words this repository uses on purpose, each with one meaning.

## Terms

| Value | Meaning |
| --- | --- |
| `harness` | Not used alone in this repository. In the field it names both the program that runs a model and the code that runs an evaluation, so we say `agent harness` or `evaluation harness`. |
| `agent harness` | In Anthropic's words, "the system that enables a model to act as an agent": the agent loop plus its tools, context and guardrails. This repository counts the tools as part of it. |
| `evaluation harness` | In Anthropic's words, "the infrastructure that runs evals end-to-end". An experiment's evaluation harness lives in its `code/` folder. |
| `agent loop` | The cycle in which a model reads its context, calls a tool, reads the result and repeats until the task is done or the loop is stopped. |
| `extension layer` | What is added on top of the agent loop to shape what the model knows and can reach: instructions, skills and extra tools. It is part of the agent harness. |
| `runtime` | This repository's term: the part of an agent harness that runs the model in a loop and executes its tool calls. |
| `target` | The outside tool under test. |
| `synthesis` | A set of findings, each graded, with a falsifier and the pinned sources that support it. |
| `generated file` | A file with a `fixture` row in `docs/admissions.md`. `tools/reproduce.py` rebuilds it by running the generator that row names, and compares the result with the committed copy. |

The quoted phrases in `agent harness` and `evaluation harness` are from Anthropic, "Demystifying evals for AI agents" (2026-01-09). The other definitions are this repository's wording.

## Evidence grades

| Value | Meaning |
| --- | --- |
| `verified` | Checked directly against the primary source: the page was fetched, the command was run, the commit was pinned. The time of the check is recorded with the claim. |
| `reported` | Stated by a trusted secondary source, such as documentation, a blog or a publication, and not checked directly. |
| `inferred` | Derived from more than one piece of evidence, with the reasoning shown. |
| `speculative` | Plausible but ungrounded: a hypothesis worth exploring and nothing more. |
| `unknown` | The claim could not be tested by anyone, and no evidence backs it or counts against it. It is logged with a note of the attempts made, and no plan may rest on it. |

## Source types

| Value | Meaning |
| --- | --- |
| `standard-spec` | An open standard, protocol or specification. |
| `code-repo` | A source-code repository, pinned to one commit. |
| `release-artifact` | A downloadable release file, pinned by the SHA-256 of its bytes. |
| `paper` | A published or preprinted research paper. |
| `report` | A technical or institutional report. |
| `web-page` | A single page on the web. |
| `dataset` | A published collection of data. |

## Read-log reasons

| Value | Meaning |
| --- | --- |
| `interface` | Read to learn how to install, run or call the target. |
| `method` | Read for how the target does the thing being measured. |
| `verification` | Read to check a result against the target's own behaviour or text. |

## Read-log kinds

| Value | Meaning |
| --- | --- |
| `contemporaneous` | Each entry was written when the file was opened. |
| `reconstructed` | The entries were written afterwards, from memory and history. |

## Experiment protocols

| Value | Meaning |
| --- | --- |
| `preregistered` | The protocol was committed here before the first recorded run. It was not entered in an outside registry. |
| `exploratory` | The protocol was not committed before the first recorded run. |

## Experiment claims

| Value | Meaning |
| --- | --- |
| `confirmatory` | The protocol was committed before the first recorded run, and the method was written before its author read the target for method work. A result may be stated as supporting the prediction or counting against it. |
| `exploratory` | Not confirmatory: either the protocol was not committed before the first recorded run, or the method was written after its author read the target for method work. |

## Redistributable licences

| Value | Meaning |
| --- | --- |
| `MIT` | MIT License. |
| `Apache-2.0` | Apache License 2.0. |
| `BSD-2-Clause` | BSD 2-Clause License. |
| `BSD-3-Clause` | BSD 3-Clause License. |
| `CC0-1.0` | Creative Commons Zero 1.0 Universal. |
| `CC-BY-4.0` | Creative Commons Attribution 4.0 International. |
