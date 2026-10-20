from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.conditioning import (
    NOTE_UNITS,check_conditioning,compile_chunk_annotation,quantize_annotation,
    quantize_duration,require_conditioning,rest_tokens)


def sample():
    return dict(item_name='example',word=['あ','い'],word_dur=[.3,.4],
        pitch=[60,62],pitch_dur=[.3,.4],pitch2word=[0,1],bpm=120)


class ConditioningSafety(unittest.TestCase):
    def test_quantization_matches_all_native_note_centers(self):
        for bpm in [60,87,120,174]:
            for token,units in NOTE_UNITS.items():
                self.assertEqual(quantize_duration(60/bpm*units,bpm),token)

    def test_ghost_boundary_notes_are_blocked_before_rendering(self):
        e=sample();e.update(pitch=[0,60,62],pitch_dur=[1e-7,.3-1e-7,.4],pitch2word=[0,0,1])
        e=quantize_annotation(e)
        with self.assertRaisesRegex(ValueError,'sub_resolution_note_fragments'):require_conditioning([e])

    def test_true_short_unit_is_retained_and_warned_without_deletion(self):
        e=sample();e['word_dur'][0]=e['pitch_dur'][0]=.01;e=quantize_annotation(e)
        before=deepcopy(e);r=check_conditioning(e)
        self.assertTrue(r['valid_for_render']);self.assertEqual(e,before)
        self.assertIn('short_source_lyric_units',[w['code'] for w in r['warnings']])
        self.assertEqual(r['quality_status'],'not_assessed')

    def test_unknown_pitch_is_flagged_without_guessing(self):
        e=sample();e['pitch'][0]=0;e=quantize_annotation(e)
        r=check_conditioning(e);self.assertTrue(r['valid_for_render'])
        self.assertIn('unknown_lyric_pitch',[w['code'] for w in r['warnings']])

    def test_word_mapping_and_duration_errors_fail(self):
        for fault in ['missing','time','token','nan']:
            e=quantize_annotation(sample())
            if fault=='missing':e['pitch2word']=[0,0]
            if fault=='time':e['pitch_dur'][0]+=.1
            if fault=='token':e['note'][0]='<NOTE_UNKNOWN>'
            if fault=='nan':e['word_dur'][0]=float('nan')
            self.assertFalse(check_conditioning(e)['valid_for_render'],fault)

    def test_compilation_keeps_original_words_and_english(self):
        chunk=dict(id='chunk',content_start=3.,words=[
            dict(word='あ',start=3.,end=3.3,pitch=[60],pitch_dur=[.3],note=['<NOTE_8>']),
            dict(word='happy',start=3.5,end=3.9,pitch=[62],pitch_dur=[.4],note=['<NOTE_DOT_8>'])])
        e=compile_chunk_annotation(chunk,120)
        self.assertEqual(e['word'],['SP','あ','SP','happy','SP'])
        self.assertAlmostEqual(sum(e['pitch_dur']),sum(e['word_dur']))

    def test_dotted_rest_quantization_preserves_native_tick_grid(self):
        self.assertEqual(rest_tokens(60/120*.1875,120),['<NOTE_DOT_32>'])


if __name__=='__main__':unittest.main()
