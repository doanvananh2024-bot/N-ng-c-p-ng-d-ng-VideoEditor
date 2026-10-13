"""Check the shipped public four-stem CUDA job against installed local models."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import numpy as np
import soundfile as sf
import engine
import gpu_runtime

ROOT = Path(__file__).resolve().parent
QA = ROOT.parent / "qa"
run = QA / ("gpu_stems_" + uuid.uuid4().hex[:8])
run.mkdir()
pack = gpu_runtime.discover_demucs_pack()
if not pack:
    raise RuntimeError("Installed GPU model not found")
fingerprint = hashlib.sha256(Path(pack["checkpoint"]).read_bytes()).hexdigest()
fixture = run / "source.wav"
time = np.arange(4*44100) / 44100
sf.write(fixture,np.column_stack([.08*np.sin(2*np.pi*220*time)+.05*np.sin(2*np.pi*440*time)]*2),44100,subtype="FLOAT")
job = run / "separate.json"
job.write_text(json.dumps(dict(operation="separate",input=str(fixture),output=str(run/"stems"),mode="stems",device="cuda")),encoding="utf-8")
env = os.environ.copy()
local = Path(env["LOCALAPPDATA"])
env.update(VEI_DEMUCS_PACK=str(local/"VideoEditor/voicesep/smartvoice_cuda"),VEI_CUDA_PATH=str(local/"VideoEditor/cuda"),
           LOCALAPPDATA=str(run/"local-data"),APPDATA=str(run/"roaming-data"),HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")
env.pop("PYTHONPATH",None)
executable = ROOT / "dist/VideoEditorIndependent/VideoEditorIndependent.exe"
completed = subprocess.run([str(executable),"--job",str(job)],env=env,capture_output=True,timeout=120,creationflags=engine._creation_flags())
payload = json.loads(job.with_suffix(".result.json").read_text(encoding="utf-8"))
assert completed.returncode == 0 and payload.get("ok"), payload
paths = payload["result"]
assert all(role in paths for role in ("vocals","drums","bass","other")), paths
assert any("GPU NVIDIA" in item["message"] for item in payload["progress"]), "GPU result not confirmed"
tracks = {}
for role in ("vocals","drums","bass","other"):
    values, rate = sf.read(paths[role],dtype="float32",always_2d=True)
    assert rate == 44100 and len(values) == len(time) and values.shape[1] == 2 and np.isfinite(values).all()
    tracks[role] = dict(path=paths[role],sample_rate=rate,frames=len(values),rms=float(np.sqrt(np.mean(values**2))))
assert hashlib.sha256(Path(pack["checkpoint"]).read_bytes()).hexdigest() == fingerprint
report = dict(status="passed",passed=1,failed=0,executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
              checks=[dict(name="Shipped EXE separates four real CUDA stems",status="passed",details=tracks)],
              shared_checkpoint_unchanged=True,public_job=True,run_dir=str(run))
(QA/"gpu-stems-packaged-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report,indent=2))
