"""Timed independently composable FFmpeg color-filter layers."""
import math

FILTERS = ("none","vignette","vintage","grain","glow","crt","stripe","sharpen","white_balance",
           "grayscale","saturation","bright_contrast","lift_gamma_gain","lightleak")

def expression(selected):
    values = dict(none="null",vignette="vignette=angle=0.7853981634",
                  vintage="eq=saturation=.4:contrast=1.1,colorbalance=rs=.14:bs=-.12",
                  grain="noise=alls=28:allf=t+u:all_seed=7",glow="gblur=sigma=5,eq=brightness=.12",
                  crt="noise=alls=12:allf=t,eq=saturation=.75,drawgrid=w=iw:h=4:t=1:c=black@.35",
                  stripe="drawgrid=w=iw:h=6:t=1:c=black@.35",sharpen="unsharp=5:5:1.6:5:5:0",
                  white_balance="colorbalance=rs=.1:bs=-.1",grayscale="hue=s=0",saturation="eq=saturation=2",
                  bright_contrast="eq=brightness=.1:contrast=1.25",lift_gamma_gain="eq=gamma=1.15:brightness=.03:contrast=1.1",
                  lightleak="colorbalance=rs=.2:gs=.06:bs=-.03")
    if selected not in values:
        raise ValueError("Bộ lọc của lớp không được hỗ trợ.")
    return values[selected]

def append_layers(graph,current,layers,duration):
    if not isinstance(layers,(list,tuple)):
        raise ValueError("Danh sách lớp bộ lọc chưa hợp lệ.")
    for index,item in enumerate(layers):
        if not isinstance(item,dict):
            raise ValueError("Lớp bộ lọc chưa hợp lệ.")
        if not item.get("enabled",True):
            continue
        intensity=float(item.get("intensity",.5))
        start=float(item.get("start",0)); end=float(item.get("end",0)) or duration
        if not all(math.isfinite(v) for v in (intensity,start,end)) or not 0<=intensity<=1 or not 0<=start<end<=duration:
            raise ValueError("Độ mạnh hoặc khoảng thời gian bộ lọc chưa hợp lệ.")
        selected=item.get("filter","none")
        effect=expression(selected)
        if selected=="none" or intensity==0:
            continue
        prefix="colorLayer"+str(index)
        graph.append(f"[{current}]format=yuv444p,split=2[{prefix}base][{prefix}source]")
        graph.append(f"[{prefix}source]{effect}[{prefix}effect]")
        amount=intensity*.45 if selected=="glow" else intensity
        graph.append(f"[{prefix}base][{prefix}effect]blend=all_expr='if(between(T,{start:.9g},{end:.9g}),A*{1-amount:.9g}+B*{amount:.9g},A)'[{prefix}out]")
        current=prefix+"out"
    return current
