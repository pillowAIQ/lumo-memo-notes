"""Filter Qt files before collection; audit finished bundles without deleting files."""
from pathlib import Path
import json
import sys

QT_MODULES = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "Qt6Svg.dll"}
QT_PLUGINS = {"qwindows.dll", "qmodernwindowsstyle.dll", "qwindowsvistastyle.dll", "qsvg.dll", "qsvgicon.dll", "qico.dll",
              # 图片附件：照片 / 动图 / WebP 解码
              "qjpeg.dll", "qgif.dll", "qwebp.dll"}


def keep_binary(destination):
    parts = destination.replace("\\", "/").split("/")
    if "PySide6" not in parts:
        return True
    name = parts[-1]
    # All windows use the raster Widgets backing store; no QOpenGLWidget/QML.
    if name == "opengl32sw.dll":
        return False
    if "plugins" in parts:
        return name in QT_PLUGINS
    if name.startswith(("Qt6", "av", "sw")) and name.endswith(".dll"):
        return name in QT_MODULES
    return True


def filter_binaries(entries):
    return [entry for entry in entries if keep_binary(entry[0])]


def filter_datas(entries):
    # No QTranslator or QML is used by this Widgets application.
    return [e for e in entries if not (
        "PySide6" in e[0].replace("\\", "/").split("/")
        and any(part in e[0].replace("\\", "/").split("/") for part in ("translations", "qml")))]


def audit_bundle(bundle):
    bundle = Path(bundle)
    files = [p for p in bundle.rglob("*") if p.is_file()]
    unwanted = [str(p.relative_to(bundle)) for p in files if not keep_binary(str(p.relative_to(bundle)))]
    if unwanted:
        raise RuntimeError("Unexpected Qt components: " + ", ".join(unwanted))
    required = ("Lumo.exe", "_internal/PySide6/Qt6Core.dll", "_internal/PySide6/Qt6Gui.dll",
                "_internal/PySide6/Qt6Widgets.dll", "_internal/PySide6/Qt6Svg.dll",
                "_internal/PySide6/plugins/platforms/qwindows.dll")
    missing = [name for name in required if not (bundle / name).is_file()]
    if missing:
        raise RuntimeError("Missing runtime files: " + ", ".join(missing))
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "largest": [{"file": str(p.relative_to(bundle)), "bytes": p.stat().st_size}
                        for p in sorted(files, key=lambda p:p.stat().st_size, reverse=True)[:15]]}


if __name__ == "__main__":
    print(json.dumps(audit_bundle(sys.argv[1] if len(sys.argv)>1 else "dist/Lumo"), indent=2))
