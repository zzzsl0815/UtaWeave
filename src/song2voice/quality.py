"""SingMOS input and frame pooling without the former crawler-script dependency."""


def load_audio(path, target_sr=16000, max_duration=None):
    import librosa
    import torch
    try:
        samples, _ = librosa.load(path, sr=target_sr, mono=True)
        if max_duration is not None:
            samples = samples[:round(max_duration*target_sr)]
        return dict(wave=torch.from_numpy(samples).float(),error=None)
    except Exception as error:
        return dict(wave=None,error=str(error))


def pad_sequences(waves):
    import torch
    lengths = torch.tensor([len(w) for w in waves],dtype=torch.long)
    return torch.nn.utils.rnn.pad_sequence(waves,batch_first=True), lengths


class FrameScoreCapture:
    def __init__(self):
        self.frame_scores = None
        self.handle = None

    def register(self, decoder):
        def capture(module, inputs, output):
            self.frame_scores = output.squeeze(-1)
        self.handle = decoder.register_forward_hook(capture)

    def remove(self):
        if self.handle is not None:
            self.handle.remove()


def compute_masked_mean(scores, lengths, hop_size=320):
    import torch
    counts = torch.div(lengths+hop_size-1,hop_size,rounding_mode='floor')
    mask = torch.arange(scores.shape[1],device=scores.device)[None,:] < counts[:,None]
    return scores.masked_fill(~mask,0).sum(dim=1)/counts.clamp_min(1)
