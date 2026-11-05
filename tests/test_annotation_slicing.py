import unittest
import numpy as np
from song2voice.vocal2midi_native_worker import bound_audio_chunks, check_asr_capacity, record_empty_asr


class SlicingTests(unittest.TestCase):
    def test_bounds_preserve_original_samples_offsets_and_gaps(self):
        wave=np.arange(2500,dtype=float)
        def split(x,sr,*args):
            return [dict(offset=i/sr,waveform=x[i:i+900]) for i in range(0,len(x),900)]
        output=bound_audio_chunks([dict(offset=13.,waveform=wave),dict(offset=50.,waveform=wave[:300])],100,splitter=split)
        np.testing.assert_array_equal(np.concatenate([x['waveform'] for x in output[:-1]]),wave)
        self.assertEqual([r['offset'] for r in output],[13.,22.,31.,50.])
        self.assertLessEqual(max(len(r['waveform']) for r in output),1000)

    def test_slicer_cannot_skip_or_duplicate_samples(self):
        with self.assertRaisesRegex(ValueError,'coverage'):
            bound_audio_chunks([dict(offset=0.,waveform=np.ones(2000))],100,
                splitter=lambda *args:[dict(offset=.1,waveform=np.ones(900))])

    def test_static_asr_never_truncates(self):
        check_asr_capacity(441000,44100,160000,16000)
        with self.assertRaisesRegex(ValueError,'never truncate'):
            check_asr_capacity(441001,44100,160000,16000)

    def test_strong_unrecognized_content_blocks_and_weak_content_has_audit(self):
        with self.assertRaisesRegex(ValueError,'active chunk'):
            record_empty_asr(dict(line_index=3),.1,.2)
        row=record_empty_asr(dict(line_index=0,audio='source.wav'),.02,.2)
        self.assertEqual(row['audio'],'source.wav')
        self.assertEqual(row['status'],'unrecognized_low_energy')


if __name__=='__main__':unittest.main()
