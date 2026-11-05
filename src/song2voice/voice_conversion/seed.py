"""Seed-VC singing adapter; local weights, original pitch and tempo."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def audit_weights(module, state, ignored=(), regenerated=()):
    state = {k.removeprefix('module.'): v for k, v in state.items()}
    expected = module.state_dict()
    missing = [k for k, v in expected.items() if k not in state or state[k].shape != v.shape]
    parameters = dict(module.named_parameters())
    locally_generated = [k for k in missing if k in regenerated and k not in parameters]
    required_missing = [k for k in missing if not k.startswith(ignored) and k not in locally_generated]
    if required_missing:
        raise ValueError('Checkpoint misses active model weights: '+str(required_missing))
    return dict(matched=len(expected)-len(missing),
                missing_inactive=[k for k in missing if k not in locally_generated],
                regenerated_buffers=locally_generated,
                unused_checkpoint_keys=sorted(set(state)-set(expected)))



class SeedVC:
    def __init__(self, request):
        import yaml
        self.root = Path(request['assets'])
        self.options = request['options']
        self.run = Path(request['run'])/'seed_vc'
        sys.path.insert(0, self.options['repo'])
        import inference
        self.native = inference
        original_load = inference.load_checkpoint
        def checked_load(model, optimizer, path, *args, **kwargs):
            import torch
            state = torch.load(path, map_location='cpu', weights_only=False)['net']
            audits = {name: audit_weights(module, state[name],
                      regenerated=('estimator.input_pos', 'estimator.t_embedder.freqs'))
                      for name, module in model.items()}
            (self.run/'weight_loading.json').write_text(json.dumps(audits, indent=2))
            return original_load(model, optimizer, path, *args, **kwargs)
        inference.load_checkpoint = checked_load
        def local_aux(repo, model_filename, config_filename=None):
            paths = {'rmvpe.pt': self.root/'rmvpe/rmvpe.pt',
                     'campplus_cn_common.bin': self.root/'campplus/campplus_cn_common.bin'}
            return str(paths[model_filename])
        inference.load_custom_model_from_hf = local_aux
        config = yaml.safe_load((self.root/'seed/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml').read_text())
        config['model_params']['vocoder']['name'] = str(self.root/'bigvgan-44k')
        config['model_params']['speech_tokenizer']['name'] = str(self.root/'whisper-small')
        config_path = self.run/'resolved_model.yaml'
        config_path.write_text(yaml.safe_dump(config))
        self.args = SimpleNamespace(f0_condition=True, fp16=self.options.get('fp16', True),
            checkpoint=str(self.root/'seed/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth'),
            config=str(config_path), auto_f0_adjust=False, semi_tone_shift=0, length_adjust=1.,
            diffusion_steps=self.options.get('steps', 30), inference_cfg_rate=self.options.get('cfg', .7),
            output=str(self.run/'native'))
        loaded = inference.load_models(self.args)
        inference.load_models = lambda args: loaded

    def convert(self, source, reference):
        import torchaudio
        captured = {}
        original_save = torchaudio.save
        def capture(path, tensor, sr, **kwargs):
            captured.update(audio=tensor.detach().float().cpu().numpy().squeeze(), sr=sr)
        self.args.source, self.args.target = source, reference
        # Capture the upstream's final waveform before its default PCM quantization.
        torchaudio.save = capture
        try:
            self.native.main(self.args)
        finally:
            torchaudio.save = original_save
        return captured['audio'], captured['sr'], dict(auto_shift=False, pitch_shift=0,
            length_adjust=1., steps=self.args.diffusion_steps, cfg=self.args.inference_cfg_rate)
