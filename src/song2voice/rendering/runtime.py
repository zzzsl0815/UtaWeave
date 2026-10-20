"""Portable process configuration for the archived rendering baseline."""
import json
import os
from pathlib import Path
import shutil
import sys


def runtime():
    path = os.environ.get('UTAWEAVE_RENDER_CONFIG')
    if not path:
        raise ValueError('Launch rendering with utaweave render --config CONFIG --out OUT')
    return json.loads(Path(path).read_text())


def activate_vocalrender():
    root = Path(runtime()['vocalrender_repo']).resolve()
    if not (root/'src/vocalrender').is_dir():
        raise ValueError('vocalrender_repo must point to the pinned VocalRender source checkout')
    sys.path[:0] = [str(root/'src'), str(root/'scripts')]


def ffmpeg_binary():
    configured = runtime().get('ffmpeg', 'ffmpeg')
    result = shutil.which(configured)
    if not result:
        raise ValueError('FFmpeg is missing; set ffmpeg in the render config')
    return result


def configure_lora(model):
    import math
    import torch
    from vocalrender.modules.layers.lora import LoRALinear
    scopes = ('base_lm', 'residual_lm', 'feat_decoder.estimator')
    requested = runtime().get('lora_scales', {})
    if set(requested)-set(scopes):
        raise ValueError('Unknown LoRA scale scope')
    ratios = {s: float(requested.get(s, .5 if s == scopes[-1] else 1.)) for s in scopes}
    if not all(math.isfinite(v) and v >= 0 for v in ratios.values()):
        raise ValueError('LoRA scales must be finite and nonnegative')
    audit = {s: dict(relative_strength=ratios[s], modules=0, effective_scalings=[]) for s in scopes}
    with torch.no_grad():
        for name, module in model.named_modules():
            if not isinstance(module, LoRALinear):
                continue
            scope = next((s for s in scopes if name.startswith(s+'.')), None)
            if scope is None:
                raise ValueError('Unexpected LoRA module: '+name)
            module.scaling.fill_(module._base_scaling*ratios[scope])
            audit[scope]['modules'] += 1
            audit[scope]['effective_scalings'].append(float(module.scaling.item()))
    if not any(a['modules'] for a in audit.values()):
        raise ValueError('No LoRA modules found')
    for item in audit.values():
        item['effective_scalings'] = sorted(set(item['effective_scalings']))
    return audit


def load_model(checkpoint):
    import torch
    activate_vocalrender()
    from vocalrender.training.model_factory import detect_model_classes
    config = json.loads((Path(checkpoint)/'lora_config.json').read_text())
    # The original training checkpoint embeds an absolute base_model path.
    # Always resolve the base model from this machine's runtime config.
    base = runtime()['base_model']
    classes = detect_model_classes(base)
    model = classes.model_cls.from_local(base, training=True,
        lora_config=classes.lora_config_cls(**config['lora_config']))
    model.config.dtype = 'bfloat16'
    model.device = 'cuda:0'
    model = model.float().to('cuda:0').eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    loaded, skipped = model.load_lora_weights(str(checkpoint), device='cuda:0')
    if skipped or not loaded:
        raise ValueError(f'Incomplete LoRA load: {len(loaded)} loaded, {len(skipped)} skipped')
    expected = runtime().get('expected_lora_tensors', 384)
    if expected is not None and len(loaded) != expected:
        raise ValueError(f'Expected {expected} LoRA tensors, got {len(loaded)}')
    model.set_lora_enabled(True)
    return model, configure_lora(model)
