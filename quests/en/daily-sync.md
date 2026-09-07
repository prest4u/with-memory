[English](daily-sync.md) · [中文](../每日同步任务.md)

# Quest: trusted memory sync

Read `skills/严格技能.md`. Search current authorized scope first. A normal harness submits candidates and never directly activates, deprecates, reviews, or purges facts.

For harvesting: list assigned sources, begin a run, read only returned changed files inside the canonical root, distill 1–3 sentences (maximum 1,200 characters), submit with a stable `submission_uid` and source locator, then complete the run only after success. Never copy source text, secrets, or minor-performance records.

Describe possible conflicts in the candidate. The owner runs local `review` and chooses supersession atomically. Do not say a candidate became a fact unless review accepted it.

Local unattended maintenance can use `harvest begin SOURCE_UID`, then submit candidates with stable submission IDs, and only after processing every returned file use `harvest complete RUN_UID --cursor CURSOR`. Interrupted or truncated runs redeliver unacknowledged files. Never complete a run with unprocessed files.

`source scan` and plain `sync` perform indexing and immediately acknowledge the scan; they do not distill memories. Do not run them before harvesting. Repair Obsidian using `sync --projection-only` or authorized `memory_sync(projection_only=true)` so pending sessions remain available.

Report queries, changed sources, candidate UIDs/states, conflicts, truncated/stale runs, pending count, and projection state. If `committed=true, projection=dirty`, repair the projection; do not repeat the database write.
