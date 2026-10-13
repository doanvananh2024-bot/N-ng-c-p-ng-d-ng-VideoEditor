"""One timed RGBA input for styled captions, bounding FFmpeg's input count."""
import math
from pathlib import Path

import ai_tools
import engine
import subtitle_tools
from text_effects import render_text

def caption_stream(source,style,folder,width,height,duration,cancel):
    from PIL import Image
    source=Path(source)
    if source.suffix.lower() in (".ass",".ssa"):
        converted=folder/"captions-import.srt"
        ai_tools._run_ffmpeg(["-i",str(source),str(converted)],cancel)
        source=converted
    rows=subtitle_tools.load_subtitles(source)
    alignment=int(style.get("alignment",2))
    if not 1<=alignment<=9: raise ValueError("Vị trí phụ đề chưa hợp lệ.")
    starts,ends={},{}
    for index,row in enumerate(rows):
        start=max(0,float(row["start"])); end=min(duration,float(row["end"]))
        if end>start:
            starts.setdefault(start,[]).append(index)
            ends.setdefault(end,[]).append(index)
    boundaries=sorted({0,duration,*starts,*ends})
    blank=folder/"caption-blank.png"
    Image.new("RGBA",(width,height),(0,0,0,0)).save(blank)
    active={}; paths={}; clips=[]
    for number,boundary in enumerate(boundaries[:-1]):
        engine._check_cancel(cancel)
        for index in ends.get(boundary,[]): active.pop(index,None)
        for index in starts.get(boundary,[]): active[index]=rows[index]
        identity=min(active) if active else None
        path=blank
        if identity is not None:
            path=paths.get(identity)
            if path is None:
                item=dict(style,text=rows[identity]["text"],font=style.get("font","Arial"),font_size=style.get("size",24),
                          wrap_width=max(2,width-48),width=max(2,width-48),opacity=style.get("opacity",1))
                raster=render_text(item,width)
                column=(alignment-1)%3; vertical=(alignment-1)//3
                x=24 if column==0 else (width-raster.width)/2 if column==1 else width-raster.width-24
                y=height-raster.height-24 if vertical==0 else (height-raster.height)/2 if vertical==1 else 24
                if style.get("x") is not None: x=float(style["x"])-raster.width*(0 if column==0 else .5 if column==1 else 1)
                if style.get("y") is not None: y=float(style["y"])-raster.height*(1 if vertical==0 else .5 if vertical==1 else 0)
                if not math.isfinite(x+y): raise ValueError("Vị trí phụ đề chưa hợp lệ.")
                canvas=Image.new("RGBA",(width,height),(0,0,0,0))
                canvas.paste(raster,(round(x),round(y)))
                path=folder/f"caption-{identity}.png"; canvas.save(path); paths[identity]=path
        seconds=boundaries[number+1]-boundary
        if clips and clips[-1][0]==path: clips[-1][1]+=seconds
        else: clips.append([path,seconds])
    target=folder/"captions.ffconcat"
    lines=["ffconcat version 1.0"]
    for path,seconds in clips:
        lines.extend([f"file '{path.name}'",f"duration {seconds:.9f}"])
    # The repeated final frame makes the last declared duration effective.
    lines.append(f"file '{(clips[-1][0] if clips else blank).name}'")
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return target
