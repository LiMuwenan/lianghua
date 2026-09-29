# Docker 部署 + CI 打包 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将量化交易平台打包为单镜像（后端 + 前端 Web），用 docker-compose 注入配置/数据/端口即可直接运行，并用 GitHub Actions 构建并推送镜像到 Docker Hub。

**Architecture:** 单镜像多阶段构建（node 先行构建前端 → python 作为运行环境）。镜像无状态：配置走 `APP_CONFIG` 环境变量选文件，数据/数据库/输出全部挂载容器 `/data` 基目录。CI 用 buildx 多架构（本期 amd64）build & push。

**Tech Stack:** Docker / BuildKit multi-stage、Docker Compose、GitHub Actions、Python(FastAPI/Uvicorn)、Node/Vite。

**设计依据：** `docs/设计方案.md` 第 13 章《Docker 部署与 CI 打包》。Git 提交一律在 `platform` 分支。

---

### Task 1: 支持 `APP_CONFIG` 环境变量选择配置文件

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_config.py`，验证设置了绝对路径 `APP_CONFIG` 环境变量时读取对应文件，并按绝对路径解析出 data_dir/database/output_dir。

```python
# -*- coding: utf-8 -*-
"""配置加载测试：验证 APP_CONFIG 环境变量选择配置文件。"""
from app import config


def test_app_config_env_picks_custom_file(tmp_path, monkeypatch):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "database: /tmp/x/meta.db\n"
        "output_dir: /tmp/x/outputs\n"
        "data_dir: /tmp/x/market\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(custom))
    cfg = config.load_config()
    assert str(cfg.database) == "/tmp/x/meta.db"
    assert str(cfg.output_dir) == "/tmp/x/outputs"
    assert str(cfg.data_dir) == "/tmp/x/market"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_config.py::test_app_config_env_picks_custom_file -v`
Expected: FAIL（当前 `load_config()` 固定读 `ROOT/config/config.yaml`，不会读自定义文件）。

- [ ] **Step 3: 实现——config.py 支持 APP_CONFIG**

在 `app/config.py` 顶部引入 `import os`，并新增一个内部函数取配置文件路径：

```python
# -*- coding: utf-8 -*-
"""平台配置加载：读取 config/config.yaml（或 APP_CONFIG 指定文件），提供全局路径与参数。"""
import os
import sys
from pathlib import Path

import yaml

# 仓库根目录 = 本文件上两级（容器内为 /app）
ROOT = Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    """配置文件路径：APP_CONFIG 环境变量优先（用于 Docker 挂载配置），否则仓库内默认。"""
    env = os.environ.get("APP_CONFIG")
    if env:
        return Path(env).expanduser()
    return ROOT / "config" / "config.yaml"
```

并修改 `load_config()`：

```python
def load_config() -> Config:
    cfg_path = _config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Config(data)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/config.py tests/test_config.py
git commit -m "支持APP_CONFIG环境变量选择配置文件"
```

---

### Task 2: 新增 docker 运行时配置文件

**Files:**
- Create: `config/config.docker.yaml`

- [ ] **Step 1: 创建 `config/config.docker.yaml`**

容器内路径绝对化，全部指向 `/data` 布局（与本地 `config/config.yaml` 并行，本地开发不受影响）:

```yaml
# 量化交易平台 容器运行配置（打入镜像，docker-compose 挂载宿主机配置可覆盖）
# 数据基目录 /data 由容器卷挂载提供：/data/market、/data/meta.db、/data/outputs
database: /data/meta.db

output_dir: /data/outputs

# 主数据（Parquet 按股票分文件）容器内目录
data_dir: /data/market

# CORS 白名单（本机来源 + 常见 host 访问）
cors_origins:
  - http://127.0.0.1:8000
  - http://localhost:8000
  - http://127.0.0.1:5173
  - http://localhost:5173

# manifest 扫描目录（相对仓库根 /app，打入镜像）
scan_dirs:
  - script
  - strategy/strategy
  - strategy/backtest

datasets:
  - name: 平台日K数据
    type: 日K
    dir: /data/market
    file_glob: "*.parquet"

task_timeout_sec: 7200
```

- [ ] **Step 2: 提交**

```bash
git add config/config.docker.yaml
git commit -m "新增docker运行时配置文件"
```

---

### Task 3: 新增 Dockerfile（多阶段构建）

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: 创建 `Dockerfile`**

```dockerfile
# 阶段一：构建前端 Vue3 -> web/dist
FROM node:20-alpine AS fe-build
WORKDIR /fe
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# 阶段二：后端运行环境（单镜像，静态托管 web/dist）
FROM python:3.10-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    APP_CONFIG=/app/config/config.docker.yaml \
    TZ=Asia/Shanghai
WORKDIR /app

# 系统依赖（sqlite3、tzdata）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Python 依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 平台代码 + manifest 扫描目录 + 容器配置
COPY app/ ./app/
COPY config/ ./config/
COPY script/ ./script/
COPY strategy/ ./strategy/

# 前端构建产物
COPY --from=fe-build /fe/dist ./web/dist

# 数据挂载点（compose 从宿主机注入）
RUN mkdir -p /data/market /data/outputs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: 本地验证镜像能构建并启动**

Run: `docker build -t lianghua:test .`
Expected: 构建成功，最终镜像包含 `app/` 与 `web/dist`。

Run（临时跑一次冒烟，数据临时目录）:
```bash
docker run --rm -d --name lh-test -p 8000:8000 -v "$(pwd)/data:/data" lianghua:test
curl http://127.0.0.1:8000/api/health
docker stop lh-test
```
Expected: `/api/health` 返回 `{"status":"ok",...}`。

- [ ] **Step 3: 提交**

```bash
git add Dockerfile
git commit -m "新增多阶段Dockerfile"
```

---

### Task 4: 新增 .dockerignore

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: 创建 `.dockerignore`**

```gitignore
# 构建上下文裁剪：避免把大数据/产物/密钥带入镜像上下文
.git
.gitignore
.idea
node_modules
web/dist
web/node_modules
data
outputs
*.log
__pycache__
*.pyc
*.csv
*.xlsx
*.xls
*.zip
*.md
.github
docs
tests
docker-compose.yml
```

- [ ] **Step 2: 提交**

```bash
git add .dockerignore
git commit -m "新增dockerignore裁剪构建上下文"
```

---

### Task 5: 新增 docker-compose.yml（直接运行）

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: 创建 `docker-compose.yml`**

```yaml
services:
  lianghua:
    image: "${DOCKERHUB_USERNAME}/lianghua:${TAG:-latest}"
    container_name: lianghua
    ports:
      - "${PORT:-8000}:8000"
    environment:
      - APP_CONFIG=/app/config/config.docker.yaml
    volumes:
      # 数据基目录（含 /data/market、/data/meta.db、/data/outputs）
      - "${DATA_DIR:-./data}:/data"
      # 可选：用宿主机配置覆盖镜像默认；不设则用镜像内置 config.docker.yaml
      - "${CONFIG_FILE:-./config/config.docker.yaml}:/app/config/config.docker.yaml:ro"
    restart: unless-stopped
```

- [ ] **Step 2: 校验 compose 语法合法**

Run: `docker compose config`（在装了 docker compose 的环境）
Expected: 无报错，能展示展开后的服务定义。

- [ ] **Step 3: 提交**

```bash
git add docker-compose.yml
git commit -m "新增docker-compose一键运行"
```

---

### Task 6: 新增 GitHub Actions 工作流（构建 + 推送 Docker Hub）

**Files:**
- Create: `.github/workflows/docker-publish.yml`

- [ ] **Step 1: 创建 `.github/workflows/docker-publish.yml`**

```yaml
name: Build & Push Docker Image

on:
  push:
    branches: ["platform"]
    tags: ["v*"]
  workflow_dispatch:

env:
  IMAGE_NAME: ${{ vars.DOCKERHUB_IMAGE || format('{0}/lianghua', secrets.DOCKERHUB_USERNAME) }}
  DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}

jobs:
  build-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Build & Push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: |
            ${{ env.IMAGE_NAME }}:latest
            ${{ env.IMAGE_NAME }}:sha-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: 检查 YAML 合法（本地可先 `python -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))"`）**

Expected: 无异常。

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "新增GitHub Actions构建推送Docker镜像"
```

---

## 端到端验证

1. 本地 `docker build -t lianghua:test .` 成功。
2. `DATA_DIR=E:/资料/证券/lianghua docker compose up -d` 后，浏览器访问 `http://localhost:8000` 能打开前端首页。
3. `GET /api/health` 返回 `{"status":"ok","app":"量化交易平台","version":"0.1.0"}`。
4. 全仓 pytest 通过：`python -m pytest`。
5. GitHub Actions 推 `platform` 后，Actions 日志显示 build + push 成功，Docker Hub 仓库出现 `latest` 与 `sha-*` 镜像。