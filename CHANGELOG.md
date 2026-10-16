# Changelog

## 1.0.0

First release.

- `/imagine` 2x2 grids (Qwen Image 2.1 + Turbo LoRA, 1024 px), optional LLM prompt helper, aspect ratios, `raw` mode.
- U1-U4, V1-V4, 🔄, Vary (Subtle/Strong) via the *PIC_GEN Variation Noise* ComfyUI node.
- ✏️ Edit pop-up and `/edit`: 4 edited versions per request, light LLM touch-up of the instruction.
- ⬆️ 2x / 4K upscales and `/upscale` with 4x-UltraSharpV2.
- Server, DM and user-install support; one job at a time with place in line; optional `max_queue` and role filter.
- `settings.json` hot reload, pictures saved without ComfyUI metadata, `python -m picgen.selftest`.
- Docker image on `ghcr.io/levoniust/picgen-bot`.
