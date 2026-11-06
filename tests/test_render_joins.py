import unittest
import numpy as np
from song2voice.rendering.assemble import protect_join, fit_timeline, SR


class JoinTests(unittest.TestCase):
    def test_timeline_trims_only_outside_padding(self):
        wave=np.arange(3*SR,dtype=np.float32)
        fitted,start,audit=fit_timeline(wave,-SR//2,.2,1.8,2*SR)
        self.assertEqual(start,0)
        np.testing.assert_array_equal(fitted,wave[SR//2:5*SR//2])
        self.assertEqual(audit['left_seconds'],.5)
        self.assertEqual(audit['right_seconds'],.5)

    def test_timeline_never_trims_lyrics_outside_original_clock(self):
        wave=np.zeros(3*SR,dtype=np.float32)
        for first,last in [(-.01,1.8),(.2,2.01)]:
            with self.assertRaisesRegex(ValueError,'Aligned lyrics leave'):
                fit_timeline(wave,-SR//2,first,last,2*SR)

    def clip(self, name, start, end, first, last):
        wave=np.ones(round((end-start)*SR),dtype=np.float32)*.1
        return dict(id=name,start=round(start*SR),wave=wave,weights=np.ones(len(wave)),first=first,last=last)

    def assert_body_untouched(self,clip):
        a=round(clip['first']*SR)-clip['start'];b=round(clip['last']*SR)-clip['start']
        self.assertTrue(np.all(clip['weights'][a:b]==1.))

    def test_incoming_first_phone_at_sample_zero_needs_only_outgoing_fade(self):
        left=self.clip('left',0.,2.1,.2,1.65)
        right=self.clip('right',2.,4.,2.,3.8)
        audit=protect_join(left,right)
        self.assertEqual(audit['method'],'independent_non_lyric_fades')
        self.assert_body_untouched(left);self.assert_body_untouched(right)
        self.assertTrue(np.all(left['weights'][2*SR:]==0.))
        self.assertTrue(np.all(right['weights']==1.))

    def test_shared_gap_crossfades_without_touching_phones(self):
        left=self.clip('left',0.,2.4,.2,1.8)
        right=self.clip('right',2.,4.,2.3,3.8)
        audit=protect_join(left,right)
        self.assertEqual(audit['method'],'shared_crossfade')
        self.assert_body_untouched(left);self.assert_body_untouched(right)
        self.assertGreater(audit['join_seconds'],1.8)
        self.assertLess(audit['join_seconds'],2.3)

    def test_overlapping_phone_bodies_still_block(self):
        with self.assertRaisesRegex(ValueError,'No phone-safe join'):
            protect_join(self.clip('left',0.,2.4,.2,2.2),self.clip('right',2.,4.,2.1,3.8))

    def test_midi_lyric_boundary_uses_only_the_available_short_gap(self):
        left=self.clip('left',0.,2.4,.2,2.0)
        right=self.clip('right',1.8,4.,2.01,3.8)
        audit=protect_join(left,right,allow_tight=True)
        self.assertLess(audit['fade_seconds'],.002)
        self.assert_body_untouched(left);self.assert_body_untouched(right)
        with self.assertRaisesRegex(ValueError,'No phone-safe join'):
            protect_join(self.clip('left',0.,2.4,.2,2.2),self.clip('right',2.,4.,2.1,3.8),allow_tight=True)


if __name__=='__main__':unittest.main()
