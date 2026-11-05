/* Shared full-song transport. Preserve the latest seek across asynchronous loads. */
(() => {
  const player = document.querySelector('#main');
  const seek = document.querySelector('#seek');
  const toggle = document.querySelector('#play-toggle');
  const clock = document.querySelector('#playback-time');
  const status = document.querySelector('#playback-status');
  const buttons = [...document.querySelectorAll('#tracks button')];
  let pending = null, position = 0, wantsPlay = false, dragging = false, generation = 0;
  const duration = () => Number.isFinite(player.duration) ? player.duration : Number(player.dataset.duration);
  const clamp = time => Math.max(0, Math.min(time, duration()));
  const format = time => `${Math.floor(time / 60)}:${String(Math.floor(time % 60)).padStart(2, '0')}`;
  function display(time = pending ?? position) {
    seek.max = duration();
    if (!dragging) seek.value = clamp(time);
    const visible = dragging ? Number(seek.value) : clamp(time);
    clock.textContent = `${format(visible)} / ${format(duration())}`;
    seek.setAttribute('aria-valuetext', `${format(visible)}，共 ${format(duration())}`);
    toggle.textContent = wantsPlay ? '暂停' : '播放';
    toggle.setAttribute('aria-label', wantsPlay ? '暂停' : '播放');
  }
  function play() {
    const request = generation;
    player.play().catch(error => {
      if (request !== generation || error.name === 'AbortError') return;
      wantsPlay = false;
      status.textContent = error.name === 'NotAllowedError' ? '点击播放按钮继续试听。' : '音频未能播放，请重新选择音轨。';
      display();
    });
  }
  function pause() {
    wantsPlay = false;
    player.pause();
    status.textContent = '已暂停';
    display();
  }
  function applySeek() {
    if (pending === null || player.readyState < 1 || !Number.isFinite(player.duration)) return;
    const target = clamp(pending);
    try { player.currentTime = target; } catch (_) { return; }
    pending = null;
    position = target;
    status.textContent = wantsPlay ? '正在定位…' : `已定位到 ${format(target)}`;
    display();
  }
  function jump(time, autoplay = wantsPlay) {
    pending = clamp(time);
    position = pending;
    wantsPlay = autoplay;
    status.textContent = '正在定位…';
    display();
    applySeek();
    if (wantsPlay) play();
  }
  toggle.addEventListener('click', () => {
    if (wantsPlay) return pause();
    wantsPlay = true;
    if (player.ended) jump(0, true);
    else { applySeek(); play(); display(); }
  });
  seek.addEventListener('pointerdown', () => { dragging = true; });
  seek.addEventListener('input', () => {
    clock.textContent = `${format(Number(seek.value))} / ${format(duration())}`;
    seek.setAttribute('aria-valuetext', `${format(Number(seek.value))}，共 ${format(duration())}`);
  });
  seek.addEventListener('change', () => { dragging = false; jump(Number(seek.value)); });
  seek.addEventListener('pointerup', () => {
    // Release outside the slider must also restore time updates.
    if (dragging) { dragging = false; jump(Number(seek.value)); }
  });
  seek.addEventListener('pointercancel', () => { dragging = false; display(); });
  document.querySelectorAll('[data-time]').forEach(button => button.addEventListener('click', () => {
    jump(Number(button.dataset.time), true);
  }));
  document.querySelectorAll('[data-skip]').forEach(button => button.addEventListener('click', () => {
    jump((pending ?? position) + Number(button.dataset.skip));
  }));
  document.querySelector('#volume').addEventListener('input', event => {
    player.volume = Number(event.target.value);
  });
  buttons.forEach(button => button.addEventListener('click', () => {
    if (button.getAttribute('aria-pressed') === 'true') return;
    pending = clamp(dragging ? Number(seek.value) : pending ?? position);
    position = pending;
    dragging = false;
    generation++;
    buttons.forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    document.querySelector('#current').textContent = `当前：${button.textContent}。切换音轨时保留播放位置。`;
    status.textContent = '正在加载音轨…';
    player.src = button.dataset.src + '?transport=2';
    player.load();
    display();
    if (wantsPlay) play();
  }));
  for (const event of ['loadedmetadata', 'durationchange', 'canplay']) {
    player.addEventListener(event, () => { applySeek(); display(); });
  }
  for (const event of ['timeupdate', 'seeked']) {
    player.addEventListener(event, () => {
      if (pending !== null || dragging) return;
      position = player.currentTime;
      display();
      if (event === 'seeked') status.textContent = wantsPlay ? '播放中' : '已暂停';
    });
  }
  player.addEventListener('playing', () => { status.textContent = '播放中'; });
  player.addEventListener('waiting', () => { if (wantsPlay) status.textContent = '缓冲中…'; });
  player.addEventListener('ended', () => { wantsPlay = false; status.textContent = '播放结束'; display(); });
  player.addEventListener('error', () => {
    wantsPlay = false;
    status.textContent = '音频加载失败，请重新加载页面后再试。';
    display();
  });
  document.querySelectorAll('audio').forEach(audio => audio.addEventListener('play', () => {
    document.querySelectorAll('audio').forEach(other => {
      if (other !== audio) { if (other === player) pause(); else other.pause(); }
    });
  }));
  display();
})();
