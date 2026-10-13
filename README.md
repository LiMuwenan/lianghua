# 简介
证券数据分析

## 启动顺序

1. 安装依赖
```bash
pip install -r requirements.txt
pnpm install
```
2. 后端服务启动
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

3. 前端启动服务
```bash
npm run dev 
```


## Docker 启动指南

镜像为单镜像（前端构建产物 + FastAPI 静态托管合一），**无状态**：数据、配置、输出全部通过卷挂载注入容器。后端启动命令为 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，容器内固定暴露 `8000`。

关键约定：

- **`APP_CONFIG` 是容器内的配置文件路径，不是宿主机路径。** 宿主机路径只能出现在 `-v` 的冒号左侧。
- 数据基目录统一挂到容器 `/data`，其下含 `/data/market`（Parquet）、`/data/meta.db`（SQLite）、`/data/outputs`（结果/日志）。
- 镜像内置 `config/config.docker.yaml`（容器 `/data` 布局），并已设 `APP_CONFIG=/app/config/config.docker.yaml`，不额外挂配置即可直接跑。

### 方式一：docker-compose（推荐）

`docker-compose.yml` 已就绪，指定数据目录与端口后一条命令启动：

```bash
DATA_DIR=/volume5/docker/program/prod/lianghua/data PORT=8000 docker compose up -d
```

可选环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `./data` | 宿主机数据目录，挂到容器 `/data` |
| `PORT` | `8000` | 宿主机映射端口（容器内固定 8000） |
| `CONFIG_FILE` | `./config/config.docker.yaml` | 宿主机配置文件，挂到 `/app/config/config.docker.yaml` |
| `TAG` | `latest` | 镜像 tag |

如需用自己的配置覆盖镜像内置配置，设置 `CONFIG_FILE` 指向宿主机配置文件即可（会以只读方式挂到 `/app/config/config.docker.yaml`）。**注意**：该文件内的路径必须使用容器内路径（`/data/...`），不要写 `F:\`、`G:\` 等 Windows 路径——容器是 Linux，Windows 绝对路径会被当成相对路径解析，导致数据目录错位。

### 方式二：单容器命令行

不依赖 compose，直接 `docker run`（镜像名以实际推送的 `DOCKERHUB_IMAGE:latest` 为准）：

```bash
docker run -d --name lianghua \
  --restart unless-stopped \
  -p 8000:8000 \
  -e APP_CONFIG=/app/config/config.docker.yaml \
  -v /volume5/docker/program/prod/lianghua/data:/data \
  bearli/lianghua:latest
```

如需挂载自定义配置（务必让 `APP_CONFIG` 指向**容器内**的挂载目标路径）：

```bash
docker run -d --name lianghua \
  --restart unless-stopped \
  -p 8000:8000 \
  -e APP_CONFIG=/app/config/config.yaml \
  -v /volume5/docker/program/prod/lianghua/data:/data \
  -v /volume5/docker/program/prod/lianghua/config/config.yaml:/app/config/config.yaml:ro \
  bearli/lianghua:latest
```

启动后浏览器访问 `http://localhost:8000`，健康检查接口为 `/api/health`。

排查提示：若日志报 `FileNotFoundError: ... 'xxx/config.yaml'`，说明 `APP_CONFIG` 指向的容器内路径不存在——排查 `-v` 是否把宿主机文件挂到了该容器路径，不要把宿主机路径直接填进 `APP_CONFIG`。

## 使用说明