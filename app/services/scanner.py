# -*- coding: utf-8 -*-
"""manifest 扫描器：扫描约定目录下的 *.py.manifest.yaml，登记脚本。

新增脚本流程：写 .py + 写同名 .manifest.yaml → 放入对应目录 → 调用本扫描器自动登记。
"""
import logging
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..config import Config
from ..models import Strategy

logger = logging.getLogger("app.scanner")


def find_manifests(cfg: Config) -> list[Path]:
    """返回所有 *.py.manifest.yaml 文件路径。"""
    found = []
    for rel in cfg.scan_dirs:
        base = cfg.ROOT / rel
        if not base.exists():
            continue
        for p in base.rglob("*.py.manifest.yaml"):
            found.append(p)
    return found


def load_manifest(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _entry(mf: Path, cfg: Config) -> tuple:
    """从 manifest 提取 (kind, script_path) 登记键。"""
    rel_mf = mf.relative_to(cfg.ROOT).as_posix()
    data = load_manifest(mf)
    kind = data.get("kind", "script")
    script = data.get("script", "")
    if not script:
        script = rel_mf[: -len(".manifest.yaml")] if rel_mf.endswith(".manifest.yaml") else ""
    return (kind, script, rel_mf)


def scan(cfg: Config, db: Session) -> dict:
    """扫描并登记/更新脚本，随后移除已失效登记。"""
    registered = upserted = 0
    manifests = find_manifests(cfg)
    alive_keys = set()

    for mf in manifests:
        kind, script_path, rel_mf = _entry(mf, cfg)
        data = load_manifest(mf)
        alive_keys.add((kind, script_path))

        row = (
            db.query(Strategy)
            .filter(Strategy.kind == kind, Strategy.script_path == script_path)
            .first()
        )
        if row is None:
            row = Strategy(kind=kind, script_path=script_path)
            db.add(row)
            registered += 1
        else:
            upserted += 1

        name = data.get("name") or Path(script_path).stem
        tags = data.get("tags")
        row.name = name
        row.manifest_path = rel_mf
        row.params_schema = data.get("params", {}) or {}
        row.data_dep = str(data.get("data_dep", "") or "")
        row.output_pattern = str(data.get("output", "") or "")
        row.tags = ",".join(tags) if isinstance(tags, list) else str(tags or "")

    db.commit()

    # 移除已不存在的登记
    removed = remove_stale(cfg, db, alive_keys)

    return {
        "total": len(manifests),
        "registered": registered,
        "upserted": upserted,
        "removed": removed,
    }


def remove_stale(cfg: Config, db: Session, alive_keys: set = None) -> int:
    """删除不再由 manifest 支持的脚本登记。"""
    if alive_keys is None:
        alive_keys = {(_entry(mf, cfg)[0], _entry(mf, cfg)[1]) for mf in find_manifests(cfg)}
    alive = {tuple(k) for k in alive_keys}
    count = 0
    for s in db.query(Strategy).all():
        if (s.kind, s.script_path) not in alive:
            db.delete(s)
            count += 1
    db.commit()
    return count