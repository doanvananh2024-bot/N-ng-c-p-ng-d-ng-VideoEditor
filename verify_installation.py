"""Check the installed release and shortcuts using an isolated runtime profile."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


ROOT = Path(__file__).resolve().parent


def main():
    installed = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Video Editor Independent" / "versions" / "1.2.0"
    executable = installed / "VideoEditorIndependent.exe"
    release = json.loads((installed / "release.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    assert digest == release["executable_sha256"]
    assert release["version"] == "1.2.0" and release["total_checks"] == 56
    run = ROOT / "qa" / ("installed_1.2_" + uuid.uuid4().hex[:8])
    run.mkdir()
    result_path = run / "runtime.json"
    env = os.environ.copy()
    env.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
               QT_QPA_PLATFORM="offscreen", HF_HOME=str(run / "hf-cache"),
               TORCH_HOME=str(run / "torch-cache"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    process = subprocess.run([str(executable), "--self-check", "--check-output", str(result_path)],
                             env=env, cwd=run, capture_output=True, timeout=90,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.returncode == 0 and result_path.is_file(), "Installed runtime self-check failed"
    runtime = json.loads(result_path.read_text(encoding="utf-8"))
    assert runtime["version"] == "1.2.0"
    assert all(value == "OK" for value in runtime["modules"].values())
    assert Path(runtime["data_root"]).is_relative_to(run)
    for name in ("ffmpeg", "ffprobe", "yt_dlp"):
        assert Path(runtime[name]).is_relative_to(installed)
    shell = r"""
$taskShortcutShell = New-Object -ComObject WScript.Shell
$taskFolders = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
$taskRows = foreach ($taskFolder in $taskFolders) {
    $taskShortcutPath = Join-Path $taskFolder 'Video Editor Independent.lnk'
    if (-not (Test-Path -LiteralPath $taskShortcutPath)) { throw 'Missing independent application shortcut' }
    $taskShortcut = $taskShortcutShell.CreateShortcut($taskShortcutPath)
    [pscustomobject]@{Shortcut=$taskShortcutPath;Target=$taskShortcut.TargetPath;WorkingDirectory=$taskShortcut.WorkingDirectory}
}
$taskRows | ConvertTo-Json -Compress
"""
    shortcuts = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", shell],
                               capture_output=True, text=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert shortcuts.returncode == 0, "Cannot inspect shortcuts"
    targets = json.loads(shortcuts.stdout)
    assert len(targets) == 2
    for row in targets:
        assert Path(row["Target"]).resolve() == executable.resolve()
        assert Path(row["WorkingDirectory"]).resolve() == installed.resolve()
    report = dict(status="passed", version="1.2.0", installed_executable=str(executable),
                  executable_sha256=digest, created_at=datetime.now(timezone.utc).isoformat(),
                  shortcuts=targets, runtime_report=str(result_path),
                  prior_version_retained=(installed.parent / "1.1.0" / "VideoEditorIndependent.exe").is_file(),
                  user_profile_modified=False)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    (ROOT / "qa" / "installation-upgrade-report.json").write_text(content, encoding="utf-8")
    (installed / "qa" / "installation-upgrade-report.json").write_text(content, encoding="utf-8")
    print(json.dumps(dict(status="passed", version="1.2.0", shortcuts=len(targets),
                          installed_executable=str(executable))))


if __name__ == "__main__":
    main()
