"""PyInstaller 打包(spec §15):onedir,产 GUI 与 CLI 两个入口。

用法:python build.py
→  dist/paperpulse/paperpulse.exe(CLI,任务计划用)
→  dist/paperpulse-gui/paperpulse-gui.exe(界面)
"""

import os

import PyInstaller.__main__ as pymain

CLI_ENTRY = "cli_entry.py"
GUI_ENTRY = "paperpulse/gui_entry.py"

COMMON = [
    "--clean", "--noconfirm",
    "--paths=.",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebChannel",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtMultimedia",
    "--add-data", "presets" + os.pathsep + "presets",
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
    build("paperpulse", CLI_ENTRY, extra=[
        "--console",
        "--exclude-module", "PySide6",
        "--exclude-module", "shiboken6",
    ])
    # GUI(带窗口)
    build("paperpulse-gui", GUI_ENTRY, extra=["--windowed"])
    print("done: dist/paperpulse/")
