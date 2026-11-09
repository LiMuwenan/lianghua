# -*- coding: utf-8 -*-
"""子进程导入环境测试：平台运行脚本副本时必须能 import app.*。

背景：参数化副本落在系统临时目录（容器内 /tmp/params_xxx.py），脚本用
`Path(__file__).resolve().parents[1]` 推导仓库根会算成 /，此时 /app 被当成
命名空间包，`from app.config import load_config` 报 ImportError(unknown location)。
平台需通过 PYTHONPATH 注入真实仓库根。
"""
import sys
sys.path.insert(0, "d:/Project/lianghua")

from types import SimpleNamespace

import app.config as app_config
from app.config import Config
from app.services import task_service as ts


def _make_layout(tmp_path):
    """模拟容器目录布局：root/app 为仓库根，root/tmpdir 放参数化副本。"""
    root = tmp_path / "root"
    repo = root / "app"                       # 模拟容器内 /app（仓库根）
    (repo / "app").mkdir(parents=True)        # 模拟 /app/app（app 包，无 __init__.py）
    (repo / "app" / "config.py").write_text(
        "def load_config():\n    return 'ok'\n", encoding="utf-8")
    # 模拟容器内 /app/config 目录（会与 app.config 模块同名，是命名空间包混淆的来源）
    (repo / "config").mkdir()
    (repo / "config" / "config.docker.yaml").write_text("x: 1\n", encoding="utf-8")
    (repo / "script").mkdir()
    (repo / "script" / "probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from app.config import load_config\n"
        "print('LOADED', load_config())\n",
        encoding="utf-8")
    tmpdir = root / "tmpdir"                  # 模拟 /tmp
    tmpdir.mkdir()
    return repo, tmpdir


def test_run_subprocess_can_import_app_package(tmp_path, monkeypatch):
    """临时副本位于仓库外时，子进程仍应能 import app 包内的模块。"""
    repo, tmpdir = _make_layout(tmp_path)
    monkeypatch.setattr(app_config, "ROOT", repo)
    monkeypatch.setattr(ts.param_inject, "build_temp_copy", lambda src, params, schema: (
        _copy_to(tmpdir, src)))

    cfg = Config({"data_dir": str(tmp_path / "market"),
                  "output_dir": str(tmp_path / "out")})
    svc = ts.TaskService(cfg)
    log_path = tmp_path / "task.log"
    strategy = SimpleNamespace(script_path="script/probe.py", params_schema={})

    res = svc._run_subprocess(1, strategy, log_path, params={}, timeout=60)

    text = log_path.read_text(encoding="utf-8")
    assert res["status"] == "success", text
    assert "LOADED ok" in text


def _copy_to(tmpdir, src):
    """把脚本写到模拟的临时目录，等价于 build_temp_copy 的行为。"""
    dst = tmpdir / f"params_{abs(hash(str(src))) % 10**8}.py"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst
