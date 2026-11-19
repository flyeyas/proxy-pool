# 代理检测 V2：详细开发方案

编制日期：2026-11-19（Asia/Shanghai）

状态：已进入实现与验收；实际完成状态见配套开发计划和开发记录，不代表功能已经上线。

代码基线：v1.5.1，提交 6793a3cf9c544c345bcee996196d47a7a8028051。

建议发布版本：v1.6.0，实际发版时重新核对仓库版本。

配套计划：[开发计划](proxy-validation-v2-development-plan.md)。

## 1. 目标与交付边界

将现有“先 HTTP HEAD，后 HTTPS HEAD”的检测升级为“地区策略选择目标、代理协议独立探测、能力分开保存、结果按用途筛选”。

本次完整交付包括：HTTP CONNECT、真正的 TLS 代理、SOCKS4/4a、SOCKS5/5h；地区识别；QQ/Google 主目标验证；结构化日志；失败隔离和目标健康保护；持久队列；API/WebUI；历史数据兼容；本地 Docker 验收；GitHub 镜像发布与服务器灰度部署。

第一版以 Redis 8 为 V2 存储后端。现有 SSDB 保留 legacy 模式，不假称支持 Redis 原子脚本、租约和 V2 队列；配置为 SSDB 且开启 V2 时启动检查明确报不支持，不静默退化为不安全实现。完整 SSDB V2 适配列为后续事项。

不在本轮加入付费代理采购、浏览器执行、验证码绕过、账号登录检测、匿名等级认证、自动破解代理认证。公网免费代理通过率不作为固定承诺。

## 2. 已确认现状与必须解决的问题

以下来自本地基线代码检查，未在编写本文时重新登录服务器。

| 位置 | 当前行为 | V2 改动 |
| --- | --- | --- |
| helper/validator.py | HEAD；只接受 200；HTTPS 代理值固定为 https；异常返回 False | 独立传输适配器、GET、正确协议、明确原因 |
| helper/check.py | HTTP 成功才测 HTTPS，之后才查地区；20 个线程 | 地区策略前置；协议独立；统一协调器 |
| helper/proxy.py | 单个 https 布尔值，主要标识为地址字符串 | 版本化记录及协议、目标、DNS 维度 |
| fetcher/baseFetcher.py、helper/fetch.py | 抓取器主要返回地址字符串，丢失协议和国家信息 | 兼容字符串并新增结构化候选 |
| fetcher/sources/geonode.py | 接受 HTTP/HTTPS，过滤 SOCKS | 保留原始协议提示与地区，放行已支持协议 |
| helper/fetcherProxy.py | HTTPS 目标已经使用 HTTP CONNECT；连接方案固定为 http | 共用连接 URL 构造器，选择实测可用协议 |
| helper/scheduler.py、api/webuiApi.py | 定时和手动路径都可直接运行检测；任务间可能叠加 | 只提交任务，共用队列、限流、租约 |
| db/dbClient.py、db/redisClient.py | 可变表名、地址 Hash、读取后再删除 | 独立 V2 Repository；原子领取、提交、pop/delete |
| api/webuiApi.py | 鉴权依赖受保护路径前缀 | V2 新路径显式纳入鉴权，配置写操作仅会话管理员 |
| WebUI Dashboard | 将 HTTP/HTTPS 显示为互斥协议 | 分开显示连接协议、HTTPS 能力和目标可达性 |

当前配置与代码默认值不同的情况必须在实施时读取实际生效值；不以文档中的历史测试数量、镜像 digest、运行容器状态替代重新验证。

## 3. 地区和检测目标

### 3.1 目标策略

| profile | 默认地区 | 强制主目标 | 准入条件 |
| --- | --- | --- | --- |
| domestic | CN，中国大陆 | https://www.qq.com | 对应协议通过 QQ HTTPS 检测 |
| overseas | 非 CN；HK/MO/TW 保留独立地区码，默认归此网络策略 | https://www.google.com | 对应协议通过 Google HTTPS 检测 |
| unknown | 无法确认或信息冲突 | 分别探测 QQ、Google | 至少一项通过；保留 unknown，只暴露已通过 profile |

境外代理只通过 QQ、不通过 Google，不满足 overseas 默认分发条件。国内代理访问 Google 的能力可以作为补充记录，但不替代 QQ 主目标。地区到 profile 的映射允许配置，不修改原始国家/地区码。

### 3.2 地区来源和优先级

优先级：已验证出口 IP 的地区缓存 > 代理主机 IP 归属查询结果 > 采集源国家提示 > unknown。同时保留 endpoint_country、exit_country、country_source、country_confidence、country_checked_at。

第一版复用现有 IP 地区服务的适配逻辑：查询结果成功缓存 7 天，失败缓存 10 分钟，请求超时参数 2 秒；地区队列独立限流为每秒 1 次，不占用主检测 worker。地区查询同样使用可回收的独立执行单元，5 秒整任务截止，避免阻塞 DNS 绕过请求超时。没有缓存时先排地区查询；候选不等待查询完成，可以按 unknown 处理。外部服务不可用时不阻断候选处理。

出口信息只在目标检测成功之后低频补查，建议最长每 24 小时一次；取得出口 IP 不等于已确认出口国家，二者必须分别记录。查询失败不撤销已证实的连通能力。第一版不强制下载需要账户或许可密钥的离线数据库，后续通过 provider 接口扩展。

主机地区只是出口地区的近似。发生冲突时保存两份事实，优先采用较新的已验证出口信息，重新计算所需主目标并安排检测；不能根据 QQ/Google 可达性猜测国家。

## 4. 协议、地址和认证模型

### 4.1 协议矩阵

| variant | 连接代理方式 | 访问 HTTPS 目标 | DNS/认证要点 |
| --- | --- | --- | --- |
| http | 明文 HTTP 代理 | CONNECT 后与目标 TLS 握手 | 目标域名由代理处理；可选 Basic 认证 |
| https | 与代理 TLS 连接 | TLS 内 CONNECT，再与目标 TLS 握手 | 代理、目标两层证书分别验证 |
| socks4 | SOCKS4 | 隧道内目标 TLS | 本地解析；IPv4；可选 userid，不伪称支持密码认证 |
| socks4a | SOCKS4a | 隧道内目标 TLS | 代理端解析域名；可选 userid |
| socks5 | SOCKS5 | 隧道内目标 TLS | 本地解析；支持 IPv4/IPv6 与用户名密码 |
| socks5h | SOCKS5 | 隧道内目标 TLS | 代理端解析；默认优先的 SOCKS5 模式 |

API 的 protocol 是代理连接家族 http/https/socks4/socks5；variant 区分 DNS 模式。supports_https 表示能访问 HTTPS 目标，绝不表示必须使用 TLS 连接代理。技术依据见附录参考资料。

### 4.2 候选标准化

兼容旧抓取器返回字符串；结构化候选新增 host、port、declared_protocols、country_hint、source、credential_ref。标准化入口兼容 IPv4、带方括号 IPv6、明确提供的域名、合法 URL 编码认证信息；端口限 1—65535，禁止仅凭宽松正则接受非法 IP。SOCKS4 对不支持的地址族返回 unsupported，不算网络失效。

同一 host、port、credential_ref 形成 endpoint_id；每种协议和 DNS 模式单独保存结果。认证不同的代理不相互覆盖，凭证不得进入日志和可公开枚举的标识。凭证引用使用持久化随机密钥计算的稳定 HMAC，不以低熵密码的裸哈希充当保密措施。

来源出现 https 只作为来源声明保存。没有明确的代理 URL scheme 或实测 TLS 握手，不把它直接当成 TLS 代理。

未知协议按 http、socks5h、socks4a 顺序探索，每轮最多引入两个未验证 variant；只有来源明确声明 TLS 或配置允许时探测 https。已知正确协议优先。socks5/socks4 本地 DNS 模式仅在来源明确指定或远端 DNS 模式协商不兼容时追加，不重复穷举。预算耗尽的剩余项目留待下轮，不判失败。

去重合并来源、协议提示和地区证据，不能只在当次抓取内合并；重复来源不能覆盖已实测结果或重置失败计数。

## 5. 检测引擎与完整执行流程

### 5.1 模块和依赖决策

新增 helper/validation 包。验证传输层使用 urllib3 的 ProxyManager 与 SOCKSProxyManager，新增并锁定兼容的 PySocks；urllib3 从间接依赖提升为显式依赖。具体版本在实施时结合现有 requests 约束、Python 3.10—3.13 矩阵和镜像构建验证后确定，不在计划中臆定最新版本。

现有采集请求仍使用 Requests；共用代理连接描述和 URL 构造器，不顺带重写所有采集源。不在新检测引擎复用 util/webRequest.py：该工具存在重试耗尽时构造空 200 响应的兼容行为，不能作为真实检测成功依据。

### 5.2 顺序

候选标准化 → 合并来源证据 → 查地区缓存/安排地区查询 → 生成协议和主目标任务 → 等待限流额度 → 领取带 fencing token 的任务 → worker 探测 → 分类结果 → 原子更新状态、索引、兼容投影 → 安排复检。

所有代理协议独立，不再先要求明文 HTTP 网站通过。HTTP-only 附加能力默认关闭主动探测；若开启，使用单独配置的 HTTP 目标与额度。仅 HTTP 成功的记录标为 http_only，可显式查询，但不进入默认主目标可用集合。

### 5.3 单次请求成功标准

1. 请求必须经过指定候选代理；不继承 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY，不回退直连，不复用采集上下文的其他代理。
2. 默认 GET 流式读取；不执行 JavaScript、不请求图片等资源、不携带业务登录 Cookie。响应必须来自真实传输结果。
3. 强制验证目标证书和域名；TLS 代理额外验证代理证书。分别提供显式 CA 配置，不能用关闭证书验证的结果判通过。
4. 禁止自动重定向，由引擎逐跳处理，最多 3 次；每一跳重新申请目标站点额度。只能 HTTPS，域名必须精确命中 profile 的白名单，不能用字符串包含或任意后缀判定。初始白名单为 qq.com/www.qq.com 和 google.com/www.google.com；其他正常跳转须经有证据的配置更新。
5. 最终状态码必须为 200。403、429、认证页、验证码、同意页面、Google sorry 路径等归 target_restricted；有 Retry-After 时在预算内遵守，否则延后任务，不阻塞 worker 睡长时间。
6. 正文最多读取解码后 32 KiB。默认请求 identity 编码；异常压缩响应同时受压缩输入、解码输出和整轮时间上限约束。超过上限前未取得足够证据则 content_inconclusive，不下载完整首页来强行判定。
7. 先匹配拦截/错误页面负面特征，再检查站点的宽松正面特征。特征规则版本化；仅标题或关键词变化造成的证据不足不算硬连接失败。TLS 证书验证、主机名和受控跳转仍是主要真实性证据，内容规则不声称完全识别所有拦截页。
8. 每次请求、重试、重定向结束均关闭响应。连接池按任务隔离或有严格的 endpoint/认证/CA 键，避免把上一代理的连接复用于下一代理。

### 5.4 超时与真正停止执行

| 参数 | 默认建议 | 语义 |
| --- | --- | --- |
| connect_timeout | 3 秒 | 每阶段连接上限，还受剩余预算限制 |
| read_timeout | 5 秒 | 读取等待上限，不等于总耗时 |
| round_deadline | 30 秒 | 单 endpoint 本轮执行预算，包含实际 DNS、握手、读取、重试和跳转 |
| shutdown_grace | 2 秒 | 超时后回收对应 worker 的最长宽限 |
| transient_retries | 1 | 临时网络错误最多额外一次，总尝试数最多两次 |
| response_limit | 32 KiB | 解码后正文上限 |

排队和等待全局额度时间单独记录，不伪装成代理响应耗时。每个已派发请求必须先获得额度；若预算内还需后续请求而额度不足，返回 deferred，释放 worker 后续再调度。

阻塞 DNS 和缓慢持续回包可能超过普通 socket timeout；Future 等待超时、cancel 或线程 join 超时也不能终止已运行的网络调用。因此采用受协调器监督的独立 worker 进程，每个 worker 同时只处理一个 endpoint 轮次，使用显式 spawn 上下文和独立 Pipe。

超过 deadline，协调器终止并回收该 worker，必要时 kill，再重建其独立通信通道和 worker。不依赖 Python 3.14 才新增的进程池接口，也不强杀共享 Queue 的写入方后继续使用损坏通道。worker 只做网络探测，不持有 Redis 写事务、文件锁或日志文件；状态和结构化日志由协调器提交。

deadline_exceeded 根据已返回的阶段区分网络失败、内容超时、环境错误。已完成项目保留；未开始的其他协议是 pending，不能跟着算失败。验收要求超时后 2 秒内没有该任务残留 worker/socket，反复超时不积累僵尸进程。

并发上限配置为 20，但不能直接假定服务器可承担 20 个 Python 进程。首轮部署 worker_count 从 4 起，依据实测内存和队列吞吐调整，最大不超过 20。worker_count 是实际并发数；这是将上一轮“20 并发”细化为可测资源预算。

## 6. 状态、计数与淘汰

状态以 variant × profile 保存，endpoint 状态由各项结果派生。区分“检测结果”“分发资格”“删除资格”，避免一个布尔值承担全部含义。

| 结果/状态 | 默认分发 | 硬失败计数 | 后续动作 |
| --- | --- | --- | --- |
| available | 通过主目标且未过期时允许 | 成功则对应计数清零 | 120 秒后加抖动复检 |
| suspect | 暂停该结果分发 | 独立复检轮次 +1 | 约 120 秒后复核 |
| unavailable | 不允许 | 连续 3 次有结论的硬失败 | 停用对应 variant，评估 endpoint 清理 |
| target_restricted | 不允许该目标；其他已通过目标不受影响 | 不增加连接硬失败计数 | 2/5/15 分钟退避；遵守 Retry-After |
| auth_required/auth_failed | 不允许对应 variant | 不增加无意义网络重试计数 | 凭证变更或隔离期到期后复核 |
| tls_invalid/content_inconclusive | 不允许对应结果 | 不当作硬连接失败 | 明确展示原因，延后诊断 |
| deferred/environment_error | 不形成新的失败结论 | 不增加 | 等额度、依赖或目标恢复后重排 |
| stale | 不允许 | 不增加 | 优先补检 |
| http_only | 不满足本次默认 HTTPS 主目标 | 单独记录 | 显式筛选可见 |

连续失败的统计单位是独立 round_id；同轮重试和两目标访问不能重复累加。一次有效成功将对应连续失败清零，不沿用“成功减一”的语义。对明确在代理连接/握手阶段失败的 variant，本轮不再无意义访问其他目标。

仅当已知/已完成发现的 variant 全部达到硬失败阈值，且当前目标/运行环境健康，才删除 endpoint。未完成协议发现、目标健康状态未知、DNS/配置/依赖错误，均不满足自动删除条件。结果证据仅能确认“目标连接超时”时，不谎报为“代理端口超时”。

从未通过的新候选至少保留到有限重试和协议发现完成；无成功记录候选与受限/认证异常记录默认在 24 小时无新证据后过期。已有可用记录的资格在最后成功 10 分钟后过期；不可因熔断无限延长可用时间。保留 7 天结构化日志以供追溯。

## 7. 目标健康与防止批量误删

每个 profile 独立维护 healthy、suspect、open、half_open、unknown。以最近 60 秒至少 10 个不同 endpoint 的结果作为候选窗口，若 80% 出现相似目标阶段错误，转 suspect，暂停该目标引起的删除计数并触发少量对照。

对照优先使用至少 3 个最近成功、不同 endpoint 的参考代理。两项以上对照同样失败时进入 open；没有足够参考代理则保持 unknown 并告警，不把缺少证据当成目标正常。服务器直连仅辅助，不因直连 Google 失败就断言 Google 故障。

open 初始冷却 60 秒，只允许全局每分钟最多 2 次 canary；失败指数退避到最多 15 分钟。half_open 需要两个不同参考代理成功，才恢复 healthy。所有对照与 canary 都经过同一域名限流。

仅证据明确的代理连接失败可继续计入对应 variant；疑似公共目标/环境故障的失败冻结，不直接清库。恢复后分批补检，不瞬间释放全部积压。

## 8. 持久队列、租约和容量

定时采集、Geonode、手动刷新、复检都只生产同一队列的任务；API worker 不再启动自己的 Checker。调度进程负责一个有 leader lease 的协调器，网络 worker 不做调度。

| 机制 | 默认设计 |
| --- | --- |
| 去重 | endpoint_id 合并待测 variant/profile，存在 in-flight 时仅追加需求 |
| 租约 | endpoint 独占租约 45 秒、10 秒心跳续约；只允许 owner token 续租/释放 |
| 迟到写保护 | 每次领取生成 fence；提交校验 leader、租约、endpoint generation、策略版本 |
| 总并发 | worker_count 实测配置，允许 1—20，首轮 4 |
| 目标限流 | 每个目标域名全局 2 请求/秒，burst=1；重试、重定向、人工操作共用 |
| 调度 | 2 分钟扫描 due 项；协调器持续消费，加入约 ±10% 抖动 |
| 抓取频率 | 常规源 5 分钟、Geonode 15 分钟；与检测独立配置 |
| 背压 | 新候选队列上限 5000、候选保留 24 小时；已验证代理复检优先，保留新候选公平份额 |
| 告警 | 最老 due 延迟超过 5 分钟；可用结果超期；worker 重建过多；目标熔断 |

120 秒是复检目标间隔，不承诺任何规模都能 2 分钟全池检查一次。容量计算必须考虑请求次数：500 个相同 profile 的代理，每个只请求一次，2 请求/秒的理论最低时间也是 250 秒，尚未计入慢连接、重试和跳转。限流不够时明确显示排队延迟，调高额度必须依据真实限频和资源测试，而不是私自突破配置。

基础设施失败、主动取消和 worker 启动失败不能计为代理失效。协调器重启后从租约和 due 索引恢复，已领取任务最多重复探测一次，结果凭 fence 幂等提交。leader 丢失后停止派发，旧结果不能覆盖新 leader 的更新。

## 9. 数据结构与原子存储

### 9.1 领域对象

| 对象 | 主要字段 |
| --- | --- |
| ProxyCandidate | host、port、protocol_hints、country_hint、source、credential_ref |
| ProxyEndpointV2 | schema_version=2、endpoint_id、generation、host、port、sources、地区证据、created_at、updated_at |
| VariantResult | variant、dns_mode、协商结果、认证状态、target_results、consecutive_hard_failures、discovery_state |
| TargetResult | profile、目标域名、result_code、stage、HTTP 状态、elapsed_ms、最后成功/尝试时间、valid_until、policy_version |
| ValidationTask | task_id、round_id、endpoint_id、generation、所需项目、due_at、lease/fence、优先级 |
| ValidationPolicy | 目标/白名单、地区映射、超时、重试、限流、准入、版本和配置摘要 |

supports_http、supports_https 是具体 variant 的实测能力摘要，未经检测必须为 unknown，不能把缺失当 False。默认分发资格还要求对应 profile 最近成功、规则版本兼容且未处于 suspect/受限状态。

国家/来源重新声明不能覆盖验证结果；旧格式读取必须保留兼容默认值，并把未经 V2 检测的能力标为 pending，不把旧 https=true 直接当成已通过新规则。

### 9.2 Redis 命名空间

使用独立 V2 Repository，复用受控连接池但不更改 DbClient 单例的全局表名。以现有 tableName 为前缀：

| key 后缀 | 类型与用途 |
| --- | --- |
| :v2:endpoints | Hash，endpoint_id 到版本化记录 |
| :v2:due | Sorted Set，下次调度时间 |
| :v2:due:new / :v2:due:use | 新候选和曾通过主目标的复检分区，双队列都有到期任务时保留 20% 新候选份额 |
| :v2:ready:profile:variant | Sorted Set，按 valid_until 建分发索引；查询严格检查时间 |
| :v2:ready:profile:variant:country:CC | 国家/地区索引，避免只扫描首批代理导致地区筛选漏报 |
| :v2:lease:endpoint_id | String，带唯一 owner/fence 的短租约 |
| :v2:leader | String，协调器租约 |
| :v2:target_health | Hash，目标状态与窗口摘要 |
| :v2:rate:domain | 原子限流状态 |
| :v2:geo:ip | 带 TTL 的地区缓存 |
| :v2:credentials / :v2:credential_counts | 受保护的认证数据和引用计数，不进入公开列表与日志 |
| :v2:tombstone:endpoint_id | generation 墓碑，防止删除后旧结果复活，默认保留 24 小时 |

领取、结果提交、状态和 ready 索引变更、兼容投影变更、pop/delete 使用有界原子脚本。脚本不得遍历整个池，也不得在每次 get 时 HVALS 全池扫描；使用有限候选、索引和分页。实现使用 Redis 8.0 可用命令，不依赖更晚小版本才新增的解锁命令。

提交须校验 generation、fence、租约所有权和当前模式 epoch；网络结果延迟、pop/delete、规则切换后返回的旧结果直接丢弃。删除 endpoint 同时移除所有协议索引和旧投影；pop 默认消费整个 endpoint，与旧接口的“取出并删除该代理”语义一致。再次抓取允许新 generation 入队，不受旧任务回写。

本设计针对单实例 Redis，不宣称在任意主从故障切换下提供严格一次执行。Redis 不可用时不直连回退、不返回过期代理；队列保留恢复语义，接口给出可识别服务不可用错误。

## 10. API 和 WebUI 契约

### 10.1 旧接口

保留 /get/、/pop/、/all/、/count/、/delete/、region 和 type=https 参数、响应外层结构及已有字段。旧接口默认只返回明文 HTTP 连接方式的代理，不能把 SOCKS 或 TLS 代理交给会自动拼接 http:// 的调用方。

active 模式下旧接口同样要求对应地区主目标通过且新鲜；因此默认可用数量可能变化，发布说明必须写明。这是检测策略升级，不声称旧判定语义完全不变。type=https 继续表示 HTTPS 目标能力。旧 https 字段只针对选出的兼容 HTTP variant 派生，不用任何 SOCKS 成功结果填充它。

旧接口只查新的兼容投影和资格，不在 V2 没有结果时悄悄返回旧未验证数据。shadow 模式维持原服务行为，V2 只供管理员对照。

### 10.2 V2 接口

| 接口 | 设计 |
| --- | --- |
| GET /api/v2/proxies/ | 分页列表；默认 available；敏感认证数据脱敏 |
| GET /api/v2/proxies/random/ | 随机可用连接，返回精确 proxy_url、variant 和实测能力；凭有效授权获取认证信息 |
| POST /api/v2/proxies/pop/ | 原子取出并删除 endpoint，避免多个请求消费同一记录 |
| DELETE /api/v2/proxies/id/ | 删除指定 endpoint 及派生索引，取消在途提交 |
| GET /api/v2/validation/status/ | 队列、worker、目标健康、统计、规则版本 |
| POST /api/v2/validation/jobs/ | 提交指定范围任务，202 返回 task/batch ID，不同步等待探测 |
| GET /api/v2/validation/config/ | 脱敏配置、来源及生效版本 |
| PATCH /api/v2/validation/config/ | 仅管理员会话；验证后发布新策略版本 |

筛选参数：protocol、dns_mode、profile、region、status、max_latency_ms、page、page_size；page_size 默认 50、上限 200。未知参数值返回 400，不忽略成宽泛查询。普通获取默认不含 suspect、stale、http_only；管理员可以显式查这些状态。

所有 /api/v2/ 路径必须纳入鉴权保护。数据读取沿用有效 Token/会话；配置和切换模式操作仅允许已登录管理员，普通 Token 不能修改规则。凭证不出现在列表、日志、URL 查询参数和指标标签中；如需返回可用认证 URL，仅在授权获取连接接口中返回。

WebUI 分开显示连接协议分布、目标可达能力、国家/地区、可用/待检/受限/过期、耗时和最后成功；新增检测详情、队列延迟、目标健康、影子差异和配置表单。复用现有页面视觉和轮询方式，不重做整体界面。

## 11. 配置和结构化日志

新增独立的 validation 配置命名空间，通过现有 runtimeConfig.updateConfig 事务读写，保留 Token、采集开关和统计。优先级为受支持的显式环境变量 > 运行时 validation 配置 > 默认值；WebUI 显示被环境变量锁定的配置，不假装修改已经生效。

策略为不可变快照，含 policy_version 和 mode_epoch；任务提交记录快照版本。目标/CA/地区映射变更时，对受影响能力标 pending 并分批重检；仅修改 UI 展示等无关项不让所有代理失效。模式切换需要先停止旧 writer，再更新 epoch，杜绝双写冲突。

运行时文件保存 desired 配置，Redis 保存已发布的有效策略与 mode_epoch；后者是正在运行的权威状态。先校验并持久化 desired，再由协调器原子发布有效版本，API 返回 desired/effective 的差异。进程在两步之间崩溃时继续使用上一个有效策略并重试发布，不能因文件和 Redis 不一致悄悄切回旧池。生产模式转换还需满足第 12 节的排空和备份条件。

旧 HTTP_URL/HTTPS_URL 保留给 legacy 引擎，不能覆盖新地区策略；V2 的 QQ/Google 使用独立配置项。VERIFY_TIMEOUT/MAX_FAIL_COUNT 作为旧配置展示并提示 V2 替代项，不把 MAX_FAIL_COUNT=0 静默继承为 V2 首败即删。

日志采用一行一个结构化事件，按现有小时切割和 7 天保留机制落盘；字段包括 event、UTC 时间及展示时区、batch_id、round_id、endpoint_id、脱敏地址、source、variant、profile、country_source、stage、result_code、status_code、attempt、elapsed_ms、queue_wait_ms、policy_version、状态变化及清理动作。

日志不记录完整响应正文、认证 URL、密码、Token、Cookie 或包含敏感 userinfo 的原始异常。错误栈按需调试且先脱敏。不提供无法实际测量的细分耗时；SOCKS 远端 DNS 无独立观测时标 unknown。

批次汇总区分 records_received、candidates_parsed、unique_endpoints、variants_tested、主目标通过、各类失败、deferred、exist/来源合并、实际新增可用数；这些不同口径不能直接相加冒充候选数。每个 round 对每个 variant/profile 最多提交一个最终结果，attempt 另计。

## 12. 迁移、影子运行和回滚

三种模式：legacy 为当前引擎；shadow 将一次采集的候选同时送给旧、新引擎，但 V2 不改旧代理 Hash；active 为 V2 负责分发和兼容投影。shadow 不得重复采集同一个来源。

迁移步骤：备份旧代理 Hash/Redis 快照及运行时配置 → 批量幂等导入 V2 pending → 保留来源/旧属性 → 限流复检 → 影子比较 → 生成只含 V2 合格 HTTP variant 的临时兼容投影 → 停止旧检测 writer 并等待在途结束 → 原子切换投影和 mode_epoch → active。临时 Hash 为空的特殊情况需明确处理，不能对不存在的 key 盲目 rename。

切换前至少 95% 旧代理完成 V2 检测或有明确隔离原因；服务所需最小可用数量必须满足部署前约定阈值，建议沿用 POOL_SIZE_MIN=20 作为初始业务门槛，但真实免费源不足时报告不可切换，不伪造通过。其余 pending 不混入默认结果。

切换后旧 Hash 是 V2 的受限兼容投影，不承载全部 SOCKS 数据。原始备份单独冻结，不能一边被新引擎改写一边声称可完整回滚。

回滚：暂停生产者和 V2 协调器 → 提升 mode_epoch/吊销租约 → 停止 worker → 恢复旧镜像 digest 与 legacy 模式 → 按需要恢复旧 Hash 备份并立即启动旧规则复检。备份仅恢复数据，不把旧记录声称为“新检测通过”。配置恢复只覆盖本次 validation 字段，不覆盖灰度期间新生成的 Token 和其他人工配置。

迁移/回滚不得清空 Redis，不重建 Redis 数据卷，不更改 API Token、源开关、登录信息和非本次日志。

## 13. 测试、验收和发布

必须有受控 HTTP CONNECT、TLS 代理、SOCKS4/4a/5/5h 和 HTTPS 目标，使用测试 CA、指定域名和可记录链路的代理验证真实隧道；不能只 mock 返回 200。专门验证本地 DNS 与远端 DNS、错误代理协议、认证、两层证书、超时回收、环境代理污染、不能直连回退。

状态/存储覆盖连续失败、成功清零、重试不重复计数、多协议互不拖累、过期过滤、熔断恢复、租约过期、迟到结果、pop/delete 并发、重启恢复、幂等迁移和回滚。真实 Redis 8 测试原子性，fakeredis 不能代替此项。完整用例见开发计划的验收矩阵。

保留 Python 3.10—3.13 单元测试矩阵；本地交付环境使用 Python 3.13、Node 26、Redis 8。本机执行 Python 前先激活 xiangmu。公网 QQ/Google/Geonode 检查限量并单独记录，不作为每次 CI 的不稳定强制依赖。

发布顺序固定：本地源码与 Docker 验收 → 推送 GitHub → CI 通过 → 发布版本/标签 → GitHub Actions 构建 amd64/arm64 GHCR 镜像 → 校验标签、提交与 digest → 服务器拉取固定 digest → shadow → 分阶段扩大独立灰度入口覆盖 → 全局原子切换 active。服务器只部署 GitHub 构建镜像。

现有 tests 和 docker workflow 独立触发，且 tests 只配置分支/PR；需补足 tag/release 的质量门禁，避免 tag 镜像在未验证发布提交时被误用。建议将测试逻辑提取为可复用 workflow，由镜像发布流程通过 needs 显式依赖同一提交的测试结果；失败时不推送版本镜像或更新 latest。服务器部署以受测提交和完成的 tag 构建为依据，不只检查某个标签存在。

首次上线使用独立灰度 API/测试入口：通过稳定 endpoint 哈希先选 10% 端点、最多 50 个进行 V2 shadow，连续观察至少 30 分钟；再扩大到 50% 和全量 shadow，达到覆盖和可用量门槛后，全局原子切换 active。10%/50% 表示影子候选与灰度入口的覆盖范围，不在每次线上请求时随机切换引擎。

本版不实现线上混合 writer 灰度。shadow 期间线上旧 Hash 只由 legacy 写，V2 只写独立命名空间；切换时先排空旧 writer，再接管兼容投影。这样给出唯一且可审计的切换点，避免两引擎互相恢复或删除同一个地址。

## 14. 模块改动清单

| 模块 | 责任 |
| --- | --- |
| helper/validation/models.py、normalize.py、policy.py | 候选、结果、策略、地址规范 |
| helper/validation/transport.py、worker.py | 显式代理传输、TLS/SOCKS、进程回收 |
| helper/validation/region.py、classify.py、health.py | 地区证据、响应分类、目标保护 |
| helper/validation/coordinator.py、repository.py | 队列、限流、租约、原子结果和索引 |
| helper/validation/compat.py、migration.py | 旧字段投影、模式切换、迁移回滚 |
| helper/proxy.py、helper/check.py、helper/validator.py | 保留旧模式和扩展点，接入 V2 facade |
| fetcher/baseFetcher.py、helper/fetch.py、各来源 | 兼容结构化候选；Geonode 优先接入，其他源保持可运行 |
| helper/fetcherProxy.py、helper/scheduler.py、helper/schedulerStatus.py | 正确连接 URL，共用任务入口和状态 |
| api/proxyApi.py、新增 api/validationApi.py、api/webuiApi.py | 兼容 API、V2 API、鉴权和管理配置 |
| webui/src/api.js、DashboardView.vue、SchedulerView.vue、ApiDocsView.vue | 协议与目标展示、队列与文档 |
| 新增 ValidationView.vue、webui/src/router.js | 检测详情及配置页面 |
| requirements.txt、tests、Docker 测试夹具、.github/workflows | 依赖、真实链路验收和发布门禁 |
| docs/configuration.md、api.md、webui.md、docker.md、changelog.md | 用户说明、参数迁移、回滚步骤 |

新增文件名是责任划分，实施时可合并小模块；不能为了模块数量拆出没有独立责任的文件。

## 15. 主要风险与关闭条件

| 风险 | 对策/完成条件 |
| --- | --- |
| 地区和出口不一致 | 地区分来源、缓存时间与可信度，未知不猜测 |
| QQ/Google 页面或重定向变化 | 版本化规则、受控 fixtures、有限实测；证据不足不硬删 |
| TLS 代理兼容和证书误配 | 两层 TLS 独立测试，错误分层，支持显式 CA |
| 阻塞 DNS/慢流导致假超时 | worker 硬回收实测，30+2 秒上限，无残留资源 |
| 多入口突破限流 | 统一队列和 Redis 额度，重试/跳转/健康探测同样计费 |
| 新 worker 占用过大 | 4 worker 起测，测得 RSS/FD/CPU 后扩容；按资源降低并发 |
| 新规则导致可用数下降 | shadow 差异、逐项归因，业务门槛未满足不切换 |
| 旧接口把 SOCKS 当 HTTP | 兼容投影仅含 HTTP 连接方式，完整 URL 走 V2 |
| 删除后任务复活记录 | generation、租约和 fencing 原子校验，真实 Redis 并发测试 |
| 发布测试与镜像不一致 | 发布提交、tag、CI 结果、镜像 digest 逐项关联 |

## 16. 技术参考

参考资料于 2026-11-19 核对，具体依赖版本在实施阶段冻结。

- [urllib3：HTTP/HTTPS 代理及 TLS-in-TLS](https://urllib3.readthedocs.io/en/stable/advanced-usage.html)。
- [urllib3：SOCKS4/4a/5 与远端 DNS](https://urllib3.readthedocs.io/en/stable/reference/contrib/socks.html)。
- [Requests：证书、流式读取与超时语义](https://requests.readthedocs.io/en/stable/user/advanced/)。
- [Requests：Session 与 trust_env](https://requests.readthedocs.io/en/stable/api/)。
- [Python 3.13：Future 取消与运行中任务](https://docs.python.org/3.13/library/concurrent.futures.html)。
- [Python 3.13：进程与独立通信通道](https://docs.python.org/3.13/library/multiprocessing.html)。
- [Redis：单实例租约、所有者校验与 fencing 注意事项](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)。
- [PySocks：官方项目](https://github.com/Anorov/PySocks)。
