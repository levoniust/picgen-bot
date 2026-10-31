"""Optional /say: text to speech through an OpenAI-compatible /v1/audio/speech server (any voice server that speaks that API)."""
import json
import logging
import time

import aiohttp

log = logging.getLogger("picgen")

_voices = {"url": None, "at": 0.0, "names": []}


def voices_url(speech_url):
    """.../v1/audio/speech -> .../v1/audio/voices (the voice list most such servers offer)."""
    return speech_url.rsplit("/", 1)[0] + "/voices"


def _headers(sp):
    return {"Authorization": f"Bearer {sp['api_key']}"} if sp.get("api_key") else {}


async def speak(sp, text, voice):
    """Returns (audio bytes, file extension). Raises RuntimeError with a short reason the user can see."""
    body = {"input": text, "response_format": sp["format"]}
    if sp.get("model"):
        body["model"] = sp["model"]
    if voice or sp.get("voice"):
        body["voice"] = voice or sp["voice"]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=float(sp["timeout_seconds"]))) as session:
            async with session.post(sp["url"], json=body, headers=_headers(sp)) as r:
                data = await r.read()
                if r.status != 200:
                    log.warning("Speech failed: HTTP %s %s", r.status, data[:300])
                    raise RuntimeError(f"the voice server said HTTP {r.status}")
    except aiohttp.ClientError as ex:
        raise RuntimeError(f"the voice server is not reachable ({type(ex).__name__})") from None
    except TimeoutError:
        raise RuntimeError("the voice server took too long") from None
    if len(data) < 200:
        raise RuntimeError("the voice server sent no audio")
    return data, {"mp3": "mp3", "wav": "wav", "opus": "ogg", "flac": "flac"}.get(sp["format"], "mp3")


async def voices(sp):
    """Voice names for autocomplete: the speech.voices setting, else the server's list (cached 5 minutes, [] on errors)."""
    if sp.get("voices"):
        return list(sp["voices"])
    url = voices_url(sp["url"])
    if _voices["url"] == url and time.time() - _voices["at"] < 300:
        return _voices["names"]
    names = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get(url, headers=_headers(sp)) as r:
                if r.status == 200:
                    data = json.loads(await r.text())
                    names = [v.get("id") or v.get("name") for v in data.get("voices", []) if isinstance(v, dict)]
                    names = [n for n in names if isinstance(n, str) and n]
    except Exception as ex:  # offline voice server: autocomplete just stays empty
        log.debug("Voice list failed: %s", ex)
    _voices.update(url=url, at=time.time(), names=names)
    return names
