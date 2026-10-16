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


async def rewrite(s, request, user_name, edit=False):
    """Returns the rewritten prompt, or None if the LLM is off/unreachable/answered nonsense (callers then use the user's words)."""
    llm = s["llm"]
    if not llm.get("enabled"):
        return None
    system = llm["edit_system_prompt"] if edit else llm["imagine_system_prompt"]
    body = {"model": llm["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": request}], "max_tokens": 1500, "temperature": 0.7}
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
                    log.warning("Prompt helper failed: HTTP %s %s", r.status, text[:300])
                    return None
        content = json.loads(text)["choices"][0]["message"].get("content") or ""
        result = extract_prompt(content)
        if result is None:
            log.warning("Prompt helper answer had no prompt: %s", content[:300])
        return result
    except Exception as ex:  # network errors, timeouts, bad JSON
        log.warning("Prompt helper failed: %s: %s", type(ex).__name__, ex)
        return None
