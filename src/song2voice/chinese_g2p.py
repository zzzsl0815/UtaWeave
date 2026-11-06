"""Mandarin characters stay characters in VR; TiFA gets qualified phones."""
from pathlib import Path
import re
import sys
import unicodedata

from .midi_score import clean_lyrics


def phonemize_chinese(text=None, readings=None, texts=None, english_runtime=None, indexed=False):
    if not english_runtime:
        raise ValueError('中文读音需要配置已有 TiFA 中文字典')
    sys.path.insert(0, str(Path(english_runtime['upstream_repo']).resolve()))
    from inference.TiFA.g2p.converters.chinese import PinyinConverter
    root = Path(english_runtime['tifa_model'])
    chinese = PinyinConverter(dict_path=str(root/'dictionaries/ds-zh-pinyin-lite.txt'))
    english = None
    token = r"[\u4e00-\u9fa5]+|[A-Za-z]+(?:['’][A-Za-z]+)*"

    def phrase(value):
        nonlocal english
        value = unicodedata.normalize('NFKC', clean_lyrics(value))
        residue = re.sub(token, '', value)
        if any(not (c.isspace() or unicodedata.category(c).startswith('P')) for c in residue):
            raise ValueError('中文模式请填写汉字或英文单词；数字请写成汉字，延音填 -，休止填 SP')
        units = []
        for match in re.finditer(token, value):
            part = match.group()
            language = 'zh' if '\u4e00' <= part[0] <= '\u9fa5' else 'en'
            if language == 'en':
                if english is None:
                    from inference.TiFA.g2p.converters.lstm import LSTMConverter
                    english = LSTMConverter(dict_path=str(root/'dictionaries/ds_cmudict-07b.txt'),
                        model_path=str(root/'assets/LstmG2p-Eng'), beam_size=4)
                words = english.convert(part.lower())
            else:
                words = chinese.convert(part)
            for word in words:
                groups = word.readings[0].paths[0]
                phones = [language+'/'+p.removeprefix(language+'/') for group in groups for p in group.phonemes]
                if not phones:raise ValueError('无法确定读音：'+word.text)
                weight = 1 if language == 'zh' else max(1, sum(p.split('/')[-1] in
                    ('aa','ae','ah','ao','aw','ax','ay','eh','er','ey','ih','iy','ow','oy','uh','uw') for p in phones))
                units.append(dict(text=word.text if language == 'zh' else part, lang=language,
                    weight=weight, phones=phones, vowel=None, pinyin=' '.join(g.script for g in groups)))
        return dict(reading=' '.join(u['text'] for u in units), moras=[u['text'] for u in units], units=units,
                    warnings=[], warning='中文按汉字分配；多音字、一字多音符和英文混唱请核对并试听。')

    if readings is not None:
        if not indexed:
            return {s:[p for u in phrase(s)['units'] for p in u['phones']]
                    for s in dict.fromkeys(readings) if s not in ('-', 'SP', 'AP')}
        # Keep adjacent Chinese characters together for polyphone disambiguation.
        # Explicit rests delimit context; sustained notes add no new syllable.
        result = [[] for _ in readings]
        groups = []; group = []
        for i, value in enumerate(readings):
            if value in ('SP', 'AP'):
                if group:groups.append(group);group=[]
            elif value != '-':group.append((i,value))
        if group:groups.append(group)
        for group in groups:
            tokens = []
            for i,value in group:
                parsed = phrase(value)['units']
                if not parsed:raise ValueError('音符没有可演唱的汉字或英文：'+value)
                tokens.extend((i,u['text'],u['lang']) for u in parsed)
            joined = ''.join((' '+v+' ') if lang == 'en' else v for _,v,lang in tokens)
            contextual = phrase(joined)['units']
            if len(contextual) != len(tokens):raise ValueError('中文读音单元数量发生变化')
            for (i,_,_),unit in zip(tokens,contextual):result[i].extend(unit['phones'])
        return result
    if texts is not None:return dict(lines=[phrase(s) for s in texts])
    return phrase(text)
