<h1>>Wildcard tools</h1>

# Generate a new wildcard with an LLM

`wildcard_generator.py` creates a complete tags-mode wildcard from a valid YAML
skeleton. The skeleton contains one namespace, empty category lists, the normal
`MODE: tags` header, and instructions written as `# GENERATOR:` comments. See
[`../../howto/wildcard-generator-skeleton.yaml`](../../howto/wildcard-generator-skeleton.yaml)
for a documented example.

The generator uses a staged plan, bounded category batches, deterministic
assembly, the same OpenAI-compatible interface as the linter, and iterative
lint/review/repair passes. Required skeleton categories cannot be removed or
renamed. Supporting categories are added by default, subject to graph-depth and
category-count limits.

Default category sizes are 20 component leaves, 12 combo/public-scene leaves,
50 spotlight leaves, and as many reference-only router leaves as needed. Put an
explicit count such as `# GENERATOR: Create 30 leaves ...` immediately above a
category to override its default.

From `gkr-wildcards`:

```bash
python3 tools/download_danbooru_tags.py \
  --output safebooru_general_tags.csv \
  --min-post-count 100 \
  --verbose

OLLAMA_API_KEY=ollama uv run tools/classify_danbooru_tags.py \
  safebooru_general_tags.csv \
  --output safebooru_general_tags.classified.csv \
  --model qwen3:latest \
  --base-url http://localhost:11434/v1 \
  --api-key-env OLLAMA_API_KEY \
  --verbose

uv run tools/build_danbooru_index.py \
  safebooru_general_tags.classified.csv \
  --output safebooru_general_tags.index.sqlite \
  --content-profile general

uv run tools/wildcard_generator.py \
  ../howto/wildcard-generator-skeleton.yaml \
  --output gkr-superhero.yaml \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --content-profile general \
  --model "$OPENAI_MODEL" \
  --base-url "$OPENAI_BASE_URL" \
  --api-key-env OPENAI_API_KEY \
  --verbose
```

The tool preserves the initial generated draft at `gkr-superhero.yaml` and
always writes the best post-repair copy to `gkr-superhero.fixed.yaml`. Remaining
errors and warnings are marked `[UNRESOLVED]` in the matching
`gkr-superhero.fixed-report.md`. It also writes
`gkr-superhero.generation.json` with the accepted plan, generation-call
metadata, reported token usage, and unresolved findings. API credentials are
never written. Use `--fixed-output` and `--report` to override the corresponding
paths.

Skeleton headers may prohibit exact tags with one or more comment entries:

```yaml
# DO_NOT_USE_TAGS: [comic, western_comics_(style)]
```

Excluded tags are removed from retrieved candidate palettes, included as hard
constraints in generation and repair prompts, rejected by deterministic
category validation (including weighted and space-rendered equivalents), and
recorded in the generation manifest. The directive applies to the complete
generated wildcard, not only required skeleton categories.

Choose how strongly generation must prefer the canonical vocabulary in the skeleton header:

```yaml
# CANONICAL_POLICY: prefer
```

The setting is durable and travels with the skeleton. `--canonical-policy strict|prefer|flexible`
overrides it for one run. When neither is supplied, `flexible` preserves legacy behavior:

- `strict` permits retrieved canonical tags, wildcard references, and narrowly recognized compact
  relationships composed from canonical parts; other literal fallbacks fail category validation.
- `prefer` uses the ordinary concept-level candidate palette, then extracts every literal from the
  draft, searches the SQLite index for that complete phrase, and sends those candidates plus the
  complete draft to a canonical-revision LLM pass. A retained literal must have aligned provenance
  recording its exact text, candidates considered, and a specific reason canonical candidates lose
  necessary visible information.
- `flexible` uses the initial candidate palette but permits compact literal visual phrases without
  the fallback-specific retrieval/revision pass.

`prefer` normally adds one cached generation call for each category chunk containing proposed
literals. Those calls count toward `--max-generation-calls`; large runs may need a higher limit.
Hybrid similarity is used when the index contains compatible embeddings and `--retrieval auto` or
`--retrieval hybrid` selects them. Otherwise the same pass uses lexical SQLite retrieval.

Useful limits and controls:

```text
--batch-categories 3
--category-chunk-size 25
--concept-continuation-buffer 3
--canonical-policy prefer
--max-generation-calls 20
--max-planner-retries 1
--max-category-retries 1
--interactive
--max-repair-passes 2
--max-category-depth 6
--max-added-categories 30
--max-total-tokens 100000
--color auto
```

`--max-category-depth` measures the longest category-reference chain, including
its starting category. `--max-total-tokens` uses `usage.total_tokens` when the
endpoint reports it; an endpoint that omits usage cannot be stopped at an exact
token boundary. Canonical Danbooru tags are preferred when a semantically exact
match exists, while short literal visual phrases remain allowed so vocabulary
coverage never removes theme content.

When a generated category fails deterministic validation, the generator reports
the exact invalid tags or references and retries only that category. The default
is one corrective retry; use `--max-category-retries 0` to disable it. Corrective
requests count toward `--max-generation-calls`.

Large categories are generated autonomously in chunks controlled by
`--category-chunk-size` (25 by default). Each chunk has a distinct cache key,
receives the earlier concept summaries and leaves as exclusions, and is checked
for duplicates against all preceding chunks. The generator aggregates the
chunks under the original category, verifies the final requested count, and
checks dependency use across the complete category. Smaller categories still
use a single chunk. Increase `--max-generation-calls` for very large files;
concept and leaf generation normally require two calls per chunk-round, plus
planner, correction, review, and repair calls.

Partial concept corrections are cumulative. The generator retains usable
concepts, requests only the missing continuation, and asks for a small reserve
controlled by `--concept-continuation-buffer` (3 by default). Excess unique
concepts are deterministically trimmed after the chunk is filled.

Whenever an image is intentionally restricted to a finite color set, that
restriction uses one structured tags-mode item in any category:

```text
(red, blue, gold) limited_palette
```

The generator rejects `limited palette`, prose such as `red and blue palette`,
duplicate colors, and non-color/material descriptions such as `mahogany`,
`rusted brown`, or `electric cyan`. Ordinary color names may still be used as
unmodified palette values. `limited_palette` must not be added merely because a
leaf mentions one or more colors; it is reserved for an intentional whole-image
palette restriction.

Verbose prefixes use ANSI colors when stderr is a terminal. Use `--color always`
to preserve colors through a compatible pipe or log viewer, or `--color never`
to disable them. Within verbose messages, successful HTTP and cache/no-call
details are green, while requested LLM work and corrective retries are yellow.

Invalid plans also receive bounded corrective retries before leaf generation.
The planner receives the exact graph-validation error and its rejected plan.
Required categories named `random`, `random_*`, `*_random`, or `*_router` are
deterministically treated as routers even when the model labels them otherwise.

When every required skeleton category is a spotlight, the accepted plan is
restricted to those required spotlights. The planner cannot add component,
combo, scene, or random/router pools because spotlights are already complete
public outputs.

Generated component pools also enforce strict lead-motif diversity across the
entire category and across generation chunks: the same first content-bearing
subject or setting may not appear twice. Subject-count markers such as `1man`
are ignored when identifying the lead motif. Violations receive normal category
corrective retries with the repeated motif and leaf positions. Repetition may be
retained only after the final retry is exhausted; it is then recorded with every
affected leaf as an unresolved manual-review finding instead of aborting the run.

With `--interactive`, exhausting those retries prompts once for every invalid
tag. The prompt shows the category and every complete leaf affected by that tag,
including leaf numbers, before asking for a decision. Press Enter to accept the
displayed tag unchanged, or type a replacement.
Explicitly accepted and replacement tags bypass palette and subsequent canonical
vocabulary membership checks; the chosen mapping is recorded under
`interactive_tag_overrides` in the generation manifest. Structural checks such
as leaf counts, provenance shape, and declared wildcard references still apply.
Each decision is also written immediately to
`<output-stem>.interactive-overrides.json`, so incomplete or interrupted runs
reuse it without prompting again. Use `--interactive-overrides PATH` to choose a
different persistent decision file. Delete or edit that file to reconsider a
previous answer.

## Tag indexing, content profiles, and local embeddings

The generator retrieves candidates from a reusable SQLite index before asking
the LLM to realize final tag leaves. It first generates minimal visual concepts,
searches the index for each concept, and supplies a bounded canonical palette to
the realization call. Unknown or unavailable underscore-form output is rejected
immediately.

The planned and realized category graphs are both validated. When routers are
present, every planned category must be reachable from at least one router, and
every generated category must reference each dependency declared in its plan at
least once across its leaves. Missing dependency usage enters the same bounded
category-correction flow. If corrective retries cannot make every planned edge
useful, the generator retains the structurally valid category and continues.
The final report marks the omitted dependencies `[UNRESOLVED]` with manual repair
guidance. It also reports every category that is actually unreachable from the
public `random*` routers, so an overconnected plan does not prevent completion.
Generated leaves are also compared using a normalized tags-mode signature that
ignores tag order, weights, underscore/hyphen spelling, and whitespace. This
prevents cosmetically different duplicates from satisfying requested leaf counts.
The linter reports duplicates across categories as warnings and rejects them
within one category; it also reports empty phrases caused by stray commas.
If a model returns more leaves than requested with enough aligned provenance,
the generator deterministically retains the requested prefix and continues. The
removed leaves are preserved in an `[UNRESOLVED]` report entry for manual review.
Excess concepts are handled the same way before candidate retrieval: the
requested prefix is retained and omitted concept summaries are reported.
LLM response parsing also recovers the last valid JSON object array when a model
wraps multiple attempts in Markdown or explanatory commentary.
If a fix-suggestion batch omits an ID or returns an empty rewrite, valid rewrites
from that batch are retained while affected original leaves remain unchanged and
`[UNRESOLVED]`; later repair batches and final report generation continue.

The default `general` content profile requires a CSV containing a
`content_class` column. `classify_danbooru_tags.py` creates that enriched CSV in
restartable OpenAI-compatible batches. It classifies tags as `general`,
`sensitive`, `explicit`, or `ambiguous`; a general index includes only
`general`. Use a reviewed YAML `--overrides` mapping for human corrections.
Selecting `--content-profile unrestricted` explicitly permits building from the
original unclassified CSV.

Optional semantic embeddings use the OpenAI-compatible `/v1/embeddings`
endpoint with portable array inputs. For Ollama:

```bash
ollama pull embeddinggemma
export OLLAMA_API_KEY=ollama

uv run tools/build_danbooru_index.py \
  safebooru_general_tags.classified.csv \
  --output safebooru_general_tags.index.sqlite \
  --content-profile general \
  --embeddings \
  --embedding-model embeddinggemma \
  --embedding-base-url http://localhost:11434/v1 \
  --embedding-batch-size 512 \
  --verbose
```

Embedding progress is committed after every request, so repeating the command
resumes missing vectors. Model, endpoint, prefixes, dimensions, CSV checksum,
and content profile are stored in the index. Incompatible partial embeddings
are rejected; use `--no-resume` to rebuild vectors deliberately.

Retrieval modes are:

```text
--retrieval auto      # default: hybrid when a compatible endpoint responds
--retrieval lexical   # deliberately disable semantic retrieval
--retrieval hybrid    # require a complete index and live embedding endpoint
```

`embeddinggemma` is the recommended Ollama starting point. `all-minilm` is a
lighter alternative; `qwen3-embedding` is a quality-oriented alternative to
evaluate against curated correction examples. Indexing and querying must use
the same embedding model and prefixes.

## Practical example:

### Obtain the safebooru_general_tags.csv file and prepare it for use

```bash
python3 tools/download_danbooru_tags.py \
  --output safebooru_general_tags.csv \
  --min-post-count 100 \
  --verbose

# classify the tags for use
# once concluded, feel free to review the created CSV's ambiguous tags
OLLAMA_API_KEY=ollama uv run tools/classify_danbooru_tags.py \
  safebooru_general_tags.csv \
  --output safebooru_general_tags.classified.csv \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --batch-size 100 \
  --verbose

# Generate a database with embeddings
OLLAMA_API_KEY="ollama" uv run tools/build_danbooru_index.py \
  safebooru_general_tags.classified.csv \
  --output safebooru_general_tags.index.sqlite \
  --content-profile general \
  --embeddings \
  --embedding-api-key-env OLLAMA_API_KEY \
  --embedding-base-url http://localhost:11434/v1 \
  --embedding-model embeddinggemma:latest \
  --embedding-batch-size 512 \
  --verbose

```

### Obtain a test file

```bash
# Without Sqlite and embeddings
OLLAMA_API_KEY="ollama" uv run tools/wildcard_generator.py \
  ../howto/wildcard-generator-skeleton.yaml \
  --output gkr-superhero.yaml \
  --danbooru-tags safebooru_general_tags.csv \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --verbose

# WITH both
THEME="superhero"; OLLAMA_API_KEY="ollama" uv run tools/wildcard_generator.py \
  gkr-$THEME.skeleton.yaml \
  --output gkr-$THEME.yaml \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --content-profile general \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --retrieval-candidates 30 \
  --canonical-policy prefer \
  --llm-log gkr-$THEME.json \
  --max-planner-retries 3 \
  --category-chunk-size 25 \
  --max-category-retries 3 \
  --max-generation-calls 200 \
  --concept-continuation-buffer 5 \
  --interactive \
  --verbose \
  --color auto
```

# Lint an existing wildcard

`wildcard_linter.py` audits GKR wildcard YAML files in two stages:

1. Deterministic parsing, reference, route, marker, forbidden-language, and heuristic checks.
2. Optional semantic review through OpenAI or an OpenAI-compatible endpoint such as LiteLLM.

The deterministic stage is always run. The LLM stage never edits files and is opt-in.

## How command-line options are documented

Use `uv run tools/wildcard_linter.py --help` or
`uv run tools/wildcard_generator.py --help` for the authoritative option list. The
usage synopsis follows standard `argparse` notation:

- an option inside `[...]` is optional;
- an option absent from brackets is required;
- `{a,b}` means exactly one listed value may be selected;
- `PATH`, `TIMEOUT`, and similar uppercase words are values supplied after an option.

The help description also states what happens when an option is omitted. This matters
because an optional path can have two different meanings:

- Generator `--fixed-output` is a path override. A repaired file is always written;
  omitting the option derives `<output-stem>.fixed.yaml` automatically.
- Linter `--fixed-output` enables writing a fixed copy. Omitting it means no fixed YAML
  is written, although linting and report output still run.

Defaults do not necessarily mean that a feature is enabled. For example,
`--canonical-tag-style underscore` supplies the default spelling only when canonical
suggestions or repairs need to render a candidate. It does not enable LLM review or
canonical suggestions by itself. Those require `--llm`, `--suggest-fixes`,
`--danbooru-tags`, and `--canonical-tag-suggestions` as documented by their help.

The built-in help is intended for exact requiredness, defaults, omission behavior, and
option dependencies. This README provides workflows, rationale, and longer examples.
Keeping both levels is preferable to putting all documentation in only one place: users
can understand a single flag without leaving the terminal, while related multi-option
workflows remain readable here.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)

The script contains PEP 723 dependency metadata, so `uv` installs `PyYAML` into its managed cache automatically. No project virtual environment or manual `pip install` is required.

## Run deterministic checks

From `gkr-wildcards`:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml
```

Audit one subsection and the category pools it references directly:

```bash
uv run tools/wildcard_linter.py gkr-comics.yaml --only spotlight_us_comics
uv run tools/wildcard_linter.py gkr-comics.yaml \
  --only spotlight_us_comics --only spotlight_covers
uv run tools/wildcard_linter.py gkr-comics-new.yaml \
  --only spotlight --only-depth 2
```

`--only` accepts repeated options or comma-separated names. A plain category name matches
that name in any input namespace; use `namespace/category` to disambiguate. The scope is
one reference hop by default: selected categories are audited, as are all leaves in the
categories referenced directly by their leaves. Set `--only-depth 0` to audit only the
named categories, or a larger integer such as `--only-depth 2` to continue through that
many reference levels. Traversal stops early when no new categories remain, and cycles
cannot make it loop because each category is included at most once. Missing-reference
and cycle checks still use
the complete file inventory so the partial audit does not invent errors merely because
an out-of-scope category was intentionally omitted. Whole-namespace reachability, route-
motif probability, and namespace-policy checks are skipped because their results would
be misleading on a subsection. `--fixed-output`, LLM review, canonical lookup, and report
generation operate only on the selected scope while the fixed copy retains the rest of
the original YAML unchanged.

For large pools, add `--semantic-duplicates` to compare leaf meaning rather than only
normalized spelling:

```bash
uv run tools/wildcard_linter.py gkr-comics.yaml \
  --only spotlight_covers \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --retrieval auto \
  --semantic-duplicates \
  --semantic-duplicate-threshold 0.94 \
  --format markdown --output spotlight-covers-duplicates.md \
  --fail-on never --verbose
```

This pass embeds scoped content leaves in batches and compares cosine similarity only
within the same category. Reference-only/router leaves are excluded because their shared
category names create false positives while their route combinations remain structurally
distinct. Above-threshold pairs are joined into connected clusters, producing one
`semantic_duplicate_leaf` finding per cluster rather than one finding per pair. Exact
normalized duplicates remain covered by `duplicate_leaf`. Structured evidence contains
the representative, every member and source location, similarity to the representative,
and configured threshold. The unresolved Markdown renders all clusters in one dedicated
section with one manual consolidation decision per cluster. The default threshold is
`0.94`; increase it for fewer, stronger matches or lower it cautiously for broader
review. Similarity does not prove identity, so cluster merging and deletion remain
report-only and never enter fixed YAML automatically.

When `--llm --suggest-fixes` makes semantic-duplicate warnings eligible for repair (for
example through `--fix-severity both` or `--fix-rules semantic_duplicate_leaf`), proposed
rewrites are embedded again before acceptance. A rewrite that remains at or above the
configured similarity threshold is rejected and remains `[UNRESOLVED]`; it is not marked
fixed merely because the LLM changed its wording.

Audit every wildcard YAML file in the folder:

```bash
uv run tools/wildcard_linter.py .
```

Generate JSON or Markdown:

```bash
uv run tools/wildcard_linter.py . --format json --output audit-reports/all.json
uv run tools/wildcard_linter.py gkr-anime.yaml --format markdown --output audit-reports/anime.md
```

By default, the command exits with status 1 for errors and succeeds when only warnings remain. Use `--fail-on warning` for strict CI or `--fail-on never` for reporting only.

## Audit generated post-LLM prompts

`theme_organizer.py` records the prompt mode and final positive prompt in `details.md`. Audit those completed generations without loading wildcard source files:

```bash
uv run tools/wildcard_linter.py \
  --validate-post-prompts details.md \
  --format markdown \
  --output post-prompt-audit.md \
  --annotated-details details.audited.md \
  --fail-on warning
```

This mode is an offline, post-generation audit. It reports each prompt as `compliant`, `noncompliant`, or `unable`; it does not retry generation, reject an image, modify the original `details.md`, or change the workflow's positive prompt. `--annotated-details` writes an optional copy with `Audit status` and any `Audit issue` entries inserted into each corresponding image section. With the default `--fail-on error`, a noncompliant prompt is reported but does not fail the command; `unable` does. Use `--fail-on warning` to fail for noncompliance or `--fail-on never` for reporting only.

Post-prompt issues are classified as format, subject-count, unresolved-alternative, camera, weight-syntax, representability, temporal-transition, unknown-canonical-tag, or invalid-service-response failures. Summaries report both generated-image totals and unique pre/post prompt-pair totals, so repeated generations do not inflate issue frequency.

Danbooru subject counters use a strict built-in vocabulary (`1`-`5`, `6+`, and `multiple` for girls, boys, and others, plus `no_humans`). Invalid constructions such as `1family`, `3men`, or `7others` fail validation. `crowd` and `people` are accepted for indefinite unnamed groups, which Danbooru does not count as foreground characters.

Optionally provide a local one-tag-per-line or CSV tag list:

```bash
uv run tools/wildcard_linter.py \
  --validate-post-prompts details.md \
  --danbooru-tags danbooru-tags.csv \
  --format markdown --output post-prompt-audit.md
```

`--danbooru-tags` is usable with YAML paths, post-prompt auditing, or both. For Tags-mode YAML leaves, the linter reports:

- `canonical_tag_normalization` when a literal phrase has an exact underscore-form tag in the vocabulary;
- `canonical_tag_alias` when an alias maps unambiguously to one canonical tag and the source CSV includes aliases;
- `canonical_tag_separator_normalization` when a hyphenated phrase has an exact space-equivalent canonical tag (`high-contrast` → `high_contrast`);
- `canonical_tag_contained_span` when a longer comma-separated item contains one or more exact multiword tags;
- `unknown_canonical_tag` for an unknown canonical-looking underscore token, with a small ranked candidate list.

Contained-span matching searches longest, non-overlapping spans of at least two words. Single-word tags remain in the vocabulary for complete-item validation but are not used to decompose longer phrases. Exact matches always win. When exact and alias lookup fail, candidate retrieval also tests conservative singular/plural variants of the final component (`holding_books` → `holding_book`, `blue_eye` → `blue_eyes`). These are candidates, not unconditional rewrites, because plurality can be visually meaningful. For example, `vibrant red hair` reports canonical identity `red_hair` and unmatched word `vibrant`; it does not infer `colorful` or recommend the hybrid `vibrant red_hair`.

During LLM visual review, an exact complete comma-separated Tags-mode item from this vocabulary is supplied as a verified visual tag. Thus `dreaming` passes the representability test when it is its own item and exists in the CSV. A larger phrase such as `detective dreaming of victory` receives no such exemption merely because it contains that word. Structural and composition checks still apply to verified items.

The same vocabulary is used narrowly during the Comprehension test. An exact canonical
item is treated as a recognized atomic image concept and is not rejected solely because
its name appears abstract, unfamiliar, or dependent on learned tag semantics. This does
not make the entire leaf automatically comprehensible: a canonical relational tag such
as `holding` can still be reported when no held object is identified, and canonical
items do not excuse a missing subject, ambiguous action target, or unclear relationship.

It is also consulted for the Single-moment test. Exact atomic action and effect tags such
as `jumping`, `breaking`, `digital_dissolve`, and `transformation` are treated as learned
freeze-frame concepts rather than rejected merely because their ordinary-language names
imply time. Explicitly multi-state concepts such as `before_and_after`, progressions,
sequences, and stage layouts remain invalid in an ordinary single-image route. Contextual
misuse also remains reviewable—for example, a person-oriented `jumping` tag does not
automatically validate a space fleet described as making a hyperspace jump.

Hyphen normalization is also exact-first. A literal canonical tag that contains a hyphen remains unchanged. Otherwise ASCII hyphens and common Unicode dash forms are treated as word separators for lookup, allowing `high-contrast`, `high‑contrast`, and `high—contrast` to discover `high_contrast` without globally rewriting legitimate hyphenated canonical tags.

The Tags-mode `canonical_composition` policy also recognizes overlapping modifier tags that share the final noun. When at least two vocabulary-backed components exist, `glowing red eye` yields `glowing_eye` (exact) and `red_eyes` (conservative inflection from `red eye`) under `canonical_tag_composition`. The components are passed to the constrained LLM and must remain represented in an accepted rewrite. The analysis is disabled outside Tags mode, does not fire for only one component, and never treats candidate existence alone as permission to change semantics.

Narrative-mode YAML leaves are not canonicalized. Fuzzy candidates are advisory and never become automatic replacements solely because of string similarity. Short literal phrases remain allowed when no canonical tag expresses the relationship.

Tags-mode style text is also checked for medium words that diffusion models commonly literalize as objects. The `ambiguous_brush_medium` rule flags style shorthand such as `bold brush contours`, `dry-brush shadows`, and `visible brushwork`, and recommends the intended visible mark instead. It does not flag a brush explicitly held, used, or depicted as an object. Configure this vocabulary under `literalized_style_terms` in `tags-rules.yaml`.

To let the existing LLM fix stage choose from constrained candidates, add:

```text
--danbooru-tags safebooru_general_tags.csv
--canonical-tag-suggestions
--canonical-tag-candidate-count 5
--canonical-tag-style underscore
```

`--canonical-tag-suggestions` requires `--llm --suggest-fixes`. It makes canonical-tag findings eligible for that fix pass even when `--fix-severity error` is otherwise in effect; an explicit `--fix-rules` allowlist still takes precedence. For each questionable Tags-mode item, the LLM receives only the retrieved candidates and may select one when semantically equivalent, retain a short literal phrase, or omit an unsupported/nonvisual concept. It is explicitly prohibited from inventing another underscore tag. Deterministic fix validation then rejects a rewrite that retains the targeted canonical issue or introduces a new canonical-tag finding. The normal semantic verification pass still checks preservation of visible facts.

Add `--canonical-literal-review` to review short plain-space multiword phrases that
would otherwise bypass underscore-tag validation. It searches the complete phrase
against the SQLite index and records already canonical component words for context.
A phrase made entirely from known vocabulary words is accepted as a literal composition.
For other phrases, `canonical_literal_concept` is emitted only when a retrieved candidate
is compatible with the phrase's noun head (for example, `glass biodome` can retrieve a
`dome` candidate). This suppresses merely nearby alternatives for specialized phrases
such as vehicle details. Embeddings are queried in batches before review when hybrid
retrieval is available.

Literal review searches both the whole phrase and each visible word component. The
result includes `component_guidance` and a larger `candidate_tag_set_palette`, allowing
the report to show possible vocabulary coverage. Component-only results are advisory:
the repair stage does not rewrite them unless whole-phrase retrieval also supplies a
meaning-preserving compound candidate. This prevents nearby component tags from turning
one attached concept into unrelated singleton tags. A candidate that was already present
elsewhere in the source leaf cannot be reused as evidence that a different literal phrase
was preserved; the repair must introduce the replacement for that phrase.
Unmatched components remain literal. For example, `crumbling stone cloister` may retrieve
`ruins`, `rubble`, `stone_wall`, `arch`, and `column`, but `arch` or `column` may be used
only when the source/context actually establishes those details; vector proximity alone
does not authorize them. The generator's `prefer` policy uses the same component-aware
retrieval before accepting a literal fallback.
Candidate proximity alone never authorizes replacement: the
LLM may combine several canonical components or retain the literal when none preserves
the complete visible concept, and the rewritten leaf is checked again before acceptance.
Retaining such an ambiguous literal does not invalidate an otherwise safe repair to the
same leaf; the literal finding remains unresolved for manual review. Atomizing its words
or damaging their relationship is still rejected. Validation also preserves plural
objects and explicit subject counts, so rewrites such as `robes` to `robe` or `5others`
to `5students` cannot enter the fixed copy. Fix-manifest statuses are exclusive; when a
safe deterministic repair survives but a later LLM enhancement fails, the accepted
rewrite and rejected enhancement are recorded in separate fields.

Literal review uses a canonical-anchor coverage target for short phrases: at least half
of their non-preposition content words should already be exact vocabulary items when
faithful tags exist (one of two words, two of three or four words). Phrases meeting that
target are treated as valid human-curated compositions and are not sent to embedding
review merely because the complete phrase is not one canonical tag. Falling below the
target emits `canonical_literal_concept` evidence with `content_words`, current and
required coverage counts, whole-phrase candidates, and component candidates. The target
never authorizes destructive splitting; unmatched modifiers and relationships remain
literal when the vocabulary cannot preserve them.

When fix generation is enabled, exact deterministic canonical and redundancy repairs
are applied to an in-memory working copy before LLM review. All subsequent content
review, candidate generation, correction retries, and verification therefore operate on
the already-cleaned leaf. The original YAML remains unchanged; the combined result is
written only to `--fixed-output`.

The deterministic validator applies preservation checks to every LLM repair rule, not
only canonical-literal findings. It rejects newly invented concepts, comma-splitting of
attached descriptive phrases, loss of canonical source tags, and substitution of an
unknown underscore token with a merely similar retrieved neighbor. Embedding candidates
remain suggestions: they do not prove that tags such as `steam_pipe` and `exhaust_pipe`
are interchangeable.
Pure letter-case rewrites are also rejected because they do not improve model
representability. A standalone literal descriptor cannot be silently deleted by an LLM
repair—even when an LLM finding criticizes it; the rewrite must retain it or provide a
concrete replacement. This keeps subjective removals such as dropping `stark` in the
unresolved review artifacts instead of applying them automatically.

Generator underscore validation uses the complete authoritative CSV vocabulary. The
retrieved concept palette remains preferred authoring guidance, but a real vocabulary
tag outside that palette is valid and an invented underscore token is invalid. Weighted
items validate their inner token. Shared policy also requires contextual coherence for
all categories: props need a visible worn/held/attached/nearby relationship, anatomy and
actions must be compatible, technical details must be visible from the selected
viewpoint, and arbitrary scale-reference objects are omitted.

This review is deliberately opt-in because compound-word similarity is heuristic and a
nearest embedding candidate is not proof of equivalence. Its findings are report-only by
default, even with `--fix-severity both`. To add them to the ordinary severity selection,
use `--fix-literal-concepts`; proposed rewrites still pass canonical, structural, and
semantic-preservation verification before entering the fixed copy. An explicit
`--fix-rules canonical_literal_concept` remains available when that is the only rule to
repair, because `--fix-rules` is an allowlist that replaces `--fix-severity`. This
separation lets a project evaluate candidate quality before allowing changes.

Compact relationship phrases are treated separately from ordinary descriptive literals. The
linter recognizes a canonical relationship action plus canonical target, such as
`holding black_rose` or `standing against_mirror`, and a small body-part/spatial frame around a
canonical weighted object, such as `hands on (steering_wheel:1.2)`. These are sent to semantic
review as verified relationship compositions and are not canonical-literal repair targets. The
recognition is intentionally narrow: unknown underscore tokens and arbitrary prose remain subject
to normal validation and similarity review.

The same recognition covers compact visual action/preposition/object forms such as
`looking at playing_card` and `leaning against machinery`, direct actions such as
`scanning holographic_interface`, and canonical modifier/object forms such as
`black wax_seal`. A valid `(red, blue, ...) limited_palette` expression is a specialized
tags-mode construct and is exempt from generic long-phrase and contained-span repairs.

`--canonical-tag-style underscore` renders canonical suggestions as database identifiers such as `red_hair`. Use `--canonical-tag-style spaces` for models whose documented tag syntax uses `red hair`; matching and verification still retain `red_hair` internally as the canonical identity.

### SQLite and embedding-assisted canonical candidates

The linter can reuse the same SQLite tag index as the generator. When
`--danbooru-tags` is supplied, `--retrieval auto` (the default) looks beside the CSV
for `<csv-stem>.index.sqlite`. If the index exists, matches the CSV checksum, and has
complete embedding metadata, the linter queries the recorded embedding endpoint and
combines semantic and lexical results. Unknown-tag queries are embedded in batches and
cached in memory for the run. Exact canonical matches, aliases, and conservative
singular/plural matches still take priority over approximate retrieval.

Use `--retrieval lexical` to use the compatible SQLite index without contacting the
embedding endpoint. Use `--retrieval hybrid` when embeddings are mandatory: the command
fails if the index is missing, stale, incomplete, has incompatible vector dimensions,
or the embedding endpoint cannot be queried. `auto` reports the problem in verbose mode
and falls back to SQLite or in-memory lexical matching instead.

Override discovery or metadata only when needed:

```text
--danbooru-index safebooru_general_tags.index.sqlite
--retrieval auto
--embedding-model embeddinggemma:latest
--embedding-base-url http://localhost:11434/v1
--embedding-api-key-env OLLAMA_API_KEY
```

Normally the model, URL, query prefix, and vector dimensions should be omitted so they
come from index metadata. Supplying `--danbooru-index` requires `--danbooru-tags`; the
CSV remains the authoritative vocabulary and is checksum-verified against the index.

During post-prompt auditing, unknown underscore-style output tokens receive soft `unknown_canonical_tag` warnings. These warnings leave the per-image status compliant by themselves, but count under `--fail-on warning`.

Create or refresh the local vocabulary with:

```bash
python3 tools/download_danbooru_tags.py \
  --output safebooru_general_tags.csv \
  --min-post-count 100 \
  --verbose
```

The default `--source auto` mode first downloads the small `danbooru_tags.csv` snapshot from `newtextdoc1111/danbooru-tag-csv` at a pinned Hugging Face revision. It verifies the published SHA-256, retains only General tags (`category == 0`) meeting `--min-post-count`, and normalizes the result to the linter's `name,post_count,alias` format. Pinning and checksum verification make repeated downloads reproducible and prevent a changed or incomplete remote file from silently replacing the vocabulary. API sources do not provide aliases and therefore emit `name,post_count` instead.

If Hugging Face is unavailable, automatic mode tries the Donmai Safebooru API and finally Safebooru.org's XML tag API. Safebooru.org is not count-ordered, so that last fallback must scan every page before applying the threshold and can take substantially longer. Select one source explicitly when desired:

```bash
python3 tools/download_danbooru_tags.py --source huggingface --output safebooru_general_tags.csv --verbose
python3 tools/download_danbooru_tags.py --source donmai --output safebooru_general_tags.csv --verbose
python3 tools/download_danbooru_tags.py --source safebooru-org --output safebooru_general_tags.csv --verbose
```

The CSV is atomically replaced only after a successful download. The downloader also writes `safebooru_general_tags.csv.metadata.json` with the actual source, retrieval time, threshold, tag count, and provenance. The default threshold is 100 posts; use `--min-post-count 1` to retain every General tag in the selected source. `--max-pages` applies only to API sources and records that the output is partial.

Use the downloaded copy in a standalone post-prompt audit:

```bash
uv run tools/wildcard_linter.py \
  --validate-post-prompts details.md \
  --danbooru-tags safebooru_general_tags.csv \
  --annotated-details details.audited.md \
  --format markdown \
  --output post-prompt-audit.md \
  --fail-on never
```

Or add these two options to a wildcard lint/fix run:

```text
--validate-post-prompts details.md --danbooru-tags safebooru_general_tags.csv
```

For retrieval-assisted YAML fixes in that combined run, also add:

```text
--canonical-tag-suggestions --canonical-tag-candidate-count 5
```

When YAML paths and `--validate-post-prompts` are used together, ordinary `--output` contains only the YAML lint/fix report. `--annotated-details` carries per-image audit entries in the copied details file. To also write a standalone audit summary, use a distinct destination:

```text
--post-prompt-output post-prompt-report.md
```

To deliberately restore the former combined report, use:

```text
--include-post-prompt-report
```

`--post-prompt-output` and `--include-post-prompt-report` are mutually exclusive. In audit-only mode with no YAML paths, `--output` continues to mean the post-prompt report; `--post-prompt-output` may be used instead for naming consistency.

## Verbose mode and LLM traces

Use `-v` or `--verbose` to show progress on standard error while keeping the selected report format clean on standard output:

```bash
uv run tools/wildcard_linter.py . --verbose --fail-on never
```

Verbose output includes discovered files, inventory totals, deterministic finding counts, LLM batch progress, elapsed time and HTTP status for every completed LLM call, cache hits, and report destinations. Failed HTTP calls report the endpoint, batch and offset, elapsed time, status/reason, and server response body. The full untruncated body is also written to the trace when tracing is active.

When `--llm` and `--verbose` are used together, the linter automatically creates a JSON Lines trace in the operating system's temporary directory and prints its path:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --verbose \
  --api-key-env OPENAI_API_KEY \
  --model your-model
```

Choose a specific trace location with `--llm-log` whether or not verbose mode is enabled:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --llm \
  --api-key-env OPENAI_API_KEY \
  --model your-model \
  --llm-log audit-reports/anime-llm-trace.jsonl
```

The trace records:

- Model and endpoint, without credentials
- Review scope, batch size, and policy hash
- Stable leaf IDs, categories, line numbers, and text sent in each batch
- Raw assistant response returned for each batch

The trace never records the API key or authorization headers. It can still contain sensitive wildcard content and model-generated text, so review it before sharing and delete it when it is no longer needed. Automatically created traces remain in the system temporary directory until the operating system or user removes them.

## LLM response cache

Successful review, fix-suggestion, and fix-verification results are cached per item by default in the system temporary directory under `wildcard-linter-cache`. Each new entry is named `item-<sha256>.json` and keyed by the phase, endpoint, model, instructions, and one prompt's semantic inputs—including mode, category, text, issues, or proposed rewrite as applicable. File path, source line, list index, and transient leaf ID are excluded, so an unchanged prompt remains reusable after reordering. Before each LLM call, the linter resolves individual hits and submits only missing items as a batch. Failed, malformed, omitted, and incomplete item responses are not cached. Cache files created by the older batch-level implementation do not have the `item-` prefix and are not reused; they may be deleted.

A cache hit requires every key component for that phase and item to remain identical. In particular:

- Changing `prompt.md` changes the complete review instruction and invalidates all `LLM batch` review entries, even for unchanged YAML leaves.
- Changing built-in fix instructions or the per-item issue/canonical guidance invalidates affected `fix-suggestion batch` entries.
- Changing the original text, proposed rewrite, or verification issues invalidates affected `fix-verification batch` entries.
- Changing `--model` or `--base-url` invalidates entries for every phase.
- Changing `--canonical-tag-style`, candidate count, vocabulary-derived candidates, or applicable rules can change fix inputs and therefore invalidate their entries.
- Changing only file paths, line numbers, list positions, leaf IDs, batch size, or harmless YAML ordering does not invalidate an otherwise identical per-item entry.

This conservative instruction-level invalidation is intentional: a response produced under an older policy must not hide a finding introduced by the new policy. After one complete run under the new policy, immediately repeating the same command and inputs should normally show all eligible items as cached.

Choose another cache folder or disable caching with:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --llm-cache-dir audit-cache
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --no-llm-cache
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --llm-cache-max-age-minutes 1440
```

`--llm-cache-max-age-minutes` is disabled by default. When set, the cache is swept at the start of the LLM stage; entries older than the given number of minutes are deleted, and any needed items are requested again. Delete the displayed cache directory to force a completely fresh run. Cache files contain model-generated audit or rewrite content but never the API key or authorization header.

## Report layout, differences, and color

Text and Markdown reports render every finding as a separate section. When a potential fix exists, the report shows the original leaf and proposed replacement as a diff.

When `--fixed-output` actually receives an accepted rewrite, the report labels it
`Applied fix — LLM generated and written to fixed output`. `Potential fix` is reserved
for a suggestion that was not written. Finding locations always identify the original
input YAML; the linter never overwrites that source file.

Every leaf-level finding also shows the complete `Source leaf` immediately after the
location and message. This provides the prompt context even when no fix was suggested or
the finding remains unresolved. File- or category-level structural findings that do not
belong to one specific leaf omit this block.

When `--fixed-output` is generated, findings whose leaves were not included in the accepted replacements are marked `[UNRESOLVED]` in text and Markdown reports. The report summary includes the unresolved count, making `rg '\[UNRESOLVED\]' gkr-comics.fixed-report.md` a quick review filter. JSON reports expose the same state as `fix_status` (`fixed`, `unresolved`, or `not_attempted`) and include `summary.unresolved`. The marker is report metadata only; it is never inserted into wildcard YAML content.

Terminal color defaults to `auto`: ANSI colors are enabled only when text is written directly to an interactive terminal. Redirected output and `--output` files remain free of escape codes. Override detection with:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --color always
uv run tools/wildcard_linter.py gkr-anime.yaml --color never
```

Terminal reports use yellow for warnings, red for errors, magenta for LLM markers, green for potential fixes, and red/green diff lines.

Markdown does not have portable foreground-color support across renderers. Markdown reports use colored status symbols, separate headings, horizontal rules, and fenced `diff` blocks, which supporting renderers color as removed and added lines.

## LLM review tests and potential fixes

The LLM stage applies all seven tests defined in `prompt.md`:

1. Visual test
2. Single-moment test
3. Theme test
4. Focus test
5. Appeal test
6. Route test
7. Comprehension test

It is therefore broader than the Visual test alone. Each LLM finding reports the specific failed test returned by the reviewer. LLM-origin findings receive a visible `[LLM]` marker in text reports and an **LLM** marker in Markdown reports. JSON findings use `"source": "llm"`, which makes them easy to filter programmatically.

Request potential fixes with `--suggest-fixes`:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --suggest-fixes \
  --verbose \
  --api-key-env OPENAI_API_KEY \
  --model your-model
```

This option runs a separate remediation prompt after review. It proposes one replacement for each leaf with a deterministic or LLM finding. The remediation prompt must preserve valid visual facts, theme, subject count, actions, format, and compact wildcard style while addressing only the reported issues.

Accepted deterministic candidates receive a final LLM preservation check before they are written. This verifier rejects removed or invented facts, changed alternatives, weakened relationships, changed subjects/actions, and format evasion. `--skip-fix-verification` disables this pass when explicitly requested, but is not recommended for generated fixed files.

Fix generation uses `--batch-size`; the stricter preservation pass uses `--verification-batch-size`, which defaults to 15 so each comparison receives more attention. Identical no-op rewrites are discarded. Structural graph findings such as camera conflicts are reported but are not sent to the prose fixer.

By default, only error-level findings are sent to the fixer. Use `--fix-severity warning` for warnings only or `--fix-severity both` for errors and warnings. You can instead select an exact comma-separated allowlist with `--fix-rules`; the allowlist overrides the severity setting:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --suggest-fixes \
  --fix-rules tags_sequential_format,invisible_history_or_status
```

Suggested rewrites appear as `potential fix:` lines in text, nested suggestions in Markdown, and the `suggestion` field in JSON. They are advisory and are never written back to a wildcard file automatically.

Every item labeled `Potential fix` is generated by the second LLM pass. Deterministic rules may show a `Suggested approach` inside their explanatory message, but that is static rule guidance—not a replacement leaf—and does not produce a diff or fixed-file change.

`--suggest-fixes` requires `--llm` and adds another set of model requests. When tracing is enabled, fix requests and responses are stored as `fix_request` and `fix_response` JSONL events.

## Write a fixed copy

Use `--fixed-output` to apply accepted LLM suggestions to a new YAML file automatically:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --suggest-fixes \
  --fixed-output gkr-anime.fixed.yaml \
  --verbose \
  --api-key-env OPENAI_API_KEY \
  --model your-model
```

Requirements and safeguards:

- `--fixed-output` requires both `--llm` and `--suggest-fixes`.
- Exactly one input YAML file is allowed.
- The output path must differ from the original path.
- Only leaf lines with generated suggestions are changed.
- Before requesting LLM fixes, unambiguous canonical normalization, unique aliases, separator normalization, and fully matched contained-span/shared-head compositions are combined into a staged leaf repair. Ambiguous or partially matched phrases remain for contextual review.
- The LLM fix stage receives the staged leaf and only the findings not resolved by that deterministic pass; a rejected LLM enhancement does not discard an already safe deterministic repair.
- Wildcard references and their weights must remain exactly unchanged.
- Every rewrite is deterministically re-linted; rewrites that retain a targeted error or introduce a new finding are rejected.
- A rejected deterministic rewrite receives fresh corrective LLM attempts containing the exact rejection reasons. `--max-fix-retries COUNT` controls these attempts (default: 2; use 0 for one-shot behavior).
- Repair requests include a policy version, so cached suggestions produced for older validation behavior are not silently reused.
- Multiword concepts may use a meaning-preserving compound candidate, but repairs that merely split a relationship such as `bone armor` into `bone, armor` are rejected.
- A separate semantic-preservation pass rejects fact loss, invention, altered alternatives or relationships, and format evasion.
- LLM rewrites must pass two independently keyed semantic-preservation passes. `--skip-fix-verification` disables both and is not recommended.
- Deterministic validation rejects removed `or`/`either` alternatives and newly introduced dangling relational fragments.
- Canonical-literal rewrites must retain every meaningful visible source word (allowing conservative inflection), so material, modifier, action, and quantity details cannot disappear merely to reach a nearby vocabulary tag.
- Exact, cross-category, and semantic duplicate findings are report-only during repair. The linter will not invent camera, mood, action, or quality tags simply to make duplicate text differ.
- A separate canonical redundancy pass safely removes repeated complete canonical items, retaining the more highly weighted copy. It also prefers explicit quantity tags where implication is unambiguous, such as `sword, multiple_swords` becoming `multiple_swords`. It does not collapse relationship/state tags such as `holding_sword` or `broken_sword` into or over a plain object tag.
- Comments, category ordering, router leaves, and unaffected formatting remain intact.
- The copy is written atomically and parsed as YAML before it replaces the selected output path.
- The original file is never modified.

Use `--fix-manifest path.json` to record every proposed rewrite, its rationale and triggering issues, and whether validation accepted or rejected it. Markdown reports also summarize findings separately from affected leaves and show each rejected rewrite with its validation reasons once per leaf.

For a smaller second-stage manual review, add `--unresolved-output path.yaml` and
optionally `--unresolved-report path.md`. The unresolved YAML starts from the accepted
`--fixed-output` content and then substitutes the safest rejected attempt for each
unresolved leaf. It is marked `REVIEW_CANDIDATES` in its header and must not be treated
as validated production content. The unresolved Markdown groups findings once per leaf,
shows a direct fixed-baseline-to-candidate diff, explains why the candidate was rejected,
and collapses the other attempted rewrites. Every initial suggestion, correction retry,
and semantic-verification rejection participates in ranking. If either unresolved path
is omitted, it defaults to the supplied path's stem with the other extension. Both
options require `--fixed-output`.

```bash
--fixed-output _tmp/gkr-comics.fixed.yaml \
--unresolved-output _tmp/gkr-comics.unresolved.yaml \
--unresolved-report _tmp/gkr-comics.unresolved.md
```

Potential fixes remain LLM-generated and should be reviewed by diffing the files:

```bash
diff -u gkr-anime.yaml gkr-anime.fixed.yaml
```

## Review scopes

`--llm-scope` provides three mutually exclusive selection modes:

- `candidates`: review only leaves already flagged by deterministic or heuristic checks.
- `content`: review candidates plus clean leaves containing literal prompt text, while excluding pure router leaves.
- `all`: review every leaf, including pure router leaves.

Content scope excludes router-only leaves composed solely of references such as:

```yaml
- "__gkr_anime/anime_action_combo__"
```

Leaves that mix references with literal text are included because their added text can introduce incompatibilities:

```yaml
- "__gkr_anime/anime_action_scene__, preserve every subject in one wide frame"
```

Content scope is available only when LLM review, fix suggestions, and fixed-file output are enabled:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --llm-scope content \
  --suggest-fixes \
  --fixed-output gkr-anime.reviewed.yaml \
  --verbose \
  --api-key-env OPENAI_API_KEY \
  --model your-model
```

The expanded selection uses the normal `--batch-size` mechanism. Clean leaves that pass LLM review remain unchanged in the fixed copy. Newly discovered failures receive a second-pass potential fix and are changed only in the fixed copy.

This differs from `--llm-scope all`: exhaustive scope also sends pure router leaves to the model, while content scope intentionally excludes them. Because scope is a single enumerated option, conflicting selection modes cannot be combined.

## OpenAI semantic review

Secrets are read only from environment variables. Never put API keys in this repository, `rules.yaml`, shell history, or command arguments.

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_MODEL="your-model"
uv run tools/wildcard_linter.py gkr-anime.yaml --llm
```

The default endpoint is `https://api.openai.com/v1/chat/completions`. `--llm` reviews deterministic warning/error candidates. For a true leaf-by-leaf semantic audit, use:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --llm-scope all
```

Full review can make many API calls. Start with one file, inspect costs for the selected model, and use `--batch-size` to tune request size.

## LiteLLM or another compatible endpoint

Set the compatible base URL and the model name accepted by the proxy:

```bash
export OPENAI_API_KEY="your-proxy-key"
export OPENAI_BASE_URL="http://localhost:4000/v1"
export OPENAI_MODEL="gpt-oss-20b"
uv run tools/wildcard_linter.py gkr-anime.yaml --llm --llm-scope all
```

The base URL may also be passed with `--base-url`, and the model with `--model`. Keep the API key in an environment variable. If your organization uses a differently named variable:

```bash
export LITELLM_API_KEY="your-proxy-key"
uv run tools/wildcard_linter.py gkr-anime.yaml --llm \
  --api-key-env LITELLM_API_KEY \
  --base-url http://localhost:4000/v1 \
  --model gpt-oss-20b
```

The compatible server must implement `POST /v1/chat/completions` and return assistant text in `choices[0].message.content`.

## OpenRouter

OpenRouter exposes an OpenAI-compatible Chat Completions endpoint, so it works with the same LLM integration. Keep the OpenRouter key in an environment variable:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
```

Review only leaves selected by deterministic findings:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --api-key-env OPENROUTER_API_KEY \
  --model openai/gpt-oss-20b
```

Perform an exhaustive semantic review of every leaf:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --llm-scope all \
  --api-key-env OPENROUTER_API_KEY \
  --model openai/gpt-oss-20b
```

The base URL can be supplied directly instead of exporting `OPENAI_BASE_URL`:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --api-key-env OPENROUTER_API_KEY \
  --base-url https://openrouter.ai/api/v1 \
  --model openai/gpt-oss-20b
```

OpenRouter model names use provider-qualified slugs. Confirm the current slug in the OpenRouter model catalog before running a large audit. For example:

- `openai/gpt-oss-20b` selects the standard routed model.
- `openai/gpt-oss-20b:free` selects the free variant when it is available and your account is eligible.

Free models may have lower rate limits or less predictable availability. Begin with candidate-only review or a small wildcard file before requesting an exhaustive audit.

OpenRouter supports optional attribution headers such as `HTTP-Referer` and `X-OpenRouter-Title`. The linter does not currently send them because they are not required for authentication or Chat Completions requests.

Do not place an OpenRouter key in this README, `rules.yaml`, the linter source, or command-line arguments. `--api-key-env OPENROUTER_API_KEY` tells the process which environment variable to read without exposing its value.

## Ollama

Ollama is primarily a local model runtime, while LiteLLM is primarily a multi-provider gateway. Both expose an OpenAI-compatible Chat Completions endpoint and can therefore serve as the linter's LLM backend.

Use Ollama directly when you want to run one local model:

```text
wildcard linter → Ollama → local model
```

Place LiteLLM in front of Ollama when you also need centralized routing, provider fallback, accounting, or access to several model services:

```text
wildcard linter → LiteLLM → Ollama, OpenRouter, OpenAI, or other providers
```

Install Ollama separately, download the model, and ensure its server is running:

```bash
ollama pull gpt-oss:20b
ollama serve
```

Ollama's OpenAI-compatible endpoint is normally available at `http://localhost:11434/v1`. Ollama ignores the API-key value, but the linter requires a named environment variable so that all compatible providers use the same authentication path:

```bash
export OLLAMA_API_KEY="ollama"
```

Review deterministic candidates with a local model:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gpt-oss:20b
```

Perform an exhaustive semantic review of every leaf:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --llm-scope all \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gpt-oss:20b
```

Before starting a large audit, confirm that the model is available:

```bash
ollama list
```

An exhaustive review can generate many requests and may take substantially longer on local hardware. Start with candidate-only review, reduce `--batch-size` if the model runs out of memory or context, and increase `--timeout` when local generation is slow:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml \
  --llm \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gpt-oss:20b \
  --batch-size 5 \
  --timeout 300
```

## Practical example

### Obtain the safebooru_general_tags.csv file

```bash
python3 tools/download_danbooru_tags.py \
  --output safebooru_general_tags.csv \
  --min-post-count 100 \
  --verbose
```

### Fix a theme file (with LLM)

```bash
# Delete and re-create the wildcard-linter-cache folder to start a full llm step (otherwise it will use the cache)
rm -rf wildcard-linter-cache
mkdir wildcard-linter-cache

mkdir _tmp; THEME="comics"; OLLAMA_API_KEY="ollama" uv run tools/wildcard_linter.py \
  gkr-$THEME.yaml \
  --verbose \
  --llm \
  --llm-scope content \
  --suggest-fixes \
  --fix-severity both \
  --danbooru-tags safebooru_general_tags.csv \
  --canonical-tag-suggestions \
  --canonical-tag-candidate-count 5 \
  --canonical-tag-style underscore \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --batch-size 15 \
  --verification-batch-size 15 \
  --timeout 300 \
  --llm-cache-dir wildcard-linter-cache \
  --llm-log "_tmp/gkr-$THEME.llm.jsonl" \
  --fixed-output _tmp/gkr-$THEME.fixed.yaml \
  --format markdown \
  --output _tmp/gkr-$THEME.fixed.report.md \
  --color auto \
  --fail-on never
```

To use the linter to also use the embeddings stored in the SQLite DB:

```bash
mkdir _tmp; THEME="comics"; OLLAMA_API_KEY="ollama" uv run tools/wildcard_linter.py \
  gkr-$THEME.yaml \
  --verbose \
  --llm \
  --llm-scope content \
  --suggest-fixes \
  --fix-severity both \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --retrieval auto \
  --canonical-tag-suggestions \
  --canonical-tag-candidate-count 5 \
  --canonical-tag-style underscore \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --batch-size 15 \
  --verification-batch-size 15 \
  --timeout 300 \
  --llm-cache-dir wildcard-linter-cache \
  --llm-log "_tmp/gkr-$THEME.llm.jsonl" \
  --fixed-output _tmp/gkr-$THEME.fixed.yaml \
  --format markdown \
  --output _tmp/gkr-$THEME.fixed.report.md \
  --color auto \
  --fail-on never
```

After completion:

1. Compare the bsase YAML against the `.fixed` YAML and make decisions
2. Review the `.fixed-report.md` for `UNRESOLVED` entries

Or to get a futher "fixed" `fixed.yaml` file:

```bash
mkdir _tmp; THEME="comics"; OLLAMA_API_KEY="ollama" uv run tools/wildcard_linter.py \
  gkr-$THEME.yaml \
  --semantic-duplicates \
  --semantic-duplicate-threshold 0.94 \
  --canonical-literal-review \
  --fix-literal-concepts \
  --verbose \
  --llm \
  --llm-scope content \
  --suggest-fixes \
  --fix-severity both \
  --max-fix-retries 2 \
  --fix-manifest _tmp/gkr-comics-new.fix-manifest.json \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --retrieval auto \
  --canonical-tag-suggestions \
  --canonical-tag-candidate-count 5 \
  --canonical-tag-style underscore \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --batch-size 15 \
  --verification-batch-size 15 \
  --timeout 300 \
  --llm-cache-dir wildcard-linter-cache \
  --llm-log "_tmp/gkr-$THEME.llm.jsonl" \
  --fixed-output _tmp/gkr-$THEME.fixed.yaml \
  --format markdown \
  --output _tmp/gkr-$THEME.fixed.report.md \
  --unresolved-output _tmp/gkr-$THEME.unresolved.yaml \
  --unresolved-report _tmp/gkr-$THEME.unresolved.md
  --color auto \
  --fail-on never
```

### Fix "only" a section in a generated file

Use `--only` (and select your `--only-depth`), `--semantic-duplicates` and `--canonical-literal-review`:

```bash
mkdir _tmp; THEME="comics"; SECTION="spotlight"; OLLAMA_API_KEY="ollama" uv run tools/wildcard_linter.py \
  gkr-$THEME.yaml \
  --only $SECTION \
  --only-depth 5 \
  --semantic-duplicates \
  --semantic-duplicate-threshold 0.94 \
  --canonical-literal-review \
  --fix-literal-concepts \
  --verbose \
  --llm \
  --llm-scope content \
  --suggest-fixes \
  --fix-severity both \
  --danbooru-tags safebooru_general_tags.classified.csv \
  --danbooru-index safebooru_general_tags.index.sqlite \
  --retrieval auto \
  --canonical-tag-suggestions \
  --canonical-tag-candidate-count 5 \
  --canonical-tag-style underscore \
  --api-key-env OLLAMA_API_KEY \
  --base-url http://localhost:11434/v1 \
  --model gemma4:cloud \
  --batch-size 15 \
  --verification-batch-size 15 \
  --timeout 300 \
  --llm-cache-dir wildcard-linter-cache \
  --llm-log "_tmp/gkr-$THEME.$SECTION.llm.jsonl" \
  --fixed-output _tmp/gkr-$THEME.$SECTION.fixed.yaml \
  --format markdown \
  --output _tmp/gkr-$THEME.$SECTION.fixed.report.md \
  --color auto \
  --fail-on never
```

After review, you can add `--fix-literal-concepts`

## Analyze a details.md file

```bash
## 1. Generate the details.md file
# In the folder with all our PNGS, move the files and generate the md file

uv run --with pillow <TOOL_PATH>/theme_organizer.py *.png -m --details details.md

## 2. Analyse the md file

uv run <TOOL_PATH>/wildcard_linter.py \
  --validate-post-prompts details.md \
  --format markdown \
  --output post-prompt-audit.md \
  --annotated-details details.audited.md \
  --fail-on warning
```

## Rules

[`rules.yaml`](rules.yaml) contains reusable cross-theme patterns. [`tags-rules.yaml`](tags-rules.yaml) contains constraints applied only to files whose header declares `MODE: tags`: sequential-format rejection, tag-phrase length, sentence-connector, and emphasis-count checks. Missing mode declarations default to narrative, matching `prompt.md`.

`route_motifs` entries in `rules.yaml` detect overrepresented concrete objects through configured roots such as `random`. The linter calculates the exact probability of a motif appearing through uniformly selected nested routes, including repeated references, and warns when it exceeds the configured threshold.

`namespace_policies` contains theme-specific, machine-checkable contracts: recursively excluded output families, expanded route budgets, forbidden content, and category-scoped content. General authoring principles remain in `prompt.md`; concrete category names and limits belong here. Route budgets are calculated after recursive expansion and report p90, maximum size, and the probability of exceeding the configured item or word ceiling.

Deterministic errors identify objective failures such as forbidden filler, unresolved markers, missing references, cycles, tags-mode sequential content, and camera/format conflicts. Semantic or heuristic patterns are warnings because surrounding visible evidence can make a matched phrase valid.

Representability warnings identify interpretive modifiers (`familiar`, `eccentric`, `historical`, `recurring`), unspecified `impossible` or `looming` claims, time-dependent state transitions, and ambiguous `crashing` shorthand. They are candidates rather than blanket `-ing` bans: directly visible poses and states such as holding, kneeling, glowing, and floating remain valid. When automatic fixing targets one of these warnings, the accepted rewrite must remove the triggering shorthand and preserve only a stable, concrete visible state.

The `underspecified_specificity` warning similarly rejects hyphenated shorthand such as `region-specific`, `setting-specific`, `mission-specific`, `sport-specific`, `material-specific`, and `class-specific`. A fix must remove the label and retain only concrete visual evidence already present; it may not invent clothing, tools, materials, architecture, or equipment to fill the gap. Plain uses of the word `specific` are not matched by this rule.

In narrative mode, sequence-related warnings are suppressed when a leaf explicitly declares panels, a page, storyboard, diptych, triptych, contact sheet, sequence, or spread. In tags mode those formats are errors; only single-image rendering signatures such as panel-border framing are allowed.

Use a custom rule file with:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --rules path/to/rules.yaml
```

Override the tags-mode constraints independently with:

```bash
uv run tools/wildcard_linter.py gkr-anime.yaml --tags-rules path/to/tags-rules.yaml
```

## Recommended workflow

```text
deterministic full-file scan
        ↓
LLM review of candidates or all leaves
        ↓
human approval and manual corrections
        ↓
deterministic scan again
```

The tool intentionally does not apply LLM rewrites automatically. JSON output includes stable leaf IDs and suggested rewrites for a separate review process.

## Exit codes

- `0`: checks satisfied at the configured `--fail-on` level
- `1`: lint findings reached the configured failure level
- `2`: configuration, file, YAML, or LLM transport failure

## Tests

Run the standard-library test suite from `gkr-wildcards`:

```bash
uv run tools/tests/test_wildcard_linter.py
```
