from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from song2voice.pitch_repair import propose_pitch,repair_annotations


def point(t,midi,probability=1.):
    return dict(time=t,hz=440*2**((midi-69)/12),voiced_probability=probability)


def entry(rest=False):
    return dict(line_index=0,word=['あ','SP' if rest else 'い','う'],word_dur=[.1,.1,.1],
        ph=['a','SP' if rest else 'i','u'],ph_dur=[.1,.1,.1],ph2word=[0,1,2],
        pitch=[60,0,60],pitch_dur=[.1,.1,.1],pitch2word=[0,1,2],note=['<NOTE_32>']*3)


def measurement():
    return dict(line_index=0,unit_index=1,word='い',start=.1,end=.2,unit_rms=.1,phrase_reference_rms=.2,
        primary=[point(t,64) for t in [.12,.13,.14,.15]],secondary=[point(t,64.1) for t in [.12,.13,.14,.15]])


class PitchRecovery(unittest.TestCase):
    def test_agreeing_measured_pitch_changes_only_unknown_pitch(self):
        raw=entry();before=deepcopy(raw)
        output,audit=repair_annotations([raw],[measurement()])
        self.assertEqual(raw,before)
        self.assertEqual(output[0]['pitch'],[60,64,60])
        for field in ['word','word_dur','ph','ph_dur','ph2word','pitch_dur','pitch2word','note']:
            self.assertEqual(output[0][field],raw[field])
        self.assertEqual(audit[0]['kind'],'measured')

    def test_a_second_repair_stage_preserves_previous_provenance(self):
        raw=entry();history=[dict(stage='previous',status='unresolved')]
        raw['pitch_repair_provenance']=deepcopy(history)
        output,audit=repair_annotations([raw],[measurement()])
        self.assertEqual(raw['pitch_repair_provenance'],history)
        self.assertEqual(output[0]['pitch_repair_provenance'][:1],history)
        self.assertEqual(output[0]['pitch_repair_provenance'][1:],audit)

    def test_near_silence_is_not_filled_even_with_spurious_F0(self):
        m=measurement();m['unit_rms']=.00001
        self.assertEqual(propose_pitch(entry(),m)['status'],'unresolved')

    def test_octave_disagreement_is_not_guessed(self):
        m=measurement();m['secondary']=[point(t,76) for t in [.12,.13,.14,.15]]
        self.assertEqual(propose_pitch(entry(),m)['status'],'unresolved')

    def test_single_unconfirmed_frame_does_not_trigger_a_pitch(self):
        m=measurement();m.update(start=.1,end=.11,primary=[point(.105,83)],secondary=[])
        e=entry();e['word_dur']=[.1,.01,.19]
        self.assertEqual(propose_pitch(e,m)['status'],'unresolved')

    def test_breath_rest_and_existing_pitched_units_are_protected(self):
        with self.assertRaises(ValueError):propose_pitch(entry(rest=True),measurement())
        e=entry();e['pitch'][1]=65
        with self.assertRaises(ValueError):propose_pitch(e,measurement())

    def test_short_consonant_context_is_explicitly_inferred(self):
        e=entry();e['word'][1]='せ';e['ph'][1]='s'
        m=measurement();m.update(word='せ',primary=[point(t,68) for t in [.22,.23,.24,.25]],
            secondary=[point(t,68.1) for t in [.22,.23,.24,.25]])
        decision=propose_pitch(e,m)
        self.assertEqual(decision['status'],'recovered')
        self.assertEqual(decision['kind'],'inferred')
        self.assertEqual(decision['confidence'],'low')

    def test_context_does_not_cross_an_explicit_rest(self):
        e=entry();e.update(word=['せ','SP','あ'],word_dur=[.1,.01,.19],ph=['s','SP','a'])
        m=measurement();m.update(unit_index=0,word='せ',start=0.,end=.1,
            primary=[point(t,68) for t in [.12,.13,.14,.15]],secondary=[point(t,68) for t in [.12,.13,.14,.15]])
        e['pitch'][0]=0
        self.assertEqual(propose_pitch(e,m)['status'],'unresolved')

    def test_mismatched_measurement_timing_is_rejected(self):
        m=measurement();m['start']+=.02
        with self.assertRaises(ValueError):propose_pitch(entry(),m)


if __name__=='__main__':unittest.main()
