# Global Wildcard Image-Generation Rule

This rule applies to every wildcard YAML file in this folder. Theme-specific header rules refine it and may override it only where they say so explicitly.

## Primary goal

Generate visually engaging, theme-faithful images that a person would intentionally choose to create or view. Engaging may mean spectacular, adventurous, beautiful, humorous, mysterious, frightening, tragic, strange, or quietly observational, as appropriate to the theme. Prefer a decisive, visually memorable moment, but allow posed subjects, landscapes, artifacts, ordinary life, setup, and aftermath when they remain visually worthwhile.

## Absolute visual-conversion rule

If an idea cannot be represented visually in the requested image, convert it into observable evidence or remove it.

- Express roles, relationships, status, emotion, history, institutions, technology, and worldbuilding through visible anatomy, posture, gesture, eyelines, clothing, tools, objects, damage, architecture, vehicles, ecology, documents, interfaces, barriers, or spatial arrangement.
- Familiar theme terms may remain as useful anchors, but they do not replace visible evidence. A specialized term such as `cyber samurai` must be supported by details that distinguish it visually within that theme.
- Do not depend on motives, backstory, causality, occupation, ownership, recognition, sound, or off-frame events to make the central idea understandable.
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
- Practical sheets for characters, creatures, props, vehicles, and environments may use neutral views and production annotations rather than narrative staging.
- Composite compatibility is best effort. Avoid obviously contradictory cross-pool combinations.

## Prompt language

- Write compact visual language suitable for LLM expansion; complete prose is optional.
- Do not use generic quality fillers such as `masterpiece`, `best quality`, `highly detailed`, or `epic`.
- Prompt weights such as `(subject:1.2)` are allowed only for genuinely critical elements and should be uncommon.
- Do not add negative prompts to these wildcard files.

## Review tests

Apply stricter review to authored scenes, combos, and spotlights than to modular pools.

1. **Visual test:** Can every essential idea be seen? If not, convert or remove it.
2. **Single-moment test:** Can the image exist at one instant? If not, use a limited explicit multi-panel format or rewrite it.
3. **Theme test:** Do the visible elements clearly belong to this file's theme?
4. **Focus test:** Is there one dominant subject or visual idea, with no more than two principal characters unless an ensemble is necessary?
5. **Appeal test:** For authored leaves, would someone plausibly choose to generate or view this image?
6. **Route test:** Does a composite add useful compatible variation rather than meaningless indirection?

If an entry may fail a test and cannot be resolved immediately, prefix its YAML comment with `[VISUAL-REVIEW]`. The marker is temporary, searchable, and must be removed after review; uncertain entries must not silently pass.
