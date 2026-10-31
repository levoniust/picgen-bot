# Changelog

## 1.4.0

- `/say` gets a `mood` option (scared, excited, frustrated, whisper, shout...): the voice says it that way. New settings `speech.moods` (the suggestions) and `speech.mood_format` (how the mood is sent, default `[mood] ` in front of the text, for voice servers that read bracket cues).

## 1.3.0

- New `/say text [voice]`: reads text aloud and posts the voice clip. Works with any OpenAI-compatible text-to-speech server; off until you set `speech.enabled` and `speech.url` (new `speech.*` settings). Voice names autocomplete from the server.

## 1.2.0

- Prompt suggestions on every grid, not only the first `/imagine` grid:
  - Edit grids get 4 suggested follow-up edits of the same picture (new setting `llm.suggest_edit_system_prompt`).
  - A grid made from a suggestion gets new suggestions for its prompt, so you can keep exploring.
  - 🔄 and V grids keep the suggestions of the grid they came from (same prompt, no extra LLM call).

## 1.1.1

- Fix: prompt suggestions never showed with reasoning LLMs. The answer (reasoning + 4 full prompts) was cut off at 1500 tokens and could not be read.
- New setting `llm.max_tokens` (default 4000) for all LLM calls; the log now says when an answer was cut off.
- Suggestions that arrived complete are kept even if the rest of the answer was cut off.

## 1.1.0

- Prompt suggestions: with the prompt helper on, the first `/imagine` grid gets 4 suggested prompt changes (💡 lighting, 📐 composition, ✨ two more). A click makes 4 new pictures with that change and the same seed.
- New settings `llm.suggest` (on by default) and `llm.suggest_system_prompt`.

## 1.0.0

First release.

- `/imagine` 2x2 grids (Qwen Image 2.1 + Turbo LoRA, 1024 px), optional LLM prompt helper, aspect ratios, `raw` mode.
- U1-U4, V1-V4, 🔄, Vary (Subtle/Strong) via the *PIC_GEN Variation Noise* ComfyUI node.
- ✏️ Edit pop-up and `/edit`: 4 edited versions per request, light LLM touch-up of the instruction.
- ⬆️ 2x / 4K upscales and `/upscale` with 4x-UltraSharpV2.
- Server, DM and user-install support; one job at a time with place in line; optional `max_queue` and role filter.
- `settings.json` hot reload, pictures saved without ComfyUI metadata, `python -m picgen.selftest`.
- Docker image on `ghcr.io/levoniust/picgen-bot`.
