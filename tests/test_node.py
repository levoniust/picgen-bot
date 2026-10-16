"""Tests the ComfyUI node with stand-ins for comfy.sample / comfy.nested_tensor (needs torch; skipped without it)."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")


@pytest.fixture(scope="module")
def node():
    comfy = types.ModuleType("comfy")
    sample = types.ModuleType("comfy.sample")

    def prepare_noise(latent, seed, inds=None):
        g = torch.manual_seed(seed)
        return torch.randn(latent.size(), generator=g, dtype=torch.float32)

    sample.prepare_noise = prepare_noise
    nested = types.ModuleType("comfy.nested_tensor")
    nested.NestedTensor = list
    comfy.sample, comfy.nested_tensor = sample, nested
    sys.modules.update({"comfy": comfy, "comfy.sample": sample, "comfy.nested_tensor": nested})
    path = Path(__file__).parent.parent / "comfyui_nodes" / "picgen_nodes" / "__init__.py"
    spec = importlib.util.spec_from_file_location("picgen_nodes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_variation_noise(node):
    latent = {"samples": torch.zeros(1, 16, 64, 64)}
    get = lambda strength: node.PicGenVariationNoise().get_noise(5, 9, strength)[0].generate_noise(latent)
    base, other = get(0.0), get(1.0)
    assert torch.equal(base, torch.randn(1, 16, 64, 64, generator=torch.manual_seed(5)))
    assert torch.allclose(other, torch.randn(1, 16, 64, 64, generator=torch.manual_seed(9)))
    mid = get(0.15)
    # close to the base noise, and still unit-variance noise (slerp keeps the statistics)
    assert torch.nn.functional.cosine_similarity(mid.flatten(), base.flatten(), dim=0) > 0.95
    assert abs(mid.std().item() - 1) < 0.02
    assert "PicGenVariationNoise" in node.NODE_CLASS_MAPPINGS
