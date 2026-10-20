"""Optional, explicitly requested song import through an existing local API bridge."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request

from .score import save, sha


def fetch_reference_song(url, bridge_script, credential_file, out, level='exhigh'):
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ['music.163.com', 'y.music.163.com']:
        raise ValueError('Expected a music.163.com song URL')
    query = urllib.parse.parse_qs(parsed.query)
    match = re.search(r'(?:[?&]id=|song/)(\d+)', url)
    song_id = str(query.get('id', [match[1] if match else ''])[0])
    if not song_id.isdigit():
        raise ValueError('Song id is missing')
    out = Path(out).resolve()
    marker = out/'source.json'
    if marker.exists():
        record = json.loads(marker.read_text())
        path = out/record['audio']
        if record['song_id'] != song_id or sha(path) != record['audio_sha256']:
            raise ValueError('Cached source mismatch; choose a new directory')
        return record
    out.mkdir(parents=True, exist_ok=True)
    endpoint = 'https://music.163.com/api/song/detail/?' + urllib.parse.urlencode(dict(id=song_id, ids='['+song_id+']'))
    with urllib.request.urlopen(urllib.request.Request(endpoint, headers={'User-Agent':'Mozilla/5.0'}), timeout=25) as response:
        detail = json.load(response)
    songs = detail.get('songs', [])
    if not songs:
        raise ValueError('Song metadata unavailable')
    song = songs[0]
    env = os.environ.copy()
    env.pop('NETEASE_COOKIE', None)
    if credential_file:
        if not Path(credential_file).is_file():
            raise ValueError('Configured credential file is unavailable')
        env['NETEASE_COOKIE_FILE'] = str(Path(credential_file).resolve())
    process = subprocess.Popen(['node', str(Path(bridge_script).resolve())],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    def call(method, **params):
        time.sleep(.8)
        request = urllib.request.Request(address, json.dumps(dict(method=method, params=params)).encode(),
                                         {'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=25) as response:
            body = json.load(response)
        if body.get('code', 200) != 200:
            raise ValueError('Provider request denied or unavailable; code '+str(body.get('code')))
        return body
    try:
        port = process.stdout.readline().strip()
        if not port.isdigit():
            raise ValueError('Local song API bridge did not start')
        address = 'http://127.0.0.1:'+port
        login = call('login_status').get('data', {})
        authenticated = bool(login.get('profile') and login.get('account'))
        if credential_file and not authenticated:
            raise ValueError('Configured account login could not be confirmed')
        items = call('song_url_v1', id=song_id, level=level).get('data', [])
        track = items[0] if items else {}
        if track.get('freeTrialInfo') or not track.get('url'):
            raise ValueError('Full source audio is unavailable for this account')
        extension = str(track.get('type', 'mp3')).lower()
        if extension not in ['mp3','flac','m4a','aac','wav','ogg']:
            raise ValueError('Unsupported provider audio type')
        temporary = out/('audio.part.'+extension)
        destination = out/('audio.'+extension)
        try:
            request = urllib.request.Request(track['url'], headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open('wb') as stream:
                size = 0
                while block := response.read(1024*1024):
                    size += len(block)
                    if size > 150*1024**2:
                        raise ValueError('Source audio exceeds import size limit')
                    stream.write(block)
            if track.get('size') and size != track['size']:
                raise ValueError('Incomplete source audio download')
        except Exception:
            temporary.unlink(missing_ok=True)
            # Download exceptions may contain a temporary signed URL.
            raise ValueError('Source audio download failed') from None
        temporary.replace(destination)
        record = dict(song_id=song_id, title=song['name'],
            artists=[a['name'] for a in song.get('artists', song.get('ar', []))],
            duration_seconds=song.get('duration', song.get('dt', 0))/1000,
            source_url='https://music.163.com/song?id='+song_id,
            audio=destination.name, audio_sha256=sha(destination), bytes=size,
            authenticated=authenticated, preview=False,
            audio_response={k:track.get(k) for k in ['level','type','br','size','time']})
        save(marker, record)
        return record
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
