"""Settings: defaults here, overridden by DATA_DIR/settings.json (reloaded automatically when the file changes)."""
import copy
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("picgen")

IMAGINE_SYSTEM_PROMPT = """### Task:
Write the prompt for an image generator (Qwen Image 2.1) based on the user's request. The user message is the image they want.

### How to write the prompt:
- One flowing English paragraph of 60-150 words that describes the finished image as if you were looking at it. No lists, no keyword soup, no "4k, masterpiece, trending" tags.
- Keep every detail the user gave (subjects, number of people, clothing, colors, style, mood, setting). Never leave out, soften or censor anything the user asked for, and do not add disclaimers.
- Fill in what the user left open with sensible choices: medium/style (photo, oil painting, anime, 3D render...; default to a realistic photo if nothing suggests otherwise), main subject and what they are doing, setting and background, lighting and time of day, color palette, camera angle and framing (close-up, wide shot...), and mood.
- People: describe age range, expression, pose, and what their hands are doing, so anatomy comes out right.
- Any words that must appear in the image (signs, titles, labels, speech bubbles) go in single quotes ('LIKE THIS') exactly as they should be written, with where they appear. Never use double quotes inside the prompt. Do not add text the user did not ask for.
- Describe only what should be in the image, never what should be missing.

### Output:
Strictly return only JSON, no other text:
{
    "prompt": "Your prompt here."
}"""

EDIT_SYSTEM_PROMPT = """### Task:
The user message is an instruction for an AI photo editor (Qwen Image 2.1 edit), saying what to change in a picture. Tidy it up with a light touch.

### Rules:
- Fix spelling and grammar and make the instruction clear and specific, written as a direct command (for example: "Make it daytime with a bright blue sky").
- Keep exactly what the user asked for. Never add new objects, people, styles or changes they did not ask for, and never leave out, soften or censor anything they asked for.
- Keep it short: at most about 40 words. If the instruction is already clear, return it almost unchanged.
- Any words that must appear in the picture go in single quotes ('LIKE THIS'). Never use double quotes.

### Output:
Strictly return only JSON, no other text:
{
    "prompt": "The edit instruction."
}"""

SUGGEST_SYSTEM_PROMPT = """### Task:
The user message is the prompt that an image generator (Qwen Image 2.1) just turned into a picture. Suggest 4 changes to the prompt that would likely make the picture better or more interesting.

### The 4 suggestions, in this order:
1. Lighting: change the light (time of day, light source, direction, color, contrast or mood of the light).
2. Composition: change the framing (camera angle, distance, lens, subject placement, foreground/background, depth of field).
3. and 4. Your choice: whatever else would help this picture most (style, setting, colors, details, expression, weather...). Make them different from each other and from 1 and 2.

### Rules:
- Each suggestion is one clear change; keep everything else about the picture the same.
- "label": a short button text of at most 6 words saying the change (for example "Golden hour backlight" or "Low angle close-up").
- "prompt": the whole new prompt with the change worked in, one flowing paragraph like the original. Keep every detail of the original that the change does not replace.
- Never use double quotes inside the label or the prompt; words that must appear in the image stay in single quotes.

### Output:
Strictly return only JSON, no other text:
{
    "suggestions": [
        {"label": "Short label", "prompt": "The whole new prompt."}
    ]
}"""

DEFAULTS = {
    "comfy_url": "http://127.0.0.1:8188",
    "models": {
        "unet": "qwen_image_2.1_int8_convrot.safetensors",
        "clip": "qwen3vl_8b_int8_convrot.safetensors",
        "vae": "qwen_image_2.1_vae_bf16.safetensors",
        "lora": "qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors",
        "lora_strength": 1.0,
        "upscale": "4x-UltraSharpV2.safetensors",
    },
    "steps": 8,
    "sampler": "euler",
    "scheduler": "simple",
    "size": 1024,
    "grid_count": 4,
    "quality_suffix": "high quality, highly detailed, sharp focus, natural realistic anatomy, correct hands with five fingers, well-proportioned faces and bodies, clean composition, no watermark, no signature, no added text",
    "edit_suffix": "Keep everything not mentioned unchanged, preserve the people's identity, faces and pose, natural realistic anatomy, correct hands with five fingers, high quality, sharp focus, no watermark, no added text",
    "vary_grid": 0.3,
    "vary_subtle": 0.15,
    "vary_strong": 0.45,
    "max_upscale_side": 3840,
    "llm": {
        "enabled": False,
        "url": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "",
        "api_key": "",
        "timeout_seconds": 120,
        "user_header": "",
        "extra_body": {},
        "imagine_system_prompt": IMAGINE_SYSTEM_PROMPT,
        "rewrite_edits": True,
        "edit_system_prompt": EDIT_SYSTEM_PROMPT,
        "suggest": True,
        "suggest_system_prompt": SUGGEST_SYSTEM_PROMPT,
    },
    "allowed_role_ids": [],
    "job_days": 30,
    "max_queue": 0,
    "max_upload_mb": 20,
    "max_post_mb": 9,
    "save_pictures": True,
    "pictures_hint": "",
}


def merge(base, override):
    """Deep-merges override into a copy of base (dicts merge, everything else replaces)."""
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], val)
        else:
            out[key] = val
    return out


def validate(s):
    """Raises ValueError for settings that would break things."""
    if not 1 <= int(s["steps"]) <= 150:
        raise ValueError("steps must be 1-150")
    if not 256 <= int(s["size"]) <= 4096:
        raise ValueError("size must be 256-4096")
    if not 1 <= int(s["grid_count"]) <= 4:
        raise ValueError("grid_count must be 1-4")
    for key in ("vary_grid", "vary_subtle", "vary_strong"):
        if not 0 < float(s[key]) <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if not 1024 <= int(s["max_upscale_side"]) <= 8192:
        raise ValueError("max_upscale_side must be 1024-8192")
    if int(s["max_queue"]) < 0:
        raise ValueError("max_queue must be 0 (no limit) or more")


class Settings:
    """Holds the current settings and reloads settings.json when it changes. Bad JSON keeps the last good settings (and logs why)."""

    def __init__(self, data_dir):
        self.path = Path(data_dir) / "settings.json"
        self.mtime = None
        self.values = merge(DEFAULTS, {})
        self.error = ""
        self.reload()

    def reload(self):
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self.mtime:
            return
        self.mtime = mtime
        try:
            values = merge(DEFAULTS, json.loads(self.path.read_text(encoding="utf-8")))
            validate(values)
            self.values = values
            self.error = ""
            log.info("Settings loaded from %s", self.path)
        except (ValueError, TypeError, KeyError) as ex:
            self.error = f"{self.path.name} not loaded: {ex}"
            log.error("%s (keeping the previous settings)", self.error)

    def get(self):
        self.reload()
        return self.values


def env_path(name, default):
    return Path(os.environ.get(name, default))
