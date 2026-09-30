# 阶段一：构建前端 Vue3 -> web/dist
FROM node:20-alpine AS fe-build
WORKDIR /fe
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# 阶段二：后端运行环境（单镜像，静态托管 web/dist）
# 注：akshare==1.18.97 要求 Python>=3.11，故用 3.11-slim（满足项目 Python 3.10+ 约束）
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    APP_CONFIG=/app/config/config.docker.yaml \
    TZ=Asia/Shanghai
WORKDIR /app

# 系统依赖（tzdata + 编译工具链，供个别包源码构建 wheel）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata gcc g++ make \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Python 依赖：先升级 pip/setuptools/wheel，确保 pyarrow 等大包能命中预编译 wheel（旧 pip 会退回源码编译而失败）
COPY requirements.txt ./
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

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