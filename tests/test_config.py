import importlib
import sys
from pathlib import Path


def test_data_dir_under_repo_root(monkeypatch):
    # 开发态:数据目录 = 仓库根/data
    monkeypatch.delattr(sys, "frozen", raising=False)
    from sm_scan import config

    importlib.reload(config)
    assert config.ROOT == Path(__file__).resolve().parent.parent
    assert config.data_dir().name == "data"
    assert config.data_dir().parent == config.ROOT


def test_dirs_created_on_call(tmp_path, monkeypatch):
    from sm_scan import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert config.data_dir() == tmp_path / "data"
    assert config.reports_dir() == tmp_path / "reports"
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "reports").is_dir()


def test_frozen_mode(tmp_path, monkeypatch):
    # 打包态:数据目录 = exe 所在目录/data(spec §2)
    fake_exe = tmp_path / "dist" / "sm-scan.exe"
    fake_exe.parent.mkdir()
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    from sm_scan import config

    try:
        importlib.reload(config)
        assert config.ROOT == fake_exe.parent
        assert config.data_dir().parent == tmp_path / "dist"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
