"""Everything except Discord: jobs on disk, the one-at-a-time queue, and generate / edit / vary / upscale."""
import asyncio
import dataclasses
import json
import logging
import random
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import images, llm, workflows
from .comfy import Comfy, ComfyError

log = logging.getLogger("picgen")


class UserError(Exception):
    """A problem to show to the user as-is."""


@dataclasses.dataclass
class Job:
    """One finished request, kept on disk so the buttons under its pictures keep working (also after a restart)."""
    id: str
    kind: str  # imagine, redo, vary, edit, upload, suggest
    user: str
    request: str = ""  # what the user typed
    prompt: str = ""  # what was sent to the model (after the prompt helper, without the hidden quality text)
    width: int = 0
    height: int = 0
    seed: int = 0  # picture i: seed + i, or (var_strength > 0) seed with variation seed var_seed + i
    var_seed: int = 0
    var_strength: float = 0.0
    is_edit: bool = False  # edit results (source.png in the job folder is the picture that was edited)
    created: float = 0.0
    files: list = dataclasses.field(default_factory=list)
    suggestions: list = dataclasses.field(default_factory=list)  # [{"label", "prompt"}] from the prompt helper, shown as buttons under the grid


class Engine:
    def __init__(self, settings, data_dir):
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "jobs"
        self.pictures_dir = self.data_dir / "pictures"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.pending = 0
        self.completed = 0
        self.failed = 0
        self._comfy = None

    # ---------- basics ----------

    @property
    def s(self):
        return self.settings.get()

    @property
    def comfy(self):
        url = self.s["comfy_url"]
        if self._comfy is None or self._comfy.url != url.rstrip("/"):
            if self._comfy is not None:
                asyncio.ensure_future(self._comfy.close())
            self._comfy = Comfy(url)
        return self._comfy

    async def in_line(self, work, on_wait=None):
        """Runs work() when it is this request's turn on the GPU. on_wait(n) is told how many are ahead."""
        ahead = self.pending
        limit = int(self.s.get("max_queue") or 0)
        if limit and ahead >= limit:
            raise UserError(f"PIC_GEN is busy ({ahead} jobs waiting). Please try again in a few minutes.")
        self.pending += 1
        try:
            if ahead and on_wait:
                await on_wait(ahead)
            async with self.lock:
                result = await work()
                self.completed += 1
                return result
        except Exception:
            self.failed += 1
            raise
        finally:
            self.pending -= 1

    # ---------- jobs ----------

    def new_job(self, kind, user, **fields):
        return Job(id=secrets.token_hex(5), kind=kind, user=user, created=time.time(), **fields)

    def folder(self, job):
        return self.jobs_dir / job.id

    def save_job(self, job, pictures=None):
        folder = self.folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        if pictures is not None:
            job.files = []
            for n, data in enumerate(pictures, 1):
                (folder / f"{n}.png").write_bytes(data)
                job.files.append(f"{n}.png")
        (folder / "job.json").write_text(json.dumps(dataclasses.asdict(job), indent=1), encoding="utf-8")

    def load_job(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{6,32}", job_id or ""):
            return None
        path = self.jobs_dir / job_id / "job.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Job(**{k: v for k, v in data.items() if k in Job.__dataclass_fields__})

    def picture(self, job, index):
        if not 0 <= index < len(job.files):
            raise UserError("That picture no longer exists.")
        return (self.folder(job) / job.files[index]).read_bytes()

    def save_source(self, job, data):
        self.folder(job).mkdir(parents=True, exist_ok=True)
        (self.folder(job) / "source.png").write_bytes(data)

    def source(self, job):
        path = self.folder(job) / "source.png"
        if not path.exists():
            raise UserError("The original picture of this edit is gone, please edit again.")
        return path.read_bytes()

    def cleanup(self):
        """Deletes job folders older than job_days (pictures stay in the pictures folder)."""
        cutoff = time.time() - float(self.s["job_days"]) * 86400
        for folder in self.jobs_dir.iterdir():
            try:
                if folder.is_dir() and folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder)
            except OSError as ex:
                log.warning("Could not clean up %s: %s", folder, ex)

    def keep_picture(self, data, job, label):
        """Copies a finished picture into pictures/<date>/ (the share)."""
        if not self.s.get("save_pictures", True):
            return None
        day = self.pictures_dir / datetime.now().strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        user = re.sub(r"[^A-Za-z0-9_-]+", "_", job.user)[:24] or "user"
        path = day / f"{datetime.now().strftime('%H%M%S')}_{user}_{job.kind}_{job.id}_{label}.png"
        path.write_bytes(data)
        return path

    # ---------- prompts ----------

    @staticmethod
    def with_suffix(prompt, suffix):
        return prompt if not suffix else f"{prompt.rstrip().rstrip('.')}. {suffix}"

    async def helper(self, request, user, edit=False):
        """Prompt helper; returns (prompt, note)."""
        s = self.s
        if not s["llm"].get("enabled") or (edit and not s["llm"].get("rewrite_edits", True)):
            return request, ""
        result = await llm.rewrite(s, request, user, edit=edit)
        if result is None:
            return request, "prompt helper unavailable, used your words"
        return result, ""

    async def suggestions(self, prompt, user, edit=False):
        """Up to 4 suggested prompt (or edit instruction) changes from the prompt helper ([] if it is off or fails)."""
        s = self.s
        if not s["llm"].get("enabled") or not s["llm"].get("suggest", True):
            return []
        return await llm.suggest(s, prompt, user, edit=edit)

    def save_suggestions(self, job, suggestions):
        job.suggestions = list(suggestions)
        self.save_job(job)

    def suggest_job(self, old, index, user):
        """New job that remakes `old` with suggestion `index` as the prompt (edit jobs: as the edit instruction, on the same source picture),
        same seed so the pictures are easy to compare."""
        if not 0 <= index < len(old.suggestions):
            raise UserError("That suggestion no longer exists.")
        job = self.new_job("suggest", user, request=old.request, prompt=old.suggestions[index]["prompt"], width=old.width, height=old.height, seed=old.seed, is_edit=old.is_edit)
        if old.is_edit:
            self.save_source(job, self.source(old))
        return job

    # ---------- generation (call these inside in_line) ----------

    async def _grid(self, job, source=None):
        """Makes grid_count pictures for a job (create, or edit when source is given). One ComfyUI run per picture."""
        s = self.s
        count = int(s["grid_count"])
        image_name = None
        if source is not None:
            image_name = await self.comfy.upload(images.clean_png(source), f"picgen_{job.id}_source.png")
        if job.var_strength > 0 and not await self.comfy.has_node("PicGenVariationNoise"):
            raise UserError("Variations need the PIC_GEN node in ComfyUI (copy comfyui_nodes/picgen_nodes into ComfyUI's custom_nodes and restart ComfyUI).")
        pictures = []
        for i in range(count):
            if job.var_strength > 0:
                seed, var_seed, strength = job.seed, job.var_seed + i, job.var_strength
            else:
                seed, var_seed, strength = job.seed + i, 0, 0.0
            if source is not None:
                wf = workflows.edit(s, self.with_suffix(job.prompt, s["edit_suffix"]), image_name, seed, var_seed, strength)
            else:
                wf = workflows.create(s, self.with_suffix(job.prompt, s["quality_suffix"]), job.width, job.height, seed, var_seed, strength)
            data = (await self.comfy.run(wf, workflows.OUT))[0]
            pictures.append(images.clean_png(data))
        self.save_job(job, pictures)
        for n, data in enumerate(pictures, 1):
            self.keep_picture(data, job, str(n))
        return pictures

    async def imagine(self, job):
        return await self._grid(job)

    async def edit(self, job, source):
        self.save_source(job, images.clean_png(source))
        return await self._grid(job, source)

    async def regrid(self, job):
        """For 🔄 and vary jobs: makes the grid (edit jobs redo the edit from their saved source)."""
        if job.is_edit:
            return await self._grid(job, self.source(job))
        return await self._grid(job)

    def vary_job(self, old, index, strength, user):
        """New job for variations of picture `index` of `old` (same prompt/seed, plus variation noise)."""
        # Picture i of the old job came from seed + i, or (if it was itself a variation) from seed with variation seed var_seed + i.
        # Only two noises can be mixed, so a variation of a variation varies around the same original seed.
        job = self.new_job("vary", user, request=old.request, prompt=old.prompt, width=old.width, height=old.height, is_edit=old.is_edit)
        if old.var_strength > 0:
            job.seed = old.seed
            job.var_strength = min(0.9, max(old.var_strength, strength))
        else:
            job.seed = old.seed + index
            job.var_strength = strength
        job.var_seed = random.randint(1, 2**31 - 1)
        job.suggestions = list(old.suggestions)  # same prompt, so the same suggestions still fit
        if old.is_edit:
            self.save_source(job, self.source(old))
        return job

    def redo_job(self, old, user):
        job = self.new_job("redo", user, request=old.request, prompt=old.prompt, width=old.width, height=old.height, is_edit=old.is_edit, seed=random.randint(1, 2**31 - 1), suggestions=list(old.suggestions))
        if old.is_edit:
            self.save_source(job, self.source(old))
        return job

    async def upscale(self, data, factor):
        """AI upscale. factor 2 = twice as big; factor 4 = '4K' (long side max_upscale_side). Returns (png, width, height)."""
        s = self.s
        w, h = images.image_size(data)
        if not w:
            raise UserError("I couldn't read that picture.")
        max_side = int(s["max_upscale_side"])
        tw, th = images.upscale_target(w, h, 1000 if factor >= 4 else factor, max_side)
        if tw <= w * 1.05:
            raise UserError(f"That picture is already {w}x{h}, as big as {'4K' if factor >= 4 else '2x'} gets here.")
        # The model always makes 4x; feeding more than half the target only costs time.
        small = images.clean_png(images.shrink_to(data, max(256, -(-max(tw, th) // 2))))
        name = await self.comfy.upload(small, f"picgen_up_{secrets.token_hex(4)}.png")
        out = (await self.comfy.run(workflows.upscale(s, name, tw, th), workflows.OUT))[0]
        return images.clean_png(out), tw, th
