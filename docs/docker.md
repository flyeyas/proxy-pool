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

`docker-compose.yml` 为**自包含部署**：应用 + 内置 Redis（AOF 持久化 + 数据卷，密码取自 `.env` 的 `REDIS_PASSWORD`），配置统一从 `.env` 读取，日志挂载到 `./log/`，并设置 `restart: unless-stopped`：

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
    depends_on:
      - proxy_redis
    restart: unless-stopped

  proxy_redis:
    image: redis:alpine
    container_name: proxy_redis
    command: ["redis-server", "--appendonly", "yes",
              "--requirepass", "${REDIS_PASSWORD:-proxypool}"]
    volumes:
      - redis_data:/data
    # 仅发布到宿主机回环: 本机裸跑程序可连 127.0.0.1:26739, 不暴露公网
    ports:
      - "127.0.0.1:26739:6379"
    restart: unless-stopped

volumes:
  redis_data:
```

部署步骤：

```console
cp .env.example .env
#   编辑 .env: REDIS_PASSWORD 与 DB_CONN 密码保持一致, 以及 WEBUI_PASSWORD
docker compose up -d --build
```

!!! note
    - `DB_CONN` 使用服务名 `proxy_redis:6379`（compose 内网直连，不经过公网）
    - Redis 端口只发布到宿主机 `127.0.0.1:26739`：宿主机上裸跑的程序（`python proxyPool.py`）可通过 `DB_CONN=redis://:密码@127.0.0.1:26739/0` 连同一份数据，且不暴露公网
    - Redis 数据在命名卷 `redis_data` 中持久化，重建容器不丢失
    - 如需改用外部 Redis：注释 `.env` 中的 `REDIS_PASSWORD`，把 `DB_CONN` 指向外部地址即可（compose 中的 proxy_redis 可删除或保留闲置）

## 容器环境注意事项

在 Docker 容器中，建议使用前台模式启动服务：

```console
./proxy_pool.sh start --fg
```

Dockerfile 中的 ENTRYPOINT 配置：

```dockerfile
ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
```
