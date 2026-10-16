# PIC_GEN

A Midjourney-style Discord bot for **your own ComfyUI**. People type `/imagine`, get a 2x2 grid, and click buttons to get
full-size pictures, close variations, AI edits and 4K upscales. Everything runs on your GPU; nothing goes to a cloud service.

It uses **Qwen Image 2.1** (text-to-image and editing in one model) with its 8-step Turbo LoRA, and **4x-UltraSharpV2** for upscaling.
An optional LLM (any OpenAI-compatible chat API, e.g. llama.cpp, Ollama, LM Studio) turns short ideas into detailed prompts.

| `/imagine a cozy bookshop at night in the rain, chalkboard sign that says 'OPEN LATE'` | ✏️ Edit → "make it daytime with sunshine" |
|---|---|
| ![grid](docs/example-grid.jpg) | ![edit grid](docs/example-edit.jpg) |

## What users get

| Command / button | What it does |
|---|---|
| `/imagine prompt [aspect] [raw]` | 4 pictures in a grid (1024 px each). `raw:True` skips the prompt helper. |
| **U1-U4** | that picture full size |
| **V1-V4** | 4 close variations of that picture |
| 🔄 | 4 new pictures from the same prompt |
| **Vary (Subtle) / Vary (Strong)** | under a full-size picture: 4 variations, very close / more different |
| **✏️ Edit** | under a full-size picture: a pop-up asks what to change, then 4 edited versions in a grid (edit again as often as you like) |
| **⬆️ 2x / ⬆️ 4K** | AI upscale (4K = 3840 px on the long side), e.g. for a desktop background |
| `/edit image prompt [raw]` | upload a picture and say what to change: 4 versions |
| `/upscale image [size]` | upscale your own picture |
| `/help` | short help |

Works in server channels, in DMs with the bot, and as a **user install** (in any DM/group chat). Requests run one at a time
(the bot shows your place in line). Buttons keep working for 30 days (configurable), also across restarts.

Typical times on an RTX 3060 12 GB: grid ~50 s, edit grid ~70 s, upscale ~40 s.

## Requirements

1. **ComfyUI** (recent; tested with 0.39) reachable from where the bot runs, with these model files:

   | Folder | File | Source |
   |---|---|---|
   | `models/diffusion_models` | `qwen_image_2.1_int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
   | `models/text_encoders` | `qwen3vl_8b_int8_convrot.safetensors` | same |
   | `models/vae` | `qwen_image_2.1_vae_bf16.safetensors` | same |
   | `models/loras` | `qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors` | same (Turbo LoRA, 8 steps) |
   | `models/upscale_models` | `4x-UltraSharpV2.safetensors` | [Kim2091/UltraSharpV2](https://huggingface.co/Kim2091/UltraSharpV2) (CC BY-NC-SA 4.0) |

   Qwen Image 2.1 is under the Qwen Research License (non-commercial). File names are settings, so other quantizations work too.

2. **The PIC_GEN ComfyUI node** (for variations): copy `comfyui_nodes/picgen_nodes` into ComfyUI's `custom_nodes` folder and
   restart ComfyUI. It adds one node, *PIC_GEN Variation Noise*. Everything except V/Vary works without it.

3. **A Discord application** (free):
   1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application → name it.
   2. *Bot* → Reset Token → copy the token (keep it secret). No privileged intents are needed.
   3. *Installation* → enable **Guild Install** (scopes `applications.commands`, `bot`; permissions *Send Messages*,
      *Attach Files*, *Embed Links*, *Use Application Commands*) and, if you want, **User Install** (scope `applications.commands`).
   4. Use the install link to add the bot to your server (and/or your account).

4. Optional: an OpenAI-compatible LLM endpoint for the prompt helper.

## Run it

### Docker (recommended)

Ready-made images: `ghcr.io/levoniust/picgen-bot:latest` (newest release) or a fixed version like `:1.0.0`.

```sh
mkdir picgen && cd picgen
curl -O https://raw.githubusercontent.com/levoniust/picgen-bot/main/compose.example.yml
printf '%s' 'YOUR_BOT_TOKEN' > discord_token && chmod 600 discord_token
mkdir data
docker compose -f compose.example.yml up -d
```

**Updating:** `docker compose -f compose.example.yml pull && docker compose -f compose.example.yml up -d`. `latest` only moves
on releases (see [Releases](https://github.com/levoniust/picgen-bot/releases)), so tools that watch for new images (TrueNAS
Apps, Watchtower, Portainer...) offer an update exactly when a new release is out.

**TrueNAS SCALE:** Apps → Discover → ⋮ → *Install via YAML*, paste the compose service (with host paths for `data` and the
token file). TrueNAS then shows an *Update* button on the app whenever a new release is published.

The first start writes `data/settings.json` with all defaults. Set at least `comfy_url` (e.g. `http://192.168.1.10:8188`;
`127.0.0.1` inside a container is the container itself). The bot reloads the file automatically, no restart needed.

### Plain Python (3.12+)

```sh
pip install -r requirements.txt
export DISCORD_TOKEN=...    # or PICGEN_TOKEN_FILE=/path/to/file
export PICGEN_DATA=./data
python -m picgen
```

### Test without Discord

```sh
python -m picgen.selftest /tmp/picgen-test --data ./data
```

It runs every step (imagine, raw 16:9, variations, an edit grid, 2x and 4K upscales) against your ComfyUI and writes the
pictures plus `results.txt` to the folder. Unit tests: `pip install pytest && python -m pytest tests`.

## Settings (`data/settings.json`)

| Key | Default | Meaning |
|---|---|---|
| `comfy_url` | `http://127.0.0.1:8188` | your ComfyUI |
| `models.*` | see above | file names in ComfyUI (`lora: ""` = no LoRA, then raise `steps` to ~25) |
| `steps`, `sampler`, `scheduler` | 8, euler, simple | sampling (cfg is always 1) |
| `size` | 1024 | picture size (area; the aspect ratio picks the shape) |
| `grid_count` | 4 | pictures per grid (1-4) |
| `quality_suffix` / `edit_suffix` | ... | hidden text added to every prompt / edit |
| `vary_grid` / `vary_subtle` / `vary_strong` | 0.3 / 0.15 / 0.45 | how different variations are (0 = same, 1 = new picture) |
| `max_upscale_side` | 3840 | size of "4K" |
| `llm.enabled`, `llm.url`, `llm.model`, `llm.api_key` | off | the prompt helper (OpenAI-compatible `/v1/chat/completions` URL) |
| `llm.extra_body` | `{}` | extra JSON fields for the LLM request, e.g. `{"chat_template_kwargs": {"reasoning_effort": "low"}}` for llama.cpp reasoning models |
| `llm.user_header` | "" | optional HTTP header that carries "Discord: <name>" (e.g. for per-user logging in a proxy) |
| `llm.rewrite_edits` | true | light spelling/clarity touch-up of edit instructions |
| `llm.suggest` | true | after `/imagine`, ask the helper for 4 prompt changes (lighting, composition, two free) shown as buttons under the grid |
| `llm.imagine_system_prompt`, `llm.edit_system_prompt`, `llm.suggest_system_prompt` | built in | the helper's instructions |
| `allowed_role_ids` | `[]` | Discord role IDs allowed to use the bot in servers (empty = everyone) |
| `job_days` | 30 | how long buttons keep working |
| `max_queue` | 0 | refuse new requests while this many are waiting (0 = no limit) |
| `save_pictures`, `pictures_hint` | true, "" | keep every picture in `data/pictures/<date>/`; `pictures_hint` = how users reach that folder (shown under upscales) |

Invalid settings are refused (the bot keeps the last good ones and logs why).

## Good to know

- **Content:** there is no content filter. What gets made depends on your models, LoRAs and LLM. You are responsible for what
  your bot posts; Discord requires adult content to stay in age-restricted channels.
- **Long queues:** Discord lets a bot answer an interaction for 15 minutes. If a request waits longer than that, the bot posts
  the result as a normal channel message instead (only possible where the bot is a member of the server).
- **The ✏️ Edit pop-up** belongs to the running bot; if the bot restarts while a pop-up is open, just click Edit again.
- **One GPU, many users:** jobs run one at a time. Other ComfyUI users (e.g. Open WebUI) share the same queue in ComfyUI.

## Troubleshooting

| Message / symptom | Fix |
|---|---|
| "The application did not respond" | the bot is not running or cannot reach Discord; check its log |
| "Variations need the PIC_GEN node" | copy `comfyui_nodes/picgen_nodes` into ComfyUI's `custom_nodes`, restart ComfyUI |
| "ComfyUI refused the job: ... not in list" | a model file name in `settings.json` doesn't match a file in ComfyUI |
| "Could not reach ComfyUI" | wrong `comfy_url` (inside Docker, `127.0.0.1` is the container itself) |
| "The job was stopped in ComfyUI" | someone pressed Cancel/Interrupt in ComfyUI; try again |
| "prompt helper unavailable, used your words" | the LLM is off or unreachable; pictures still work |
| `settings.json not loaded: ...` in the log | a typo or invalid value; the previous settings stay active until it is fixed |

## How it works

- `picgen/workflows.py` builds ComfyUI API workflows: positive-only sampling (`BasicGuider`, cfg 1), `TextEncodeQwenImage21`
  for prompts and edit pictures, `RandomNoise` or *PIC_GEN Variation Noise* (spherical mix of two seeds' noise, like
  "variation seed" in other UIs), and the upscale model plus a lanczos resize to the exact size.
- One ComfyUI job per picture (a batch of 4 at 1024 px does not fit 12 GB), so ComfyUI caches the models and the prompt encoding
  between the 4 pictures. Other ComfyUI users keep their normal place in ComfyUI's queue; the bot never interrupts anything.
- Pictures are uploaded to ComfyUI's temp folder and results come back from preview nodes, so nothing piles up in ComfyUI's
  input/output folders. Saved PNGs are re-encoded without ComfyUI's embedded workflow.
- Jobs (`data/jobs/<id>/`) remember seed, prompt and source picture, so buttons can redo or vary exactly what was made.

## Development

Changes are listed in [CHANGELOG.md](CHANGELOG.md). See [AGENTS.md](AGENTS.md) for the code layout, rules, tests and how to release (written for AI coding agents, useful for humans too).
Pull requests welcome: run `python -m pytest tests` and, if you have ComfyUI, `python -m picgen.selftest`.

## License

MIT (this code). Model licenses are their own (see above).
