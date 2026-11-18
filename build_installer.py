"""Build the per-user installer from an independently verified Windows bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
APP_ID = "9157727B-A0A5-4444-9235-E98DEDF1C27D"


def app_version():
    theme = (ROOT / "src/memo_app/theme.py").read_text(encoding="utf-8-sig")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8-sig")
    version = re.search(r'^APP_VERSION = "([0-9.]+)"', theme, re.M).group(1)
    if not re.search(r'^version = "' + re.escape(version) + '"', project, re.M):
        raise RuntimeError("Application and project versions differ")
    return version


def find_compiler(explicit=None):
    candidates = [explicit, os.environ.get("ISCC"), shutil.which("ISCC.exe"),
                  ROOT / "build/tools/inno/ISCC.exe",
                  Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
                  Path("C:/Program Files/Inno Setup 7/ISCC.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError("Install Inno Setup, or pass --iscc path/to/ISCC.exe")


def build(bundle, output, compiler, *, test_mode=False, test_app_id=None):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if not (bundle / "Lumo.exe").is_file():
        raise RuntimeError("Verified Lumo bundle missing")
    # Always verify this exact bundle; an old report is not evidence for new bytes.
    from prune_dist import audit_bundle
    audit_bundle(bundle)
    output.mkdir(parents=True, exist_ok=True)
    report = output / ("installer-bundle-test-verification.json" if test_mode else "installer-bundle-verification.json")
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"):
        env.pop(key, None)
    windows = Path(env.get("SystemRoot", "C:/Windows"))
    env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    report.unlink(missing_ok=True)
    subprocess.run([str(bundle / "Lumo.exe"), "--verify-package", str(report)],
                   cwd=bundle, env=env, check=True, timeout=90)
    verification = json.loads(report.read_text(encoding="utf-8"))
    if not verification.get("ok"):
        raise RuntimeError("Installer bundle verification failed")
    version = app_version()
    if verification.get("version") != version:
        raise RuntimeError("Bundle version differs from source; rebuild the EXE first")
    app_id = str(uuid.UUID(test_app_id or str(uuid.uuid4()))) if test_mode else APP_ID
    if test_mode and app_id.upper() == APP_ID:
        raise RuntimeError("Test mode cannot use production AppId")
    filename = f"Lumo-{version}-windows-x64-setup" + ("-test" if test_mode else "")
    definitions = {
        "AppId": app_id, "AppVersion": version, "BundleDir": str(bundle),
        "OutputDir": str(output), "OutputBaseFilename": filename, "RepoRoot": str(ROOT),
        "TestMode": str(int(test_mode)),
        "LangZh": str(int((compiler.parent / "Languages/ChineseSimplified.isl").exists())),
    }
    subprocess.run([str(compiler), "/Qp", *[f"/D{k}={v}" for k, v in definitions.items()],
                    str(ROOT / "installer/lumo.iss")], cwd=ROOT, check=True)
    artifact = output / (filename + ".exe")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    result = {"version": version, "file": str(artifact), "size": artifact.stat().st_size,
              "sha256": digest, "app_id": app_id, "test_mode": test_mode,
              "bundle_verified": True, "authenticode_signed": False}
    (output / ("installer-test-report.json" if test_mode else "installer-report.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not test_mode:
        (output / "SHA256SUMS.txt").write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
    print(json.dumps(result, ensure_ascii=True))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc")
    parser.add_argument("--bundle-dir", default=str(ROOT / "dist/Lumo"))
    parser.add_argument("--output-dir", default=str(ROOT / "build"))
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--test-app-id")
    args = parser.parse_args()
    build(args.bundle_dir, args.output_dir, find_compiler(args.iscc),
          test_mode=args.test_mode, test_app_id=args.test_app_id)
