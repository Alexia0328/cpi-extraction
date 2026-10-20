# AI and decision log

I used Codex throughout the project, mostly for focused implementation, debugging, and review tasks. Codex helped me test those decisions and turn them into working code. The prompts below are shortened for readability.

## Representative prompts

1. "Read the assignment carefully and list the required deliverables, constraints, and anything that needs clarification. Do not start implementing yet."

2. "Implement the fetch and cache part first. Use the official feed, cache the raw HTML, retry only transient errors, and avoid fetching an accepted URL again. Add focused tests for retry, cache, and non-retryable errors before moving to extraction."

3. "Run the frozen eval and inspect the failed cases. Compare prediction, gold, and source evidence field by field. Do not change the cases or labels. Find the shared cause and make the smallest fix that handles both failures."

4. "The local model path is taking several seconds. Profile where the time goes. Keep all current validation checks, but see whether clear CPI wording can use a deterministic fast path and reserve Qwen for ambiguous cases. Rerun the same eval after the change."

## Suggestions I accepted

Codex suggested changing `methodology_change_in_effect` from a boolean to a four-state value. I checked the release examples and found that one document could describe a change taking effect now and another change planned for later. I accepted the suggestion because the boolean would lose that distinction, while the enum still fits within the eight-field limit.

After profiling, Codex suggested moving Qwen off the normal path and using deterministic extraction when all eight fields could be tied to the reference month and verified. I accepted this only after rerunning the unchanged eval. It reduced known-format processing from seconds to single-digit milliseconds, while ambiguous wording still goes through Qwen and the same validator.

I also accepted pinning every eval page with its URL, retrieval time, and SHA-256. This made the as-published policy reproducible and prevented predictions and gold labels from silently using different page versions.

## Suggestions I overrode

The initial design used a paid OpenAI API model. I replaced it with local `qwen2.5:1.5b-instruct-q4_K_M`. The assignment allows a small local model, and the frozen eval let me check whether the smaller option was good enough. This also removed API credentials and usage cost from the project.

Codex suggested using Statistics Canada's current time-series database to help create gold labels. I rejected that approach because the database may contain later revisions. I used the cached release documents as the source of truth instead.

I also decided not to put broader market or policy interpretations into the eight-field schema. Labels such as "hawkish" are useful downstream, but they do not have one clearly defined correct answer in the release. I kept the scored schema factual and left those ideas for a future reporting layer.
