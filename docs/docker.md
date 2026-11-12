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
  -v proxy_pool_runtime:/app/data \
  flyeyas/proxy-pool:latest
```

常用环境变量：

- `DB_CONN`：数据库连接（覆盖 `setting.py`，也支持 `.env` 文件）
- `WEBUI_USERNAME` / `WEBUI_PASSWORD`：WebUI 登录账号
- `FETCHER_AUTO_DISABLE_FAILS`：代理源连续失败自动禁用阈值
- `RUNTIME_CONFIG_FILE`：运行时配置文件路径，镜像默认 `/app/data/runtime_config.json`。Compose 固定为该路径，与 `runtime_data` 数据卷对应；本地直接运行默认仍是项目根目录的 `runtime_config.json`。

`/app/data` 保存 API Token、代理源启停/代理开关和采集统计。上例使用命名卷 `proxy_pool_runtime`，后续创建容器时挂载同一卷即可继续使用原配置；首次运行无需手动创建 JSON 文件。也可用 `-v /宿主机数据目录:/app/data` 保存到宿主机目录。

## 在宿主机查看日志

镜像将 `/app/log` 声明为卷（`VOLUME`）。运行时用 `-v` 把它挂到宿主机目录，即可直接查看/切割日志文件，无需进入容器：

```console
docker run ... -v /var/log/proxy_pool:/app/log ...

# 宿主机上直接查看
tail -F /var/log/proxy_pool/fetcher.log
ls /var/log/proxy_pool/
```

文件日志按本地时区的每小时整点切割（Docker 默认 `Asia/Shanghai`），按小时边界保留最近七天，不依赖归档数量。不挂载时 Docker 会创建匿名卷，可通过 `docker inspect <容器> | grep -A3 log` 找到宿主机路径，但推荐显式挂载。

- 当前文件：`fetcher.log`、`checker.log`、`scheduler.log` 等，WebUI 默认读取这些文件。
- 小时归档：`fetcher.log.2026111122` 表示 2026-11-11 22 时的日志。跨小时写入时自动归档；启动时与调度器每小时整点也会切割空闲日志并清理过期归档。
- 保留规则：早于当前小时减去 `7 × 24` 小时的归档会删除，日志较少时同样执行；已有小时归档继续参与清理。
- 并发写入：切割、写入与 WebUI 读取使用文件锁，支持 API worker 和调度器共享日志；归档重名时追加 `.1`、`.2`，保留历史内容。
- `*.log.lock` 是小时标记和进程锁文件，不属于日志归档，运行时应保留。

使用 `tail -F` 跟随当前日志路径，可在切割后继续查看新文件。

`docker logs <容器>` 仍可查看标准输出日志，其保存策略由 Docker 日志驱动管理。

## 使用 docker-compose

`docker-compose.yml` 为**自包含部署**：应用 + 内置 Redis（AOF 持久化 + 数据卷，密码取自 `.env` 的 `REDIS_PASSWORD`），运行时配置保存到独立的 `runtime_data` 数据卷，日志挂载到 `./log/`，并设置 `restart: unless-stopped`：

```yaml
services:
  proxy_pool:
    build: .
    container_name: proxy_pool
    ports:
      - "5010:5010"
    env_file:
      - .env
    environment:
      RUNTIME_CONFIG_FILE: /app/data/runtime_config.json
    volumes:
      - ./log:/app/log
      - runtime_data:/app/data
    depends_on:
      - proxy_redis
    restart: unless-stopped

  proxy_redis:
    image: redis:8-alpine
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
  runtime_data:
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
    - API Token、代理源设置和统计在命名卷 `runtime_data` 中持久化，`up --force-recreate` 或普通 `down` 后再 `up` 会继续使用原配置；`down -v` 会删除这两个命名卷。
    - 如需改用外部 Redis：注释 `.env` 中的 `REDIS_PASSWORD`，把 `DB_CONN` 指向外部地址即可（compose 中的 proxy_redis 可删除或保留闲置）

## 迁移运行时配置

旧镜像将配置写在容器内的 `/app/runtime_config.json`。升级前先停止应用并导出旧文件，再将它导入新数据卷，避免重建容器时丢失已有 Token 和代理源设置：

```console
(
set -eu
runtime_backup_dir=$(mktemp -d)
printf '运行时配置备份目录: %s\n' "$runtime_backup_dir"
docker compose stop proxy_pool
docker cp proxy_pool:/app/runtime_config.json "$runtime_backup_dir/runtime_config.json"
docker compose build proxy_pool
docker compose run --rm --no-deps -T --entrypoint sh proxy_pool -c \
  'umask 077; set -C; cat > /app/data/runtime_config.json' \
  < "$runtime_backup_dir/runtime_config.json"
docker compose up -d
)
```

导入命令仅适用于空的新数据卷，目标文件已存在时会报错并保留原文件。若旧容器从未创建过运行时配置，可跳过导出和导入；如果原先将 `/app/runtime_config.json` 绑定到了宿主机文件，也可将该文件作为导入输入。迁移后仍保留备份目录中的原文件。

手动 `docker run` 部署时，也应先导出旧文件，再将其复制为宿主机数据目录下的 `runtime_config.json` 并挂载该目录到 `/app/data`。已有部署如果继续将单个文件挂载到 `/app/runtime_config.json`，需同时设置 `--env RUNTIME_CONFIG_FILE=/app/runtime_config.json`。

## 容器环境注意事项

在 Docker 容器中，建议使用前台模式启动服务：

```console
./proxy_pool.sh start --fg
```

Dockerfile 中的 ENTRYPOINT 配置：

```dockerfile
ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
```
