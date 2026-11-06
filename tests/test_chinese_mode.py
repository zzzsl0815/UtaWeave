"""Language selection must reach the model, score, and annotation router."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import MagicMock, patch

from song2voice.language import song_language, use_lora
from song2voice.rendering.runtime import load_model
from song2voice.rendering.pipeline import resolve_config, PATH_KEYS
from song2voice.midi_score import phonemize, notes_to_score
from song2voice.vocal2midi_native_worker import text_languages, MixedG2P, language_spans


class ChineseModeTests(unittest.TestCase):
    def test_default_and_invalid_language(self):
        self.assertEqual(song_language({}),'ja');self.assertTrue(use_lora({}))
        self.assertFalse(use_lora({'language':'zh'}))
        for cfg in [dict(language='zh',use_lora=True),dict(language='cn'),dict(use_lora='false')]:
            with self.assertRaises(ValueError):use_lora(cfg)

    def test_base_load_never_reads_or_constructs_lora(self):
        model=MagicMock();model.float.return_value.to.return_value.eval.return_value=model
        cls=MagicMock();cls.from_local.return_value=model
        factory=ModuleType('vocalrender.training.model_factory')
        factory.detect_model_classes=MagicMock(return_value=SimpleNamespace(model_cls=cls,lora_config_cls=MagicMock()))
        with patch.dict(sys.modules,{'vocalrender.training.model_factory':factory}), \
             patch('song2voice.rendering.runtime.activate_vocalrender'), \
             patch('song2voice.rendering.runtime.runtime',return_value=dict(base_model='/base',language='zh')), \
             patch('song2voice.rendering.runtime.configure_lora') as scales, \
             patch.object(Path,'read_text',side_effect=AssertionError('must not read LoRA config')):
            result,audit=load_model('/missing-japanese-checkpoint')
        self.assertIs(result,model);self.assertEqual(audit,{})
        cls.from_local.assert_called_once_with('/base',training=True)
        factory.detect_model_classes.return_value.lora_config_cls.assert_not_called()
        model.load_lora_weights.assert_not_called();model.set_lora_enabled.assert_not_called();scales.assert_not_called()

    def test_chinese_config_does_not_require_a_checkpoint_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg={key:folder for key in PATH_KEYS if key!='checkpoint'}
            cfg.update(language='zh',checkpoint='/nonexistent',render_python=sys.executable,align_python=sys.executable,ffmpeg=sys.executable)
            path=root/'render.json';path.write_text(json.dumps(cfg))
            with patch('song2voice.rendering.pipeline.require_reference'),patch('song2voice.rendering.pipeline.resolve_reference_run',side_effect=lambda p:p):
                result=resolve_config(path)
            self.assertIsNone(result['checkpoint']);self.assertFalse(result['use_lora'])
            cfg['language']='ja';path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'checkpoint'):resolve_config(path)

    def test_legacy_japanese_load_still_requires_complete_lora(self):
        model=MagicMock();model.float.return_value.to.return_value.eval.return_value=model
        model.load_lora_weights.return_value=(list(range(384)),[])
        cls=MagicMock();cls.from_local.return_value=model
        factory=ModuleType('vocalrender.training.model_factory')
        adapter=MagicMock()
        factory.detect_model_classes=MagicMock(return_value=SimpleNamespace(model_cls=cls,lora_config_cls=adapter))
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'lora_config.json').write_text(json.dumps({'lora_config':{'rank':64}}))
            with patch.dict(sys.modules,{'vocalrender.training.model_factory':factory}), \
                 patch('song2voice.rendering.runtime.activate_vocalrender'), \
                 patch('song2voice.rendering.runtime.runtime',return_value=dict(base_model='/base')), \
                 patch('song2voice.rendering.runtime.configure_lora',return_value={'scaled':True}):
                _,audit=load_model(folder)
            adapter.assert_called_once_with(rank=64)
            model.load_lora_weights.assert_called_once_with(folder,device='cuda:0')
            model.set_lora_enabled.assert_called_once_with(True)
            self.assertEqual(audit,{'scaled':True})

    def test_han_script_uses_selected_language(self):
        self.assertEqual(text_languages('春风 hello','zh'),['zh','en'])
        self.assertEqual(text_languages('春風 hello'),['ja','en'])
        pipeline=MagicMock();MixedG2P(pipeline,'zh').convert('春风 hello')
        pipeline.convert.assert_called_once_with('春风 hello',languages=['zh','en'])
        words=[SimpleNamespace(text='春',start=.1,end=.4,phonemes=[SimpleNamespace(text='zh/ch')])]
        self.assertEqual(language_spans(words,1)[0]['language'],'zh')

    def test_contextual_phones_are_kept_per_note_not_per_character(self):
        notes=[dict(start=.5+i*.5,end=1+i*.5,pitch=60) for i in range(4)]
        phones=[['zh/y','zh/in'],['zh/h','zh/ang'],['zh/x','zh/ing'],['zh/z','zh/ou']]
        score,_=notes_to_score(notes,list('银行行走'),phones,120)
        self.assertEqual(score[0]['word'],list('银行行走'))
        self.assertEqual(score[0]['ph'],sum(phones,[]))

    def test_chinese_g2p_keeps_characters_and_rejects_silent_drops(self):
        # Lightweight upstream dictionaries work without a neural model/runtime.
        upstream=Path(__file__).resolve().parents[1]/'vendor/Vocal2Midi'
        config=Path(__file__).resolve().parents[1]/'configs/vocal2midi.local.json'
        if not config.exists():self.skipTest('Local TiFA configuration is optional')
        dictionary=Path(json.loads(config.read_text())['tifa_model'])
        if not (upstream/'inference/TiFA/g2p/converters/chinese.py').exists() or not dictionary.exists():
            self.skipTest('Bootstrap TiFA assets for Chinese G2P integration')
        runtime=dict(upstream_repo=str(upstream),tifa_model=str(dictionary))
        with patch.dict(sys.modules,{'pyopenjtalk':None}):
            result=phonemize(text='春风吹过山间',language='zh',english_runtime=runtime)
            self.assertEqual(result['moras'],list('春风吹过山间'))
            self.assertTrue(all(p.startswith('zh/') for u in result['units'] for p in u['phones']))
            phones=phonemize(readings=list('银行行走'),indexed=True,language='zh',english_runtime=runtime)
            self.assertEqual(phones[1],['zh/h','zh/ang']);self.assertEqual(phones[2],['zh/x','zh/ing'])
            for text in ['今年2026年','春ア','春😀']:
                with self.subTest(text=text),self.assertRaises(ValueError):phonemize(text=text,language='zh',english_runtime=runtime)
