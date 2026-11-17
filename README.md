# Cache

**A disposable workspace for Codex builds.** Keep the work you need. Give rebuildable outputs a place with a limit and a clean exit.

Cache is a local Codex plugin. It assesses a path, runs an explicitly disposable command in a guarded job slot, and previews exact completed jobs before reclaim. It does not upload files, move chats, rewrite Git history, or relocate macOS swap.

## What it solves

Builds, dependency caches, test runs and generated previews can crowd a development machine. Cache routes the parts that can be rebuilt into a separate mounted volume. It checks free space while the command runs and stops the job if the buffer or configured bank limit is reached. The assessor is deliberately cautious: a folder named `build` is not automatically safe to remove.

## Start

1. Install the [Codex marketplace](https://developers.openai.com/plugins/build/plugins): `codex plugin marketplace add starkfiber/Cache`, then `codex plugin add cache@cache`. A local executor is required. If the app does not show the new skill immediately, restart it.
2. On macOS or Linux, prepare a dedicated cache volume. Configure it once: `python3 plugins/cache/scripts/cachectl.py configure /path/to/mounted-volume` (for example, `/Volumes/BuildCache` on macOS). The runner refuses to write if the configured mount is absent. It does not create or format a volume.
3. Type `/cache` and select the enabled **Cache** skill from the slash list, or mention `$cache:cache` in Codex. The desktop client lists enabled skills alongside slash commands; the skill is also available through `$` mention.

CLI examples from the repository root:

```sh
python3 plugins/cache/scripts/cachectl.py status
python3 plugins/cache/scripts/cachectl.py assess "$HOME/Projects/example/build"
python3 plugins/cache/scripts/cachectl.py run --disposable my-app -- npm test
python3 plugins/cache/scripts/cachectl.py reclaim
```

`reclaim --yes` applies only to the completed owned jobs shown in the preview. It never sweeps an arbitrary directory.

## Limits

- macOS is tested. Linux uses the bundled mounted-volume runner but has not had a full end-to-end release test. Windows job execution is not supported yet.
- The 150 GB value on the original Mac is an APFS volume quota, not a reserved SSD partition. Other computers need their own volume and optional filesystem quota. The bundled runner's `CACHE_LIMIT_GB` is a **soft runtime cap**, not a filesystem quota.
- Environment variables cover common tool caches. Project-local outputs may still go to the source tree unless the build tool receives an explicit output path.
- Cache cannot control Codex's persistent session storage or the operating system's swap.
- A single device is not a backup. Cache is for reproducible data only.

## Product status

Version 0.2 is a public alpha. The source and local marketplace are available here; the plugin has not been submitted to the universal Plugins Directory.

## Development

The plugin is self-contained under `plugins/cache/`, with a repository marketplace at `.agents/plugins/marketplace.json`. It uses Python's standard library and local system utilities. The same bundled bank runs on configured computers. No remote service is required.
