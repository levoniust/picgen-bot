"""Drives the Discord handlers with fake interactions and a fake ComfyUI, to catch errors in every flow without Discord or a GPU."""
import asyncio
import io
import json
import types
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image

from picgen import bot as botmod
from picgen import llm as llmmod
from picgen.config import Settings
from picgen.engine import Engine


def png(w, h, color=(10, 120, 200)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


class FakeComfy:
    def __init__(self, url, has_variation=True):
        self.url = url.rstrip("/")
        self.has_variation = has_variation
        self.runs = []

    async def has_node(self, name):
        return self.has_variation

    async def upload(self, data, name):
        return f"{name} [temp]"

    async def run(self, wf, out):
        self.runs.append(wf)
        if "scale" in wf:
            return [png(wf["scale"]["inputs"]["width"], wf["scale"]["inputs"]["height"])]
        if "latent" in wf:
            return [png(wf["latent"]["inputs"]["width"], wf["latent"]["inputs"]["height"])]
        return [png(1024, 1024)]

    async def close(self):
        pass


class FakeResponse:
    def __init__(self):
        self.done = False
        self.modal = None
        self.messages = []

    def is_done(self):
        return self.done

    async def defer(self, thinking=False, ephemeral=False):
        self.done = True

    async def send_message(self, text, ephemeral=False):
        self.done = True
        self.messages.append(text)

    async def send_modal(self, modal):
        self.done = True
        self.modal = modal


class FakeInteraction:
    def __init__(self):
        self.user = types.SimpleNamespace(display_name="Tester", global_name="Tester", name="tester", id=1)
        self.response = FakeResponse()
        self.edits = []
        self.channel = MagicMock()
        self.channel.send = AsyncMock()

    async def edit_original_response(self, **kw):
        self.edits.append(kw)

    @property
    def final(self):
        return self.edits[-1]


def make_bot(tmp_path, has_variation=True):
    settings = Settings(tmp_path)
    engine = Engine(settings, tmp_path)
    engine._comfy = FakeComfy(settings.get()["comfy_url"], has_variation)
    bot = botmod.PicGenBot(engine)
    botmod.BOT = bot
    return bot, engine


def attachment(data, name="photo.png"):
    a = MagicMock()
    a.content_type, a.size, a.filename = "image/png", len(data), name
    a.read = AsyncMock(return_value=data)
    return a


def job_id_from(edit):
    return edit["view"].children[0].job_id


def test_all_flows(tmp_path):
    async def run():
        bot, engine = make_bot(tmp_path)
        # /imagine
        inter = FakeInteraction()
        await bot.guarded(inter, bot.do_imagine(inter, "a fox", "16:9", True))
        final = inter.final
        assert "attachments" in final and final["attachments"][0].filename.endswith("_grid.jpg"), final.get("content")
        jid = job_id_from(final)
        assert [c.custom_id for c in final["view"].children][:5] == [f"pg|u|{jid}|{i}" for i in range(4)] + [f"pg|r|{jid}|0"]
        # U2, V1, 🔄, Vary subtle/strong, 2x, 4K
        for action in ("u", "v", "r", "vs", "vx", "x2", "x4"):
            b = FakeInteraction()
            await bot.on_button(b, action, jid, 1)
            assert "attachments" in b.final, (action, b.final.get("content"))
            if action == "u":
                assert [c.custom_id.split("|")[1] for c in b.final["view"].children] == ["vs", "vx", "e", "x2", "x4"]
            if action == "x4":
                assert "3840x" in b.final["content"]
        # ✏️ Edit: pop-up, then submit
        b = FakeInteraction()
        await bot.on_button(b, "e", jid, 2)
        assert isinstance(b.response.modal, botmod.EditModal)
        m = FakeInteraction()
        await bot.on_edit_submit(m, b.response.modal.job, 2, "make it night")
        assert "attachments" in m.final and "picture 3" in m.final["content"]
        edit_job = engine.load_job(job_id_from(m.final))
        assert edit_job.is_edit and (engine.folder(edit_job) / "source.png").exists()
        # 🔄 and V on an edit grid redo the edit
        for action in ("r", "v"):
            b = FakeInteraction()
            await bot.on_button(b, action, edit_job.id, 0)
            assert "attachments" in b.final, b.final.get("content")
            assert "image" in engine._comfy.runs[-1]
        # /edit and /upscale with uploads
        e = FakeInteraction()
        await bot.guarded(e, bot.do_edit_upload(e, attachment(png(800, 600)), "add a hat", True))
        assert "attachments" in e.final
        u = FakeInteraction()
        await bot.guarded(u, bot.do_upscale_upload(u, attachment(png(800, 600)), 4))
        assert "3840x2880" in u.final["content"]
        # errors: bad upload, too-big upscale, unknown job
        bad = FakeInteraction()
        await bot.guarded(bad, bot.do_upscale_upload(bad, attachment(b"not a picture"), 2))
        assert bad.final["content"].startswith("❌")
        big = FakeInteraction()
        await bot.guarded(big, bot.do_upscale_upload(big, attachment(png(3840, 2160)), 4))
        assert "already" in big.final["content"]
        gone = FakeInteraction()
        await bot.on_button(gone, "u", "ffffffffff", 0)
        assert "too old" in gone.response.messages[0]
        # pictures were saved for the share
        assert len(list((tmp_path / "pictures").rglob("*.png"))) > 10
    asyncio.run(run())


def test_vary_without_node_says_so(tmp_path):
    async def run():
        bot, engine = make_bot(tmp_path, has_variation=False)
        inter = FakeInteraction()
        await bot.guarded(inter, bot.do_imagine(inter, "a fox", "1:1", True))
        jid = job_id_from(inter.final)
        b = FakeInteraction()
        await bot.on_button(b, "v", jid, 0)
        assert "PIC_GEN node" in b.final["content"]
    asyncio.run(run())


def test_role_filter(tmp_path):
    async def run():
        (tmp_path / "settings.json").write_text('{"allowed_role_ids": ["42"]}')
        bot, engine = make_bot(tmp_path)
        inter = FakeInteraction()
        inter.user = MagicMock(spec=discord.Member)
        inter.user.roles = [types.SimpleNamespace(id=7)]
        await bot.guarded(inter, bot.do_imagine(inter, "a fox", "1:1", True))
        assert "don't have a role" in inter.response.messages[0]
    asyncio.run(run())


def test_prompt_suggestions(tmp_path):
    async def run():
        (tmp_path / "settings.json").write_text('{"llm": {"enabled": true}}')
        bot, engine = make_bot(tmp_path)
        ideas = [{"label": f"Idea {i}", "prompt": f"a fox, change {i}"} for i in range(4)]
        edit_ideas = [{"label": f"Edit idea {i}", "prompt": f"Make it night, change {i}."} for i in range(4)]
        asked = []

        async def fake_chat(s, system, message, user_name, what="Prompt helper"):
            asked.append(system)
            if system == s["llm"]["suggest_system_prompt"]:
                return '```json\n{"suggestions": ' + json.dumps(ideas) + '}\n```'
            if system == s["llm"]["suggest_edit_system_prompt"]:
                return json.dumps({"suggestions": edit_ideas})
            if system == s["llm"]["edit_system_prompt"]:
                return '{"prompt": "Make it night."}'
            return '{"prompt": "a detailed fox"}'

        llmmod.chat = fake_chat
        inter = FakeInteraction()
        await bot.guarded(inter, bot.do_imagine(inter, "a fox", "1:1", False))
        jid = job_id_from(inter.edits[-2])
        assert "view" in inter.final and "attachments" not in inter.final  # the row is added to the posted grid
        ids = [c.custom_id for c in inter.final["view"].children]
        assert ids[-4:] == [f"pg|p|{jid}|{i}" for i in range(4)]
        assert [c.item.label for c in inter.final["view"].children][-4:] == [d["label"] for d in ideas]
        job = engine.load_job(jid)
        assert job.suggestions == ideas
        # clicking a suggestion remakes the grid with that prompt and the same seed, and gets new suggestions for that prompt
        b = FakeInteraction()
        await bot.on_button(b, "p", jid, 2)
        new = engine.load_job(job_id_from(b.edits[-2]))
        assert new.prompt == "a fox, change 2" and new.seed == job.seed and new.kind == "suggest"
        assert "Idea 2" in b.edits[-2]["content"] and new.suggestions == ideas
        assert asked[-1] == engine.s["llm"]["suggest_system_prompt"]
        # 🔄 and V keep the suggestions (same prompt) without asking the LLM again
        for action in ("r", "v"):
            n = len(asked)
            c = FakeInteraction()
            await bot.on_button(c, action, jid, 0)
            assert engine.load_job(job_id_from(c.final)).suggestions == ideas and len(asked) == n
            assert [x.custom_id.split("|")[1] for x in c.final["view"].children][-4:] == ["p"] * 4
        # an edit grid gets edit suggestions; clicking one redoes the edit on the same picture with that instruction
        u = FakeInteraction()
        await bot.on_button(u, "e", jid, 1)
        m = FakeInteraction()
        await bot.on_edit_submit(m, u.response.modal.job, 1, "make it night")
        edit_job = engine.load_job(job_id_from(m.edits[-2]))
        assert edit_job.is_edit and edit_job.suggestions == edit_ideas
        assert [x.custom_id for x in m.final["view"].children][-4:] == [f"pg|p|{edit_job.id}|{i}" for i in range(4)]
        e2 = FakeInteraction()
        await bot.on_button(e2, "p", edit_job.id, 3)
        sug_edit = engine.load_job(job_id_from(e2.edits[-2]))
        assert sug_edit.is_edit and sug_edit.prompt == "Make it night, change 3." and sug_edit.seed == edit_job.seed
        assert (engine.folder(sug_edit) / "source.png").exists() and "image" in engine._comfy.runs[-1]
        assert sug_edit.suggestions == edit_ideas
        # raw prompts and a helper that fails get no row
        r = FakeInteraction()
        await bot.guarded(r, bot.do_imagine(r, "a fox", "1:1", True))
        assert not engine.load_job(job_id_from(r.final)).suggestions

        async def broken_chat(*a, **k):
            return None

        llmmod.chat = broken_chat
        f = FakeInteraction()
        await bot.guarded(f, bot.do_imagine(f, "a fox", "1:1", False))
        assert "attachments" in f.final and not engine.load_job(job_id_from(f.final)).suggestions

    real = llmmod.chat
    try:
        asyncio.run(run())
    finally:
        llmmod.chat = real


def test_say(tmp_path):
    """/say: off by default, then a voice clip from a fake OpenAI-style speech server; errors are shown plainly."""
    from aiohttp import web

    seen = []

    async def speech_handler(request):
        body = await request.json()
        seen.append(body)
        if body["input"] == "fail please":
            return web.Response(status=500, text="boom")
        return web.Response(body=b"ID3" + b"\0" * 500, content_type="audio/mpeg")

    async def voices_handler(request):
        return web.json_response({"voices": [{"id": "Deep", "name": "Deep"}, {"id": "aria", "name": "aria"}]})

    async def run():
        app = web.Application()
        app.router.add_post("/v1/audio/speech", speech_handler)
        app.router.add_get("/v1/audio/voices", voices_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            bot, engine = make_bot(tmp_path)
            inter = FakeInteraction()
            await bot.guarded(inter, bot.do_say(inter, "hello", ""))
            assert "isn't set up" in inter.final["content"]

            (tmp_path / "settings.json").write_text(json.dumps({"speech": {"enabled": True, "url": f"http://127.0.0.1:{port}/v1/audio/speech", "voice": "Deep"}}))
            inter = FakeInteraction()
            await bot.guarded(inter, bot.do_say(inter, "  Dinner   is ready! ", ""))
            final = inter.final
            assert final["attachments"][0].filename == "say.mp3"
            assert final["content"] == "🔊 **Tester**: Dinner is ready!"
            assert seen[-1] == {"input": "Dinner is ready!", "response_format": "mp3", "voice": "Deep"}

            inter = FakeInteraction()
            await bot.guarded(inter, bot.do_say(inter, "Hi there", "aria"))
            assert seen[-1]["voice"] == "aria" and "(aria)" in inter.final["content"]

            assert await engine.voice_names() == ["Deep", "aria"]

            inter = FakeInteraction()
            await bot.guarded(inter, bot.do_say(inter, "fail please", ""))
            assert "HTTP 500" in inter.final["content"] and "attachments" in inter.final and inter.final["attachments"] == []

            inter = FakeInteraction()
            await bot.guarded(inter, bot.do_say(inter, "x" * 1001, ""))
            assert "too long" in inter.final["content"]
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_say_server_down(tmp_path):
    async def run():
        (tmp_path / "settings.json").write_text(json.dumps({"speech": {"enabled": True, "url": "http://127.0.0.1:9/v1/audio/speech"}}))
        bot, engine = make_bot(tmp_path)
        inter = FakeInteraction()
        await bot.guarded(inter, bot.do_say(inter, "hello", ""))
        assert "not reachable" in inter.final["content"]
        assert await engine.voice_names() == []

    asyncio.run(run())
