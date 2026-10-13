"""Alpha-aware layer blends, independently shared by export and Qt preview.

FFmpeg graph contracts: https://ffmpeg.org/ffmpeg-filters.html#blend
and https://ffmpeg.org/ffmpeg-filters.html#maskedmerge .
"""
from __future__ import annotations


MODES = ("normal", "lighten", "screen", "darken", "overlay", "hardlight", "softlight", "multiply")


def mode_id(value):
    mode = str(value or "normal").lower()
    if mode not in MODES:
        raise ValueError("Chế độ hòa trộn thành phần không được hỗ trợ.")
    return mode


def qt_composition_mode(value):
    from PySide6.QtGui import QPainter
    names = dict(normal="SourceOver", lighten="Lighten", screen="Screen", darken="Darken",
                 overlay="Overlay", hardlight="HardLight", softlight="SoftLight", multiply="Multiply")
    return getattr(QPainter.CompositionMode, "CompositionMode_" + names[mode_id(value)])


def _expression(mode):
    # A is the current opaque canvas; B is the foreground color. Alpha is
    # applied separately, so translucent edges and outside pixels stay intact.
    return {
        "lighten": "max(A,B)",
        "screen": "255-(255-A)*(255-B)/255",
        "darken": "min(A,B)",
        "overlay": "if(lte(A,127.5),2*A*B/255,255-2*(255-A)*(255-B)/255)",
        "hardlight": "if(lte(B,127.5),2*A*B/255,255-2*(255-A)*(255-B)/255)",
        "softlight": ("if(lte(B,127.5),A-(1-2*B/255)*A*(1-A/255),"
                      "A+(2*B/255-1)*(if(lte(A,63.75),((16*A/255-12)*A/255+4)*A,255*sqrt(A/255))-A))"),
        "multiply": "A*B/255",
    }[mode]


def append_blend_overlay(graph, bottom, top, *, width, height, fps, duration,
                         x, y, enable, eof_action, mode, output, prefix):
    mode = mode_id(mode)
    overlay = f"overlay=x='{x}':y='{y}':enable='{enable}':eof_action={eof_action}:shortest=0"
    if mode == "normal":
        graph.append(f"[{bottom}][{top}]{overlay}[{output}]")
        return output
    # Prepare the positioned top as an RGBA canvas. Its alpha is the mask used
    # after blending RGB; directly blending transparent RGB would darken the
    # entire source rectangle and ignore the layer's actual alpha.
    graph.append(f"color=c=black:s={width}x{height}:r={fps:.9g}:d={duration:.9g},"
                 f"format=rgba,colorchannelmixer=aa=0[{prefix}blank]")
    graph.append(f"[{prefix}blank][{top}]{overlay}:format=auto:alpha=straight[{prefix}placed]")
    graph.append(f"[{prefix}placed]split=2[{prefix}color][{prefix}alpha]")
    graph.append(f"[{prefix}color]format=gbrp[{prefix}rgb]")
    graph.append(f"[{prefix}alpha]alphaextract,setrange=full,format=gbrp[{prefix}mask]")
    graph.append(f"[{bottom}]format=gbrp,split=2[{prefix}keep][{prefix}under]")
    graph.append(f"[{prefix}under][{prefix}rgb]blend=all_expr='{_expression(mode)}':shortest=1[{prefix}mixed]")
    graph.append(f"[{prefix}keep][{prefix}mixed][{prefix}mask]maskedmerge[{output}]")
    return output
