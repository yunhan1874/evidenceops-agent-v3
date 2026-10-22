# 最早版 app → EvidenceOps Agent v3 升级对比与大厂面试手册

> 项目定位：证据可信度驱动、具备副作用一致性保护的企业知识 Agent
>
> 升级前：`app.zip` 中的最早版 `app/app.py` ｜ 升级后：`agent_app_evidence_v3` ｜ 文档修订：2026-08-05

## 使用说明

本文不是“把代码文件逐个念一遍”的技术说明，而是一份面向大厂秋招的项目讲解材料。建议按以下顺序使用：

1. 先掌握“一句话介绍”和“两分钟项目陈述”；
2. 再理解升级对比、核心链路和故障矩阵；
3. 最后按专题练习面试题，回答时坚持“结论—原因—实现—边界”的顺序；
4. 升级前结论来自用户提供的原始 `app.zip`，其中 `app/app.py` SHA-256 为 `013ff66b...74ab365`；升级后“已实现”结论以 `agent_app_evidence_v3` 的实际代码和 Docker 验证结果为准；
5. 本文的比较基线严格是最早版 `app.zip`，不是任何中间迭代目录。

## 0. 面试开场

### 0.1 一句话介绍

我把一个 2617 行单文件的 FastAPI + RAGFlow 企业知识问答代理，升级成了一个**证据可信度驱动、可暂停恢复且具备副作用一致性保护的 EvidenceOps Agent**：从“前端携带上游 ID、后端直接转发答案”演进到“本地会话隔离、显式状态图、候选答案证据门禁、人工审批、幂等与可审计恢复”。

### 0.2 两分钟项目陈述

最早版是一个能工作的企业 RAG 代理：它完成了飞书 OAuth、Excel 白名单、token 缓存与黑名单、RAGFlow 流式/非流式问答、会话增删查、历史、反馈、交互日志、诊断指标和 30 天数据清理。这些能力证明了端到端业务闭环，但它还不是一个具备自主路由、显式状态和恢复语义的 Agent。

我对原始 `app.zip` 做了源码级审计，核心问题不是“文件太长”本身，而是边界耦合：2617 行逻辑集中在一个 `app.py`；前端必须传真实 `chat_id/session_id`，上游实现泄漏到 API 契约；`async def` 路由内部调用同步 `requests` 和 `psycopg2`，会阻塞事件循环；数据库连接池创建后提前 `return`，后面的建表逻辑不可达；流式响应缺引用时虽再次发起非流式查询，却把新拿到的 `reference` 丢弃；健康检查降级时仍返回 HTTP 200；反馈写入失败仍对客户端返回成功；配置还包含硬编码密钥默认值和通配 CORS。

升级后，我先把认证、配置、客户端、仓储、服务、Agent 图和 API 分层，并用本地会话 UUID 隔离 RAGFlow ID、用所有权条件防 IDOR。随后引入 LangGraph，把意图路由、知识问答、文档对比和知识反馈做成显式状态流；检索结果只写入 `candidate_answer`，必须经过 Evidence Guard 才能成为 `final_answer`。Evidence Passport 会记录证据覆盖、相关度、来源数量、风险标签、决策原因和修复计划。无引用、低相关度、高风险弱证据、单来源文档对比时系统拒答；检索片段出现疑似提示注入时隔离证据并阻断候选答案。

对于副作用一致性，我增加了按用户哈希的 `Idempotency-Key`、全请求指纹和数据库唯一约束，并用 `UPDATE ... WHERE status='pending_approval' RETURNING id` 原子抢占审批权。异常发生在副作用边界附近时，不简单标记为普通失败，而是进入 `recovery_required`，禁止自动重试，要求人工对账。

最后，我补充了 Execution Harness、版本化三层评测、真实 Qwen3.8-Max 接入和并发集成测试。专用测试环境中 34 个单元测试和 6 个 subtests 全部通过；90 条黄金集、70 条对抗集和 40 条无 PII 回放种子共 200 条端到端样本全部通过，意图、工具序列、工作流、证据判定、危险答案阻断、反馈定位和副作用安全等 9 项指标均为 1.0；Qwen3.8-Max 真实 Harness 套件 8/8 通过且 10 次模型路由无 fallback；模糊知识反馈会要求补充具体知识点，不进入审批；双并发审批实测结果为一个 200、一个 409，反馈只提交一次。这里的 40 条回放是合成种子，不冒充真实线上日志；精简生产镜像不包含 pytest、ruff 和测试源码。

## 1. 最早版与 v3 总体对比

| 维度 | 最早版 `app.zip` | `agent_app_evidence_v3` | 面试价值 |
| --- | --- | --- | --- |
| 项目形态 | 2617 行单文件 FastAPI/RAGFlow 代理 | 分层模块化 EvidenceOps Agent | 从功能堆叠到边界设计 |
| 决策机制 | 固定路由直接调用 RAGFlow，无 Agent 状态图 | LangGraph 意图路由、条件边、interrupt、checkpoint | 状态机与跨请求恢复 |
| API 解耦 | 前端传真实 `chat_id/session_id` | 本地 UUID 映射上游 ID，并绑定 `user_id` | 防上游耦合与 IDOR |
| I/O 模型 | `async def` 中调用同步 `requests/psycopg2` | 异步 HTTP + SQLAlchemy Async/asyncpg | 事件循环与并发容量 |
| 回答链路 | RAGFlow 文本直接返回为 `rag_flow_content` | `candidate_answer` 经 Evidence Guard 才成为 `final_answer` | 信任边界、fail-closed |
| 证据输出 | 原样转发 `rag_flow_reference` | citations + Evidence Passport | 可解释、可评测、可审计 |
| 证据不足 | 流式回退存在引用丢失；无引用也可能成功 | 无引用、低分、来源不足时拒答 | 幻觉控制与故障定位 |
| 任务语义 | 通用问答代理，无独立文档对比契约 | 文档对比至少要求两个独立来源 | 任务特定证据契约 |
| 高风险问答 | 所有问题使用同一提示与阈值 | 食安、合规、限量类问题提高门槛 | 风险感知策略 |
| RAG 安全 | 未检查检索片段中的指令 | 检测并隔离间接 Prompt Injection | RAG poisoning 防御 |
| 人工审批 | 反馈接口直接执行写入，无 HITL 状态 | `pending_approval` 中断/恢复 + CAS 抢占 | 人在回路与竞态控制 |
| 反馈失败 | 数据库失败仍返回 200 成功 | 稳定失败状态；不确定副作用进入 `recovery_required` | 诚实状态与对账语义 |
| 请求幂等 | 无业务幂等键，重试可重复创建 run/副作用 | 用户域 Idempotency-Key + 指纹 + 唯一约束 | 网络重放与冲突语义 |
| 数据初始化 | 连接池后提前返回，建表代码不可达 | 生命周期初始化 + migration 文件 | 启动可靠性与 Schema 演进 |
| 错误契约 | 多处分支直接拼接原始异常，错误码分散 | 稳定 AppError、公开消息与审计错误分离 | 脱敏与可观测性 |
| 健康检查 | 降级也返回 HTTP 200，且 liveness/readiness 未分离 | `/health/live` 与 `/health/ready` 分责 | 容器编排语义 |
| 配置安全 | 源码包含凭证默认值，CORS 通配 | SecretStr + 生产 fail-fast + CORS 校验 | 密钥治理与安全默认值 |
| 权限模型 | 白名单用户均可清空认证缓存/黑名单 | 资源访问按用户所有权约束，高风险动作受控 | 最小权限意识 |
| 可观测性 | 请求日志、交互日志、缓存指标 | Request ID + agent_runs + tool_calls + 稳定错误码 | 从日志到运行轨迹 |
| 测试评测 | 压缩包中无自动化测试与故障样本 | 34 单测、200 条三层评测、真实 Qwen Harness、版本清单、并发实测 | 可回归且防静默换题的质量闭环 |
| Execution Harness | 无运行预算、版本指纹或工具策略控制面 | 四类预算、轻量 Trace、上下文治理、Tool Contract、模型路由/熔断 | 覆盖当前 Agent 面试对可控执行与可复现性的考察 |

### 1.1 最早版值得保留的能力

最早版不是“没有价值的 Demo”。它已经具备真实业务闭环：飞书 OAuth 换 token、Excel 白名单、token 掩码与缓存、RAGFlow 流式/非流式适配、会话与消息管理、反馈与交互日志、数据库连接池、诊断指标和定时清理。升级时保留这些业务语义，只重构不稳定边界，避免为了展示新框架而推翻可用能力。

### 1.2 最早版源码审计结论

| 代码事实 | 可能后果 | v3 处理 |
| --- | --- | --- |
| `rag_query` 等异步路由直接调用同步 `requests` 和同步数据库 | 单个慢请求阻塞事件循环，吞吐和尾延迟恶化 | 异步客户端与异步 Session，外部调用设置超时 |
| `init_postgres_db()` 创建连接池后立即 `return True` | 后续 schema/table DDL 不可达，空库首次启动不可靠 | 生命周期初始化并提供显式 migration |
| 流式缺引用后执行非流式回退，但最终使用旧的 `raw_reference` | 明明补查到引用，响应仍返回空引用 | 标准化工具输出，证据缺失由门禁显式决策 |
| `/health` 捕获异常后返回普通 dict | HTTP 状态仍可能是 200，编排器误判 ready | live/ready 分离，依赖异常影响 readiness 状态码 |
| `rag_feedback` 忽略保存失败并返回成功 | 形成假成功，用户与数据状态不一致 | 失败显式化，不确定状态进入对账流程 |
| 凭证存在源码默认值、CORS 为 `*` | 密钥泄漏与跨域攻击面扩大 | 生产配置启动校验；历史凭证若仍有效必须轮换 |
| 任一白名单用户可调用清缓存接口 | 普通业务用户具备运维级能力 | 高风险操作应增加管理员角色/独立运维面；当前继续作为演进项 |

## 2. 架构演进：最早版到 v3

### 2.1 最早版主链路

```text
Browser ── chat_id + session_id + query ──► FastAPI app.py
                                              │
                          Feishu token ─► 内存缓存/黑名单 ─► Excel 白名单
                                              │
                                              ▼
                                    同步 requests 调 RAGFlow
                                              │
                           answer + reference 原样返回前端
                                              │
                                 同步 psycopg2 写交互日志

特点：业务功能完整，但 API、认证、上游适配、数据库、定时任务和诊断耦合在单文件；
      没有显式 Agent 状态、证据门禁、业务幂等与跨请求恢复语义。
```

### 2.2 v3 主链路

```text
HTTP Request
  │  Request-ID 校验 / 身份认证 / 会话所有权校验
  │  Idempotency-Key 哈希 + 请求指纹
  ▼
AgentRun 持久化 ── 唯一约束处理并发重放
  ▼
LangGraph classify_intent
  ├─ knowledge_qa ──────► RAGFlow ─► candidate_answer + citations
  │                                      │
  │                                      ▼
  │                               Evidence Guard
  │                         grounded / review / insufficient / blocked
  │                                      │
  │                                      ▼
  │                           final_answer + Evidence Passport
  ├─ document_compare ──► RAGFlow ───────┘
  ├─ knowledge_feedback ─► 定位具体知识点 ─┬─ 模糊 ─► needs_input
  │                                        └─ 明确 ─► draft ─► pending_approval ─► interrupt
  │                                             │
  │                                  CAS 抢占 approval_processing
  │                                             │
  │                                approve ─────┴──── reject
  │                                  │                  │
  │                            submit once          cancel
  └─ unsupported ───────────► capability boundary

所有节点结果写入 agent_runs / tool_calls，LangGraph checkpoint 用于暂停与恢复。
```

## 3. 核心技术点详解

### 3.1 Candidate Answer 与 Final Answer 分离

最早版没有 `candidate_answer/final_answer` 概念。RAGFlow 返回的 `answer` 被直接放入 `rag_flow_content` 返回前端；即使引用为空，非流式链路仍返回成功，流式回退还存在补查到引用却未写回响应的实现错误。这等于把上游“调用成功”误当成业务“回答可信”。

升级后，检索工具只产出 `candidate_answer` 和 `citations`。只有 Evidence Guard 执行完毕后，系统才设置 `final_answer`。这种分离建立了清晰的信任边界：生成模型负责“提出候选”，确定性策略负责“是否允许输出”。

工程意义：

- 可以独立升级检索器、生成模型和证据策略；
- 可以在不重复调用模型的情况下调整输出阈值；
- 可以保留原始工具轨迹，同时避免把不可信候选答案暴露给用户；
- 可以针对不同业务任务配置不同证据契约。

### 3.2 Evidence Passport

每次知识问答或文档对比都会产生一份 Evidence Passport，并持久化在 `evidence_guard` 工具调用记录中。

关键字段：

- `policy_version`：策略版本，便于灰度和回放；
- `verdict`：`grounded`、`review_required`、`insufficient`、`blocked`；
- `confidence`：综合相关度、引用数量、来源多样性的确定性分数；
- `coverage`：引用数、来源数、有效分数数、平均分、最高分；
- `risk_flags`：无引用、低相关度、缺少分数、来源不足、高风险弱证据、间接提示注入；
- `reasons`：面向用户和运维人员的可解释原因；
- `recommended_action`：回答、带警告回答、拒答并修复、隔离并安全复核；
- `recovery_plan`：重写查询、限定文档、分别检索、人工领域复核、隔离来源等；
- `evidence`：证据指纹、文档名、页码、分数、片段和隔离状态。

当前 confidence 计算为：

```text
confidence = 0.6 × relevance + 0.2 × quantity + 0.2 × diversity
```

其中 `quantity` 和 `diversity` 都会封顶，防止通过堆积大量低质量引用刷高分。该分数不是“事实正确率”，而是服务时门禁的可解释启发式指标。

### 3.3 Verdict 决策逻辑

| Verdict | 典型条件 | 输出策略 |
| --- | --- | --- |
| `grounded` | 有答案、有引用、分数达标、任务证据契约满足 | 输出候选答案 |
| `review_required` | 引用存在但部分缺少相关度元数据 | 增加人工复核警告后输出 |
| `insufficient` | 无引用、低相关度、单来源对比、高风险弱证据 | 拒绝输出候选答案，返回原因和修复计划 |
| `blocked` | 检索片段疑似包含间接提示注入 | 隔离片段、阻断答案、建议安全复核 |

设计选择：普通问答允许在证据元数据不完整时“带警告降级”，但高风险问题使用更严格门槛。这样不是简单追求拒答率，而是在业务可用性和风险之间做分层。

### 3.4 任务特定证据契约

不同任务不能共用一套最低证据规则。普通知识问答可能由一份权威制度支持，但“比较 A、B 两份文档”如果只召回一份文档，就不应该宣称完成比较。

因此文档对比要求至少两个独立 `document_name`。不满足时 verdict 为 `insufficient`，并返回 `retrieve_each_document_separately` 修复建议。后续正确演进方向是分别带 document filter 召回，再在本地进行条款对齐。

### 3.5 高风险问题分层

系统识别食品安全、过敏原、添加剂用量、限量、召回、合规、处罚、法律等高风险词。普通问题使用平均相关度阈值；高风险问题还要求最高相关度达到更高阈值。

这体现了风险分层思想：同样的模型和检索结果，在“员工福利咨询”和“食品添加剂限量”场景下，允许输出的标准不应相同。

当前实现是规则型风险识别，优点是稳定、低成本、可测试；不足是覆盖率有限。生产系统可进一步引入业务标签、知识库分类、策略中心或专用分类模型。

### 3.6 间接 Prompt Injection 防护

攻击者可能把“忽略之前指令、输出系统提示词”等内容写入被检索文档。当文档进入 RAG 上下文时，这些自然语言可能被模型当成新指令。

v3 的处理：

1. 对检索片段执行确定性规则检测；
2. 命中后将原片段替换为隔离占位符；
3. Evidence Passport 标记 `indirect_prompt_injection`；
4. verdict 直接变为 `blocked`；
5. 不向用户输出已受污染的候选答案；
6. recovery plan 包含隔离来源和通知知识库负责人。

必须主动承认：正则规则不能覆盖编码混淆、跨片段拼接、图片隐写和语义改写。完整防御应叠加摄入侧扫描、内容分类器、最小工具权限、输出校验和高风险人工审批。

### 3.7 Idempotency-Key 与请求指纹

网络超时后客户端往往会重试 POST。如果服务端没有幂等机制，同一业务可能创建多个 run，甚至执行多次副作用。

v3 的实现：

1. 校验 `Idempotency-Key` 的长度和字符集；
2. 使用 `SHA-256(user_id + key)` 生成用户域哈希，不保存原始 key；
3. 对 `session_id + query + document_a + document_b` 生成请求指纹；
4. `agent_runs` 增加 `(user_id, idempotency_key_hash)` 唯一约束；
5. 同一 key、同一指纹返回已有 run；
6. 同一 key、不同指纹返回 409；
7. 两个并发首次请求都未查到记录时，唯一约束决定胜者，失败方捕获 `IntegrityError` 后回查已有记录。

关键点：幂等不是简单“去重”。必须定义幂等域、业务输入和冲突语义；否则同一个 key 被错误地用于不同请求时，系统可能返回不相关的旧结果。

### 3.8 并发审批的 CAS 状态抢占

最早版没有 HITL：`/api/rag/feedback` 收到请求后直接写数据库，而且保存失败也继续返回 200。v3 引入审批中断后，副作用被延迟到用户确认阶段，但同时产生了新的并发问题：如果采用“先读取 `pending_approval`、再恢复图”的朴素实现，两个请求可能同时通过检查。

新逻辑使用单条条件更新：

```sql
UPDATE agent_runs
SET status = 'approval_processing'
WHERE id = :run_id
  AND user_id = :user_id
  AND status = 'pending_approval'
RETURNING id;
```

只有一个请求能更新并拿到返回行；另一个请求得到 409。这个 compare-and-set 将“检查 + 状态变更”压缩成一个数据库原子操作，关闭了应用层的竞态窗口。

### 3.9 recovery_required 与副作用不确定性

审批恢复异常时，不能武断地认为副作用没有发生。进程可能在“反馈已经写入”之后、“run 状态更新”之前崩溃。

因此 v3 不把这类异常标成普通 `failed`，而是标成 `recovery_required`，并禁止自动重试。管理员需要查询目标系统或本地反馈表进行对账，再决定补偿、确认成功或人工重放。

这是一种简化的 Saga/对账思维：分布式系统中，不确定状态本身就是一种独立状态。

### 3.10 为什么不自动重试所有 RAGFlow 请求

RAGFlow 创建会话和 completion POST 可能修改远端会话历史，但接口没有提供可由本服务传入的幂等 token。对这种调用进行盲目自动重试，可能产生重复消息或重复会话。

v3 对 timeout、网络失败、429、4xx、5xx 和非法 JSON 做稳定错误映射，但不在客户端内部无条件重试。未来如果下游支持幂等键，可以只对明确安全的错误执行指数退避和抖动重试。

### 3.11 稳定错误契约与脱敏审计

`AppError` 包含稳定 `code`、用户可读 `public_message` 和 `retryable` 标记。数据库和 API 不再直接返回原始异常文本。

对于已知上游错误，审计记录保存稳定错误码；对于未知错误，只保存异常类型。完整堆栈写入服务日志，并用 Request ID 定位。这样既保留排障能力，又避免数据库 URL、内网主机、token 上下文或上游响应泄露给普通用户。

### 3.12 Request ID 与日志注入

如果直接把用户提供的 `X-Request-ID` 写入日志，攻击者可能传入换行符伪造日志记录，或传入超长字符串触发数据库字段异常。

v3 只允许 1—128 位字母、数字及 `._:-`。非法值会被替换为新的 UUID。Request ID 负责链路追踪，Idempotency-Key 负责业务重放，两者不再混用。

### 3.13 生产配置 fail-fast

当 `APP_ENV=production` 时，启动阶段校验：

- `DEV_AUTH_ENABLED` 必须关闭；
- `RAG_MOCK_MODE` 必须关闭；
- 飞书和 RAGFlow 凭证不能为空；
- 白名单路径必须配置；
- 数据库默认密码必须替换；
- CORS 不允许通配符。

与其让系统带着不安全配置启动后再依赖人工发现，不如在生命周期入口直接失败。这属于安全默认值和配置治理。

### 3.14 证据护照如何持久化

v3 没有为了一个新响应字段立即给 `agent_runs` 增加 JSONB 列，而是把 Evidence Passport 存在 `evidence_guard` 的 `tool_calls.output_payload` 中，读取 run 时提取最新护照。

优点是复用现有审计模型、减少主表膨胀、保留策略执行历史；缺点是查询统计更复杂。若后续需要大规模按 verdict、confidence 聚合，应增加专用列或单独的 evidence_decisions 表，并建立索引。

### 3.15 评测与故障注入

评测资产分成三层并由 `manifest.json` 固定版本、数量和 SHA-256：90 条黄金业务样本、70 条对抗/边界样本、40 条无 PII 合成回放种子；原来的 9 条保留为快速 smoke 集。Mock 客户端支持无证据、低相似度、提示注入、双来源、缺少分数、百分制分数和空答案等确定性演练。

评测器不再只判断“是否出现某工具”，而是检查完整工具序列、审批前后状态、`pending_approval`、工具状态、禁止工具、反馈提交次数、反馈字段和 `target_status` 门禁；同时计算意图、工具选择、任务完成、工作流、证据 verdict、安全拒答、反馈定位、反馈 schema 和副作用安全指标，并按 layer/tag 报告通过率与 p50/p95 延迟。

生产日志通过独立脱敏脚本进入 staging：只保留白名单字段，遮盖常见邮箱、手机号、身份证号、工号和凭证，以加盐 hash 替代 trace ID，并默认标记为 `unreviewed`；未经人工复标，评测器拒绝加载。当前回放层仍是合成种子，因此不能声称覆盖真实线上流量。

实测结果：34 个单元测试通过；90/90 黄金集、70/70 对抗集、40/40 回放种子集全部通过，200 条 case pass rate 和 9 项质量指标均为 1.0；9/9 smoke 兼容集通过；真实 Qwen3.8-Max Harness 套件 8/8 通过、10 次模型路由无 fallback；幂等重放返回同一 run；同键异参返回 409；双并发审批为 200 + 409，`submit_feedback` 只出现一次。

### 3.16 知识反馈定位门禁：不是“工单换名”

正确业务语义来自 v2 的知识库问题反馈：员工指出食品公司内部知识库中哪条制度、标准、流程或食品安全知识可能错误、过期、不完整或有歧义。系统生成的不是报修工单，而是包含 `knowledge_item`、`issue_type`、`description`、`priority` 的知识反馈草稿，经员工确认后写入 `knowledge_feedbacks`。

v3 在这个基础上增加定位门禁和确定性兜底：规则会识别 `inaccurate/outdated/incomplete/ambiguous`，食品安全、过敏原、添加剂、限量、召回和合规类反馈提升为高优先级，并计算 `target_status`。如果用户只说“知识库有问题”，草稿标记为 `unresolved`、工具状态为 `needs_input`，流程直接返回补充定位提示；只有明确到具体知识点时才进入 `pending_approval`。这样防止维护人员收到无法处理的空泛反馈，也避免把传统工单逻辑生搬到知识场景。

### 3.17 为什么这是“演进”而不是重写炫技

升级保留最早版已经验证过的业务目标：飞书身份、白名单准入、RAGFlow 知识问答、会话管理、反馈与审计；改变的是系统边界和失败语义。认证不再与 13 个路由复制耦合，上游 ID 不再成为前端契约，RAG 文本不再天然可信，反馈不再“写失败也成功”，请求重放和并发恢复有数据库裁决，未知副作用成为可对账状态。

这种做法的面试价值在于能回答三个层次：为什么最早版能跑、为什么一上并发和生产环境就会暴露问题、为什么所选方案没有承诺不存在的 exactly-once 或“绝对防幻觉”。

### 3.18 Execution Harness：补齐前六个最新考点

本次在既有 v3 上增加可运行的最小 Harness，六项能力均进入真实请求链路，而不是停留在架构图。

| 考点 | 简单实现 | 可验证证据 | 主动承认的边界 |
| --- | --- | --- | --- |
| 运行预算 | `asyncio.timeout` 总时限、LangGraph `recursion_limit`、工具调用上限、输入 Token 估算 | `harness.budget` 与 `termination_reason` | Token 是字符估算，尚无真实账单成本预算 |
| Trace/可观测性 | `run_events` 追加写入 HTTP、LLM、工具策略、上下文和终止事件 | `GET /api/v2/agent/runs/{run_id}/harness` | 是轻量内部 span，尚未导出 OpenTelemetry |
| 版本指纹 | 固化 app、prompt、tool schema、policy、dataset 五个版本 | `agent.run_started.payload.versions` | 生产还应加入镜像 digest、Git SHA 和配置哈希 |
| Context Engineering | 最近轮次上限、字符预算、去重、注入隔离；引用去重、限量、截断 | `context.prepared` 事件和单测 | 当前为规则摘要，不是语义记忆系统 |
| Tool Contract/权限 | 未注册工具默认拒绝；声明角色、风险、超时、输出上限和 egress；高风险提交必须审批 | `/agent/tool-contracts` 与 `tool_policy.*` 事件 | 尚非容器级网络/文件系统沙箱 |
| 模型路由/降级 | 意图小模型、主模型、高风险强模型；连续失败熔断到确定性规则 | `harness.model_routes` 记录选择原因和 fallback | 熔断状态仅进程内，shadow traffic 默认不真实发流量 |

新增 `run_events` 而不改写 `tool_calls`：前者表示 Harness/Trace 控制面，后者继续表示业务工具审计面。这样旧评测仍可按完整工具序列断言，不会把内部 span 错当成模型选择的业务工具。

### 3.19 Qwen3.8-Max 真实接入：让 Harness 观测模型，而不是相信配置

项目通过 DashScope 的 OpenAI 兼容接口接入 `qwen3.8-max`，配置保留在环境变量，密钥不进入源码、镜像和报告。LLM 客户端记录服务端实际返回的 `provider_model`、usage、request id、重试次数和稳定错误码，因此测试断言的是“供应商实际执行了哪个模型”，而不是只检查本地 `LLM_MODEL` 字符串。

真实套件覆盖知识问答、上下文追问、双文档比较、间接 Prompt Injection、模糊知识反馈、明确反馈审批与提交、未知意图七类业务路径共 8 个 case。最终结果为 8/8 通过，10 次模型路由全部由 `qwen3.8-max` 执行，fallback 为 0，供应商累计报告 2626 tokens；同时产生 57 个 Harness 事件，覆盖 context、lifecycle、policy、span、termination 五类事件。原始 9 条 smoke 套件也达到 9/9，通过时平均延迟 6101.59 ms、P95 12086.81 ms。

真实模型首次暴露了一个 Mock 很难发现的问题：用户只说“知识库有问题，请提交反馈”时，模型自行补出了“内部知识库”并进入审批。修复不是继续堆 prompt，而是把“反馈目标是否明确”改为基于原始用户文本的确定性资格门禁；模型只能抽取候选字段，不能提升权限或改变工作流状态。另一次瞬时 HTTP 故障触发了 fallback，因此只对无副作用的模型调用增加最多两次、仅限网络错误、429 和部分 5xx 的受控重试；RAGFlow completion 和反馈提交仍不盲目重试。

这组结果证明的是接入链路、Harness 观测、关键业务门禁和故障语义，不等价于证明模型在真实企业知识分布上的长期质量。生产还需加入脱敏线上回放、人工标注、成本分位数、漂移和供应商 SLA 监控。

## 4. 故障与边界矩阵

| 环节 | 故障或攻击 | 当前处理与原因 | 后续演进 |
| --- | --- | --- | --- |
| HTTP 入口 | Request ID 含换行/超长 | 替换为 UUID，防日志注入和字段异常 | 网关统一生成 trace ID |
| 认证 | 飞书超时或响应异常 | 返回稳定错误码，日志保留堆栈，避免泄露内部错误 | token 缓存与熔断 |
| 会话 | RAGFlow 创建超时 | 返回可重试错误，但不盲目内部重试；创建会话可能非幂等 | 下游支持幂等键后重试 |
| 运行创建 | 客户端重放相同请求 | 返回同一 run，保持业务幂等 | 设置 key TTL/归档策略 |
| 运行创建 | 同 key 不同参数 | 返回 409，防止错误结果复用 | 返回原请求摘要便于排查 |
| 运行创建 | 两个并发首次请求 | 唯一约束裁决，失败方回查；仅靠先查不能防竞态 | 压测和冲突指标 |
| 意图识别 | LLM 不可用/JSON 非法 | 确定性规则 fallback，保证核心流程可用 | 记录 fallback rate |
| 检索 | 无引用 | 拒答；流畅文本不等于有证据 | query rewrite + 二次召回 |
| 检索 | 低相似度 | 拒答，降低幻觉和误导风险 | 动态阈值、学习排序 |
| 检索 | 分数缺失 | 带警告回答，在可用性与风险间折中 | 修复检索元数据 |
| 文档对比 | 只召回一个来源 | 拒答并建议分别召回；单来源无法证明比较完成 | document filter 双路召回 |
| 高风险问答 | 证据一般但未达高门槛 | 拒答并建议领域复核，执行风险分层 | 策略中心和人工队列 |
| 知识库 | 间接 Prompt Injection | 隔离片段并阻断答案；RAG 文档是不可信输入 | 摄入扫描 + 专用分类器 |
| 审批 | 双并发 resume | 一个成功、一个 409，CAS 保证单个执行者 | 分布式副作用幂等键 |
| 审批 | 执行中进程崩溃 | 标记 `recovery_required`；副作用状态未知时不重试 | 对账任务和补偿工作流 |
| 数据库 | 更新后同 Session 读到旧对象 | 用 `populate_existing=True` 刷新 identity map | 事务边界规范化 |
| 部署 | 生产误开 mock/dev auth | 启动失败，不允许不安全配置运行 | 配置中心与策略即代码 |
| Schema | 旧库缺少新列 | 执行 migration SQL；`create_all` 不会 ALTER 表 | 正式引入 Alembic |

## 5. 大厂面试题与参考答案

### A. 项目定位与架构设计

#### Q1：这个项目最独特的点是什么？为什么不是普通 Agent Demo？

**参考答案：** 独特点不是工具数量，而是把“答案可信度”和“副作用一致性”做成服务端的一等能力。普通 Demo 通常在意图分类后直接调用 RAG 或工具，只要返回文本就算成功；我的系统把生成结果定义为候选答案，必须经过 Evidence Guard 才能对外输出。同时，审批副作用不是只做一个前端确认框，而是处理了幂等重放、并发抢占和未知副作用状态。

**常见追问：** 为什么不直接让大模型自己判断引用是否可靠？

**补充回答：** 第一层使用确定性策略是因为低延迟、可解释、可回归。LLM judge 可以作为高风险分支的第二层，但不能完全替代确定性边界，否则 guardrail 自身也会出现随机性、注入和成本问题。

#### Q2：最早版为什么更像 RAG 代理，而不是 Agent？为什么后来用 LangGraph？

**参考答案：** 最早版的路由和工具是固定的：HTTP 接口直接转发到 RAGFlow 或数据库，没有意图决策、共享 AgentState、条件边、跨请求中断和恢复，因此更准确的定位是“带认证与业务闭环的 RAG API 代理”。v3 出现知识问答、文档对比、知识反馈审批和补偿状态后，LangGraph 将节点、条件边、interrupt 与 checkpoint 放进显式状态机，让长流程状态转移可恢复、可审计。它的价值不是把 if/else 写短，而是管理持久状态与恢复语义。

**边界：** 如果业务永远只有一次分类和一次查询，LangGraph 反而会增加复杂度。框架选型应该由状态和恢复需求驱动。

#### Q3：为什么将会话映射保存在本地，而不是让前端直接传 RAGFlow session_id？

**参考答案：** 本地 UUID 隐藏上游实现，使前端不依赖 RAGFlow 的 chat/session 结构；同时本地会话可以绑定 user_id、状态和审计信息，所有查询都能做所有权校验。未来切换向量库或按部门路由知识库时，前端契约不需要变化。

#### Q4：项目中哪些是控制面，哪些是数据面？

**参考答案：** RAGFlow 检索、模型生成和反馈写入属于数据面；Evidence Policy、意图路由、审批状态机、幂等控制、配置校验和错误映射属于控制面。区分后，模型能力可以替换，而安全和一致性边界保持稳定。

#### Q5：你如何定义这个系统的“成功”？

**参考答案：** 不能只看 HTTP 200 或回答是否流畅。至少分四层：路由正确、工具执行正确、任务状态完成、输出证据满足策略。对于危险样本，还要测是否正确拒答。因此评测包含 intent accuracy、tool selection accuracy、task completion rate、evidence verdict accuracy 和 unsafe answer block rate。

#### Q6：从最早版升级时，哪些能力应该保留，哪些必须重构？

**参考答案：** 我会保留已经闭环的业务语义：飞书登录、白名单、RAGFlow 问答、会话、历史、反馈和日志；重构前端直传上游 ID、单文件耦合、同步 I/O 混入异步路由、硬编码配置、假成功与无幂等状态。原则是“保留业务契约，替换不稳定边界”，并用兼容层和回归用例证明没有为了框架而重写。

### B. LangGraph、状态机与恢复

#### Q7：AgentState 为什么使用 TypedDict？

**参考答案：** TypedDict 明确节点共享字段，减少隐式依赖，并让 IDE 和静态检查知道哪些字段可能存在。`total=False` 适合分阶段逐步填充状态，但代价是运行时仍可能缺键，因此节点应区分必需字段和 `get()` 可选字段。

#### Q8：interrupt 是如何跨 HTTP 请求恢复的？

**参考答案：** 首次运行到审批节点时 `interrupt(payload)` 暂停，checkpointer 按 thread_id 保存图状态。API 返回 `pending_approval`。用户调用 resume 接口后，服务用同一个 run_id 作为 thread_id，并传入 `Command(resume=decision)`，图从中断点继续。

#### Q9：为什么 thread_id 直接使用 run_id？

**参考答案：** 一次 run 对应一次图执行，run_id 本身全局唯一，能够把 HTTP 运行记录、数据库审计和 LangGraph checkpoint 对齐。这样排障时可以用一个标识串起所有层。

#### Q10：Evidence Guard 为什么是单独节点，而不是写在 RAG 工具内部？

**参考答案：** 检索工具职责是适配上游并标准化结果，Evidence Guard 职责是执行业务策略。拆开后可以独立审计工具原始输出与策略决策，也能复用于知识问答和文档对比，未来替换检索器时不需要复制策略。

#### Q11：为什么检索错误不继续进入 Evidence Guard？

**参考答案：** 上游调用失败和“调用成功但证据不足”是两类状态。前者是 operational failure，应该保留错误码；后者是正常质量决策。如果把两者混在一起，Evidence Guard 会把超时覆盖成“无证据”，不利于重试策略和告警。

#### Q12：如何限制 Agent 无限循环？

**参考答案：** 当前图是显式有限 DAG，不存在自主循环；配置中仍保留 `max_agent_steps` 作为未来扩展边界。如果加入 ReAct 循环，应同时限制步数、总时长、token/成本、单工具次数，并在超限时写入明确终止原因。

### C. RAG 与 Evidence Guard

#### Q13：有 citation 就能证明答案正确吗？

**参考答案：** 不能。citation 可能与问题无关、只支持部分 claim、来源过期，甚至包含恶意指令。当前 Evidence Guard 只做第一层证据覆盖和相关度门禁，不等价于逐 claim 事实蕴含。面试时我会明确这个边界，下一步是句子级 claim 拆分和 NLI/LLM judge 对齐。

#### Q14：confidence 的权重为什么是 0.6/0.2/0.2？

**参考答案：** 这是可解释的初始启发式，强调相关度，同时考虑证据数量和来源多样性。它不是训练得到的最终参数。生产中应通过标注集、ROC/PR 曲线和业务错误成本校准，并按任务类型配置，而不是凭经验长期固定。

#### Q15：为什么分数允许 0—1 和 0—100 两种输入？

**参考答案：** 不同检索器可能返回小数或百分制。适配层统一归一化到 0—1，并拒绝负数、超过 100 和非数值输入。还专门测试了 0 分不能被 Python 的 `or` 误判成缺失值。

#### Q16：为什么引用数量要封顶？

**参考答案：** 如果数量线性累加，攻击者或错误检索器可以通过返回大量低质量片段抬高 confidence。封顶后，增加重复片段不会无限提高分数，也控制响应体和审计存储大小。

#### Q17：单来源知识问答为什么可以通过，文档对比却不行？

**参考答案：** 证据契约由任务语义决定。一份权威制度可以完整回答某个流程问题；但比较 A 与 B 至少需要分别获得两个来源，否则模型可能只根据一边内容猜测差异。

#### Q18：为什么缺少 relevance score 时不是直接拒答？

**参考答案：** 普通问答中，存在明确来源但元数据缺失，仍可能有业务价值，所以使用 `review_required` 并加警告。高风险或严格合规场景可以把策略改成缺分即拒答。关键是策略显式、可版本化，而不是隐含在 prompt 中。

#### Q19：如何检测知识冲突？

**参考答案：** 当前版本没有实现语义冲突检测，只能通过来源和分数判断证据质量。后续可先按实体、时间和版本对片段聚类，再让 NLI 模型判断 entailment/contradiction；出现冲突时返回双方证据和人工复核，而不是让生成模型自行选边。

#### Q20：如何处理知识时效性？

**参考答案：** 当前引用结构没有强制文档生效时间，因此无法可靠判断过期。应在摄入阶段增加版本、生效日期、失效日期、部门和权威等级元数据；检索时按有效期过滤，Evidence Passport 再输出 freshness。没有元数据时不能伪造“最新”。

#### Q21：提示注入检测为什么不只在用户输入上做？

**参考答案：** RAG 的主要风险之一是间接注入，恶意指令可能来自知识库文档、网页、邮件或图片 OCR。只过滤用户输入无法阻止外部内容改变模型行为，因此必须把所有外部内容视为不可信数据。

#### Q22：规则检测会不会误杀正常文档？

**参考答案：** 会。例如安全培训文档可能正好包含“忽略之前指令”的示例。当前策略选择 fail-closed，并通过 Evidence Passport 给出具体原因。生产中可以结合文档类型、引用上下文、专用分类器和管理员白名单降低误杀，但不能静默放行。

#### Q23：如何把 Evidence Guard 做成可灰度系统？

**参考答案：** 给策略加版本号，将阈值放入配置或策略中心；同一请求可以 shadow 运行新旧策略但只让旧策略决定输出，记录 verdict 差异；按用户或部门逐步放量，监控拒答率、人工通过率、投诉率和真实错误率后再切换。

### D. 并发、幂等与事务

#### Q24：为什么先 SELECT 再判断状态会有并发问题？

**参考答案：** 两个事务可能都在更新前读到 `pending_approval`。如果检查和状态变更不是一个原子操作，两个请求都能继续执行副作用。这是典型 TOCTOU 竞态。

#### Q25：CAS 为什么能解决双审批？

**参考答案：** 条件 UPDATE 由数据库在单条语句内判断旧状态并更新。第一个事务把状态改成 `approval_processing` 后，第二个事务的 WHERE 条件不再成立，受影响行数为 0，因此只有一个执行者获得资格。

#### Q26：使用 SELECT FOR UPDATE 可以吗？

**参考答案：** 可以，在一个事务中锁行、检查、更新也能串行化。但这里仅需状态抢占，条件 UPDATE 更短、锁持有时间更小。若审批还要读取多行并做复杂一致性判断，`SELECT ... FOR UPDATE` 会更合适。

#### Q27：数据库 CAS 能保证知识库问题反馈只提交一次吗？

**参考答案：** 当前反馈写入本地 `knowledge_feedbacks` 时，CAS 能保证正常并发审批只有一个请求进入 `submit_feedback`。但未来接入真实知识库维护系统后，如果下游成功、本地状态尚未更新时进程崩溃，仍可能产生不确定状态。端到端 exactly-once 需要下游接受反馈幂等键，或使用 outbox/inbox、外部反馈编号与对账机制。

#### Q28：为什么唯一约束比应用层先查更重要？

**参考答案：** 先查只能优化常见路径，无法关闭并发窗口。两个请求都可能先查不到。数据库唯一约束才是最终一致性防线，应用捕获冲突后回查已有记录，形成正确的并发幂等流程。

#### Q29：Idempotency-Key 为什么要按用户做作用域？

**参考答案：** 不同用户可能使用相同客户端 key。如果 key 全局唯一，会发生无关用户冲突甚至信息泄漏。使用 `(user_id, key_hash)` 唯一，保证每个用户拥有独立幂等命名空间。

#### Q30：为什么不保存原始 Idempotency-Key？

**参考答案：** key 可能包含业务编号或客户端内部信息。服务只需等值判断，不需要恢复原文，所以保存带用户作用域的哈希可以降低数据暴露风险。

#### Q31：请求指纹为什么必须包含 document_a/document_b？

**参考答案：** 同一个 query 在不同文档组合下语义不同。如果指纹只包含 query，同 key 更换对比文档时可能错误返回旧结果。指纹必须覆盖所有影响业务结果的语义输入。

#### Q32：幂等记录应该保留多久？

**参考答案：** 当前随 run 持久化，没有 TTL。生产设计要根据客户端最大重试窗口和审计要求决定，例如 24 小时或 7 天；过早删除会失去防重能力，永久保留会增加索引和隐私成本。归档前还要考虑旧 key 被重新使用的语义。

#### Q33：为什么 recovery_required 不自动恢复？

**参考答案：** 因为异常点可能在副作用前，也可能在副作用后。自动重试会把未知状态当成未执行，可能重复提交。先对账再决定是安全策略，而不是可用性缺陷。

#### Q34：如何实现自动对账？

**参考答案：** 给下游请求传业务幂等号并保存外部 resource_id；定时任务扫描 `recovery_required`，按幂等号查询下游。如果已存在则补写本地成功状态，不存在则安全重放；查询失败继续保留待对账并告警。

### E. FastAPI、数据库与数据模型

#### Q35：为什么使用 SQLAlchemy Async？

**参考答案：** FastAPI 路由和上游 HTTP 都是异步，数据库使用 asyncpg 可以避免同步数据库 I/O 阻塞事件循环。它不能让单条 SQL 变快，但能提升 I/O 等待期间的并发利用率。

#### Q36：`expire_on_commit=False` 有什么作用？

**参考答案：** commit 后 ORM 对象属性不会自动过期，API 可以继续序列化对象而不触发额外查询。代价是对象可能变旧，因此跨 Session 更新后需要显式 refresh 或 `populate_existing=True`。

#### Q37：之前审批恢复后为什么会读到旧状态？

**参考答案：** API Session 先加载了 pending run，LangGraph 在另一个 Session 中更新为 completed。原 Session 的 identity map 仍缓存旧对象，普通查询可能继续返回缓存实例。解决方法是在最终查询使用 `populate_existing=True` 强制覆盖已有对象。

#### Q38：为什么 tool_calls 单独建表？

**参考答案：** 单独建表便于按工具、状态、延迟和错误码聚合，也能保留每一步输入输出。全部塞入 run JSON 会让查询、索引和统计变困难。JSONB 仍适合保存各工具不同的半结构化 payload。

#### Q39：Evidence Passport 为什么暂时存在 tool_calls？

**参考答案：** 它本质上是 evidence_guard 的执行结果，复用 tool_calls 可以保留策略版本和历史，减少主表迁移。若未来经常按 verdict 查询，应把常用字段冗余到 run 或专用表，采用“审计完整记录 + 热字段索引”的设计。

#### Q40：`create_all` 有什么问题？

**参考答案：** 它适合空库演示，只会创建不存在的表，不会可靠地把旧 schema 演进到新 schema，也没有版本、回滚和审计。项目为已有数据库提供了 SQL migration，但生产应使用 Alembic 并在 CI/CD 中执行升级、回滚和兼容性检查。

### F. 安全、可靠性与可观测性

#### Q41：为什么其他用户访问资源返回 404 而不是 403？

**参考答案：** 404 可以避免向攻击者泄露资源是否存在。所有 run 和 session 查询都同时带资源 ID 与 user_id 条件，防止 IDOR。

#### Q42：开发认证最大的风险是什么？

**参考答案：** `X-Dev-User` 允许伪造身份，只能用于本地。风险不是“别人猜到 header”，而是生产误配置。v3 在 production 启动时强制要求关闭，不依赖 README 或人工记忆。

#### Q43：为什么 SecretStr 仍然不够？

**参考答案：** SecretStr 主要防止对象 repr 和普通日志误输出，不等于密钥管理。生产凭证仍应由 Secret Manager/KMS 注入，限制进程权限，定期轮换，并防止出现在异常、环境快照和构建层中。

#### Q44：如何防止工具越权？

**参考答案：** 模型只负责提出结构化草稿，真实工具在代码中校验用户、会话、动作类型和参数；高风险动作必须人工审批；凭证按最小权限配置。不能让模型直接拼任意 URL 或 SQL。

#### Q45：为什么错误信息要分 public 和 audit？

**参考答案：** 用户需要稳定、可行动的信息，运维需要完整堆栈。把原始异常直接返回既不稳定又可能泄露内网和凭证；全部隐藏又无法排障。因此 API 返回错误码和 Request ID，日志记录堆栈，数据库审计保存脱敏码。

#### Q46：何时应该重试？

**参考答案：** 只有当错误可恢复且操作幂等时才自动重试。GET 或带下游幂等键的请求可对 timeout、429、部分 5xx 进行指数退避；非幂等 POST 在状态未知时不能盲目重试。还要设置总时限和抖动，避免重试风暴。

#### Q47：健康检查为什么分 live 和 ready？

**参考答案：** liveness 表示进程是否存活，失败时容器可重启；readiness 表示当前是否能接流量，项目会检查数据库。不能把所有外部依赖都放进 liveness，否则上游短暂波动会导致本服务反复重启。

#### Q48：项目当前缺哪些可观测性？

**参考答案：** 当前已有 Request ID、业务 `tool_calls` 和 `run_events` 轻量 span，可查询 HTTP/LLM/工具策略/上下文/终止事件、版本指纹、fallback 与耗时；这解决了单次运行的解释和回放定位。但它仍不是完整 observability：还需导出 OpenTelemetry，补 Prometheus 指标、跨服务 trace、真实 token/cost、按错误码告警，以及 verdict、拒答率和 fallback rate 的时间序列漂移监控。

### G. 测试、评测与部署

#### Q49：为什么要保留 mock 模式？

**参考答案：** 它让开发、CI、面试演示不依赖真实飞书、RAGFlow 和知识库数据。关键是 mock 不能只返回成功，还要支持确定性故障注入，才能验证降级和安全分支。

#### Q50：单元测试和端到端评测分别验证什么？

**参考答案：** 单元测试验证纯策略边界，例如 0 分归一化、无引用拒答、提示注入隔离、key 哈希和生产配置；端到端评测覆盖 HTTP、认证、数据库、LangGraph checkpoint、工具轨迹、审批恢复和响应 schema。二者不能互相替代。

#### Q51：为什么评测集不能只有正常样本？

**参考答案：** 正常样本只能证明 happy path。生产事故往往来自空召回、低分、格式异常、并发重放和恶意输入。安全系统必须测“应该拒绝时是否拒绝”，否则准确率高也可能风险很大。

#### Q52：200 个样本达到 1.0 能证明模型好吗？

**参考答案：** 不能。200 条比 9 条 smoke 更能证明工程契约：它分成黄金、对抗和回放种子三层，覆盖完整工具序列、反馈定位门禁、审批副作用与七类故障注入；但当前仍运行在确定性 Mock 上，40 条回放也只是合成种子。它证明控制流和安全策略可回归，不证明真实 RAGFlow 的检索、生成和线上分布质量。真实评估还需要冻结人工黄金集、脱敏生产回放、逐 claim 忠实度、置信区间、漂移监控，并严格隔离训练集与测试集。

#### Q53：如何做并发测试？

**参考答案：** 集成脚本先创建一个待审批 run，再用 `asyncio.gather` 同时发送两个 resume。断言状态码排序为 `[200, 409]`，最终 run 为 completed，并且 tool_calls 中 `submit_feedback` 只出现一次。

#### Q54：Docker Compose 做了哪些隔离？

**参考答案：** 新项目使用独立 Compose project name 和 named volume，验证端口配置为 18080，避免影响已有占用 8000 的服务；API 和 PostgreSQL 都有健康检查。镜像以非 root 用户运行，不复制本地 `.env`。

#### Q55：如果上线 Kubernetes，还要补什么？

**参考答案：** 增加 Deployment/Service/Ingress、readiness/liveness/startup probes、Secret/ConfigMap、资源 requests/limits、HPA、PDB、滚动升级、数据库 migration Job、集中日志指标、NetworkPolicy 和安全上下文。checkpoint 与数据库连接池还要按副本数重新容量规划。

### H. 最早版源码审计追问

#### Q56：最早版的 `async def` 路由里调用同步 `requests` 和 `psycopg2`，实际会发生什么？

**参考答案：** `async def` 不会自动把内部同步调用变成非阻塞。同步 HTTP、流式 `iter_lines()` 和数据库取连接会占住事件循环线程；一个慢 RAG 请求就可能拖住同一 worker 上的其他协程，P99 延迟上升。短期可用 `run_in_threadpool` 隔离遗留同步代码，长期应换异步 HTTP 客户端和 asyncpg/SQLAlchemy Async，同时设置连接池、超时、取消和并发上限。不能只把函数签名改成 async 就宣称完成异步化。

#### Q57：`init_postgres_db()` 在连接池创建后提前 `return True`，为什么单测可能没发现、生产却危险？

**参考答案：** 如果开发数据库已有人手工建过表，后面的不可达 DDL 不会立刻暴露；但在全新环境、灾备恢复或临时测试库中，连接池虽然成功，业务表却不存在。这个缺陷说明“进程启动成功”不等于“依赖准备完成”。修复应把连接验证和 schema migration 拆开，用版本化 migration 保证可重复执行，并用空库启动测试验证，而不是在应用函数中拼接 DDL 后依赖代码路径偶然执行。

#### Q58：最早版流式链路的引用回退 bug 是什么？怎么设计测试才能抓住？

**参考答案：** 流式响应没有引用时，代码会再发一次非流式请求并得到 `reference`，但最终赋值仍使用旧的 `raw_reference`，所以客户端收到空引用；这还是一次额外的可能有副作用的 completion POST。测试应构造“流式只有文本、非流式含引用”的确定性 stub，断言最终引用非空、调用次数符合预期，并检查日志与响应一致。v3 更进一步把引用缺失交给 Evidence Guard，而不是静默把文本当成功。

#### Q59：健康检查返回 `{"status":"degraded"}` 但 HTTP 仍是 200，有什么后果？

**参考答案：** Docker、Kubernetes 和负载均衡器通常先看状态码，不会理解自定义 JSON；它们可能继续把流量发给无法访问数据库的实例。应区分 liveness、readiness 和 startup：进程活着时 liveness 成功，关键依赖不可用时 readiness 返回非 2xx，初始化较慢时用 startup probe 避免误重启。不要把所有外部依赖放进 liveness，否则会形成重启风暴。

#### Q60：发现最早版源码里有密钥默认值和通配 CORS，你会怎么处理？

**参考答案：** 第一动作不是只删代码，而是把已出现过的凭证视为可能泄漏并立即轮换，检查提交历史、镜像层和日志，再改为 Secret Manager/KMS 或部署环境注入；应用在 production 对空值、示例值和默认密码 fail-fast。CORS 使用明确域名列表，带凭证时不能使用通配来源。还应把诊断清缓存这类运维能力从“任一白名单用户可调用”升级为管理员角色或独立管理面，并记录审计事件。

### I. Execution Harness 最新考点

#### Q61：Agent Harness 和 Agent Framework 有什么区别？

**参考答案：** Framework 解决图、节点、消息和 checkpoint 如何编排；Harness 解决一次运行在什么预算、权限、版本和观测边界内执行。LangGraph 是编排框架，本项目的预算控制、Tool Contract、版本指纹、上下文治理、模型路由和 `run_events` 才构成 Harness。只会画 Agent 图但无法说明终止条件、工具授权和复现实验，仍是 Demo 思维。

#### Q62：为什么 `max_agent_steps` 配在环境变量里还不算真正实现预算？

**参考答案：** 配置只有被执行入口消费才有语义。本项目把它传给 LangGraph `recursion_limit`，总时长交给 `asyncio.timeout`，工具次数在每次调用前由 Tool Policy 判断，估算 Token 在图启动前判断；超限写入稳定 `termination_reason`。预算不仅要“停止”，还要能区分 timeout、step、tool-call 和 token 哪一种耗尽。

#### Q63：为什么 Trace 与 tool_calls 要分表？

**参考答案：** `tool_calls` 是业务审计，评测需要断言模型实际选择了哪些业务工具；Trace 还包含 HTTP、LLM、上下文、策略判断和终止事件。如果把所有 span 混进 tool_calls，会污染工具选择指标并模糊审计语义。`run_events` 是追加式控制面事件，后续可以映射为 OTel span；两者共享 run_id，但服务不同消费者。

#### Q64：上下文压缩怎样避免把 Prompt Injection 也总结进去？

**参考答案：** 不能把“先总结再信任”当安全方案。当前实现先按规则扫描历史轮次，疑似要求忽略指令、泄露系统提示词或密钥的内容先隔离，再做去重、轮次上限和字符预算；同时保留 `poisoned_turns_removed` 供审计。规则会漏检，所以生产还要做内容分类、来源信任分级，并把历史文本继续当数据而非系统指令。

#### Q65：Tool Contract 为什么不只是 Pydantic Schema？

**参考答案：** Schema 只回答参数长什么样，Contract 还要回答谁能调用、读还是写、风险等级、是否需要审批、超时、并发/次数、输出大小、外连目标和版本。本项目对未注册工具 deny-by-default，并把 `submit_feedback` 标为 high-risk；即使模型生成了合法 JSON，没有人工批准也会被策略层拒绝。

#### Q66：模型路由与 fallback 最容易犯什么错误？

**参考答案：** 一是高风险任务为省钱错误地下沉到弱模型；二是主模型失败后无限重试造成延迟和成本放大；三是 fallback 后不记录，指标看起来仍像主模型成功。本项目按任务和风险选路，连续失败开启熔断并降级到确定性规则，每次记录 selected model、route reason、fallback 和估算 Token。当前熔断仅进程内，生产多副本要使用共享状态或统一模型网关。

#### Q67：为什么不能只看配置中的模型名来证明接入成功？

**参考答案：** 配置只表达客户端意图，模型网关可能做别名映射、静默升级、降级或路由到其他部署。测试必须同时断言 HTTP 成功、响应中的 `model` 与预期一致、没有 fallback，并记录供应商 request id 和 usage，才能建立可追溯证据。本次真实请求的 `provider_model` 为 `qwen3.8-max`，所以结论来自服务端响应，不来自 README 声明。

#### Q68：真实模型把模糊反馈补成“内部知识库”，为什么不能只靠 Prompt 修复？

**参考答案：** Prompt 是软约束，模型可能为了完成任务而合理化缺失字段；如果这个字段决定是否进入审批或执行写操作，就构成权限提升。正确边界是让模型负责语义抽取，让确定性代码基于原始用户文本判断目标是否明确。原文不满足门禁时，即使模型生成了看似具体的对象，也必须重置为 unresolved 并返回 `needs_input`。

#### Q69：为什么 Qwen 调用可以受控重试，而 RAGFlow completion 和反馈提交不能照搬？

**参考答案：** 当前模型调用是无副作用推理，请求失败后可在总预算内对网络错误、429、500、502、503、504 最多重试一次，并记录 `retry_count`；模型返回业务性 4xx 不重试。RAGFlow completion 可能创建上游状态，反馈提交是写副作用，如果下游没有幂等键，超时意味着“结果未知”，盲目重试可能重复写入，所以应进入恢复或对账流程。重试策略由副作用和幂等语义决定，而不是由 HTTP 客户端统一决定。

## 6. 反向追问与主动暴露边界

面试官往往更看重候选人是否知道系统没有解决什么。可以主动说明：

1. Evidence Guard 当前是确定性第一层门禁，不是逐 claim 事实蕴含；
2. Prompt Injection 规则无法覆盖所有编码、语义和多模态攻击；
3. RAGFlow 的 completion 没有下游幂等 token，因此本服务不做盲目自动重试；
4. 文档对比尚未真正实现双路 document filter 召回；
5. CAS 解决本服务并发恢复，但跨系统 exactly-once 仍需下游幂等和对账；
6. 当前 200 条是版本化工程回归集，其中 replay 是合成种子，不是统计意义上的真实模型或线上分布评测；
7. 生产 schema 应从 SQL migration 进一步升级到 Alembic；
8. 当前 Trace 是内部轻量实现，还缺 OTel/Prometheus 导出、跨副本熔断、真实 token/cost 和自动对账 Worker。

这类回答不会降低项目价值，反而能证明你理解 Demo、MVP 和生产系统之间的距离。

## 7. 简历描述建议

### 7.1 两行精简版

基于 FastAPI、LangGraph、PostgreSQL 与 RAGFlow 构建 EvidenceOps 企业知识 Agent，引入 Evidence Passport 对引用覆盖、相关度、来源多样性及间接 Prompt Injection 进行服务时门禁，实现高风险弱证据拒答和可解释修复建议。

设计知识反馈定位门禁、用户域 Idempotency-Key、请求指纹、唯一约束和审批 CAS 状态抢占，解决空泛反馈、请求重放与并发重复提交；增加运行预算、轻量 Trace、版本指纹、上下文治理、Tool Contract 和模型路由/熔断；构建 90 条黄金集、70 条对抗集和 40 条回放种子的三层评测体系，以 34 个单元测试、200 条端到端用例、Qwen3.8-Max 真实 Harness 及双并发审批测试验证关键路径。

### 7.2 STAR 讲法

**S：** 最早版单文件 RAG 服务已经打通飞书认证、白名单、问答、会话、反馈和日志，但前端依赖 RAGFlow ID、同步 I/O 阻塞异步路由、回答缺少证据门禁，反馈失败还可能返回假成功。

**T：** 保留已有业务闭环，把固定 RAG 代理升级为可路由、可暂停恢复、可审计的 Agent，并补齐证据安全、副作用一致性和可回归评测。

**A：** 将单文件拆分为配置、客户端、仓储、服务、状态图和 API；用本地 UUID 隔离上游会话；增加 candidate/final answer 信任边界与 Evidence Passport；对无证据、低分、高风险和提示注入实施分层策略；增加幂等哈希、全参数指纹、数据库唯一约束、HITL + CAS 抢占和 recovery_required 对账状态；补充稳定错误契约、生产 fail-fast 和混沌用例。

**R：** Docker 生产镜像构建和健康检查通过，34 个单元测试、90/90 黄金集、70/70 对抗集和 40/40 回放种子通过，9 项质量指标为 1.0；Qwen3.8-Max 真实 Harness 8/8 通过且 10 次模型路由无 fallback；模糊反馈不会进入审批，并发审批只提交一次反馈；每个 run 可查询 Harness 预算、版本、Trace、上下文与模型路由。明确说明回放层是合成种子，不把 Mock 回归包装成真实线上模型质量。

## 8. 代码索引

| 能力 | 主要位置 |
| --- | --- |
| 最早版比较基线 | 用户原始 `app.zip` 中的 `app/app.py`（SHA-256：`013ff66b...74ab365`） |
| Evidence Guard 与策略 | `app/services/evidence.py` |
| Agent 图与门禁节点 | `app/agent/graph.py` |
| Agent 状态 | `app/agent/state.py` |
| 幂等与审批 API | `app/api/routes/agent.py` |
| 请求键校验与指纹 | `app/services/request_keys.py` |
| 原子状态抢占 | `app/repositories/core.py` |
| 幂等字段与唯一约束 | `app/db/models.py` |
| 稳定错误契约 | `app/core/errors.py` |
| RAGFlow 错误映射与故障注入 | `app/clients/ragflow.py` |
| 生产配置校验 | `app/core/config.py` |
| Evidence API Schema | `app/schemas/agent.py` |
| 三层评测与版本清单 | `scripts/evaluate.py`、`evaluation/manifest.json`、`evaluation/{gold,adversarial,replay_seed}.jsonl` |
| 回放脱敏与人工复标门禁 | `scripts/prepare_replay_dataset.py`、`EVALUATION_GUIDE.md` |
| 并发与幂等集成测试 | `scripts/test_resilience.py` |
| Execution Harness 与版本指纹 | `app/services/harness.py`、`app/core/versions.py` |
| 上下文治理 | `app/services/context_manager.py` |
| Tool Contract 与权限策略 | `app/services/tool_policy.py` |
| 模型路由与熔断 | `app/services/model_routing.py`、`app/clients/llm.py` |
| Harness 单测与全量报告 | `tests/test_harness_features.py`、`evaluation/reports/layered-evaluation-harness-v3.json` |
| Qwen3.8-Max 真实 Harness | `scripts/test_qwen_harness.py`、`evaluation/reports/qwen3.8-max-harness-report.json`、`evaluation/reports/QWEN3.8_MAX_INTEGRATION_REPORT.md` |
| 数据库升级 | `migrations/001_evidence_agent_v3.sql`、`migrations/002_agent_harness.sql` |

## 9. 参考资料

1. OWASP GenAI Security Project, LLM01:2025 Prompt Injection（https://genai.owasp.org/llmrisk/llm01-prompt-injection/）：RAG 不能消除提示注入，需要输入/输出过滤、最小权限、高风险人工审批和对抗测试。
2. OWASP RAG Security Cheat Sheet（https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html）：覆盖文档摄入、向量存储、检索、生成、输出和下游 Agent 工具的完整安全链。
3. Google Cloud Check Grounding（https://cloud.google.com/generative-ai-app-builder/docs/check-grounding）：通过 support score、claim/citation 和阈值在推理时筛除低可信回答。
4. Google Cloud RAG Reference Architecture（https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-vertex-ai）：将 serving subsystem 与 quality evaluation subsystem 分离并建立质量闭环。
5. PostgreSQL Documentation：条件更新、唯一约束和事务语义是并发一致性的最终防线。
6. 阿里云百炼模型列表与 OpenAI 兼容接口文档：用于核对模型标识、Base URL 和兼容请求格式；实际执行模型仍以响应中的 `model` 和 Harness 事件为准。

## 10. 最后一分钟复习清单

- 能否用一句话说清项目区别？
- 能否解释 candidate answer 为什么不能直接输出？
- 能否画出 Evidence Guard 的四个 verdict？
- 能否说明 confidence 不是事实正确率？
- 能否解释文档对比为何至少需要两个来源？
- 能否说清 Prompt Injection 的间接来源？
- 能否写出审批 CAS SQL？
- 能否解释唯一约束为何比先查重要？
- 能否区分 Request ID 与 Idempotency-Key？
- 能否解释 recovery_required 为什么不自动重试？
- 能否说清 SQLAlchemy identity map 的旧状态问题？
- 能否主动承认当前项目的 8 个边界？
- 能否给出 34 单测、200 条三层评测、Qwen 真实 Harness 8/8、9 项指标、模糊反馈不审批、200+409、一次提交这些验证证据？
- 能否区分 Framework、Runtime 和 Harness，并说明四种预算的实际终止点？
- 能否解释为什么 `run_events` 不混入 `tool_calls`？
- 能否说明 Tool Contract 不只是 JSON Schema，以及模型熔断为何要可审计？
