# ---------- build webui ----------
FROM node:25-alpine AS webui-builder

WORKDIR /webui

COPY webui/package.json webui/package-lock.json ./

RUN npm ci

COPY webui/ ./

RUN npm run build

# ---------- runtime ----------
FROM python:3.13-alpine

LABEL org.opencontainers.image.authors="flyeyas" \
      org.opencontainers.image.source="https://github.com/flyeyas/proxy-pool" \
      org.opencontainers.image.title="proxy-pool" \
      org.opencontainers.image.description="爬虫代理IP池: 定时抓取验证免费代理, 提供RESTful API与WebUI管理后台"

WORKDIR /app

COPY ./requirements.txt .

# timezone and init process
# 注意: tzdata 必须保留(APScheduler 3.11+ 用 zoneinfo 需要系统时区库, 不可 apk del);
# bash: py3.13+ 的 alpine 镜像不再内置, 入口脚本需要
RUN apk add -U tzdata tini bash && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

# runtime environment
RUN apk add musl-dev gcc libxml2-dev libxslt-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev

COPY . .

# webui 前端构建产物
COPY --from=webui-builder /webui/dist /app/webui/dist

EXPOSE 5010

ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
