# 使用Python 3.11作为基础镜像
FROM python:3.11-slim-bookworm

# 设置标签信息
LABEL maintainer="zhinianboke"
LABEL version="2.2.0"
LABEL description="闲鱼自动回复系统 - 企业级多用户版本，支持自动发货和免拼发货"
LABEL repository="https://github.com/zhinianboke/xianyu-auto-reply"
LABEL license="仅供学习使用，禁止商业用途"
LABEL author="zhinianboke"
LABEL build-date=""
LABEL vcs-ref=""

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Shanghai
ENV DOCKER_ENV=true
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Nuitka编译优化
ENV CC=gcc
ENV CXX=g++
ENV NUITKA_CACHE_DIR=/tmp/nuitka-cache
ENV NODE_PATH=/usr/lib/node_modules

# --------------------------
# 分段安装系统依赖，带重试和超时
# --------------------------

# 1️⃣ 基础工具
RUN apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends --fix-missing \
       tzdata curl ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 2️⃣ Node.js / npm
RUN apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends --fix-missing nodejs npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 3️⃣ 编译工具
RUN apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends --fix-missing \
       build-essential gcc g++ ccache patchelf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 4️⃣ 图像处理依赖
RUN apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends --fix-missing \
       libjpeg-dev libpng-dev libfreetype6-dev fonts-dejavu-core fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 5️⃣ Playwright浏览器依赖 & OpenCV
RUN apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends --fix-missing \
       libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 \
       libxdamage1 libxrandr2 libgbm1 libxss1 libasound2 libatspi2.0-0 \
       libgtk-3-0 libgdk-pixbuf2.0-0 libxcursor1 libxi6 libxrender1 libxext6 \
       libx11-6 libxft2 libxinerama1 libxtst6 libappindicator3-1 libx11-xcb1 \
       libxfixes3 xdg-utils chromium libgl1 libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# --------------------------
# 设置时区
# --------------------------
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# --------------------------
# 验证 Node.js
# --------------------------
RUN node --version && npm --version

# --------------------------
# 安装 Python 依赖（带重试和超时）
# --------------------------
COPY requirements.txt . 
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt --timeout 60 --retries 5

# --------------------------
# 复制项目文件
# --------------------------
COPY . .

# --------------------------
# 条件执行 Nuitka 编译
# --------------------------
RUN if [ -f "utils/xianyu_slider_stealth.py" ]; then \
        echo "===================================="; \
        echo "检测到 xianyu_slider_stealth.py"; \
        echo "开始编译为二进制模块..."; \
        echo "===================================="; \
        pip install --no-cache-dir nuitka ordered-set zstandard --timeout 60 --retries 5 || true; \
        python build_binary_module.py; \
        BUILD_RESULT=$?; \
        if [ $BUILD_RESULT -eq 0 ]; then \
            echo "===================================="; \
            echo "✓ 二进制模块编译成功"; \
            echo "===================================="; \
            ls -lh utils/xianyu_slider_stealth.* 2>/dev/null || true; \
        else \
            echo "===================================="; \
            echo "✗ 二进制模块编译失败 (错误码: $BUILD_RESULT)"; \
            echo "将继续使用 Python 源代码版本"; \
            echo "===================================="; \
        fi; \
        rm -rf /tmp/nuitka-cache utils/xianyu_slider_stealth.build utils/xianyu_slider_stealth.dist; \
    else \
        echo "===================================="; \
        echo "未检测到 xianyu_slider_stealth.py"; \
        echo "跳过二进制编译"; \
        echo "===================================="; \
    fi

# --------------------------
# 安装 Playwright 浏览器（带重试）
# --------------------------
RUN playwright install chromium || playwright install chromium --force
RUN playwright install-deps chromium || true

# --------------------------
# 创建目录并设置权限
# --------------------------
RUN mkdir -p /app/logs /app/data /app/backups /app/static/uploads/images \
    && chmod 777 /app/logs /app/data /app/backups /app/static/uploads /app/static/uploads/images

# --------------------------
# 配置系统限制，防止core文件生成
# --------------------------
RUN echo "ulimit -c 0" >> /etc/profile

# --------------------------
# 暴露端口 & 健康检查
# --------------------------
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# --------------------------
# 启动脚本
# --------------------------
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
