from types import SimpleNamespace,ModuleType
import unittest
from unittest.mock import patch
from song2voice.midi_score import phonemize


class MixedG2PTests(unittest.TestCase):
    def test_english_word_is_not_spelled_by_japanese_frontend_and_ruby_is_not_duplicated(self):
        seen=[]
        def japanese(text,kana=False):
            self.assertFalse(any('a'<=c.lower()<='z' for c in text))
            self.assertNotIn('解',text)
            seen.append(text)
            return 'ホツ' if kana else 'h o'
        class English:
            def __init__(self,**kw):pass
            def convert(self,word):
                self.word=word
                return [SimpleNamespace(readings=[SimpleNamespace(paths=[[SimpleNamespace(phonemes=['t','ih','k'])]])])]
        frontend=ModuleType('pyopenjtalk');frontend.g2p=japanese
        converter=ModuleType('inference.TiFA.g2p.converters.lstm');converter.LSTMConverter=English
        with patch.dict('sys.modules',{'pyopenjtalk':frontend,'inference.TiFA.g2p.converters.lstm':converter}),patch('sys.path',list(__import__('sys').path)):
            runtime=dict(upstream_repo='/unused',tifa_model='/unused')
            result=phonemize(texts=['解(ほつ) ｔｉｃｋ'],english_runtime=runtime)['lines'][0]
            self.assertEqual(result['moras'],['ホ','ツ','tick'])
            self.assertEqual(result['units'][-1]['phones'],['en/t','en/ih','en/k'])
            self.assertTrue(any('ほつ' in text for text in seen))
            phones=phonemize(readings=['ホ tick','-','SP'],english_runtime=runtime)
            self.assertEqual(phones,{'ホ tick':['h','o','en/t','en/ih','en/k']})

    def test_english_without_runtime_fails_explicitly(self):
        frontend=ModuleType('pyopenjtalk')
        with patch.dict('sys.modules',{'pyopenjtalk':frontend}):
            with self.assertRaisesRegex(ValueError,'英文歌词'):phonemize(text='hello')
