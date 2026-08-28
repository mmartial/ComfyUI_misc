# Global Wildcard Image-Generation Rule

This rule applies to every wildcard YAML file in this folder. Theme-specific header rules refine it and may override it only where they say so explicitly.

## Mode

Every theme file declares one authoring mode in its header, right next to the "Read and apply ./prompt.md" line:

- `MODE: narrative` — leaves are dense diffusion-ready prose clauses. No weight syntax, no negative prompts. This is the default when a file states no mode.
- `MODE: tags` — leaves are compact comma-separated Danbooru/booru-style tag phrases with optional `(term:weight)` emphasis. See **Tags Mode** below for its own prompt-language rules, which replace the Narrative-mode "Prompt language" section for that file only.

Everything in this document applies to both modes except where a section says otherwise. The two modes exist because the downstream LLM rewrite step (`01z1`/`01z2`/`01z3`) reads either style of input and adapts its output accordingly — the wildcard file only needs to commit to one authoring style per theme.

## Primary goal

Generate visually engaging, theme-faithful images that a person would intentionally choose to create or view. Engaging may mean spectacular, adventurous, beautiful, humorous, mysterious, frightening, tragic, strange, or quietly observational, as appropriate to the theme. Prefer a decisive, visually memorable moment, but allow posed subjects, landscapes, artifacts, ordinary life, setup, and aftermath when they remain visually worthwhile.

## Absolute visual-conversion rule

If an idea cannot be represented visually in the requested image, convert it into observable evidence or remove it.

- Express roles, relationships, status, emotion, history, institutions, technology, and worldbuilding through visible anatomy, posture, gesture, eyelines, clothing, tools, objects, damage, architecture, vehicles, ecology, documents, interfaces, barriers, or spatial arrangement.
- Familiar theme terms may remain as useful anchors, but they do not replace visible evidence. A specialized term such as `cyber samurai` must be supported by details that distinguish it visually within that theme.
- Do not depend on motives, backstory, causality, occupation, ownership, recognition, sound, or off-frame events to make the central idea understandable.
- Do not rely on interpretive adjectives such as `familiar`, `eccentric`, `historical`, `impossible`, or `recurring` to carry visual meaning. Keep such a word only when the same leaf also states the concrete evidence that makes it visible; otherwise remove it. For example, replace familiarity with repeated route markers or worn everyday use already supplied, eccentricity with specified clothing/objects/pose, historical with a named period and material details, impossible with the exact violated geometry or scale, and recurring with visibly repeated matching forms in the same image.
- Do not use hyphenated `*-specific` shorthand such as `region-specific`, `setting-specific`, `mission-specific`, `sport-specific`, `material-specific`, or `class-specific`. It does not state what is visible. Remove the shorthand and retain only named clothing, tools, materials, architecture, anatomy, equipment, or props already supplied; do not invent replacement evidence.
- Replace `looming` with explicit scale and placement such as a silhouette filling the upper frame, a structure rising above small foreground figures, or a low-angle view—but only when those facts are already supplied or required by the concept. Otherwise omit it.
- Grammatical `-ing` is not itself a visual failure. A continuing pose, contact, or material state such as `holding`, `kneeling`, `glowing`, `floating`, or waves striking rocks can be shown at one instant. A transition that depends on change across time—`becoming`, `transforming`, `fragmenting into`, `shifting between`, or `dissolving into`—must instead describe one stable visible state or simultaneous evidence. Prefer `multiple separated comic silhouettes` to `fragmenting into multiple comic silhouettes`.
- Keep readable text very limited. Prefer symbols, layout, color, physical state, and short labels.
- Do not add invented geopolitics. Use real regional specificity only when material culture, architecture, transport, clothing, climate, or public space makes it visually relevant.

## Single-image construction

1. Describe one point in time unless the output is explicitly a comic page, manga page, storyboard, diptych, split image, or contact sheet.
2. Give the image one dominant focal subject or focal idea. The focus may be a person, pair, ensemble, creature, object, vehicle, structure, or landscape.
3. Prefer no more than two principal characters. A crowd needs a simple dominant action and foreground anchor.
4. Avoid excessive simultaneous actions. Favor one readable pose, interaction, confrontation, rescue, escape, discovery, transformation state, or environmental event.
5. When equipment or bodily action matters, prefer enough of the figure to show it clearly. Do not require unobstructed hands, scale references, contrast devices, lighting, or camera instructions unless the concept needs them.
6. Composition and lighting are optional. Use `random_composition` sparingly rather than forcing deliberate framing into every route.
7. Before-and-after, memory, prediction, parallel location, and sequential transformation require visibly separated panels and should be uncommon outside sequential-art themes.
8. Preserve the scene's defining visual relationship. When the central idea depends on a match, contrast, repetition, contradiction, exchange, concealment, or spatial relationship between visible elements, state that relationship explicitly and compactly. The scene must not become generic when its objects are read separately.

## Worldbuilding

- Worldbuilding must influence the generated image through visible places, materials, bodies, behavior, infrastructure, ecology, transport, tools, or artifacts.
- An environment may be the primary subject.
- Politics, institutions, inequality, labor, protest, displacement, policing, and war are supporting material only when required by the theme and visually staged. Avoid exposition-led or purely bureaucratic scenes unless their visual construction is unusually strong.
- Contemporary political conflict and imagery tied to real tragedy should be limited and used only when the theme explicitly needs it.
- Keep cultural and regional details materially specific. Avoid unsupported broad regional labels and arbitrary cultural mixing; users can combine theme wildcards when they want a hybrid.

## Route and leaf requirements

- Authored scene, combo, and spotlight leaves should normally identify a compelling visible subject, action or pose, and setting or contextual evidence. A viewer should understand the central visual idea without external explanation.
- Modular component leaves may remain partial because downstream combinations and an LLM will complete them.
- Prefer component composition and useful randomness. Preserve curated `spotlight`, coherent `scene`, production-ready `combo`, and broad `random` routes.
- Every theme should expose character, environment, action-scene, iconic/spotlight, and relevant design-output coverage. Include `random_action_scene` or an equivalent public route.
- `spotlight` represents the most recognizable and desirable visual examples of the theme.
- `random` should emphasize scenes, combos, spotlights, and iconic theme content. Limit technical sheets and weak institutional/documentary material in its probability path.
- Keep output families structurally separate. A theme may expose `random_design` for production sheets and `random_complex` for ensembles, spreads, and other deliberately dense outputs; neither route is implicitly part of `random`.
- A normal scene composite should select one subject or relationship, one setting, and only the minimum supporting style or composition. Do not stack multiple independently complete scenes merely to increase variety.
- Treat prompt budgets as route-level constraints after recursive wildcard expansion, not as per-leaf limits. Prefer a coherent selection within the route's budget over retaining every compatible detail; linters may initially report budget excesses as warnings.
- Practical sheets for characters, creatures, props, vehicles, and environments may use neutral views and production annotations rather than narrative staging.
- Composite compatibility is best effort. Avoid obviously contradictory cross-pool combinations.

## Prompt language (Narrative Mode)

Applies only to files declaring `MODE: narrative`.

- Write compact visual language suitable for LLM expansion; complete prose is optional.
- Do not use generic quality fillers such as `masterpiece`, `best quality`, `highly detailed`, or `epic`.
- Prompt weights such as `(subject:1.2)` are allowed only for genuinely critical elements and should be uncommon.
- Do not add negative prompts to these wildcard files.
- A concept that would require sequential panels to show (before/after, memory, prediction, parallel location) uses an explicit multi-panel, storyboard, comic-page, or diptych format per the Single-image construction rules above.

## Prompt language (Tags Mode)

Applies only to files declaring `MODE: tags`. Leaves are comma-separated Danbooru/booru-style tag phrases, not sentences — this replaces the Narrative-mode prompt-language rules above for that file, not the rest of this document.

- Every leaf is a flat, comma-separated list of short tag phrases (1-4 words each). Full sentences and connecting prose (verbs like "is," "while," "as") are the exception, not the rule — used only inside a literal phrase when a relationship genuinely cannot survive being split into separate tags (see Preserve scene-defining relationships and the Comprehension test below).
- When a canonical tag vocabulary or retrieval palette is supplied, treat it as an authoring input rather than an after-the-fact spelling check. Design the visible concept first, retrieve relevant vocabulary candidates, and realize the leaf from those candidates. Do not draft descriptive prose and attempt to canonicalize it afterward.
- Prefer an exact canonical tag whenever it preserves the intended visible concept. An exact normalization or unambiguous alias must use the supplied canonical spelling. Every underscore-form token must be verified by the supplied vocabulary; never invent an underscore tag from plausible words.
- Use a short literal phrase only when the supplied candidates cannot preserve necessary visible meaning, especially a subject/object or spatial relationship. The literal must add information not already expressed by canonical items. Do not retain descriptive padding, standalone decorative adjectives, or a verbose paraphrase of a known tag.
- Order complete tag prompts by visual control: subject count and focal subject first, then defining action or pose, appearance and equipment, setting, composition, lighting, and effects as applicable. Earlier categories may be omitted when a modular leaf supplies only one component.
- Avoid redundant near-synonyms and micro-description. Prefer the smallest set of model-known tags that preserves the requested subject, relationship, and visible theme evidence.
- Weight syntax `(term:weight)` is the normal way to express priority, not a rare exception. Typical range is `0.8`-`1.2`; reserve `1.3`+ and sub-`0.8` for elements that must clearly dominate or recede. Do not weight every item in a leaf — weight only the 1-3 items that matter most, and leave the rest unweighted.
- Do not use generic quality fillers such as `masterpiece`, `best quality`, `highly detailed`, or `epic`.
- Describe a rendering medium by its visible marks rather than an ambiguous physical tool name when the model tends to literalize that tool. In particular, do not use `brush`, `brushwork`, `brush strokes`, or `dry-brush` as style shorthand. Prefer the intended visible result, such as `bold ink contours`, `varied-width ink lines`, `broken ink texture`, `layered paint texture`, `opaque painted edges`, `soft tonal blending`, or `feathered shadows`. Keep `brush` only when an actual brush should appear in the image.
- Do not add a `negative_prompt` category to these wildcard files; negative prompting is handled elsewhere in the workflow.
- A concept that would require sequential panels or a multi-page layout in Narrative Mode (manga panel grammar, page-by-page storytelling, before/after progressions) has no flat-tag equivalent in an ordinary scene route. Keep such layouts out of `random`. A theme may expose a deliberately selected `random_complex` route for page/spread outputs and a `random_design` route for multi-view production sheets when its machine rules declare those category exemptions. Otherwise keep only the medium's single-image rendering signature (for example, black-and-white ink or screentone) and drop panel, page, sequence, or multi-view content.

## Review tests

Apply stricter review to authored scenes, combos, and spotlights than to modular pools.

1. **Visual test:** Can every essential idea be seen? If not, convert or remove it.
2. **Single-moment test:** Can the image exist at one instant? If not, use a limited explicit multi-panel format or rewrite it.
   - For every action written with `-ing`, ask what a freeze-frame would visibly contain. Keep it when the pose, contact, direction, or material state is directly visible. Rewrite it when understanding requires an earlier state, a later state, or knowledge that change is occurring.
3. **Theme test:** Do the visible elements clearly belong to this file's theme?
4. **Focus test:** Is there one dominant subject or visual idea, with no more than two principal characters unless an ensemble is necessary?
5. **Appeal test:** For authored leaves, would someone plausibly choose to generate or view this image?
6. **Route test:** Does a composite add useful compatible variation rather than meaningless indirection?
7. **Comprehension test:** Without explanatory prose, do the visible subjects, objects, and their stated relationships communicate the scene's defining event, clue, contrast, or theme-specific idea?

If an entry may fail a test and cannot be resolved immediately, prefix its YAML comment with `[VISUAL-REVIEW]`. The marker is temporary, searchable, and must be removed after review; uncertain entries must not silently pass.

## Mandatory exhaustive review procedure

These steps are mandatory whenever a task asks to review, audit, validate, check, inspect, or verify a wildcard file. A summary, spot check, or sampled review does not satisfy the task unless the user explicitly requests sampling.

1. Inspect every leaf individually and apply the Review tests to it. Do not infer that nearby leaves pass because they share a category or pattern.
2. Apply the strictest review to every authored scene, combo, spotlight, and public route. Treat modular component leaves as partial, but still verify that their literal content is concrete, theme-compatible, and safe for downstream composition.
3. For every composite leaf, separate literal text from wildcard references. Resolve the referenced category graph and check that the literal text can coexist with every possible expansion without conflicting subject counts, actions, settings, eras, equipment, scale, composition, camera format, or output format.
4. Flag invisible intent, decisions, backstory, causality, sound, institutional meaning, unspecified reactions, and time progression unless the leaf replaces them with observable evidence or explicitly requests a suitable multi-panel format.
5. Record every definite or uncertain failure with its category, exact leaf text, source line, failed test, and required correction. Do not silently repair, ignore, or generalize individual failures.
6. Report the total number of categories and leaves inspected, distinguish definite failures from uncertain entries, and state any portions that could not be resolved.
7. Do not declare a file compliant while a definite failure or unresolved `[VISUAL-REVIEW]` marker remains.
8. After corrections, repeat the complete audit, resolve composite routes again, validate YAML syntax, and confirm that no definite failures or temporary review markers remain.

During LLM review and rewriting, an interpretive or transition word does not pass merely because a diffusion model might associate it with a visual trope. The leaf must explicitly contain the visible geometry, pose, material, repetition, relative scale, or stable intermediate state. When that evidence is already present, remove redundant abstract wording; when it is absent, omit the unsupported concept rather than inventing replacement facts.
