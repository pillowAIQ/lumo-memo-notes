"""Reproducible build: test -> stage -> audit -> frozen checks -> publish -> zip."""
from pathlib import Path
import json
import os
import subprocess
import sys
import zipfile

from prune_dist import audit_bundle

ROOT = Path(__file__).resolve().parent


def run(args, env):
    print("Running:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str,args)), cwd=ROOT, env=env, check=True)


def main():
    if Path(sys.executable).resolve() != (ROOT / ".venv/Scripts/python.exe").resolve():
        raise RuntimeError("Run build_exe.bat using .venv/Scripts/python.exe (AGENTS.md)")
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM"):
        env.pop(key, None)
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join(map(str, (Path(sys.executable).parent, windows/"System32", windows)))
    env["PYTHONPATH"] = str(ROOT / "src")
    shell = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
    publish = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ROOT/"publish_release.ps1"]
    baseline = ROOT / "dist/Lumo"
    before = sum(p.stat().st_size for p in baseline.rglob("*") if p.is_file())
    if "--publish-only" not in sys.argv:
        run([sys.executable, "-W", "ignore::ResourceWarning", "-m", "unittest", "discover", "-s", "tests"], env)
        run([sys.executable, "ui_smoke.py"], env)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", "build/release-stage", "memo.spec"], env)
    stage = ROOT / "build/release-stage/Lumo"
    summary = audit_bundle(stage)
    # The frozen process must run without access to the development Python or Qt.
    frozen_env = env.copy()
    frozen_env.pop("PYTHONPATH", None)
    frozen_env["PATH"] = os.pathsep.join(map(str, (windows/"System32", windows)))
    check_report = ROOT / "build/package-verification.json"
    check_report.unlink(missing_ok=True)
    subprocess.run([str(stage/"Lumo.exe"), "--verify-package", str(check_report)],
                   cwd=stage, env=frozen_env, check=True, timeout=90)
    checks = json.loads(check_report.read_text(encoding="utf-8"))
    if not checks.get("ok"):
        raise RuntimeError(checks)
    if "--stage-only" in sys.argv:
        final = stage
    else:
        run(publish, env)
        final = ROOT / "dist/Lumo"
        if audit_bundle(final) != summary:
            raise RuntimeError("Published bundle differs from verified staging bundle")
    archive = ROOT / "build/Lumo-release.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(final.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(final.parent))
    summary.update(before_bytes=before, zip_bytes=archive.stat().st_size, checks=checks)
    (ROOT/"build/release-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified bundle: {final} ({summary['bytes']/1024**2:.2f} MiB)")
    print(f"Download: {archive} ({summary['zip_bytes']/1024**2:.2f} MiB)")


if __name__ == "__main__":
    main()
