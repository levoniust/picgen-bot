import io
import json

import pytest
from PIL import Image, PngImagePlugin

from picgen import images, workflows
from picgen.config import DEFAULTS, Settings, merge, validate
from picgen.engine import Engine
from picgen.llm import extract_prompt


@pytest.mark.parametrize("content,expected", [
    ('{"prompt": "a red fox"}', "a red fox"),
    ('```json\n{"prompt": "a red fox"}\n```', "a red fox"),
    ('Sure! {"prompt": "a red fox"} hope that helps', "a red fox"),
    ('{"prompt": "a sign that says "OPEN LATE" in chalk"}', 'a sign that says "OPEN LATE" in chalk'),
    ('<think>hmm</think>{"prompt": "x y"}', "x y"),
    ("no json here", None),
    ("", None),
])
def test_extract_prompt(content, expected):
    assert extract_prompt(content) == expected


def test_sizes():
    assert images.size_for(1.0, 1024) == (1024, 1024)
    w, h = images.size_for(16 / 9, 1024)
    assert (w, h) == (1344, 768)
    assert images.parse_aspect("9:16") == pytest.approx(9 / 16)
    assert images.parse_aspect("junk") == 1.0
    assert images.upscale_target(1024, 1024, 2, 3840) == (2048, 2048)
    assert images.upscale_target(1344, 768, 1000, 3840) == (3840, 2194)
    assert images.upscale_target(4000, 3000, 2, 3840) == (3840, 2880)


def png_with_text(size=(64, 48)):
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", "x" * 10000)
    out = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(out, "PNG", pnginfo=info)
    return out.getvalue()


def test_clean_png_drops_metadata():
    data = png_with_text()
    cleaned = images.clean_png(data)
    assert len(cleaned) < len(data)
    assert not Image.open(io.BytesIO(cleaned)).text
    assert images.image_size(cleaned) == (64, 48)


def test_grid_and_jpeg():
    pics = [png_with_text((100, 100)) for _ in range(4)]
    grid = images.make_grid(pics, cell=50, gap=4)
    assert images.image_size(grid) == (104, 104)
    assert images.to_jpeg(pics[0], 10**6)[:2] == b"\xff\xd8"


def test_settings_merge_and_validate(tmp_path):
    s = merge(DEFAULTS, {"llm": {"enabled": True}, "steps": 10})
    assert s["llm"]["enabled"] and s["llm"]["model"] == "" and s["steps"] == 10
    with pytest.raises(ValueError):
        validate(merge(DEFAULTS, {"vary_subtle": 2}))
    (tmp_path / "settings.json").write_text(json.dumps({"size": 768}))
    settings = Settings(tmp_path)
    assert settings.get()["size"] == 768
    (tmp_path / "settings.json").write_text("{broken")
    settings.mtime = None
    assert settings.get()["size"] == 768 and settings.error


def test_workflows_link_to_existing_nodes():
    s = merge(DEFAULTS, {})
    for wf in (workflows.create(s, "p", 1024, 1024, 1), workflows.create(s, "p", 1024, 1024, 1, 5, 0.2),
               workflows.edit(s, "p", "x.png [temp]", 1), workflows.upscale(s, "x.png [temp]", 2048, 2048)):
        assert workflows.OUT in wf
        for node in wf.values():
            for val in node["inputs"].values():
                if isinstance(val, list):
                    assert val[0] in wf
    assert workflows.create(s, "p", 64, 64, 1, 5, 0.2)["noise"]["class_type"] == "PicGenVariationNoise"
    assert workflows.create(s, "p", 64, 64, 1)["noise"]["class_type"] == "RandomNoise"
    assert "lora" not in workflows.create(merge(DEFAULTS, {"models": {"lora": ""}}), "p", 64, 64, 1)


def test_jobs_roundtrip_and_vary_seeds(tmp_path):
    engine = Engine(Settings(tmp_path), tmp_path)
    job = engine.new_job("imagine", "tim", request="r", prompt="p", width=1024, height=1024, seed=100)
    engine.save_job(job, [png_with_text()] * 4)
    loaded = engine.load_job(job.id)
    assert loaded.seed == 100 and len(loaded.files) == 4
    v = engine.vary_job(loaded, 2, 0.15, "x")
    assert v.seed == 102 and v.var_strength == 0.15
    v2 = engine.vary_job(v, 1, 0.45, "x")
    assert v2.seed == 102 and v2.var_strength == 0.45
    assert engine.load_job("../etc") is None


def test_max_queue(tmp_path):
    import asyncio
    from picgen.engine import UserError
    (tmp_path / "settings.json").write_text('{"max_queue": 2}')
    engine = Engine(Settings(tmp_path), tmp_path)

    async def run():
        engine.pending = 2
        with pytest.raises(UserError, match="busy"):
            await engine.in_line(lambda: asyncio.sleep(0))
        engine.pending = 1
        assert await engine.in_line(lambda: asyncio.sleep(0, result="ok")) == "ok"
    asyncio.run(run())
