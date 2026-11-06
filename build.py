"""PyInstaller 打包(spec §15):onedir,产 GUI 与 CLI 两个入口。

用法:python build.py
→  dist/sm-scan/sm-scan.exe(CLI,任务计划用)
→  dist/sm-scan-gui/sm-scan-gui.exe(界面)
"""

import PyInstaller.__main__ as pymain

CLI_ENTRY = "cli_entry.py"
GUI_ENTRY = "sm_scan/gui_entry.py"

COMMON = [
    "--clean", "--noconfirm",
    "--paths=.",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebChannel",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtMultimedia",
]


def build(cli_name: str, entry: str, *, extra: list[str] | None = None) -> None:
    args = [
        "pyinstaller", "--onedir", f"--name={cli_name}", *COMMON,
        *(extra or []), entry,
    ]
    import sys
    sys.argv = args
    pymain.run()


if __name__ == "__main__":
    # CLI(任务计划用;无窗口依赖)
    build("sm-scan", CLI_ENTRY, extra=["--console"])
    # GUI(带窗口)
    build("sm-scan-gui", GUI_ENTRY, extra=["--windowed"])
    print("done: dist/sm-scan/")
