# -*- coding: utf-8 -*-
"""参数外置：把 manifest 声明的参数注入到脚本副本中，保留原文件。

策略：运行时把脚本复制到临时目录，替换顶部配置常量（如 START_DATE = xxx），
在「原脚本目录」作为工作目录下以 subprocess 运行临时副本，实现参数覆盖。
若参数未提供 var 映射或值为默认，则不注入，保持脚本原有行为。
"""
import re
import tempfile
from pathlib import Path

from ..config import Config, load_config

_CODE_ASSIGN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*?)([ \t]*#.*)?$",
    re.MULTILINE,
)


def _literal_for(value) -> str:
    """把参数值转成 Python 字面量，用于注入到代码中。

    按实际类型处理：bool/int/float/list 直接用 repr；字符串用 repr 加引号，
    确保注入后是合法 Python 赋值。
    """
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float, list, tuple)):
        return repr(value)
    # 字符串
    s = str(value).strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s
    return repr(s)


def inject_params(source: str, params: dict, schema: dict) -> str:
    """根据参数 schema 的 var 映射，把用户传入的 manifest 参数注入到代码常量。

    - params: {manifest参数名: 用户值}
    - schema: {manifest参数名: {var: "PYTHON_CONST", ...}}
    只有 schema 中声明了 var 且用户显式传入的参数才会被注入。
    """
    # 先收集「待注入」的 var -> 值 映射
    inject_map = {}
    for key, meta in schema.items():
        var = meta.get("var")
        if var and key in params:
            inject_map[var] = params[key]

    def repl(match):
        var = match.group("var")
        if var in inject_map:
            return f'{match.group("indent")}{var} = {_literal_for(inject_map[var])}'
        return match.group(0)

    return _CODE_ASSIGN.sub(repl, source)


def build_temp_copy(script_path: Path, params: dict, schema: dict) -> Path:
    """生成参数化临时副本，返回临时文件路径（调用方负责执行后清理）。"""
    source = script_path.read_text(encoding="utf-8")
    mutated = inject_params(source, params, schema)
    fd, tmp = tempfile.mkstemp(suffix=script_path.suffix, prefix="params_", dir=None)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(mutated)
    return Path(tmp)


if __name__ == "__main__":
    # 手动自测：python -m app.services.param_inject
    cfg: Config = load_config()
    demo = "START_DATE = '2020-01-01'\nEND_DATE = 'x'\nDAILY_DIR = r'每日'\n"
    schema = {"start_date": {"var": "START_DATE"}, "daily_dir": {"var": "DAILY_DIR"}}
    print(inject_params(demo, {"start_date": "2026-01-01", "daily_dir": "合并"}, schema))