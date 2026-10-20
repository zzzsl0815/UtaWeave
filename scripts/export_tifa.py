"""Export the official TiFA ST checkpoint using the unchanged upstream exporter."""
import argparse
import json
from pathlib import Path
import shutil
import sys


def main(repo, source, out):
    if out.exists():raise ValueError('Choose a new output directory')
    sys.path.insert(0,str(repo.resolve()))
    import torch,yaml
    from inference.backend import ForcedAlignmentInferenceModel
    from lib.config.schema import ModelConfig,InferenceConfig,ConfigurationScope
    from deployment.exporter import Exporter
    torch.set_num_threads(4)
    raw=yaml.safe_load((source/'config.yaml').read_text())
    model=ForcedAlignmentInferenceModel(ModelConfig.model_validate(raw['model'],scope=ConfigurationScope.FA),
        InferenceConfig.model_validate({'features':raw['inference']['features']}))
    model.load_state_dict(torch.load(source/'model.pt',map_location='cpu',weights_only=True)['state_dict'],strict=True)
    model.eval()
    Exporter(model,out,opset_version=18).export()
    features=raw['inference']['features']
    config=dict(samplerate=model.sample_rate,timestep=model.timestep,hop_size=features['hop_size'],
        fft_size=features['fft_size'],win_size=features['win_size'],num_mels=features['spectrogram']['num_bins'],
        vocab_size=raw['model']['max_vocab_size'])
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    shutil.copy2(source/'vocabulary.json',out/'vocabulary.json')
    for folder in ['dictionaries','assets']:shutil.copytree(source/folder,out/folder)
    print('EXPORT_COMPLETE',out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['repo','source','out']:p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    main(args.repo,args.source,args.out)
