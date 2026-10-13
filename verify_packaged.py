"""Verify the finished standalone executable without source-module imports.

The harness invokes only the EXE's public --self-check and --job interfaces.
FFmpeg/FFprobe come from that package. OCR uses RapidOCR's bundled English
model on a synthetic fixture and never downloads models or calls paid AI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parent
DEFAULT_EXE = ROOT / "dist" / "VideoEditorIndependent" / "VideoEditorIndependent.exe"


def require(value, message):
    if not value:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--screenshot-only",action="store_true")
    options = parser.parse_args()
    executable = Path(options.exe).resolve()
    if not executable.is_file():
        print("Packaged executable is not ready: " + str(executable))
        return 2
    package = executable.parent
    qa = ROOT / "qa"
    previous=None
    if options.screenshot_only:
        previous=json.loads((qa / "packaged-verification-report.json").read_text(encoding="utf-8"))
        require(previous["executable_sha256"]==hashlib.sha256(executable.read_bytes()).hexdigest(),
                "Cannot reuse previous results after executable changed")
        run=Path(previous["run_dir"])
    else:
        run = qa / ("package_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
        run.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
               HF_HOME=str(run / "hf-cache"), TORCH_HOME=str(run / "torch-cache"),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", QT_QPA_PLATFORM="offscreen")
    # Offscreen Qt does not discover native Windows fonts by itself. This points
    # the QA renderer to system fonts and does not redistribute any font file.
    env["QT_QPA_FONTDIR"] = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    report = previous or dict(application="VideoEditorIndependent", executable=str(executable),
                  executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                  created_at=datetime.now(timezone.utc).isoformat(), run_dir=str(run),
                  offline=True, packaged_process_source_imports=False, checks=[], artifacts={})
    result_path = qa / "packaged-verification-report.json"

    def persist():
        report["passed"] = sum(item["status"] == "passed" for item in report["checks"])
        report["failed"] = sum(item["status"] == "failed" for item in report["checks"])
        report["status"] = "failed" if report["failed"] else "passed"
        content = json.dumps(report, ensure_ascii=False, indent=2)
        result_path.write_text(content, encoding="utf-8")
        (run / result_path.name).write_text(content, encoding="utf-8")

    def check(name, function):
        began = time.monotonic()
        item = dict(name=name)
        report["checks"]=[old for old in report["checks"] if old["name"]!=name]
        report["checks"].append(item)
        try:
            item.update(status="passed", details=function() or {})
        except Exception as exc:
            item.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        item["seconds"] = round(time.monotonic() - began, 3)
        print(json.dumps({key: item[key] for key in ("name", "status", "seconds")}, ensure_ascii=False), flush=True)
        persist()

    def invoke(command, timeout=90):
        process = subprocess.run(command, cwd=run, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return process

    def tool(name):
        for directory in (package / "tools", package / "_internal" / "tools"):
            candidate = directory / (name + ".exe")
            if candidate.is_file():
                return candidate
        raise AssertionError(f"Packaged {name} tool is missing")

    def ffmpeg(arguments):
        result = invoke([str(tool("ffmpeg")), "-hide_banner", "-loglevel", "error", "-nostdin", "-n", *arguments])
        require(result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:])

    def probe(path):
        result = invoke([str(tool("ffprobe")), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
        require(result.returncode == 0, "Packaged FFprobe cannot read generated output")
        return json.loads(result.stdout)

    def job(payload, name, timeout=120):
        specification = run / (name + ".json")
        specification.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        process = invoke([str(executable), "--job", str(specification)], timeout)
        result = specification.with_suffix(".result.json")
        require(result.is_file(), f"Packaged job returned {process.returncode} with no result report; stderr=" + process.stderr.decode("utf-8", "replace")[-1500:])
        response = json.loads(result.read_text(encoding="utf-8"))
        require(response.get("ok") and process.returncode == 0, "Packaged job failed: " + str(response.get("error", response)))
        require(response.get("progress"), "Packaged job did not report progress")
        return response

    source = run / "Nguồn độc lập có dấu.mp4"
    subtitle = run / "Phụ đề riêng.srt"
    label = run / "Chữ OCR.png"
    ocr_source = run / "English OCR fixture.mp4"
    transform_logo = run / "Logo xoay hai màu.png"

    def self_check():
        output = run / "self-check.json"
        process = invoke([str(executable), "--self-check", "--check-output", str(output)], 90)
        require(output.is_file(), f"Packaged desktop self-check produced no report (exit={process.returncode})")
        payload = json.loads(output.read_text(encoding="utf-8"))
        require(process.returncode == 0, f"Packaged self-check reports missing modules: {payload}")
        require(payload.get("version") == "1.3.2", "The packaged application is not the new 1.3.2 release")
        require(payload["local_owner"] is True and payload["license_server"] is None, "Packaged app did not report independent ownership")
        require(Path(payload["data_root"]).is_relative_to(run) and Path(payload["model_root"]).is_relative_to(run), "Packaged data/model roots are not isolated")
        for name in ("ffmpeg", "ffprobe", "yt_dlp"):
            require(Path(payload[name]).is_relative_to(package), f"Packaged app resolves external tool {name}")
        require(all(value is True or value == "OK" for value in payload["modules"].values()),
                "One or more packaged dependencies unavailable")
        require(not (package / "video_editor").exists() and not (package / "_internal" / "video_editor").exists(),
                "Reference application directory appears in the independent bundle")
        report["artifacts"]["self_check"] = str(output)
        return payload

    def fixture():
        from PIL import Image, ImageDraw, ImageFont
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1.6",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.6",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
        subtitle.write_text("1\n00:00:00,050 --> 00:00:01,450\nXin chào, Việt Nam!\n", encoding="utf-8")
        image = Image.new("RGB", (640, 360), "black")
        drawer = ImageDraw.Draw(image)
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"
        font = ImageFont.truetype(str(font_path), 46)
        bounds = drawer.textbbox((0, 0), "HELLO WORLD", font=font)
        drawer.text(((640 - (bounds[2] - bounds[0])) // 2, 254), "HELLO WORLD", fill="white", font=font)
        image.save(label)
        logo = Image.new("RGBA", (60, 30), (255, 0, 0, 255))
        ImageDraw.Draw(logo).rectangle((30, 0, 59, 29), fill=(0, 255, 0, 255))
        logo.save(transform_logo)
        ffmpeg(["-loop", "1", "-i", str(label), "-t", "1.5", "-r", "10", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(ocr_source)])
        info = probe(source)
        require(any(item["codec_type"] == "video" for item in info["streams"]), "Invalid packaged test fixture")
        report["artifacts"].update(source=str(source), ocr_source=str(ocr_source))
        return dict(source=str(source), synthetic_ocr_text="HELLO WORLD")

    def export():
        target = run / "Video độc lập H265.mov"
        settings = dict(width=320, height=180, fps=15, subtitle_path=str(subtitle),
                        subtitle_style=dict(size=15, color="#FFE066", outline=1, bg_opacity=.5),
                        text_layers=[dict(text="Độc lập – tiếng Việt", x=20, y=8, font_size=16, color="#FFFFFF")],
                        image_layers=[dict(path=str(transform_logo), x=10, y=10, width=60, height=30,
                                           anchor="bottom-right", rotation=90, flip_horizontal=True,
                                           enabled=True, start=.2, end=.8)],
                        blur_zones=[dict(x=230, y=60, width=70, height=60, shape="ellipse", strength=8, feather=2)],
                        frame_border=3, frame_border_color="#00FF00",
                        encoder=dict(format="mov", codec="hevc", device="cpu", preset="fast", quality=26, bframes=False))
        response = job(dict(operation="export", clips=[dict(path=str(source), start=.2, end=1.2)], settings=settings, output=str(target)), "export-job")
        info = probe(target)
        video = next(item for item in info["streams"] if item["codec_type"] == "video")
        require(video["codec_name"] == "hevc" and video["codec_tag_string"] == "hvc1", "Packaged advanced export ignored H.265/MOV settings")
        require(video.get("has_b_frames") == 0, "Packaged encoder ignored disabled B-frames")
        frame_path = run / "packaged-transformed-frame.png"
        ffmpeg(["-ss", "0.5", "-i", str(target), "-frames:v", "1", str(frame_path)])
        from PIL import Image
        with Image.open(frame_path) as picture:
            top, bottom = picture.convert("RGB").getpixel((295, 120)), picture.convert("RGB").getpixel((295, 155))
        require(top[1] > 180 and top[0] < 80 and bottom[0] > 180 and bottom[1] < 80,
                f"Packaged rotate/flip/anchor output placement is incorrect: top={top}, bottom={bottom}")
        require(abs(float(info["format"]["duration"]) - 1) < .15, "Packaged trim duration incorrect")
        require(response["progress"][-1]["percent"] == 100, "Packaged render did not finish")
        report["artifacts"]["export"] = str(target)
        report["artifacts"]["transformed_frame"] = str(frame_path)
        return dict(output=str(target), codec=video["codec_name"], duration=info["format"]["duration"],
                    rotated_flipped_anchor=True, has_b_frames=video["has_b_frames"])

    def mp3():
        target = run / "Âm thanh độc lập.mp3"
        response = job(dict(operation="mp3", input=str(source), output=str(target)), "mp3-job")
        info = probe(target)
        audio = next(item for item in info["streams"] if item["codec_type"] == "audio")
        require(audio["codec_name"] == "mp3", "Packaged MP3 extraction did not create MP3 audio")
        report["artifacts"]["mp3"] = str(target)
        return dict(output=str(target), codec=audio["codec_name"], progress_end=response["progress"][-1]["percent"])

    def ocr():
        target = run / "English OCR result.srt"
        response = job(dict(operation="ocr", input=str(ocr_source), output=str(target), language="en", interval=.5,
                            region=dict(x=0, y=235, width=640, height=110)), "ocr-job", timeout=120)
        require(target.is_file(), "Packaged multiprocessing OCR did not create subtitles")
        text = target.read_text(encoding="utf-8-sig")
        normalized = re.sub(r"[^A-Z]", "", text.upper())
        require("HELLOWORLD" in normalized, "Bundled OCR did not recognize the synthetic English text: " + text)
        require(response["progress"][-1]["percent"] == 100, "Packaged OCR did not finish")
        require(not (run / "hf-cache").exists() or not any((run / "hf-cache").rglob("*.onnx")), "OCR downloaded a model outside its bundle")
        report["artifacts"]["ocr_subtitles"] = str(target)
        return dict(output=str(target), recognized_text=text.strip(), multiprocessing_spawn=True, model_download=False)

    def bundled_screenshot():
        from PIL import Image
        target=run / "bundled-empty-workspace.png"
        process=invoke([str(executable),"--testing","--screenshot",str(target)],timeout=45)
        require(process.returncode==0 and target.is_file(),
                f"Bundled screenshot failed (exit={process.returncode})")
        picture=Image.open(target)
        require(picture.width>=600 and picture.height>=500,"Bundled workspace screenshot has invalid dimensions")
        report["artifacts"]["bundled_screenshot"]=str(target)
        return dict(screenshot=str(target),dimensions=[picture.width,picture.height],packaged_process=True,
                    display="Qt offscreen virtual screen")

    if not options.screenshot_only:
        check("Packaged desktop self-check and isolated tools/data roots", self_check)
        check("Fixture generation using bundled FFmpeg", fixture)
        check("Actual EXE advanced export with H.265 and Vietnamese paths/text", export)
        check("Actual EXE MP3 extraction", mp3)
        check("Actual EXE English RapidOCR multiprocessing without model download", ocr)
    check("Actual bundled empty-workspace screenshot",bundled_screenshot)
    persist()
    print(json.dumps(dict(report=str(result_path), passed=report["passed"], failed=report["failed"]), ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
