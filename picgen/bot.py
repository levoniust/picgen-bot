"""The Discord side: slash commands, buttons, the Edit pop-up."""
import asyncio
import io
import logging
import random
import re
import time

import discord
from discord import app_commands

from . import __version__, images
from .comfy import ComfyError
from .engine import Engine, UserError

log = logging.getLogger("picgen")

HELP = """**PIC_GEN** makes pictures with AI (Qwen Image 2.1).
- `/imagine prompt:` describe a picture → 4 versions in a grid. Optional `aspect` (shape) and `raw:True` (skip the AI prompt helper, use your exact words).
- Under a grid: **U1-U4** = that picture full size · **V1-V4** = 4 close variations of it · 🔄 = 4 new pictures, same prompt.
- With the prompt helper on, the first grid also gets 4 suggested prompt changes (💡 lighting, 📐 composition, ✨ two more); a click makes 4 new pictures with that change, same seed.
- Under a full-size picture: **Vary (Subtle)** / **Vary (Strong)** · **✏️ Edit** = type what to change → 4 edited versions · **⬆️ 2x** / **⬆️ 4K** = bigger copy (4K = 3840 px, e.g. for a desktop background).
- `/edit image: prompt:` upload a picture and say what to change ("make it night", "add a red scarf").
- `/upscale image: size:` make your own picture bigger.
One job runs at a time, so you may wait in line. A grid takes about 50 s, an edit grid about 70 s, an upscale about 40 s."""

ASPECT_CHOICES = [app_commands.Choice(name=n, value=v) for n, v in [
    ("1:1 square", "1:1"), ("16:9 wide", "16:9"), ("9:16 tall (phone)", "9:16"), ("4:3", "4:3"), ("3:4", "3:4"),
    ("3:2 photo", "3:2"), ("2:3 portrait", "2:3"), ("21:9 cinema", "21:9")]]

INSTALLS = dict(guilds=True, users=True)
CONTEXTS = dict(guilds=True, dms=True, private_channels=True)

SUGGEST_EMOJI = ["💡", "📐", "✨", "✨"]  # lighting, composition, two free picks (see SUGGEST_SYSTEM_PROMPT)

BOT = None  # the running PicGenBot (buttons and the pop-up find it here)


def clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n - 1] + "…"


def name_of(inter):
    user = inter.user
    return getattr(user, "display_name", None) or user.global_name or user.name


class PgButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pg\|(?P<action>[a-z0-9]+)\|(?P<job>[0-9a-f]+)\|(?P<index>\d+)"):
    """All buttons. The custom ID carries action, job and picture index, so buttons keep working after a restart."""

    def __init__(self, action, job_id, index, label=None, emoji=None, style=discord.ButtonStyle.secondary, row=None):
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"pg|{action}|{job_id}|{index}"), row=row)
        self.action, self.job_id, self.index = action, job_id, index

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["job"], int(match["index"]))

    async def callback(self, interaction):
        await BOT.on_button(interaction, self.action, self.job_id, self.index)


class EditModal(discord.ui.Modal):
    change = discord.ui.TextInput(label="What should change?", style=discord.TextStyle.paragraph, placeholder="e.g. make it daytime, add a red umbrella", min_length=2, max_length=1000)

    def __init__(self, job, index):
        super().__init__(title=f"Edit picture {index + 1}", timeout=1800)
        self.job, self.index = job, index

    async def on_submit(self, interaction):
        await BOT.on_edit_submit(interaction, self.job, self.index, str(self.change.value).strip())


def grid_view(job, count):
    view = discord.ui.View(timeout=None)
    for i in range(count):
        view.add_item(PgButton("u", job.id, i, label=f"U{i + 1}", row=0))
    view.add_item(PgButton("r", job.id, 0, emoji="🔄", row=0))
    for i in range(count):
        view.add_item(PgButton("v", job.id, i, label=f"V{i + 1}", row=1))
    for i, idea in enumerate(job.suggestions[:4]):
        view.add_item(PgButton("p", job.id, i, label=clip(idea["label"], 80), emoji=SUGGEST_EMOJI[i], row=2))
    return view


def full_view(job, index):
    view = discord.ui.View(timeout=None)
    view.add_item(PgButton("vs", job.id, index, label="Vary (Subtle)", emoji="🪄"))
    view.add_item(PgButton("vx", job.id, index, label="Vary (Strong)", emoji="🎲"))
    view.add_item(PgButton("e", job.id, index, label="Edit", emoji="✏️", style=discord.ButtonStyle.primary))
    view.add_item(PgButton("x2", job.id, index, label="2x", emoji="⬆️"))
    view.add_item(PgButton("x4", job.id, index, label="4K", emoji="⬆️"))
    return view


class PicGenBot(discord.Client):
    def __init__(self, engine: Engine):
        super().__init__(intents=discord.Intents(guilds=True))
        self.engine = engine
        self.tree = app_commands.CommandTree(self)
        self.state = "starting"
        self.last_error = ""
        self._register_commands()

    # ---------- startup ----------

    async def setup_hook(self):
        global BOT
        BOT = self
        self.add_dynamic_items(PgButton)
        synced = await self.tree.sync()
        log.info("Slash commands registered: %s", " ".join(f"/{c.name}" for c in synced))
        self._housekeeper = asyncio.create_task(self._housekeeping())

    async def on_ready(self):
        self.state = f"connected as {self.user}"
        log.info("Discord: connected as %s in %d server(s)", self.user, len(self.guilds))

    async def _housekeeping(self):
        while not self.is_closed():
            try:
                self.engine.cleanup()
            except Exception as ex:
                log.warning("Cleanup failed: %s", ex)
            await asyncio.sleep(3600)

    def _register_commands(self):
        tree = self.tree

        @tree.command(name="imagine", description="Make 4 pictures from a description")
        @app_commands.describe(prompt="Describe the picture", aspect="Shape of the picture (default square)", raw="True = use your exact words (skip the AI prompt helper)")
        @app_commands.choices(aspect=ASPECT_CHOICES)
        @app_commands.allowed_installs(**INSTALLS)
        @app_commands.allowed_contexts(**CONTEXTS)
        async def imagine(inter: discord.Interaction, prompt: app_commands.Range[str, 1, 1500], aspect: app_commands.Choice[str] = None, raw: bool = False):
            await self.guarded(inter, self.do_imagine(inter, prompt.strip(), aspect.value if aspect else "1:1", raw))

        @tree.command(name="edit", description="Change a picture: upload it and say what to change (4 versions)")
        @app_commands.describe(image="The picture to edit", prompt="What to change, e.g. 'make it night'", raw="True = use your exact words (skip the AI touch-up)")
        @app_commands.allowed_installs(**INSTALLS)
        @app_commands.allowed_contexts(**CONTEXTS)
        async def edit(inter: discord.Interaction, image: discord.Attachment, prompt: app_commands.Range[str, 2, 1000], raw: bool = False):
            await self.guarded(inter, self.do_edit_upload(inter, image, prompt.strip(), raw))

        @tree.command(name="upscale", description="Make a picture bigger with an AI upscaler (2x or 4K)")
        @app_commands.describe(image="The picture to enlarge", size="How big (default 4K)")
        @app_commands.choices(size=[app_commands.Choice(name="4K (3840 px long side)", value="x4"), app_commands.Choice(name="2x", value="x2")])
        @app_commands.allowed_installs(**INSTALLS)
        @app_commands.allowed_contexts(**CONTEXTS)
        async def upscale(inter: discord.Interaction, image: discord.Attachment, size: app_commands.Choice[str] = None):
            await self.guarded(inter, self.do_upscale_upload(inter, image, 2 if size and size.value == "x2" else 4))

        @tree.command(name="help", description="How to use PIC_GEN")
        @app_commands.allowed_installs(**INSTALLS)
        @app_commands.allowed_contexts(**CONTEXTS)
        async def help_cmd(inter: discord.Interaction):
            await inter.response.send_message(f"{HELP}\n-# PIC_GEN {__version__} · github.com/levoniust/picgen-bot", ephemeral=True)

    # ---------- plumbing ----------

    def allowed(self, inter):
        roles = [str(r) for r in self.engine.s.get("allowed_role_ids") or []]
        member = inter.user if isinstance(inter.user, discord.Member) else None
        return not roles or member is None or any(str(r.id) in roles for r in member.roles)

    async def guarded(self, inter, work, defer=True):
        """Common wrapper: role check, 'thinking...', error reporting."""
        try:
            if not self.allowed(inter):
                await inter.response.send_message("Sorry, you don't have a role that may use PIC_GEN here.", ephemeral=True)
                work.close()
                return
            if defer and not inter.response.is_done():
                await inter.response.defer(thinking=True)
            await work
        except Exception as ex:
            await self.report(inter, ex)

    async def status(self, inter, text):
        try:
            await inter.edit_original_response(content=text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as ex:
            log.debug("Status update failed: %s", ex)

    async def post(self, inter, content, data, filename, view=None):
        """Replaces the 'thinking...' message with the result (falls back to a channel message if the interaction expired)."""
        content = clip(content, 1990)
        kwargs = dict(content=content, attachments=[discord.File(io.BytesIO(data), filename=filename)], allowed_mentions=discord.AllowedMentions.none())
        if view is not None:
            kwargs["view"] = view
        try:
            await inter.edit_original_response(**kwargs)
        except discord.HTTPException as ex:
            # Interaction tokens expire after 15 minutes (a long queue); then post in the channel if the bot may.
            log.warning("Could not edit the original response (%s), trying the channel instead", ex)
            send = dict(content=content, file=discord.File(io.BytesIO(data), filename=filename), allowed_mentions=discord.AllowedMentions.none())
            if view is not None:
                send["view"] = view
            try:
                await inter.channel.send(**send)
            except (discord.HTTPException, AttributeError) as ex2:
                log.warning("Could not post the result for %s at all: %s", inter.user, ex2)

    async def report(self, inter, ex):
        if isinstance(ex, (UserError, ComfyError)):
            reason = str(ex)
        else:
            reason = f"{type(ex).__name__}: {ex}"
            log.exception("Request from %s failed", inter.user)
        self.last_error = f"{time.strftime('%Y-%m-%d %H:%M')} {reason}"
        log.warning("Request from %s failed: %s", inter.user, reason)
        text = f"❌ Sorry, that didn't work: {clip(reason, 500)}"
        try:
            if inter.response.is_done():
                await inter.edit_original_response(content=text, attachments=[], view=None)
            else:
                await inter.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def queued(self, inter, work, busy_text):
        async def on_wait(ahead):
            await self.status(inter, f"⏳ In line, {ahead} job{'s' if ahead != 1 else ''} ahead of you...")

        async def run():
            await self.status(inter, busy_text)
            return await work()

        return await self.engine.in_line(run, on_wait)

    async def read_upload(self, attachment):
        s = self.engine.s
        if not (attachment.content_type or "").startswith("image/"):
            raise UserError("Please attach a picture (PNG, JPG or WebP).")
        if attachment.size > float(s["max_upload_mb"]) * 1024 * 1024:
            raise UserError(f"That picture is too big (max {s['max_upload_mb']} MB).")
        data = await attachment.read()
        if not images.image_size(data)[0]:
            raise UserError("I couldn't read that picture.")
        return data

    def post_size(self, data):
        """PNG if it fits Discord's limit, else JPEG. Returns (bytes, extension)."""
        limit = int(float(self.engine.s["max_post_mb"]) * 1024 * 1024)
        return (data, "png") if len(data) <= limit else (images.to_jpeg(data, limit), "jpg")

    # ---------- grids ----------

    async def run_grid(self, inter, job, header, note, maker, ideas=None):
        """Makes and posts a grid. ideas: an optional task with prompt suggestions, added as a button row once it is done."""
        s = self.engine.s
        count = int(s["grid_count"])
        per = 18 if job.is_edit else 12
        start = time.monotonic()
        pictures = await self.queued(inter, maker, f"🎨 Making {'your edit' if job.is_edit else 'your pictures'}... (about {count * per} s)")
        w, h = images.image_size(pictures[0])
        prompt_line = "" if job.prompt == job.request else f"\n> {clip(job.prompt, 1200).replace(chr(10), ' ')}"
        info = f"`seed {job.seed} · {w}x{h} · {time.monotonic() - start:.0f} s{' · ' + note if note else ''}`"
        content = f"{header}{prompt_line}\n{info}"
        if len(pictures) == 1:
            data, ext = self.post_size(pictures[0])
            await self.post(inter, content, data, f"picgen_{job.id}.{ext}", full_view(job, 0))
        else:
            await self.post(inter, content, images.make_grid(pictures), f"picgen_{job.id}_grid.jpg", grid_view(job, len(pictures)))
            if ideas is not None:
                await self.add_suggestions(inter, job, len(pictures), ideas)

    async def add_suggestions(self, inter, job, count, ideas):
        """Waits for the prompt suggestions (asked for while the pictures were made) and adds them under the posted grid."""
        try:
            suggestions = await ideas
            if suggestions:
                self.engine.save_suggestions(job, suggestions)
                await inter.edit_original_response(view=grid_view(job, count))
        except Exception as ex:  # suggestions are a bonus; the pictures are already posted
            log.warning("Could not add prompt suggestions: %s: %s", type(ex).__name__, ex)

    async def do_imagine(self, inter, request, aspect, raw):
        user = name_of(inter)
        prompt, note = request, ""
        if not raw and self.engine.s["llm"].get("enabled"):
            await self.status(inter, f"✍️ Writing a detailed prompt for: *{clip(request, 300)}*")
            prompt, note = await self.engine.helper(request, user)
        w, h = images.size_for(images.parse_aspect(aspect), int(self.engine.s["size"]))
        job = self.engine.new_job("imagine", user, request=request, prompt=prompt, width=w, height=h, seed=random.randint(1, 2**31 - 1))
        # Ask for suggestions while the pictures are made, so they cost no extra wait.
        ideas = None
        if not raw and int(self.engine.s["grid_count"]) > 1:
            ideas = asyncio.create_task(self.engine.suggestions(prompt, user))
        try:
            await self.run_grid(inter, job, f"**{user}**: {clip(request, 300)}", note, lambda: self.engine.imagine(job), ideas)
        finally:
            if ideas is not None and not ideas.done():
                ideas.cancel()

    async def do_edit(self, inter, source, request, raw, where=""):
        user = name_of(inter)
        prompt, note = request, ""
        if not raw and self.engine.s["llm"].get("enabled") and self.engine.s["llm"].get("rewrite_edits", True):
            await self.status(inter, f"✍️ Reading your change: *{clip(request, 300)}*")
            prompt, note = await self.engine.helper(request, user, edit=True)
        job = self.engine.new_job("edit", user, request=request, prompt=prompt, is_edit=True, seed=random.randint(1, 2**31 - 1))
        await self.run_grid(inter, job, f"✏️ **{user}**: {clip(request, 300)}{where}", note, lambda: self.engine.edit(job, source))

    async def do_edit_upload(self, inter, attachment, request, raw):
        await self.do_edit(inter, await self.read_upload(attachment), request, raw)

    # ---------- buttons ----------

    async def on_button(self, inter, action, job_id, index):
        job = self.engine.load_job(job_id)
        if job is None:
            await inter.response.send_message("That picture is too old (or was removed), please make a new one.", ephemeral=True)
            return
        if action == "e":
            # The pop-up must be the first answer to the click.
            if not self.allowed(inter):
                await inter.response.send_message("Sorry, you don't have a role that may use PIC_GEN here.", ephemeral=True)
                return
            await inter.response.send_modal(EditModal(job, index))
            return
        handlers = {
            "u": lambda: self.do_full(inter, job, index),
            "v": lambda: self.do_vary(inter, job, index, "vary_grid", "Variations"),
            "vs": lambda: self.do_vary(inter, job, index, "vary_subtle", "Vary (Subtle)"),
            "vx": lambda: self.do_vary(inter, job, index, "vary_strong", "Vary (Strong)"),
            "r": lambda: self.do_redo(inter, job),
            "p": lambda: self.do_suggestion(inter, job, index),
            "x2": lambda: self.do_upscale(inter, job, index, 2),
            "x4": lambda: self.do_upscale(inter, job, index, 4),
        }
        if action in handlers:
            await self.guarded(inter, handlers[action]())

    async def do_full(self, inter, job, index):
        data, ext = self.post_size(self.engine.picture(job, index))
        w, h = images.image_size(data)
        await self.post(inter, f"**{name_of(inter)}** · picture {index + 1} · {clip(job.request, 300)}\n`{w}x{h}`", data, f"picgen_{job.id}_{index + 1}.{ext}", full_view(job, index))

    async def do_vary(self, inter, job, index, setting, label):
        new = self.engine.vary_job(job, index, float(self.engine.s[setting]), name_of(inter))
        await self.run_grid(inter, new, f"🎲 **{name_of(inter)}**: {label} of picture {index + 1} · {clip(job.request, 250)}", "", lambda: self.engine.regrid(new))

    async def do_redo(self, inter, job):
        new = self.engine.redo_job(job, name_of(inter))
        await self.run_grid(inter, new, f"🔄 **{name_of(inter)}**: {clip(job.request, 300)}", "", lambda: self.engine.regrid(new))

    async def do_suggestion(self, inter, job, index):
        new = self.engine.suggest_job(job, index, name_of(inter))
        label = job.suggestions[index]["label"]
        await self.run_grid(inter, new, f"{SUGGEST_EMOJI[index]} **{name_of(inter)}**: {clip(label, 80)} · {clip(job.request, 250)}", "", lambda: self.engine.regrid(new))

    async def on_edit_submit(self, inter, job, index, request):
        async def work():
            source = self.engine.picture(job, index)
            where = "" if job.kind == "upload" else f" (picture {index + 1} of *{clip(job.request, 120)}*)"
            await self.do_edit(inter, source, request, False, where)
        await self.guarded(inter, work())

    # ---------- upscales ----------

    async def do_upscale(self, inter, job, index, factor):
        await self._upscale(inter, job, self.engine.picture(job, index), factor, f"picture {index + 1} · {clip(job.request, 250)}")

    async def do_upscale_upload(self, inter, attachment, factor):
        data = await self.read_upload(attachment)
        job = self.engine.new_job("upload", name_of(inter), request=attachment.filename)
        await self._upscale(inter, job, data, factor, clip(attachment.filename, 100))

    async def _upscale(self, inter, job, data, factor, what):
        label = "4K" if factor >= 4 else "2x"
        start = time.monotonic()
        png, w, h = await self.queued(inter, lambda: self.engine.upscale(data, factor), f"⬆️ Upscaling to {label}... (about 40 s)")
        saved = self.engine.keep_picture(png, job, f"upscale-{label}")
        hint = self.engine.s.get("pictures_hint") or ""
        where = f" · full PNG: {hint}{'/' if '/' in hint else chr(92)}{saved.parent.name}{'/' if '/' in hint else chr(92)}{saved.name}" if saved and hint else ""
        limit = int(float(self.engine.s["max_post_mb"]) * 1024 * 1024)
        jpeg = images.to_jpeg(png, limit)
        await self.post(inter, f"⬆️ **{name_of(inter)}** · {label} of {what}\n`{w}x{h} · {time.monotonic() - start:.0f} s{where}`", jpeg, f"picgen_{job.id}_{label}.jpg")
