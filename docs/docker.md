# Docker 部署

## 使用 Docker 镜像

构建并运行自己的镜像：

```console
docker build -t flyeyas/proxy-pool:latest .

docker run -d --name proxy_pool \
  --env DB_CONN=redis://:password@ip:port/0 \
  --env WEBUI_PASSWORD=change-me \
  -p 5010:5010 \
  -v /宿主机日志路径/log:/app/log \
  flyeyas/proxy-pool:latest
```

常用环境变量：

- `DB_CONN`：数据库连接（覆盖 `setting.py`，也支持 `.env` 文件）
- `WEBUI_USERNAME` / `WEBUI_PASSWORD`：WebUI 登录账号
- `FETCHER_AUTO_DISABLE_FAILS`：代理源连续失败自动禁用阈值

## 在宿主机查看日志

镜像将 `/app/log` 声明为卷（`VOLUME`）。运行时用 `-v` 把它挂到宿主机目录，即可直接查看/切割日志文件，无需进入容器：

```console
docker run ... -v /var/log/proxy_pool:/app/log ...

# 宿主机上直接查看
tail -f /var/log/proxy_pool/fetcher.log
ls /var/log/proxy_pool/
```

日志按小时切割（保留 7 天）。不挂载时 Docker 会创建匿名卷，可通过 `docker inspect <容器> | grep -A3 log` 找到宿主机路径，但推荐显式挂载。

`docker logs <容器>` 仍可查看标准输出日志（与文件内容一致）。

## 使用 docker-compose

`docker-compose.yml` 只定义应用本身，**配置统一从项目根目录 `.env` 读取**（`env_file`，与本地运行共用一份配置），日志挂载到 `./log/`，并设置了 `restart: unless-stopped` 开机自启/崩溃拉起：

```yaml
services:
  proxy_pool:
    build: .
    container_name: proxy_pool
    ports:
      - "5010:5010"
    env_file:
      - .env
    volumes:
      - ./log:/app/log
    restart: unless-stopped
```

部署步骤（连接你自己的 Redis，无需在 compose 里定义数据库）：

```console
cp .env.example .env
#   编辑 .env: DB_CONN=redis://:密码@主机:端口/0, WEBUI_PASSWORD=...
docker compose up -d --build
```

!!! note
    - `.env`/`.env.local`/`runtime_config.json` 已被 `.dockerignore` 排除，**不会打进镜像**；配置通过 `env_file` 在运行时注入
    - 如需在服务器本地跑一个 Redis 而不是外部 Redis：`docker run -d --name proxy_redis redis` 后把 `.env` 的 `DB_CONN` 指向它（`redis://@宿主机IP:6379/0`）即可，compose 无需改动

## 容器环境注意事项

在 Docker 容器中，建议使用前台模式启动服务：

```console
./proxy_pool.sh start --fg
```

Dockerfile 中的 ENTRYPOINT 配置：

```dockerfile
ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
```
