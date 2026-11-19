# 代理检测 V2

V2 将代理的连接协议与“能否访问 HTTPS 目标”分开。默认仍为 legacy，升级镜像不会自行切换生产结果。建议先使用 WebUI“代理检测”页面启用 shadow，对照新结果，再通过迁移门槛切换 active。

## 地区与目标

| 分组 | 规则 | 默认主目标 |
| --- | --- | --- |
| 国内 | CN | https://www.qq.com |
| 国外及其他网络区域 | 非 CN；HK/MO/TW 保留地区码，默认使用此网络策略 | https://www.google.com |
| unknown | 无可信地区资料 | 分别检测两站，只暴露已通过的目标能力 |

主机 IP 地区、采集源提示和已验证出口地区分别保存。查询失败不删除代理，不根据 Google 失败、QQ 成功来猜测国家。公网代理主目标检测成功后，可经同一代理低频查询出口 IP 与国家；默认最多每 24 小时尝试一次，不影响已取得的主目标事实。验证出口改变了分组时，需要通过新的主目标才能按新分组分发。

## 协议与通过条件

连接 variant 支持 http、https（TLS 代理）、socks4、socks4a、socks5、socks5h。后两种带 a/h 的模式由代理解析目标域名；SOCKS4 只使用 userid，不声称支持密码认证。来源中的 https 只作为能力提示，明确的 https URL 才指定 TLS 代理连接。

通过条件为 GET 经候选代理访问主目标、两层适用的 TLS 证书与域名验证成功、最多 3 次同白名单 HTTPS 跳转、最终 200 且有限正文符合站点证据。验证码、403/429、同意页面、证据不足、认证失败各自记录。验证不继承环境代理，不回退直连；采集请求原来的直连回退是另一套行为。

HTTP-only 可选探测由 policy.http_probe_url 显式开启，默认关闭。它可以保留可用的明文转发能力，但不会替代本次 HTTPS 主目标标准，也不会混入默认获取接口。

## 默认参数

| 参数 | 默认值 |
| --- | --- |
| connect_timeout / read_timeout | 3 / 5 秒 |
| round_deadline / shutdown_grace | 30 / 2 秒 |
| workers | 4，允许 1—20，按资源实测调整 |
| target_rps | 每目标域名全局 2 请求/秒，无突发额度 |
| retries | 暂态网络错误最多额外 1 次 |
| response_limit | 解码后 32768 字节，同时限制压缩输入 |
| check_interval / fresh_seconds | 120 / 600 秒 |
| max_hard_failures | 3 个独立复检轮次；成功清零 |
| unavailable_interval | 已确认失败的协议默认冷却 900 秒；有其他成功协议时仍按其正常频率复检 |
| candidate_ttl / max_candidates | 24 小时 / 5000 |
| target_ca / proxy_ca | 默认系统 CA，可显式配置挂载的 CA bundle |
| exit_geo_url | https://api.ip.sb/geoip；空字符串关闭补查 |

读取超时不是总耗时。worker 使用独立 spawn 进程，DNS 卡住、TLS 卡住及慢流超过整轮截止后会被回收。排队耗时另计，不写成代理响应时间。两分钟是目标调度间隔，不承诺任意规模都能两分钟扫完全池；界面显示实际积压。

## 配置与权限

配置保存于既有 RUNTIME_CONFIG_FILE 的 validation 命名空间，不覆盖 Token、采集开关等其他字段。例如初始影子配置：

    {"validation": {"mode": "shadow", "policy": {"workers": 4, "target_rps": 2}}}

新候选与复检任务使用独立分区；两者都有到期任务时按 20%/80% 领取，任一分区空闲时可借用全部容量。相同来源重复报告既有地址只合并信息，不把正常复检或失败冷却时间反复提前。地区提示冲突保留全部提示并归 unknown，较强的 IP/出口证据不会被较弱提示覆盖。

403/429 等受限结果按目标分别进行 2/5/15 分钟退避，遵守更长的 Retry-After；一个目标受限不会停止其他已通过用途的复检。参考代理过期或丢失时回到 unknown 并重新建立证据，避免永久熔断；半开探测会选择不同的未恢复参考代理。

支持 VALIDATION_MODE、VALIDATION_WORKERS、VALIDATION_TARGET_RPS、VALIDATION_ROUND_DEADLINE 环境覆盖；环境锁定项在管理接口中只读。不要用 VALIDATION_MODE 锁住模式后再要求界面执行生产切换，切换任务会明确拒绝这种组合。

文件为 desired，Redis 控制记录为 effective。任务携带策略版本、generation 和 fence，旧任务不能覆盖新规则、删除或模式切换后的结果。修改策略后等待生效版本更新，再观察分批复检。

所有 /api/v2/ 接口受现有会话/Token 鉴权保护；协调器心跳、队列和目标健康的只读状态允许有效 Token 读取。候选诊断列表、shadow 代理明细与连接结果、配置和迁移任务只允许管理员会话。若现有系统主动关闭了登录验证，保持原有开放部署语义。不要把未启用认证的实例直接暴露到公网。

## API

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| /api/v2/proxies/ | GET | 默认新鲜 available 结果；诊断状态需管理员 |
| /api/v2/proxies/random/ | GET | 返回完整 proxy_url、variant、profile、国家、检测和过期时间 |
| /api/v2/proxies/pop/ | POST | 原子消费整个 endpoint；并发不得重复取出 |
| /api/v2/proxies/id/ | DELETE | 按 endpoint_id 删除全部协议并作废在途提交 |
| /api/v2/validation/status/ | GET | 协调器心跳、队列、目标健康和版本 |
| /api/v2/validation/config/ | GET/PATCH | 管理员读取/修改配置 |
| /api/v2/validation/jobs/ | POST | 管理员提交 import/recheck/activate/rollback；202 返回 job_id |
| /api/v2/validation/jobs/id/ | GET | 查询异步任务进度与错误类型 |

连接筛选支持 protocol、dns_mode、profile、region、max_latency_ms。列表另支持 status、page_size、cursor，以及兼容的 page；page_size 默认 50、最多 200。分页游标基于候选目录，条件在有限目录页内筛选，因此可能返回空页但 next_cursor 非空，调用方应继续翻页，不能把空页当作整个结果集为空。catalog_total 是候选目录数量，不是假称筛选后总数。

recheck 任务可附带 endpoint_ids（最多 200 个，自动去重）和 profiles（domestic/overseas）。在途任务会合并后续目标需求；参考探测或显式目标检查先执行，仍尽力完成该地区主目标。shadow 数据仅供管理员会话对照；普通 Token 在 active 后才可获取新结果。

protocol=https 表示 TLS 连接代理，和旧接口的 type=https 不同。旧 /get/、/pop/、/all/ 在 active 下只提供通过新规则的 HTTP 连接代理，以保持旧调用方拼接 http 的兼容性；SOCKS/TLS 连接使用 V2 的完整 URL。随机获取接口在有效授权下可返回必要认证信息，列表、日志、详情不返回密码。

## 影子运行与切换

1. 保持 Redis 8 数据卷和 Token 配置，备份现有部署及镜像 digest。
2. 页面启用 shadow；一次采集的数据进入新旧引擎各自的处理路径。旧引擎的失败不会删除 V2 检测证据。
3. 导入历史代理，观察目标健康、真实失败类型、队列和资源；全局请求额度包括人工任务、重试、跳转与 canary。
4. V2 目录和实际旧池各自覆盖率至少 95%、符合旧调用方式的可用 HTTP 代理达到业务门槛（默认 20），再提交 activate。旧池使用独立分母，无效旧记录计为未覆盖；候选容量不足不能静默跳过。切换前导入实际旧池，并在旧 writer 排空后再次核验冻结池，不能以其他新代理的成功记录稀释旧池未测记录。
5. 排空旧 writer，冻结回滚 Hash，再原子切换生产投影和 mode_epoch。不存在两个引擎同时写生产 Hash 的阶段。
6. 回滚使用 rollback 任务或运维手册先撤销 V2 写入，再恢复旧镜像/备份。只恢复 validation 配置，不覆盖期间新增 Token 和其他用户变更。

首次部署建议在独立灰度入口按 10%（最多 50 条）、50%、全量扩大 shadow，满足门槛后一次性切换。未经实际验证的记录不会因导入而获得通过状态。切换失败应查看任务错误，不通过降低证书验证、放宽地区主目标或强行修改 Redis 模式来绕过门槛。

导入任务的 imported 只统计已接受或合并的候选；rejected 表示记录无法规范化，capacity_rejected 表示新端点因容量上限未被接受。后者大于零时，先处理容量再请求生产切换。

V2 第一版要求 Redis，不支持将 Redis 原子队列静默替换成 SSDB。SSDB 用户可以继续 legacy。

## 诊断与测试

validation 日志按小时切割，沿用 7 天保留策略。通过 round_id、endpoint_id、variant、profile、stage、result_code 定位；密码、Token、认证 URL 及完整页面内容不记录。target_restricted、tls_invalid、environment_error 不能简单解释为代理端口失效。

集成测试设置 V2_TEST_REDIS_URL 后使用临时命名空间验证真实 Redis；不设置时会明确跳过这组用例。CI 已提供 Redis 8，并把测试作为镜像发布前置门禁。受控协议测试使用测试 CA 和本地服务，不依赖公网免费代理。

scripts/validation_soak.py 是 Linux 容器内资源压力工具，使用唯一命名空间和本地目标，每档默认 500 个候选，覆盖 1/4/8/20 worker 及快响应、拒绝连接、慢流。它会明确记录测试限流为 20 请求/秒、关闭目标熔断以施加最坏传输压力；这不是生产配置。每档实际完成轮次单列，不把入队 500 条说成全部通过。
