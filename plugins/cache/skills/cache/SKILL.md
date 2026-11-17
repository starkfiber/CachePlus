---
name: cache
description: Inspect local storage and run explicitly rebuildable builds, tests, downloads or previews in a guarded disposable cache. Use when the user asks for Cache, /cache, less Codex storage pressure, or safe local cache reclaim. Never treat chats, source, Git or unique data as disposable.
---

# Cache

Use the bundled `scripts/cachectl.py` from this plugin root. It runs locally and sends nothing to a remote service.

1. Run `python3 scripts/cachectl.py status`. Report physical free space and whether a dedicated cache volume is mounted. If no volume is configured, guide the user through choosing an existing mounted cache volume, then run `python3 scripts/cachectl.py configure VOLUME` with the selected path. Do not create or format a volume without separate authorization.
2. For an existing path, run `python3 scripts/cachectl.py assess PATH`. Its classification is only a review aid. Inspect actual contents, active handles and regeneration path before changing anything.
3. When a command's outputs are explicitly rebuildable, run `python3 scripts/cachectl.py run --disposable LABEL -- COMMAND ...`. Reuse a stable label. The runner supplies temporary, npm, pip, uv and Cargo cache paths, checks a physical free-space floor and a bank size cap. Point tools that ignore these paths at the job slot explicitly, then confirm where they wrote.
4. To reclaim completed Cache jobs, run `python3 scripts/cachectl.py reclaim` to preview exact jobs. If the user authorized reclaiming those jobs, run `python3 scripts/cachectl.py reclaim --yes`. Report measured physical free space afterward.

Keep Codex chats/history/index, installed plugins, source, Git, credentials, project documents, app state and sole copies out of Cache. Never move macOS swap or `CODEX_HOME`. The cache volume may share the physical disk; a size cap is not a guarantee that other apps cannot fill it. The runner requires an explicitly configured **mounted** dedicated volume. It will not create a volume or choose a default home folder. `configure` saves the selected mount under `~/.config/cache/config.json`; environment settings can override the soft limits. A real filesystem quota must be configured separately if needed. Windows execution is unsupported in this release; status and assessment can still be used where Python is available.

The plugin root is two directories above this skill file. If working outside it, use the installed absolute script path. Follow the user's scope and keep private material local.
