# 知源后端镜像。
# 构建上下文是**仓库根**（不是 backend/）：scripts/seed.py 用
# Path(__file__).parents[2] / "prototype" 找种子数据，所以镜像里必须保住
# /app/backend 与 /app/prototype 的相对关系。
#   docker build -f backend/Dockerfile .
FROM python:3.11-slim

# 国内服务器可传镜像源：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 默认值就是官方源 —— ARG 在 RUN 里本来就是环境变量，pip 会自己认 PIP_INDEX_URL，
# 所以这里不需要在 RUN 里写 shell 条件判断（留空反而会让 pip 拿到空索引地址）。
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# cwd 必须是 backend/：config.py 的 Settings(env_file=".env") 按进程 cwd 解析，
# alembic.ini 与 scripts/ 也都在这一层（见 CLAUDE.md）。
WORKDIR /app/backend

# 先只拷 pyproject 装依赖，改业务代码不触发重装。
# setuptools 的 packages.find 要求 app/ 存在，先放占位包，后面 COPY 覆盖成真代码。
# 装 [dev] 而不是 base：alembic 在 dev 可选依赖里，而 entrypoint 要用它建表。
COPY backend/pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && pip install --no-cache-dir -e ".[dev]"

COPY backend/ ./
COPY prototype/ /app/prototype/

# entrypoint 去掉可能的 CRLF（Windows 上构建时的保险，另见 .gitattributes）
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh \
 && useradd -m -u 1000 app \
 && chown -R app:app /app

USER app
# 预热 jieba 词典缓存（省掉首个搜索请求的 ~1s）。必须在 USER app 之后：
# root 写的 /tmp/jieba.cache 是 0600，app 用户读不了，等于白热。
RUN python -c "import jieba; jieba.initialize()"

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
