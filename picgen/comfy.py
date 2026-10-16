"""Minimal ComfyUI API client: upload a picture, queue a workflow, wait for it, download the result."""
import asyncio
import json
import logging
import time
import uuid

import aiohttp

log = logging.getLogger("picgen")


class ComfyError(Exception):
    """A readable error from ComfyUI (shown to the user)."""


class Comfy:
    def __init__(self, url, timeout_seconds=900):
        self.url = url.rstrip("/")
        self.timeout = timeout_seconds
        self.client_id = f"picgen-{uuid.uuid4().hex[:8]}"
        self._session = None

    async def session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self._session

    async def close(self):
        if self._session is not None:
            await self._session.close()

    async def has_node(self, class_type):
        s = await self.session()
        async with s.get(f"{self.url}/object_info/{class_type}") as r:
            return r.status == 200 and class_type in await r.json()

    async def upload(self, data, name):
        """Uploads a picture into ComfyUI's temp folder (cleared when ComfyUI restarts). Returns the LoadImage name ('x.png [temp]')."""
        s = await self.session()
        form = aiohttp.FormData()
        form.add_field("image", data, filename=name, content_type="image/png")
        form.add_field("type", "temp")
        form.add_field("overwrite", "true")
        try:
            async with s.post(f"{self.url}/upload/image", data=form) as r:
                if r.status != 200:
                    raise ComfyError(f"ComfyUI refused the picture upload (HTTP {r.status}).")
                info = await r.json()
        except aiohttp.ClientError as ex:
            raise ComfyError(f"Could not reach ComfyUI ({ex}).") from ex
        name = info["name"] if not info.get("subfolder") else f"{info['subfolder']}/{info['name']}"
        return f"{name} [temp]"

    async def run(self, workflow, output_node):
        """Queues a workflow, waits for it and returns the encoded images of output_node (a PreviewImage/SaveImage node)."""
        s = await self.session()
        try:
            async with s.post(f"{self.url}/prompt", json={"prompt": workflow, "client_id": self.client_id}) as r:
                body = await r.text()
                if r.status != 200:
                    raise ComfyError(f"ComfyUI refused the job: {self.describe_refusal(body)}")
                prompt_id = json.loads(body)["prompt_id"]
        except aiohttp.ClientError as ex:
            raise ComfyError(f"Could not reach ComfyUI ({ex}).") from ex
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                await asyncio.sleep(0.5)
                async with s.get(f"{self.url}/history/{prompt_id}") as r:
                    history = await r.json()
                # ComfyUI only adds a history entry when the job has finished (successfully or not).
                entry = history.get(prompt_id)
                if entry:
                    break
                if time.monotonic() > deadline:
                    raise ComfyError(f"ComfyUI took longer than {self.timeout // 60} minutes (busy or stopped?).")
            status = entry["status"]
            if status.get("status_str") != "success":
                for kind, msg in status.get("messages", []):
                    if kind == "execution_error":
                        raise ComfyError(f"ComfyUI error in {msg.get('node_type')}: {str(msg.get('exception_message', '')).strip()[:400]}")
                    if kind == "execution_interrupted":
                        raise ComfyError("The job was stopped in ComfyUI (someone pressed Cancel/Interrupt). Please try again.")
                raise ComfyError(f"ComfyUI job failed ({status.get('status_str')}).")
            images = []
            for img in entry.get("outputs", {}).get(output_node, {}).get("images", []):
                async with s.get(f"{self.url}/view", params={"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}) as r:
                    if r.status != 200:
                        raise ComfyError(f"Could not download the result from ComfyUI (HTTP {r.status}).")
                    images.append(await r.read())
            if not images:
                raise ComfyError("ComfyUI finished but returned no picture.")
            return images
        finally:
            try:
                async with s.post(f"{self.url}/history", json={"delete": [prompt_id]}):
                    pass
            except aiohttp.ClientError:
                pass

    @staticmethod
    def describe_refusal(body):
        try:
            data = json.loads(body)
            parts = [data.get("error", {}).get("message", "")]
            for node in (data.get("node_errors") or {}).values():
                for err in node.get("errors", []):
                    parts.append(f"{node.get('class_type')}: {err.get('message')} {err.get('details', '')}".strip())
            return "; ".join(p for p in parts if p)[:500]
        except (ValueError, AttributeError):
            return body[:500]
