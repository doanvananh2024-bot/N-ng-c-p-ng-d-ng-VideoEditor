"""Connect the shared trim/join engine to the Studio layer renderer."""
from __future__ import annotations
from pathlib import Path
import tempfile
import engine


def render_project(clips, settings, output, progress, cancel):
    from advanced_render import advanced_render, has_advanced
    settings = dict(settings)
    merged = {**settings, **(settings.get('advanced') or {})}
    if len(clips) > 1 and merged.get('source_stem_paths'):
        raise ValueError('Hãy xuất từng video với các lớp âm riêng trước khi nối nhiều video.')
    # The base clip is already trimmed and retimed by engine.media_process.
    # External original stems and pre-existing narration still use source time.
    if len(clips) == 1:
        settings['_source_audio_start'] = float(clips[0].get('start', 0))
        settings['_source_audio_end'] = float(clips[0].get('end', 0))
        settings['_source_audio_speed'] = float(merged.get('speed', 1))
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise FileExistsError('Tệp đầu ra đã tồn tại. Hãy chọn tên mới.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Advanced UI always supplies encoder/style settings; basic callers retain
    # the original fast path.
    if not has_advanced(settings) and not settings.get('encoder') and not settings.get('subtitle_style'):
        return engine.media_process(clips, merged, str(target), progress, cancel)
    base = dict(settings)
    # Source composition is rendered once before the advanced layers. Public
    # controls may live inside advanced alongside the A0/A1 mix settings.
    from source_composition import DEFAULTS
    for key in (*DEFAULTS, 'rotation', 'mirror', 'fit'):
        if key in merged:
            base[key] = merged[key]
    base['subtitle_path'] = ''
    base['volume'] = 1
    base['mute'] = False
    for name in ['music_path', 'voice_path', 'background_path']:
        base[name] = ''
    with tempfile.TemporaryDirectory(prefix='.vei-render-', dir=engine.temporary_parent(target.parent)) as work:
        intermediate = str(Path(work) / 'base.mp4')
        engine.media_process(clips, base, intermediate,
                             lambda p,m: progress(int(p*.45), 'Chuẩn bị · '+m), cancel)
        result = advanced_render(intermediate, settings, str(target),
                                 lambda p,m: progress(45+int(p*.55),m), cancel)
    return result
