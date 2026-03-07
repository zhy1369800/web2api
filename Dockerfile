# 使用 Python 3.12 基础镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统依赖（Chromium 运行环境、Xvfb、字体）
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    xvfb \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    fonts-liberation \
    fonts-roboto \
    fonts-noto-cjk \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv (依赖管理工具)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目文件
COPY . .

# 使用 uv 安装依赖
RUN uv sync --frozen

# 导出端口
EXPOSE 9000

# 复制并设置执行权限
RUN chmod +x docker-entrypoint.sh

# 启动入口
ENTRYPOINT ["./docker-entrypoint.sh"]
