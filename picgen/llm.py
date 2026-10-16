"""Optional prompt helper: an OpenAI-compatible chat LLM turns short requests into detailed prompts (and tidies edit instructions)."""
import json
import logging
import re

import aiohttp

log = logging.getLogger("picgen")


def extract_prompt(content):
    """Pulls the "prompt" value out of the LLM's answer. Tolerates code fences, text around the JSON, and unescaped double quotes inside the value."""
    if not content:
        return None
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        blob = text[start:end + 1]
        try:
            value = json.loads(blob).get("prompt")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (ValueError, AttributeError):
            pass
        # Lenient: take everything between the opening quote after "prompt": and the last quote before the closing brace.
        m = re.search(r'"prompt"\s*:\s*"', blob)
        if m:
            rest = blob[m.end():]
            close = rest.rfind('"')
            if close > 0:
                value = rest[:close].replace('\\"', '"').replace("\\n", " ").strip()
                if value:
                    return value
    return None


def extract_suggestions(content):
    """Pulls [{"label", "prompt"}, ...] out of the LLM's suggestion answer (code fences, text around the JSON and a bare list are fine)."""
    if not content:
        return []
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    data = None
    for start, end in ((text.find("{"), text.rfind("}")), (text.find("["), text.rfind("]"))):
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                break
            except ValueError:
                continue
    if isinstance(data, dict):
        data = data.get("suggestions")
    if data is None:
        # Cut-off or broken JSON: keep every complete {"label": ..., "prompt": ...} pair.
        data = []
        for label, prompt in re.findall(r'"label"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"prompt"\s*:\s*"((?:[^"\\]|\\.)*)"\s*}', text):
            try:
                data.append({"label": json.loads(f'"{label}"'), "prompt": json.loads(f'"{prompt}"')})
            except ValueError:
                continue
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("prompt"), str):
            label, prompt = " ".join(item["label"].split()), item["prompt"].strip()
            if label and prompt:
                out.append({"label": label, "prompt": prompt})
    return out[:4]


async def chat(s, system, message, user_name, what="Prompt helper"):
    """One chat call to the LLM; returns the answer text, or None if the LLM is off/unreachable."""
    llm = s["llm"]
    if not llm.get("enabled"):
        return None
    body = {"model": llm["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}], "max_tokens": int(llm.get("max_tokens") or 4000), "temperature": 0.7}
    body.update(llm.get("extra_body") or {})
    headers = {}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"
    if llm.get("user_header"):
        headers[llm["user_header"]] = f"Discord: {user_name}"[:100]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=float(llm.get("timeout_seconds", 120)))) as session:
            async with session.post(llm["url"], json=body, headers=headers) as r:
                text = await r.text()
                if r.status != 200:
                    log.warning("%s failed: HTTP %s %s", what, r.status, text[:300])
                    return None
        choice = json.loads(text)["choices"][0]
        if choice.get("finish_reason") == "length":
            log.warning("%s answer was cut off at llm.max_tokens (%s); raise it if this keeps happening", what, body["max_tokens"])
        return choice["message"].get("content") or ""
    except Exception as ex:  # network errors, timeouts, bad JSON
        log.warning("%s failed: %s: %s", what, type(ex).__name__, ex)
        return None


async def rewrite(s, request, user_name, edit=False):
    """Returns the rewritten prompt, or None if the LLM is off/unreachable/answered nonsense (callers then use the user's words)."""
    system = s["llm"]["edit_system_prompt"] if edit else s["llm"]["imagine_system_prompt"]
    content = await chat(s, system, request, user_name)
    if content is None:
        return None
    result = extract_prompt(content)
    if result is None:
        log.warning("Prompt helper answer had no prompt: %s", content[:300])
    return result


async def suggest(s, prompt, user_name):
    """Up to 4 suggested prompt changes for a finished picture: [{"label": short button text, "prompt": the whole new prompt}]."""
    content = await chat(s, s["llm"]["suggest_system_prompt"], prompt, user_name, "Prompt suggestions")
    if content is None:
        return []
    result = extract_suggestions(content)
    if not result:
        log.warning("Prompt suggestions answer had none: %s", content[:300])
    return result
