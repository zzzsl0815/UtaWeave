"""Language is fixed when preparing a job; old jobs default to Japanese."""


def song_language(config):
    value = config.get('language', 'ja')
    if value not in ('ja', 'zh'):
        raise ValueError('歌曲语言仅支持 ja（日语）或 zh（中文普通话）')
    return value


def use_lora(config):
    language = song_language(config)
    enabled = config.get('use_lora', language == 'ja')
    if type(enabled) is not bool:
        raise ValueError('use_lora must be a boolean')
    if language == 'zh' and enabled:
        raise ValueError('中文模式不能加载日语 LoRA')
    return enabled
