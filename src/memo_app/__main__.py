import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import db
from . import single_instance
from . import theme
from .app import MainWindow
from .controller import Controller


def _setup_logging() -> None:
    log_dir = db.data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / "app.log", encoding="utf-8")],
        force=True,
    )

    def _hook(exc_type, exc, tb):
        logging.getLogger("memo_app").critical(
            "未捕获异常", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _hook


def main() -> None:
    app = QApplication(sys.argv)
    # 内部标识保持 memo / memo_app：QSettings、数据目录、凭据沿用，不丢设置
    app.setApplicationName("memo")
    app.setOrganizationName("memo_app")
    app.setApplicationDisplayName(theme.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))

    # 单实例：在创建窗口/托盘/热键/同步之前先确立所有权。
    # 次实例向主实例请求激活后直接退出，不触碰数据目录（含 app.log）。
    acq = single_instance.acquire(db.data_dir(), parent=app)
    if acq.role == single_instance.SECONDARY:
        return
    if acq.role != single_instance.PRIMARY:
        # 无法确定归属：宁可退出也不允许双主实例写同一数据目录
        if sys.stderr is not None:  # frozen windowed 构建无控制台
            print("Lumo: 无法确认实例归属，已退出（已有实例可能无响应）", file=sys.stderr)
        sys.exit(3)

    _setup_logging()
    app.setApplicationVersion(theme.APP_VERSION)
    if sys.platform == "win32":
        # Kept until process exit so the installer never replaces a running app.
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        app._installer_mutex = kernel.CreateMutexW(None, False, "Local\\LumoInstallerRunning-v1")
    theme.apply(theme.saved_name(), style_key=theme.saved_style())  # 全局样式：按上次选择的风格与皮肤

    win = MainWindow()
    controller = Controller(win)  # 托盘 / 全局热键 / 快捷记录 / 桌面挂件
    # 二次启动 -> 唤出主窗口（含最小化/托盘隐藏状态，复用托盘同一入口）
    acq.guard.activated.connect(controller.show_main)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
