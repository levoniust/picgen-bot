"""ComfyUI workflows (API format) for Qwen Image 2.1. Output node id is always "out"."""

OUT = "out"


def _model_nodes(s):
    m = s["models"]
    nodes = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
    }
    model = ["unet", 0]
    if m.get("lora"):
        nodes["lora"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": m["lora"], "strength_model": float(m.get("lora_strength", 1.0))}}
        model = ["lora", 0]
    nodes["cache"] = {"class_type": "QwenImage21Cache", "inputs": {"model": model, "device": "auto", "dtype": "default"}}
    return nodes


def _sampling(s, nodes, latent, seed, var_seed=0, var_strength=0.0):
    """Positive-only sampling (BasicGuider = cfg 1, so the negative prompt is never used), with optional variation noise."""
    if var_strength > 0:
        nodes["noise"] = {"class_type": "PicGenVariationNoise", "inputs": {"noise_seed": int(seed), "variation_seed": int(var_seed), "variation_strength": float(var_strength)}}
    else:
        nodes["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    nodes["guider"] = {"class_type": "BasicGuider", "inputs": {"model": ["cache", 0], "conditioning": ["encode", 0]}}
    nodes["sampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": s["sampler"]}}
    nodes["sigmas"] = {"class_type": "BasicScheduler", "inputs": {"model": ["cache", 0], "scheduler": s["scheduler"], "steps": int(s["steps"]), "denoise": 1.0}}
    nodes["sample"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0], "sigmas": ["sigmas", 0], "latent_image": latent}}
    nodes["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}}
    nodes[OUT] = {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}}
    return nodes


def create(s, prompt, width, height, seed, var_seed=0, var_strength=0.0):
    """Text to image."""
    nodes = _model_nodes(s)
    nodes["encode"] = {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["clip", 0], "prompt": prompt, "negative_prompt": "", "resolution": 1024}}
    nodes["latent"] = {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
    return _sampling(s, nodes, ["latent", 0], seed, var_seed, var_strength)


def edit(s, prompt, image_name, seed, var_seed=0, var_strength=0.0):
    """Changes an uploaded picture (image_name from Comfy.upload). The result has the picture's shape at about size x size pixels."""
    nodes = _model_nodes(s)
    nodes["image"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    nodes["encode"] = {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["clip", 0], "vae": ["vae", 0], "images.image_1": ["image", 0], "prompt": prompt, "negative_prompt": "", "resolution": int(s["size"])}}
    return _sampling(s, nodes, ["encode", 2], seed, var_seed, var_strength)


def upscale(s, image_name, width, height):
    """AI upscale (4x model) then an exact resize to width x height."""
    return {
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "model": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": s["models"]["upscale"]}},
        "up": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["model", 0], "image": ["image", 0]}},
        "scale": {"class_type": "ImageScale", "inputs": {"image": ["up", 0], "upscale_method": "lanczos", "width": int(width), "height": int(height), "crop": "disabled"}},
        OUT: {"class_type": "PreviewImage", "inputs": {"images": ["scale", 0]}},
    }
