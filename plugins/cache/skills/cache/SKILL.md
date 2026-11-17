---
name: cache
description: Inspect local storage and run explicitly rebuildable builds, tests, downloads or previews in a guarded disposable cache. Use when the user asks for CachePlus, /cache, less agent storage pressure, or safe local cache reclaim. Never treat chats, source, Git or unique data as disposable.
---

# CachePlus

Use the bundled `scripts/cacheplus.py` from this plugin root. It runs locally and sends nothing to a remote service by itself.

1. Run `python3 scripts/cacheplus.py status` and `python3 scripts/cacheplus.py backends`. Report physical free space and whether the active cache volume is mounted. To add an existing mounted volume, run `python3 scripts/cacheplus.py configure VOLUME --name NAME --kind internal|usb|external|nas|cloud-mount`. Never create or format a volume without separate authorization. For cloud mounts, the user supplies the mount and account; never gather credentials.
2. For an existing path, run `python3 scripts/cacheplus.py assess PATH`. Its classification is only a review aid. Inspect actual contents, active handles and regeneration path before changing anything.
3. When a command's outputs are explicitly rebuildable, run `python3 scripts/cacheplus.py run --disposable LABEL -- COMMAND ...`. Reuse a stable label. The runner supplies temporary, npm, pip, uv and Cargo cache paths, checks a physical free-space floor and a bank size cap. Point tools that ignore these paths at the job slot explicitly, then confirm where they wrote.
4. To reclaim completed jobs, run `python3 scripts/cacheplus.py reclaim` to preview exact jobs. If the user authorized reclaiming those jobs, run `python3 scripts/cacheplus.py reclaim --yes`. Report measured physical free space afterward.

Keep Codex chats/history/index, installed plugins, source, Git, credentials, project documents, app state and sole copies out of Cache. Never move macOS swap or `CODEX_HOME`. The cache volume may share the physical disk; a size cap is not a guarantee that other apps cannot fill it. The runner requires an explicitly configured **mounted** dedicated volume. It will not create a volume or choose a default home folder. `configure` saves the selected mount under `~/.config/cache/config.json`; environment settings can override the soft limits. A real filesystem quota must be configured separately if needed. Windows execution is unsupported in this release; status and assessment can still be used where Python is available.

Use `backends` and `use NAME` to inspect and switch profiles. Remote mounts may disconnect or synchronize data; select them only for rebuildable, non-sensitive work whose destination is authorized. The MCP server and loopback HTTP API expose read-only status, backends and assessment. They do not execute or reclaim jobs. The plugin root is two directories above this skill file. If working outside it, use the installed absolute script path. Follow the user's scope and keep private material local.
