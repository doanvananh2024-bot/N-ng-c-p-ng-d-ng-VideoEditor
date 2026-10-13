"""Reuse installed local CUDA packs without importing AI libraries on the GUI.

Only model/runtime folders are inspected. Shared packs are never changed or
removed. CUDA DLL readiness is distinct from a working inference provider.
"""
from __future__ import annotations

from collections import deque
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

_DLL_HANDLES = []
_PREPARED_PATHS: set[str] = set()
_RUNTIME_LOCK = threading.Lock()
_SEPARATION_LOCK = threading.Lock()
CHECKPOINT = "955717e8-8726e21a.th"
_MESSAGE_PREFIX = "__VEI_JSON__"


def _local_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VideoEditor"


def discover_demucs_pack() -> dict | None:
    """Return public runtime/model paths; no native imports or model downloads."""
    root = Path(os.environ.get("VEI_DEMUCS_PACK", str(_local_root() / "voicesep" / "smartvoice_cuda"))).expanduser()
    python = root / "py311" / "python.exe"
    checkpoint = root / "models" / "hub" / "checkpoints" / CHECKPOINT
    package = root / "venv" / "Lib" / "site-packages"
    if not (python.is_file() and checkpoint.is_file() and checkpoint.stat().st_size > 0
            and (package / "demucs" / "separate.py").is_file()
            and (package / "torch" / "lib" / "torch_cuda.dll").is_file()):
        return None
    return {"root": str(root.resolve()), "python": str(python.resolve()),
            "torch_home": str((root / "models").resolve()),
            "checkpoint": str(checkpoint.resolve()), "shared": True}


def configure_cuda_runtime() -> dict:
    """Prepare DLL search paths before importing ORT; this does not assert GPU use.

    Windows ORT's native loader needs PATH as well as AddDllDirectory. Keep the
    returned directory handles alive for the process. A CPU-only ORT wheel still
    requires its GPU counterpart before it can offer CUDAExecutionProvider.
    """
    folder = Path(os.environ.get("VEI_CUDA_PATH", str(_local_root() / "cuda"))).expanduser()
    needed = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
    ready = os.name == "nt" and all((folder / name).is_file() for name in needed)
    paths = [folder] if ready else []
    try:
        spec = importlib.util.find_spec("onnxruntime")
        if spec and spec.submodule_search_locations:
            paths.extend(Path(base) / "capi" for base in spec.submodule_search_locations)
    except (ImportError, ValueError):
        pass
    if getattr(sys, "_MEIPASS", None):
        paths.append(Path(sys._MEIPASS) / "onnxruntime" / "capi")
    with _RUNTIME_LOCK:
        for path in paths:
            if not path.is_dir():
                continue
            value = str(path.resolve())
            key = os.path.normcase(value)
            if key in _PREPARED_PATHS:
                continue
            os.environ["PATH"] = value + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(value))
                except OSError:
                    pass
            _PREPARED_PATHS.add(key)
    return {"available": ready, "dlls_ready": ready, "folder": str(folder),
            "requires_gpu_onnxruntime": True}


def prepare_onnx_cuda() -> bool:
    return bool(configure_cuda_runtime()["dlls_ready"])


def demucs_environment(pack: dict) -> dict[str, str]:
    env = os.environ.copy()
    env.update(TORCH_HOME=pack["torch_home"], PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env["PATH"] = str(Path(pack["python"]).parent) + os.pathsep + env.get("PATH", "")
    return env


def run_json_worker(python: str, script: str, args: list[str], progress=None,
                    cancel=None, env: dict | None = None, timeout: float | None = None):
    """Run a standalone pack worker and hard-cancel it without orphan processes."""
    import ai_tools
    ai_tools._check_cancel(cancel)
    try:
        process = subprocess.Popen([python, "-u", "-c", script, *args], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise ai_tools.AIError(f"Không mở được tiến trình GPU: {exc}") from exc
    messages: queue.Queue = queue.Queue()
    details: deque[str] = deque(maxlen=12)

    def drain():
        try:
            for line in process.stdout:
                messages.put(line.rstrip("\r\n"))
        finally:
            messages.put(None)

    reader = threading.Thread(target=drain, name="local-cuda-worker", daemon=True)
    reader.start()
    result = None
    received_result = False
    started = time.monotonic()
    try:
        while True:
            ai_tools._check_cancel(cancel)
            if timeout is not None and time.monotonic() - started > timeout:
                raise ai_tools.AIError("Tiến trình GPU không phản hồi trong thời gian kiểm tra.")
            try:
                line = messages.get(timeout=.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if not line.startswith(_MESSAGE_PREFIX):
                if line.strip():
                    details.append(line[-500:])
                continue
            try:
                message = json.loads(line[len(_MESSAGE_PREFIX):])
                if not isinstance(message, list) or not message or message[0] not in ("progress", "result", "error"):
                    raise ValueError
                if len(message) != (3 if message[0] == "progress" else 2):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ai_tools.AIError("Tiến trình GPU trả thông tin không hợp lệ.") from exc
            if message[0] == "progress":
                ai_tools._progress(progress, int(message[1]), str(message[2]))
            elif message[0] == "result":
                result, received_result = message[1], True
            elif message[0] == "error":
                raise ai_tools.AIError(str(message[1]))
            else:
                raise ai_tools.AIError("Tiến trình GPU trả thông tin không hợp lệ.")
        process.wait(timeout=5)
        ai_tools._check_cancel(cancel)
        if process.returncode or not received_result:
            detail = "\n".join(details)[-1500:]
            raise ai_tools.AIError("Tiến trình GPU không hoàn tất." + (" " + detail if detail else ""))
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        reader.join(timeout=1)
        if process.stdout:
            process.stdout.close()


_HARDWARE_SCRIPT = r'''
import json, torch
def emit(value): print("__VEI_JSON__" + json.dumps(value, ensure_ascii=False), flush=True)
try:
    available = bool(torch.cuda.is_available())
    name = torch.cuda.get_device_name(0) if available else ""
    if available:
        torch.ones(1, device="cuda").add_(1)
        torch.cuda.synchronize()
    emit(["result", {"torch_version": str(torch.__version__), "cuda": available, "device": name, "runtime": "smartvoice_cuda"}])
except Exception as exc:
    emit(["error", "Không kiểm tra được GPU NVIDIA: " + str(exc)])
'''


def inspect_demucs_hardware(progress=None, cancel=None) -> dict:
    pack = discover_demucs_pack()
    if pack is None:
        return {"torch_version": "", "cuda": False, "device": "", "runtime": "", "reason": "Chưa có bộ Demucs GPU đã cài."}
    return run_json_worker(pack["python"], _HARDWARE_SCRIPT, [], progress, cancel,
                           demucs_environment(pack), timeout=45)


DEMUCS_WORKER_SCRIPT = r'''
import argparse, json, os
from pathlib import Path
def emit(value): print("__VEI_JSON__" + json.dumps(value, ensure_ascii=False), flush=True)
parser = argparse.ArgumentParser()
parser.add_argument("-d", "--device", choices=("cuda",), required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--folder", required=True)
parser.add_argument("--checkpoint", required=True)
parser.add_argument("--mode", choices=("vocals", "stems"), required=True)
args = parser.parse_args()
handles = {}
try:
    import numpy as np, soundfile as sf, torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    if not torch.cuda.is_available():
        raise RuntimeError("Đã chọn GPU NVIDIA nhưng CUDA không khả dụng trong bộ tách giọng.")
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_file() or checkpoint.stat().st_size == 0:
        raise RuntimeError("Thiếu mô hình Demucs GPU đã cài; không tải lại tự động.")
    emit(["progress", 12, "Nạp Demucs trên GPU NVIDIA · " + torch.cuda.get_device_name(0)])
    # Explicit local repository prevents Torch Hub from accessing the network.
    model = get_model("955717e8", repo=checkpoint.parent).eval()
    if int(model.samplerate) != 44100 or model.audio_channels != 2:
        raise RuntimeError("Mô hình Demucs không đúng cấu hình 44100 Hz stereo.")
    names = ["vocals", "background"] + (["drums", "bass", "other"] if args.mode == "stems" else [])
    paths = {name: str(Path(args.folder) / (name + ".wav")) for name in names}
    source = sf.SoundFile(args.input)
    if source.samplerate != 44100 or source.channels != 2:
        source.close()
        raise RuntimeError("Âm thanh trung gian không đúng tần số/kênh Demucs.")
    handles = {name: sf.SoundFile(paths[name], mode="w", samplerate=44100, channels=2, format="WAV", subtype="FLOAT") for name in names}
    total, cursor = source.frames, 0
    block_length, context = 44100 * 30, 44100 * 2
    with source, torch.inference_mode():
        while cursor < total:
            left, right = max(0, cursor-context), min(total, cursor+block_length+context)
            source.seek(left)
            array = source.read(right-left, dtype="float32", always_2d=True)
            mix = torch.from_numpy(np.ascontiguousarray(array.T))
            reference = mix.mean(0)
            mean, std = reference.mean(), reference.std()
            if float(std) < 1e-6:
                stems = torch.zeros((len(model.sources), *mix.shape), dtype=mix.dtype)
                stems[model.sources.index("other")] = mix
            else:
                estimates = apply_model(model, ((mix-mean)/std)[None], device=args.device, shifts=0,
                                        split=True, overlap=.25, segment=7, progress=False, num_workers=0)[0].cpu()
                stems = estimates * std + mean/len(model.sources)
            start, keep = cursor-left, min(block_length, total-cursor)
            stems = stems[:, :, start:start+keep]
            vocal = stems[model.sources.index("vocals")]
            handles["vocals"].write(vocal.T.numpy())
            handles["background"].write((mix[:, start:start+keep]-vocal).T.numpy())
            for name in ("drums", "bass", "other") if args.mode == "stems" else ():
                handles[name].write(stems[model.sources.index(name)].T.numpy())
            cursor += keep
            emit(["progress", 15+int(80*cursor/max(1,total)), "Tách bằng GPU NVIDIA: %.1f/%.1f giây" % (cursor/44100,total/44100)])
    for handle in handles.values(): handle.close()
    handles = {}
    emit(["result", paths])
except Exception as exc:
    emit(["error", "Không tách được giọng/nhạc trên GPU: " + str(exc)])
finally:
    for handle in handles.values(): handle.close()
'''


def separate_demucs_gpu(source_wav: str, folder: str, mode: str, progress=None, cancel=None) -> dict:
    import ai_tools
    pack = discover_demucs_pack()
    if pack is None:
        raise ai_tools.AIError("Đã chọn GPU NVIDIA nhưng chưa có bộ smartvoice_cuda hoặc mô hình đã cài.")
    # Multiple videos may reach this stage together. Keep one Demucs model on
    # the GPU at a time instead of allocating a model in every worker process.
    waiting = False
    while not _SEPARATION_LOCK.acquire(timeout=.1):
        ai_tools._check_cancel(cancel)
        if progress and not waiting:
            progress(1, "Đang chờ GPU tách giọng…")
            waiting = True
    try:
        ai_tools._check_cancel(cancel)
        return run_json_worker(pack["python"], DEMUCS_WORKER_SCRIPT,
                               ["-d", "cuda", "--input", source_wav, "--folder", folder,
                                "--checkpoint", pack["checkpoint"], "--mode", mode],
                               progress, cancel, demucs_environment(pack))
    finally:
        _SEPARATION_LOCK.release()
