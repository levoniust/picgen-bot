"""End-to-end test without Discord: python -m picgen.selftest OUTDIR [--data DATA_DIR]

Uses DATA_DIR/settings.json (default: $PICGEN_DATA or ./data) and writes jobs to a scratch data folder inside OUTDIR.
Runs: imagine (with the prompt helper), raw 16:9, variations (if the PIC_GEN node is installed), an edit grid, 2x and 4K upscales.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import images
from .config import Settings
from .engine import Engine, UserError
from .comfy import ComfyError


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--data", default=os.environ.get("PICGEN_DATA", "data"))
    ap.add_argument("--only", default="", help="comma list of steps: imagine,raw,vary,edit,up2,up4")
    args = ap.parse_args()
    out = Path(args.outdir)
    work = out / "data"
    work.mkdir(parents=True, exist_ok=True)
    src_settings = Path(args.data) / "settings.json"
    if src_settings.exists():
        shutil.copy(src_settings, work / "settings.json")
    engine = Engine(Settings(work), work)
    only = set(filter(None, args.only.split(",")))
    results = []
    ok = True

    async def step(name, make):
        """make: a function returning the coroutine to run (so skipped steps never create it)."""
        nonlocal ok
        if only and name not in only:
            return None
        t = time.time()
        try:
            value = await engine.in_line(make)
            line = f"{name}: OK {time.time() - t:.0f}s"
        except (UserError, ComfyError) as ex:
            value = None
            skipped = isinstance(ex, UserError) and "PIC_GEN node" in str(ex)
            ok = ok and skipped
            line = f"{name}: {'SKIPPED' if skipped else 'FAIL'} {time.time() - t:.0f}s | {ex}"
        print(line, flush=True)
        results.append(line)
        return value

    def save(name, pictures):
        if not pictures:
            return
        sizes = [images.image_size(p) for p in pictures]
        (out / f"{name}.jpg").write_bytes(images.make_grid(pictures) if len(pictures) > 1 else images.to_jpeg(pictures[0], 20_000_000))
        results.append(f"    sizes {sizes}")
        print(f"    sizes {sizes}", flush=True)

    s = engine.s
    request = "a cozy bookshop at night in the rain, chalkboard sign that says 'OPEN LATE'"
    prompt, note = await engine.helper(request, "selftest")
    print(f"helper: {'used' if prompt != request else 'not used'} {note}\n    {prompt[:200]}", flush=True)
    w, h = images.size_for(1.0, int(s["size"]))
    job = engine.new_job("imagine", "selftest", request=request, prompt=prompt, width=w, height=h, seed=12345)
    pics = await step("imagine", lambda: engine.imagine(job))
    save("1_imagine", pics)

    w2, h2 = images.size_for(16 / 9, int(s["size"]))
    raw = engine.new_job("imagine", "selftest", request="fox", prompt="A red fox sitting in a snowy forest at sunrise, realistic photo", width=w2, height=h2, seed=777)
    save("2_raw_16x9", await step("raw", lambda: engine.imagine(raw)))

    if pics:
        vjob = engine.vary_job(job, 0, float(s["vary_subtle"]), "selftest")
        save("3_vary_subtle", await step("vary", lambda: engine.regrid(vjob)))
        change, note = await engine.helper("make it daytme with sunshine", "selftest", edit=True)
        print(f"edit helper: {change} {note}", flush=True)
        ejob = engine.new_job("edit", "selftest", request="make it daytme with sunshine", prompt=change, is_edit=True, seed=99)
        save("4_edit_grid", await step("edit", lambda: engine.edit(ejob, pics[0])))
        up2 = await step("up2", lambda: engine.upscale(pics[0], 2))
        if up2:
            (out / "5_up2.png").write_bytes(up2[0])
            print(f"    {up2[1]}x{up2[2]} {len(up2[0]) / 1e6:.1f} MB", flush=True)
        up4 = await step("up4", lambda: engine.upscale(pics[0], 4))
        if up4:
            (out / "6_up4k.png").write_bytes(up4[0])
            print(f"    {up4[1]}x{up4[2]} {len(up4[0]) / 1e6:.1f} MB", flush=True)
    (out / "results.txt").write_text("\n".join(results) + "\n")
    await engine.comfy.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
