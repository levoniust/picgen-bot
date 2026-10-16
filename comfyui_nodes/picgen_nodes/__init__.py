"""PIC_GEN helper nodes for ComfyUI.

PicGenVariationNoise: noise for SamplerCustomAdvanced that mixes the noise of a second seed into the noise of the main seed
(spherical interpolation). Strength 0 = exactly the main seed's picture, 1 = exactly the variation seed's picture; small values
give close variations of the same picture at full quality (unlike img2img, which with few-step turbo models looks grainy).
"""
import torch

import comfy.nested_tensor
import comfy.sample


def slerp(strength, low, high):
    """Spherical interpolation between two noise tensors, per batch item. Keeps the noise's statistics (unlike a plain mix)."""
    if strength <= 0:
        return low
    if strength >= 1:
        return high
    shape = low.shape
    low_flat = low.reshape(shape[0], -1).float()
    high_flat = high.reshape(shape[0], -1).float()
    low_norm = low_flat / low_flat.norm(dim=1, keepdim=True)
    high_norm = high_flat / high_flat.norm(dim=1, keepdim=True)
    dot = (low_norm * high_norm).sum(1).clamp(-1, 1)
    omega = torch.acos(dot)
    so = torch.sin(omega)
    mixed = []
    for i in range(shape[0]):
        if so[i].abs() < 1e-6:
            mixed.append((1 - strength) * low_flat[i] + strength * high_flat[i])
        else:
            mixed.append((torch.sin((1 - strength) * omega[i]) / so[i]) * low_flat[i] + (torch.sin(strength * omega[i]) / so[i]) * high_flat[i])
    return torch.stack(mixed).reshape(shape).to(low.dtype)


class NoiseVariation:
    """A NOISE object (same interface as ComfyUI's Noise_RandomNoise)."""

    def __init__(self, seed, variation_seed, strength):
        self.seed = seed
        self.variation_seed = variation_seed
        self.strength = strength

    def generate_noise(self, input_latent):
        latent = input_latent["samples"]
        batch_inds = input_latent.get("batch_index")
        base = comfy.sample.prepare_noise(latent, self.seed, batch_inds)
        if self.strength <= 0:
            return base
        other = comfy.sample.prepare_noise(latent, self.variation_seed, batch_inds)
        if getattr(base, "is_nested", False):
            return comfy.nested_tensor.NestedTensor([slerp(self.strength, a, b) for a, b in zip(base.unbind(), other.unbind())])
        return slerp(self.strength, base, other)


class PicGenVariationNoise:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "noise_seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "variation_seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "variation_strength": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
        }}

    RETURN_TYPES = ("NOISE",)
    FUNCTION = "get_noise"
    CATEGORY = "sampling/custom_sampling/noise"
    DESCRIPTION = "Noise of noise_seed with some noise of variation_seed mixed in (0 = same picture, 1 = the other seed's picture). For close variations of a picture."

    def get_noise(self, noise_seed, variation_seed, variation_strength):
        return (NoiseVariation(noise_seed, variation_seed, variation_strength),)


NODE_CLASS_MAPPINGS = {"PicGenVariationNoise": PicGenVariationNoise}
NODE_DISPLAY_NAME_MAPPINGS = {"PicGenVariationNoise": "PIC_GEN Variation Noise"}
