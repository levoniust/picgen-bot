# AGENTS.md - notes for AI coding agents (and humans) working on PIC_GEN

PIC_GEN is a Discord bot that makes pictures on the user's own ComfyUI. Keep it small, dependency-light and easy to self-host.

## Layout

| Path | What |
|---|---|
| `picgen/__main__.py` | entry point: token (env `DISCORD_TOKEN` or file `PICGEN_TOKEN_FILE`), data folder (`PICGEN_DATA`), waits for DNS, runs the bot |
| `picgen/config.py` | `DEFAULTS` (every setting, documented in README), `Settings` (hot reload of `settings.json` by mtime, invalid file = keep last good), `validate()` |
| `picgen/bot.py` | Discord only: slash commands, `PgButton` (one `DynamicItem` for all buttons), `EditModal`, posting results, error reporting |
| `picgen/engine.py` | everything except Discord: `Job` (on disk in `jobs/<id>/`), the one-at-a-time queue (`in_line`), generate / edit / vary / upscale |
| `picgen/workflows.py` | ComfyUI API workflows for Qwen Image 2.1; the output node is always `"out"` |
| `picgen/comfy.py` | minimal ComfyUI HTTP client (upload to temp, `/prompt`, poll `/history`, `/view`, delete history) |
| `picgen/llm.py` | optional prompt helper (OpenAI-compatible chat API) + lenient `extract_prompt` |
| `picgen/images.py` | sizes, grids, metadata-free PNG, JPEG under a byte limit |
| `picgen/selftest.py` | end-to-end run against a real ComfyUI, no Discord |
| `comfyui_nodes/picgen_nodes` | the ComfyUI custom node *PicGenVariationNoise* (users copy it into ComfyUI) |
| `tests/` | pytest: pure logic, the node (needs torch, skipped otherwise), simulated Discord flows with a fake ComfyUI |

## Rules

- **Never commit secrets or personal data:** no tokens, no real IPs/hostnames, no user names. Examples use `192.168.1.10` / `127.0.0.1`.
- **Keep `bot.py` free of generation logic and `engine.py` free of Discord.** New features: engine method + bot handler + test in `tests/test_bot_flows.py`.
- **Every setting lives in `config.DEFAULTS`**, is validated in `validate()` if a bad value could break things, and is documented in the README settings table. Defaults must work for a stranger (no assumptions about one particular LLM server or proxy).
- **Buttons:** custom ID format `pg|<action>|<job id hex>|<picture index>` (Discord limit 100 chars), handled in `PicGenBot.on_button`. Buttons must keep working after a restart, so all state goes into the job folder, never into memory.
- **A modal must be the first response to an interaction** (no `defer` before `send_modal`).
- Every other handler goes through `PicGenBot.guarded()` (role check, "thinking...", error message). Show users `UserError`/`ComfyError` text as-is; anything else is logged with a traceback and shown as a short error.
- **One ComfyUI job per picture.** Batches of 4 at 1024 px run out of memory on 12 GB GPUs. Identical node inputs let ComfyUI cache the models and prompt encoding between the pictures of a grid.
- **Never interrupt or restart ComfyUI from the bot**; it is shared with other users.
- Pictures go to ComfyUI through `/upload/image` with `type=temp` and come back from `PreviewImage`; nothing should pile up in ComfyUI's input/output folders. Always `images.clean_png()` what comes back (ComfyUI embeds the workflow in PNG text chunks).
- Sampling is positive-only (`BasicGuider`, cfg 1) because the Turbo LoRA runs at cfg 1; don't add negative-prompt encoding back.
- Variations use the variation-noise node, not img2img: img2img with 8 turbo steps looks grainy.
- User-visible text (Discord messages, README) is plain and short; times are honest estimates.
- Pin dependency versions in `requirements.txt`; prefer releases that are at least a few weeks old.

## Testing

```sh
pip install -r requirements.txt pytest
python -m pytest -q tests                      # fast, no GPU, no Discord
pip install torch --index-url https://download.pytorch.org/whl/cpu   # optional: also tests the ComfyUI node
python -m picgen.selftest /tmp/st --data ./data  # needs a real ComfyUI (comfy_url in data/settings.json)
```

The selftest makes real pictures (about 5 minutes on an RTX 3060) and writes them plus `results.txt` to the output folder; look at them, don't just trust "OK".
Discord itself can't be driven by a bot account; `tests/test_bot_flows.py` covers the handlers with fake interactions, and a human must click through once after UI changes.

## Releasing

Users (and TrueNAS installs) follow `ghcr.io/levoniust/picgen-bot:latest`, which only moves on releases. To release:
1. Make sure `main` is green (tests workflow) and the change was tried with `picgen.selftest` and, for UI changes, in Discord.
2. Bump `__version__` in `picgen/__init__.py` (semver: fix = patch, new feature/setting = minor, breaking settings change = major).
3. Add a `## x.y.z` section at the top of `CHANGELOG.md` (user-facing, short); it becomes the GitHub release notes.
4. Commit, then `git tag vx.y.z && git push origin main vx.y.z`. The `release` workflow tests, checks the tag matches
   `__version__`, pushes the image (`x.y.z`, `x.y`, `latest`) and creates the GitHub release.
Never move `latest` by hand and never re-use a tag.

## Style

Python 3.12+, standard library + discord.py + aiohttp + pillow only. Async everywhere in the bot. Short docstrings on modules, classes and non-obvious functions; comments explain *why*. Line length ~150.
