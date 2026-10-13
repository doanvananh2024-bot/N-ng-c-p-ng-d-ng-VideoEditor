"""Publish release metadata only after source and frozen-package checks pass."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "dist" / "VideoEditorIndependent"
VERSION = "1.2.0"


def main():
    names = ["verification-report.json", "upgrade-verification-report.json",
             "subtitle-editor-verification-report.json", "advanced-upgrade-verification-report.json",
             "renderer-regression-report.json", "tts-regression-report.json", "packaged-verification-report.json"]
    reports = {}
    for name in names:
        data = json.loads((ROOT / "qa" / name).read_text(encoding="utf-8"))
        if data.get("failed") != 0 or data.get("status") != "passed":
            raise RuntimeError("Release check has not passed: " + name)
        reports[name] = dict(passed=data["passed"], failed=data["failed"])
    sidebar_path = ROOT / "qa" / "sidebar_state_cc55d7a7" / "report.json"
    sidebar = json.loads(sidebar_path.read_text(encoding="utf-8"))
    if sidebar.get("status") != "passed" or len(sidebar.get("checks", [])) != 5:
        raise RuntimeError("Sidebar verification is incomplete")
    reports["sidebar-state"] = dict(passed=5, failed=0)
    bundle = json.loads((ROOT / "qa" / "bundle-source-report.json").read_text(encoding="utf-8"))
    if bundle.get("all_final_sources") is not True:
        raise RuntimeError("The bundle does not contain final source modules")
    executable = PACKAGE / "VideoEditorIndependent.exe"
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    packaged = json.loads((ROOT / "qa" / "packaged-verification-report.json").read_text(encoding="utf-8"))
    if packaged.get("executable_sha256") != digest:
        raise RuntimeError("The executable changed after package verification")
    checked = json.loads(Path(packaged["artifacts"]["self_check"]).read_text(encoding="utf-8"))
    if checked.get("version") != VERSION:
        raise RuntimeError("Unexpected executable version")
    release = dict(application="Video Editor Independent", version=VERSION,
                   created_at=datetime.now(timezone.utc).isoformat(), executable_sha256=digest,
                   reference_application="VideoEditor", reference_version="1.0.36",
                   reports=reports, total_checks=sum(row["passed"] for row in reports.values()),
                   final_source_modules=len(bundle["modules"]), final_source_parity=True,
                   owner="local", license_server=None, exact_reference_parity=False,
                   paid_provider_requests=0, provider_transport_validation="mocked HTTP and real synthetic audio",
                   limitations=["Private speech services and equivalent offline TTS/model packs remain incomplete",
                                "Some source transform/blend/background modules and full live filter preview differ",
                                "FPT/ElevenLabs require own credentials; paid live requests were not tested",
                                "Reference accounts, licensing and update service are separate"])
    content = json.dumps(release, ensure_ascii=False, indent=2)
    (ROOT / "release.json").write_text(content, encoding="utf-8")
    (ROOT / "qa" / "release-verification-summary.json").write_text(content, encoding="utf-8")
    for name in ["release.json", "README.md", "parity-report.md", "requirements-lock.txt"]:
        shutil.copy2(ROOT / name, PACKAGE / name)
    packaged_qa = PACKAGE / "qa"
    packaged_qa.mkdir(exist_ok=True)
    for name in names + ["bundle-source-report.json", "release-verification-summary.json"]:
        shutil.copy2(ROOT / "qa" / name, packaged_qa / name)
    shutil.copy2(sidebar_path, packaged_qa / "sidebar-state-report.json")
    advanced = json.loads((ROOT / "qa" / "advanced-upgrade-verification-report.json").read_text(encoding="utf-8"))
    shutil.copy2(advanced["artifacts"]["advanced_gui_screenshot"], packaged_qa / "preview-1.2.png")
    shutil.copy2(packaged["artifacts"]["bundled_screenshot"], packaged_qa / "empty-workspace.png")
    print(json.dumps(dict(version=VERSION, total_checks=release["total_checks"],
                          executable_sha256=digest, package=str(PACKAGE))))


if __name__ == "__main__":
    main()
