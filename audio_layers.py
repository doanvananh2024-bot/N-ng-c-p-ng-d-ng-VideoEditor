"""Local audio overlays for the canvas timeline.

append_audio_layers(args, graph, layers, duration, input_index, *, cancel=None)
adds FFmpeg inputs and separately labelled stereo 48 kHz streams, returning
(labels, next_input_index). The caller mixes those labels with its other audio.

Each enabled row reads its source from zero. Looping repeats the source; once
mode pads silence after the source finishes. End=0 means the canvas end. Fades
are relative to the configured interval and clamp to that interval's length.
Start/end are quantized to the nearest 48 kHz sample. Disabled rows are ignored.
All validation and cancellation checks precede mutation of args or graph.

FFmpeg filter semantics: https://ffmpeg.org/ffmpeg-filters.html
"""
from __future__ import annotations

import math
import re

import engine


SAMPLE_RATE = 48000


def _cancel(cancel):
    if cancel is not None:
        engine._check_cancel(cancel)


def append_audio_layers(args, graph, layers, duration, input_index, *, cancel=None):
    """Append validated local audio inputs without mixing or applying a limiter."""
    _cancel(cancel)
    if not isinstance(args, list) or not isinstance(graph, list):
        raise ValueError("Danh sách đầu vào và bộ lọc âm thanh không hợp lệ.")
    if type(input_index) is not int or input_index < 0:
        raise ValueError("Chỉ số đầu vào âm thanh không hợp lệ.")
    try:
        duration = float(duration)
    except (ValueError, TypeError):
        raise ValueError("Thời lượng khung âm thanh không hợp lệ.") from None
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Thời lượng khung âm thanh phải lớn hơn 0.")
    if layers is None:
        layers = []
    if not isinstance(layers, (list, tuple)):
        raise ValueError("Danh sách lớp âm thanh không hợp lệ.")

    prepared = []
    canvas_samples = round(duration * SAMPLE_RATE)
    if canvas_samples < 1:
        raise ValueError("Thời lượng khung âm thanh quá ngắn.")
    for row in layers:
        _cancel(cancel)
        if not isinstance(row, dict):
            raise ValueError("Thông tin lớp âm thanh không hợp lệ.")
        if not row.get("enabled", True):
            continue
        volume = engine._number(row, "volume", 1, 0, 2)
        start = engine._number(row, "start", 0, 0, duration)
        end = engine._number(row, "end", 0, 0, duration) or duration
        if end <= start:
            raise ValueError("Mốc kết thúc của lớp âm thanh phải sau mốc bắt đầu.")
        start_sample, end_sample = round(start * SAMPLE_RATE), round(end * SAMPLE_RATE)
        interval_samples = end_sample - start_sample
        if interval_samples < 1:
            raise ValueError("Khoảng phát của lớp âm thanh quá ngắn.")
        fade_in = min(round(engine._number(row, "fade_in", 0, 0, 5) * SAMPLE_RATE), interval_samples)
        fade_out = min(round(engine._number(row, "fade_out", 0, 0, 5) * SAMPLE_RATE), interval_samples)
        path = row.get("path")
        if not isinstance(path, (str, bytes)) and not hasattr(path, "__fspath__"):
            raise ValueError("Chọn tệp cho lớp âm thanh.")
        source = engine._source(path)
        _cancel(cancel)
        if not engine.probe(str(source))["has_audio"]:
            raise ValueError("Tệp lớp âm thanh không có luồng âm thanh.")
        _cancel(cancel)
        prepared.append((source, bool(row.get("loop", True)), volume,
                         start_sample, interval_samples, fade_in, fade_out))

    _cancel(cancel)
    occupied = {int(value) for expression in graph
                for value in re.findall(r"\[audioLayer(\d+)\]", str(expression))}
    serial = max(occupied, default=-1) + 1
    labels = []
    added_args, added_graph = [], []
    for source, loop, volume, start_sample, interval_samples, fade_in, fade_out in prepared:
        if loop:
            added_args += ["-stream_loop", "-1"]
        added_args += ["-i", str(source)]
        filters = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo",
                   "asetpts=PTS-STARTPTS", f"atrim=end_sample={interval_samples}",
                   f"apad=whole_len={interval_samples}", f"atrim=end_sample={interval_samples}",
                   f"volume={volume:.12g}"]
        if fade_in:
            filters.append(f"afade=t=in:ss=0:ns={fade_in}")
        if fade_out:
            filters.append(f"afade=t=out:ss={interval_samples-fade_out}:ns={fade_out}")
        filters += [f"adelay={start_sample}S:all=1", f"apad=whole_len={canvas_samples}",
                    f"atrim=end_sample={canvas_samples}", "asetpts=PTS-STARTPTS"]
        label = f"audioLayer{serial}"
        added_graph.append(f"[{input_index}:a:0]" + ",".join(filters) + f"[{label}]")
        labels.append(label)
        serial += 1
        input_index += 1
    _cancel(cancel)
    args.extend(added_args)
    graph.extend(added_graph)
    return labels, input_index
