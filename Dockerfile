# ---------- build webui ----------
FROM node:20-alpine AS webui-builder

WORKDIR /webui

COPY webui/package.json webui/package-lock.json ./

RUN npm ci

COPY webui/ ./

RUN npm run build

# ---------- runtime ----------
FROM python:3.10-alpine

LABEL maintainer="jhao104 <j_hao104@163.com>"

WORKDIR /app

COPY ./requirements.txt .

# timezone and init process
RUN apk add -U tzdata tini && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    apk del tzdata

# runtime environment
RUN apk add musl-dev gcc libxml2-dev libxslt-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev

COPY . .

# webui 前端构建产物
COPY --from=webui-builder /webui/dist /app/webui/dist

EXPOSE 5010

ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
