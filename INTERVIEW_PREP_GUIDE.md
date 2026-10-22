# EvidenceOps Agent v3 面试项目讲解与备战文档

日期：2026-08-05  
项目路径：`C:\Users\HP\vsocde\app_v2\agent_app_evidence_v3`  
对比旧项目：`C:\Users\HP\Desktop\app.zip`，解压分析目录为 `C:\Users\HP\vsocde\_uploaded_app_compare\app`

## 1. 一句话介绍这个项目

这是一个从旧版单文件 RAGFlow 代理服务演进出来的企业知识 Agent。新版使用 FastAPI、LangGraph、PostgreSQL、SQLAlchemy Async、RAGFlow 和可选 LLM，把原来“前端直接传 RAGFlow 会话并查询知识库”的模式升级为“后端管理本地会话、Agent 自动识别意图、调用工具、记录运行轨迹，并对知识库问题反馈进行定位校验和人工确认”的架构。

面试中可以这样开场：

> 这个项目的核心价值是把一个可用但耦合严重的 RAG 代理服务，改造成一个可扩展、可审计、可测试的企业知识 Agent 服务。新版把认证、会话、RAGFlow 客户端、Agent 工作流、数据库仓储和 API 路由拆开，并引入 LangGraph 的 checkpoint 与 interrupt，实现了可恢复的人机协同审批流程。

## 2. 新旧版本总体差异

| 维度 | 最早版 app 项目 | EvidenceOps Agent v3 |
| --- | --- | --- |
| 代码组织 | 单个 `app.py` 承载认证、接口、RAGFlow、数据库、日志、定时任务 | 按职责拆分为 `api`、`agent`、`clients`、`db`、`repositories`、`schemas`、`services` |
| API 风格 | `/api/rag/*` 为主，偏 RAGFlow 代理层 | `/api/v2/*` 为主，偏 Agent 能力层 |
| 会话模型 | 前端必须传真实 `chat_id` 与 RAGFlow `session_id` | 前端只拿本地 session UUID，真实 RAGFlow session 由后端映射 |
| 智能流程 | 基本是请求转发和结果保存 | LangGraph 显式工作流：意图识别、工具路由、审批、执行、收尾 |
| 工具能力 | RAG 查询、历史、反馈、消息删除 | 知识问答、文档对比、知识反馈草稿、定位门禁、人工确认、反馈提交 |
| 认证方式 | 依赖飞书 Bearer token，带 token 缓存和黑名单 | 生产支持飞书，开发支持 `X-Dev-User` 快速测试 |
| 数据库访问 | psycopg2 同步连接池 | SQLAlchemy Async + asyncpg，仓储层隔离数据访问 |
| 数据表设计 | 偏反馈和交互日志 | 用户、会话、运行记录、工具调用、知识反馈、LangGraph checkpoint |
| 审计能力 | 有请求和交互日志，但和业务流程混杂 | `agent_runs` 与 `tool_calls` 明确记录每次运行、工具输入输出、耗时、错误 |
| 上游依赖 | 强依赖真实 RAGFlow 和飞书 | `RAG_MOCK_MODE=true` 时可离线评测 |
| 安全性 | 源码和 compose 中出现真实密钥、数据库密码、RAGFlow Key | `.env.example` 留空敏感项，README 明确要求生产环境关闭开发认证 |
| 测试能力 | 没有独立评测脚本 | 有 90 条黄金集、70 条对抗集、40 条回放种子和版本化评测器 |
| 部署 | Python 3.10，`uvicorn app:app --workers 2` | Python 3.11，`uvicorn app.main:app --workers 1`，依赖 PostgreSQL checkpoint |

## 3. 为什么要做这次升级

旧版不是不能用，它的问题是“能跑，但难维护、难扩展、难审计”。

主要痛点：

- 单文件过大，接口、数据库、认证、业务逻辑混在一起，改一个功能容易影响其他路径。
- 前端需要知道真实 RAGFlow `chat_id` 和 `session_id`，暴露了上游系统细节。
- 缺少清晰的 Agent 运行轨迹，不容易回答“这次回答用了哪个工具、输入是什么、失败在哪里”。
- 高风险操作没有标准化的人机审批机制。
- 旧项目中存在真实密钥和数据库密码，安全风险很高。
- 缺少 mock 模式和离线评测，不方便面试、演示、CI 或本地开发。

新版的目标：

- 把 RAGFlow 包装成后端内部能力，而不是让前端直接操作。
- 把“问答、对比、知识库问题反馈”变成可路由、可追踪的 Agent 工具。
- 让已定位到具体知识点的反馈先暂停等待人工确认，再恢复提交。
- 通过本地 session UUID 和 user_id 做权限隔离。
- 通过 mock 模式让系统不依赖真实上游也能完整演示。

## 4. 新版目录结构应该怎么讲

| 目录/文件 | 作用 | 面试讲法 |
| --- | --- | --- |
| `app/main.py` | FastAPI 入口、生命周期、客户端初始化、路由注册、中间件 | “这里负责应用启动和全局资源管理，不放具体业务逻辑。” |
| `app/api/routes` | HTTP 接口层 | “只做参数接收、认证依赖、调用服务/仓储、返回 schema。” |
| `app/api/deps.py` | 认证依赖 | “统一处理开发认证和飞书认证，业务接口不用重复写认证代码。” |
| `app/agent/graph.py` | LangGraph 工作流 | “核心 Agent 编排层，定义节点、边、条件路由和审批中断。” |
| `app/agent/state.py` | Agent 状态结构 | “规定图中节点共享哪些字段，降低隐式依赖。” |
| `app/clients` | 外部系统客户端 | “把 RAGFlow、飞书、LLM 调用封装起来，方便 mock 和替换。” |
| `app/db/models.py` | SQLAlchemy ORM 模型 | “定义用户、会话、运行、工具调用、知识反馈等持久化实体。” |
| `app/repositories/core.py` | 仓储层 | “隔离数据库查询和更新，让 API 和 Agent 不直接拼 SQL。” |
| `app/schemas` | Pydantic 请求/响应模型 | “做接口输入校验和输出结构定义。” |
| `scripts/evaluate.py` | 三层离线评测器 | “验证完整工具序列、工作流、证据门禁、反馈定位和副作用安全。” |
| `evaluation/manifest.json`、三层 JSONL | 评测资产 | “用版本与 SHA-256 固定 200 条样本，按 layer/tag 报告。” |

## 5. 新版每个关键技术点

### 5.1 FastAPI 应用生命周期

位置：`app/main.py`

新版使用 `lifespan` 初始化全局资源：

- 初始化数据库表。
- 创建共享的 `httpx.AsyncClient`。
- 初始化 Feishu、RAGFlow、LLM 客户端。
- 加载白名单 Excel。
- 初始化 PostgreSQL 版 LangGraph checkpointer。
- 构建并挂载 `agent_graph`。
- 应用关闭时释放 checkpointer、HTTP client 和数据库连接。

面试要点：

- `lifespan` 比散落在模块顶层初始化更可控。
- HTTP client 复用连接池，避免每个请求重复创建连接。
- checkpointer 是 Agent 可恢复执行的基础。
- 资源关闭很重要，否则会有连接泄漏。

可能问题：

**问：为什么把客户端放到 `app.state`？**  
答：这些客户端是应用级共享资源，生命周期和服务一致。放到 `app.state` 后，路由可以通过 `request.app.state.ragflow_client` 等方式复用，不需要每次请求重新初始化。

**问：为什么使用一个共享 `httpx.AsyncClient`？**  
答：可以复用连接池，减少 TCP/TLS 握手开销，也方便统一设置超时和最大连接数。

### 5.2 请求 ID 与日志追踪

位置：`app/main.py`

中间件会读取或生成 `X-Request-ID`，写入 `request.state.request_id`，并在响应头返回，同时记录方法、路径、状态码和耗时。

面试要点：

- 请求 ID 用于串联前端、API、日志和运行记录。
- 出问题时可以用 request_id 定位单次请求。
- 性能指标的最小闭环是 status + latency。

可能问题：

**问：为什么需要 `X-Request-ID`？**  
答：在多服务调用链中，单靠时间和用户很难定位问题。request_id 是一次请求的唯一追踪标识，可以把 API 日志、Agent run、工具调用和上游调用串起来。

### 5.3 配置管理

位置：`app/core/config.py`

新版使用 `pydantic-settings` 从 `.env` 和环境变量读取配置。

关键配置：

- `APP_ENV`
- `DATABASE_URL`
- `CHECKPOINT_DATABASE_URL`
- `DEV_AUTH_ENABLED`
- `RAG_MOCK_MODE`
- `RAG_BASE_URL`
- `RAG_API_KEY`
- `RAG_CHAT_ID`
- `LLM_BASE_URL`
- `LLM_MODEL`

面试要点：

- 配置与代码分离。
- 敏感字段使用 `SecretStr`，避免日志直接泄漏。
- 开发环境允许 mock 和 dev auth，生产环境必须关闭。

可能问题：

**问：生产环境最重要的配置检查是什么？**  
答：`DEV_AUTH_ENABLED=false`，`RAG_MOCK_MODE=false`，真实飞书凭证、RAGFlow Key、白名单和数据库密码必须通过安全渠道注入，不能写死在源码或 compose 文件中。

### 5.4 认证与用户隔离

位置：`app/api/deps.py`

新版支持两种认证：

- 开发模式：请求头 `X-Dev-User: demo-user`。
- 生产模式：`Authorization: Bearer <feishu_token>`，调用飞书获取用户信息，再检查白名单。

用户进入系统后会 upsert 到 `users` 表，业务查询都基于本地 `user_id` 做隔离。

面试要点：

- 开发认证用于本地演示，不应该进入生产。
- 生产认证不只校验 token，还要做白名单准入。
- 会话和运行记录都绑定 `user_id`。
- 其他用户读取运行记录会返回 404，避免泄漏资源存在性。

可能问题：

**问：为什么其他用户访问别人的 run 返回 404，而不是 403？**  
答：404 可以避免告诉攻击者“这个资源存在但你没权限”。这是一种常见的资源隔离策略。

### 5.5 本地会话映射

位置：`app/api/routes/sessions.py`、`app/db/models.py`

旧版要求前端传：

- `chat_id`
- RAGFlow `session_id`

新版改为：

- 前端只保存本地 `agent_sessions.id`
- 后端保存 `ragflow_chat_id` 与 `ragflow_session_id`
- 每次 Agent 运行时，后端根据本地 session 找真实 RAGFlow session

面试要点：

- 前端不再暴露和依赖 RAGFlow 内部细节。
- 以后更换 RAGFlow、增加多知识库路由，不需要大改前端。
- 本地 session 可以绑定用户、状态、创建时间，用于权限和审计。

可能问题：

**问：为什么不直接沿用 RAGFlow session_id？**  
答：直接暴露上游 session 会让前端和 RAGFlow 强耦合，也不利于权限隔离。使用本地 UUID 可以把内部上游实现隐藏起来，并给会话加上用户归属和状态管理。

### 5.6 LangGraph Agent 工作流

位置：`app/agent/graph.py`

核心流程：

1. `classify_intent`：识别用户意图。
2. 根据意图路由：
   - `knowledge_qa` -> `knowledge_tool`
   - `document_compare` -> `compare_documents_tool`
   - `knowledge_feedback` -> `prepare_feedback_tool`
   - `unsupported` -> `unsupported`
3. 知识问答和文档对比直接进入 `finalize`。
4. 知识反馈先生成草稿并校验是否定位到具体知识点；模糊反馈直接要求补充信息。
5. `approval` 节点调用 `interrupt()` 暂停。
6. 用户调用 `resume` 后恢复。
7. 目标明确且同意则 `submit_feedback_tool`，拒绝则 `rejected_feedback`。
8. 最后 `finalize` 写入最终状态。

面试要点：

- LangGraph 适合有状态、多步骤、可恢复的 Agent。
- 每个节点只负责一个动作，降低复杂度。
- 条件边让工具选择显式可解释。
- `interrupt()` 是人机协同审批的关键。
- checkpointer 让暂停后的状态可以持久化，后续恢复。

可能问题：

**问：为什么用 LangGraph，而不是普通 if else？**  
答：简单 demo 可以用 if else，但一旦有多步骤状态、人工审批、失败恢复、工具调用轨迹和未来扩展，图结构更清晰。LangGraph 的节点、边和 checkpoint 让流程可恢复、可审计、可扩展。

**问：`interrupt()` 解决了什么问题？**  
答：它让 Agent 在高风险动作前暂停，把待审批 payload 返回给前端。用户审批后再通过 `resume` 把决策传回图中继续执行，避免模型或系统直接执行敏感操作。

### 5.7 意图识别与 LLM fallback

位置：`app/clients/llm.py`

新版支持两种方式：

- 配置了 OpenAI-compatible LLM 时，用模型分类和生成知识反馈草稿。
- 没配置 LLM 时，用确定性规则 fallback。

意图类型：

- `knowledge_qa`
- `document_compare`
- `knowledge_feedback`
- `unsupported`

面试要点：

- fallback 能保证本地离线可测。
- 规则不是最终能力，但适合 MVP 和测试闭环。
- LLM 输出只接受固定 JSON 和固定枚举，降低不可控性。
- 知识反馈 `issue_type` 只允许错误、过期、不完整、歧义和通用内容问题；`priority` 只允许 `low`、`normal`、`high`。
- `target_status` 为 `unresolved` 时不进入审批，工具状态记为 `needs_input`。

可能问题：

**问：为什么 LLM 调用失败后不直接报错？**  
答：意图分类和知识反馈草稿属于可降级能力。为了保证系统可用性，失败时使用确定性规则 fallback；规则会识别问题类型、食品安全高优先级和具体知识点。这样演示和测试不依赖外部模型服务。

### 5.8 RAGFlow 客户端与 mock 模式

位置：`app/clients/ragflow.py`

RAGFlow 客户端封装：

- 创建会话
- 删除会话
- 非流式问答
- 提取 answer 和 reference

`RAG_MOCK_MODE=true` 时：

- 不调用真实 RAGFlow。
- 创建 mock session。
- 返回 mock answer 和 mock citation。

面试要点：

- 客户端封装让 API 层不关心 RAGFlow 的 URL、headers、返回格式。
- mock 模式让测试不依赖外部网络和真实知识库。
- 引用信息会转换成统一 citations，便于前端展示和审计。

可能问题：

**问：为什么要保留 mock 模式？**  
答：为了本地开发、面试演示、自动评测和 CI。真实 RAGFlow、飞书或知识库不可用时，系统仍能验证 Agent 流程和数据链路。

### 5.9 数据模型设计

位置：`app/db/models.py`

核心表：

- `users`：本地用户，映射飞书用户或开发用户。
- `agent_sessions`：本地会话，映射 RAGFlow session。
- `agent_runs`：一次 Agent 运行，记录 query、intent、status、answer、citations、pending_approval、error、latency。
- `tool_calls`：每次工具调用，记录工具名、输入、输出、状态、耗时、错误。
- `knowledge_feedbacks`：经用户确认后提交的知识库问题反馈。

面试要点：

- `agent_runs` 是一次用户请求的主记录。
- `tool_calls` 是可审计性关键，可以解释 Agent 做了什么。
- JSONB 用于保存 citations、pending_approval、tool input/output 等半结构化数据。
- status 字段让前端和评测脚本判断状态机进度。

可能问题：

**问：为什么工具调用单独建表，而不是塞进 run 的 JSON？**  
答：单独建表更方便按工具名、状态、时间查询和分析，也便于未来做统计、告警、失败重试和成本追踪。JSONB 可以保留灵活输入输出，但表结构仍能提供索引和关系。

### 5.10 SQLAlchemy Async 与仓储层

位置：`app/db/session.py`、`app/repositories/core.py`

新版使用：

- `create_async_engine`
- `async_sessionmaker`
- `AsyncSession`
- repository 方法封装查询和更新

面试要点：

- FastAPI 是 async 风格，数据库也用 async，避免同步 DB 阻塞事件循环。
- `pool_pre_ping=True` 可以减少连接失效导致的请求失败。
- `expire_on_commit=False` 避免 commit 后对象属性自动过期导致额外懒加载问题。
- 仓储层让业务逻辑和 ORM 细节分离。

本次修复点：

- 问题：审批 `resume` 后，LangGraph 内部用新的 DB session 更新了 run，但 API 当前 session 已经缓存了旧对象，导致返回旧 `pending_approval`。
- 修复：`RunRepository.get_owned` 增加 `refresh` 参数，使用 `execution_options(populate_existing=True)` 强制覆盖 identity map 中的旧对象。
- 结果：`resume` 响应直接返回 `completed`。

可能问题：

**问：这次 bug 的根因是什么？**  
答：SQLAlchemy Session 有 identity map。接口开始时先查询了 run，得到 `pending_approval` 状态；Graph 恢复执行时在另一个 session 中把 run 更新为 `completed`；接口最后复用原 session 查询时拿到了缓存对象，所以返回旧状态。用 `populate_existing=True` 可以强制从数据库刷新已有对象。

### 5.11 人工审批的安全意义

知识反馈提交会改变维护队列和数据库状态，所以不能让模型直接执行。

新版处理方式：

- Agent 先生成草稿。
- run 状态变为 `pending_approval`。
- 返回审批 payload。
- 用户明确同意后才执行。
- 拒绝则进入取消分支。

面试要点：

- 查询类工具是低风险。
- 提交知识反馈、发消息、改数据等是有副作用的操作。
- 高风险动作需要 human-in-the-loop。
- 审批 payload 应该包含 action、risk_level、draft、message。

可能问题：

**问：为什么知识反馈要确认，知识问答不用确认？**  
答：知识问答主要是信息展示；知识反馈会写入维护队列，可能触发知识修订，因此先定位具体知识点，再让员工确认结构化草稿。模糊反馈不会进入审批。

### 5.12 Docker Compose 部署

位置：`docker-compose.yml`、`Dockerfile`

服务：

- `api`：FastAPI 应用。
- `postgres`：PostgreSQL 16。

关键点：

- `api` 依赖 postgres healthcheck。
- `.env` 注入配置。
- `data` 目录只读挂载给白名单文件。
- PostgreSQL 使用 named volume 持久化。

面试要点：

- Compose 让项目一条命令拉起完整依赖。
- healthcheck 避免 API 在 DB 未就绪时启动失败。
- 生产环境需要更规范的迁移、密钥管理和日志采集。

可能问题：

**问：为什么 API worker 设置为 1？**  
答：当前 MVP 有 LangGraph checkpoint 和本地初始化资源，单 worker 更容易保证演示稳定。生产可以扩展多实例，但需要确认 checkpoint、数据库连接池、幂等性和并发审批行为。

## 6. 每一项测试的目的

### 6.1 `/health/live`

目的：验证 API 进程是否存活。

能说明：

- FastAPI 服务已启动。
- 路由可访问。
- 不验证数据库或上游依赖。

面试讲法：

> live 是进程级健康检查，适合容器编排判断服务是否需要重启。

### 6.2 `/health/ready`

目的：验证服务是否准备好接收业务请求。

能说明：

- API 可访问。
- 数据库连接正常。
- `SELECT 1` 成功。

面试讲法：

> ready 比 live 更严格，适合负载均衡判断是否把流量打进来。

### 6.3 创建会话

接口：`POST /api/v2/sessions`

目的：

- 验证 `X-Dev-User` 认证能 upsert 用户。
- 验证 RAGFlow mock session 创建成功。
- 验证本地 session 能落库。
- 验证前端只需要本地 session UUID。

### 6.4 查询会话列表

接口：`GET /api/v2/sessions`

目的：

- 验证用户隔离。
- 验证只返回当前用户 active session。
- 验证创建后的会话可查询。

### 6.5 知识问答

接口：`POST /api/v2/agent/runs`

输入示例：`公司的采购审批流程是什么？`

目的：

- 验证意图识别为 `knowledge_qa`。
- 验证路由到 `knowledge_search` 工具。
- 验证 RAGFlow mock chat。
- 验证 citations 提取。
- 验证 run 最终状态为 `completed`。
- 验证 tool_calls 记录工具输入输出。

### 6.6 文档对比

输入示例：`比较2025版和2026版采购制度的差异`

目的：

- 验证意图识别为 `document_compare`。
- 验证路由到 `compare_documents`。
- 验证 prompt 增强逻辑，把文档 A/B 放进查询。
- 验证结果仍基于 RAGFlow 返回。

### 6.7 知识反馈确认提交

输入示例：`食品添加剂标准里山梨酸钾用量说明不对，请反馈给知识库维护人员`

目的：

- 验证意图识别为 `knowledge_feedback`。
- 验证先生成 `create_feedback_draft`，并得到明确的 `knowledge_item`。
- 验证 run 进入 `pending_approval`。
- 验证 `resume approved=true` 能恢复 LangGraph。
- 验证只执行一次 `submit_feedback`。
- 验证最终状态 `completed`。
- 验证本次修复后 `resume` 响应直接返回最终状态。

### 6.8 知识反馈拒绝提交

目的：

- 验证 `resume approved=false` 进入拒绝分支。
- 验证不会执行 `submit_feedback`。

### 6.9 模糊知识反馈补充定位

输入示例：`知识库有问题，请提交反馈`

- 验证意图仍为 `knowledge_feedback`；
- 验证草稿 `target_status=unresolved`、工具状态为 `needs_input`；
- 验证不产生 `pending_approval`，也不执行 `submit_feedback`；
- 验证返回“请具体说明哪一条知识存在问题”的补充提示。
- 验证最终状态也是 `completed`，但 answer 是取消说明。

### 6.9 其他用户访问 run

目的：

- 验证 run 与 user_id 绑定。
- 验证 `other-user` 无法读取 `demo-user` 的运行记录。
- 预期返回 404。

### 6.10 无认证访问

目的：

- 验证接口必须有认证身份。
- 不带 `X-Dev-User` 或 Bearer token 时返回 401。

### 6.11 离线评测脚本

脚本：`scripts/evaluate.py`

目的：

- 批量运行 90 条黄金集、70 条对抗集和 40 条回放种子集；
- 校验数据集版本、数量和 SHA-256，防止静默换题；
- 检查完整工具序列、审批前后状态、禁止工具和反馈提交次数；
- 验证 Evidence verdict、风险标签、安全拒答和知识反馈定位门禁；
- 按 layer/tag 输出通过率以及端到端 p50/p95 延迟；
- 作为回归测试，防止修一个功能破坏另一个功能。

当前修复后结果：

- 34/34 单元测试通过；
- Gold 90/90、Adversarial 70/70、Replay Seed 40/40；
- 200/200 总体 case 通过，9 项质量指标均为 1.0；
- 原 9/9 smoke 集兼容；
- 并发审批为 `[200, 409]`，反馈只提交一次。

边界：Replay Seed 是无 PII 合成种子，不是生产日志。真实回放必须先通过
`scripts/prepare_replay_dataset.py` 做字段最小化、常见 PII/凭证遮盖和 trace hash，随后由人工复标；
`review_status` 未批准的生产标签会被评测器拒绝。

### 6.12 Execution Harness 六项能力

- 运行预算：步数、工具次数、总时限、估算 Token 均有实际执行点和终止原因；
- Trace：`run_events` 记录 HTTP、LLM、策略、上下文与终止事件，不污染业务 `tool_calls`；
- 版本指纹：每个 run 固化 app/prompt/tool schema/policy/dataset 版本；
- 上下文工程：有限轮次、字符预算、去重、注入隔离和引用截断；
- Tool Contract：未知工具默认拒绝，高风险写工具必须审批，并限制角色、超时和输出；
- 模型路由：按任务和风险选模，连续失败熔断并降级到确定性规则。

面试时要主动说明：这是单体服务内的最小闭环，仍需 OTel/Prometheus、真实 token/cost、跨副本熔断、
真实模型 shadow traffic 与容器级权限沙箱才能达到完整生产 Harness。

## 7. 作为面试项目必须掌握的知识点

### 7.1 架构层面

你必须能讲清楚：

- 为什么旧版单文件要拆分。
- 每个目录负责什么。
- API 层、Agent 层、客户端层、仓储层的边界。
- 前端为什么只拿本地 session UUID。
- 如何保证用户隔离。
- 如何记录 Agent 的每一步。

### 7.2 Agent 层面

你必须能讲清楚：

- LangGraph 的 node、edge、state、checkpoint。
- 意图分类如何决定工具路由。
- `interrupt()` 如何暂停。
- `resume` 如何恢复。
- 为什么知识反馈必须先定位具体知识点并由用户确认。
- 如果新增“发送飞书消息”工具，应该怎么加。

新增工具的标准步骤：

1. 在 `AgentState` 增加必要字段。
2. 在 `graph.py` 增加工具节点。
3. 在意图分类中增加或细化 intent。
4. 增加条件边路由。
5. 写入 `tool_calls`。
6. 如果有副作用，加审批节点。
7. 增加评测用例。

### 7.3 数据库层面

你必须能讲清楚：

- `users`、`agent_sessions`、`agent_runs`、`tool_calls`、`knowledge_feedbacks` 的关系。
- 为什么用 PostgreSQL。
- 为什么 JSONB 适合保存工具输入输出和 citations。
- async session 的生命周期。
- SQLAlchemy identity map 是什么。
- 本次 `populate_existing=True` 修复了什么。

### 7.4 安全层面

你必须能讲清楚：

- 旧项目密钥硬编码的风险。
- 生产环境为什么必须关闭 `DEV_AUTH_ENABLED`。
- 白名单的作用。
- 为什么其他用户读不到别人的 session/run。
- 为什么有副作用操作要审批。
- 为什么不把 RAGFlow session 暴露给前端。

### 7.5 测试层面

你必须能讲清楚：

- live 和 ready 的区别。
- 单接口测试和离线评测脚本的区别。
- 为什么要测试同意和拒绝两条审批路径。
- 为什么要测无认证和跨用户访问。
- 为什么 mock 模式可以保证稳定回归。

### 7.6 工程不足与改进方向

你必须主动承认并给出改进：

- 当前使用 `create_all`，生产应改 Alembic migration。
- 当前 RAGFlow 是非流式，后续可以支持 SSE token streaming。
- 当前意图识别 fallback 是规则，生产可接入更强模型和更多样本评测。
- 当前知识反馈写入本地表，后续可以接真实知识库维护系统 API 或 MCP，并传下游幂等键。
- 当前测试覆盖偏接口和流程，后续应增加 repository、graph node、异常路径单测。
- 当前 Docker 镜像未包含 dev 依赖，CI 应单独构建测试环境。

## 8. 高频面试问题与参考回答

### Q1：这个项目最大的亮点是什么？

答：最大的亮点是把普通 RAG 查询服务升级为可审计、可恢复、带证据门禁和知识反馈闭环的企业 Agent。它不仅能回答问题，还能识别员工指出的具体知识问题；模糊反馈先追问，目标明确后才进入 human-in-the-loop 确认。

### Q2：为什么新版比旧版更适合维护？

答：新版按职责分层，API、Agent、外部客户端、数据库仓储和 schema 都拆开了。新增功能时不需要改一个超大的 `app.py`，而是在对应层扩展。比如新增工具主要改 graph、client、schema 和评测用例。

### Q3：LangGraph 在这里具体做了什么？

答：LangGraph 负责把一次请求编排成多节点流程。先分类意图，再根据意图走知识问答、文档对比或知识反馈分支。反馈分支先生成草稿并校验具体知识点；目标明确时在确认节点 `interrupt()` 暂停，用户调用 `resume` 后继续提交。

### Q4：checkpoint 为什么重要？

答：没有 checkpoint，审批暂停后状态只在内存里，服务重启或请求结束就丢了。PostgreSQL checkpoint 可以保存图的中间状态，让后续 `resume` 找回之前的上下文。

### Q5：本地 session 和 RAGFlow session 的区别是什么？

答：本地 session 是系统自己的会话 ID，用于前端、权限和审计。RAGFlow session 是上游知识库系统内部会话。新版用本地 session 映射 RAGFlow session，避免前端直接接触上游细节。

### Q6：为什么这次 `resume` 会返回旧状态？

答：因为 SQLAlchemy session 有 identity map。接口里先查了 run，缓存了 `pending_approval` 状态；Graph 恢复执行时在另一个 DB session 里更新成 `completed`；原接口 session 再查时拿到缓存对象，所以返回旧状态。

### Q7：你是怎么修复的？

答：给 `RunRepository.get_owned` 增加 `refresh` 参数，在需要时给查询加 `populate_existing=True`。审批恢复后返回响应时启用刷新读取，强制从数据库覆盖当前 session 里的旧对象。

### Q8：为什么不直接 `db.refresh(run)`？

答：`db.refresh(run)` 可以刷新 run 本身，但这里还要带 `tool_calls` 关系数据。使用 `populate_existing=True` 配合原查询和 `selectinload`，可以按响应需要重新加载 run 与工具调用，更贴近现有 `_load_response` 结构。

### Q9：为什么要记录 `tool_calls`？

答：Agent 的可解释性来自工具轨迹。记录工具名、输入、输出、状态、耗时和错误后，可以排查问题，也能回答用户“这次结果怎么来的”。

### Q10：为什么知识反馈被用户拒绝后状态也是 `completed`？

答：因为流程已经正常结束，只是业务分支选择了“不提交反馈”。`failed` 应表示系统错误，而拒绝确认是正常业务结果。模糊反馈要求补充信息也属于正常完成，不应伪装成系统故障。

### Q11：如果 RAGFlow 挂了会怎样？

答：真实模式下 RAGFlow client 会抛异常，工具节点捕获后记录 failed tool_call，并返回“知识库查询失败”等错误 answer，最终 run 会被标记 failed 或带 error。mock 模式下不依赖 RAGFlow。

### Q12：怎么保证生产安全？

答：关闭开发认证，关闭 mock，敏感信息走环境变量或密钥管理，不写入源码；启用飞书认证和白名单；高风险操作走审批；用户数据按 user_id 隔离；日志避免打印 token。

### Q13：如果要支持流式输出怎么做？

答：RAGFlow client 要支持 stream 模式，API 层可以增加 SSE endpoint，Agent 运行状态仍记录到 run 和 tool_calls。需要处理流式过程中的错误、断连和最终落库。

### Q14：如果新增多知识库路由怎么做？

答：可以在 session 或 request 中增加 knowledge_base 或 chat_id 配置，但仍由后端控制。Agent 根据用户、会话或意图选择不同 RAGFlow chat，不让前端直接传真实上游 ID。

### Q15：离线评测为什么重要？

答：它让 Agent 的核心行为可以量化验证。尤其是意图识别、工具选择、证据判定和反馈定位门禁，不能只靠手工点接口。评测能快速确认没有破坏知识问答、文档对比和反馈同意、拒绝、补充定位流程。

## 9. 推荐面试讲解顺序

建议按这个顺序讲，逻辑最自然：

1. 旧项目背景：单文件 RAGFlow 代理服务，能用但耦合重。
2. 升级目标：模块化、可审计、可测试、支持 Agent 和审批。
3. 新版架构：FastAPI + LangGraph + PostgreSQL + RAGFlow + LLM fallback。
4. 核心流程：创建会话 -> 发起 run -> 意图识别 -> 工具路由 -> 审批/执行 -> 记录轨迹。
5. 数据设计：users、sessions、runs、tool_calls、knowledge_feedbacks。
6. 安全设计：开发认证、飞书认证、白名单、用户隔离、审批。
7. 测试结果：健康检查、业务路径、权限、自动评测 100%。
8. 修复案例：`resume` 返回旧状态，SQLAlchemy identity map，`populate_existing` 修复。
9. 不足与改进：Alembic、SSE、真实业务 API、更多测试、CI。

## 10. 你可以背下来的项目总结

> 我这个项目的重点不是简单调用 RAGFlow，而是把 RAGFlow 封装成企业 Agent 的知识工具。新版通过本地会话隐藏上游 session，通过 LangGraph 显式编排知识问答、文档对比和知识库问题反馈；反馈先定位到具体知识点，模糊输入返回 needs_input，目标明确后才通过 checkpoint + interrupt 等待确认。Execution Harness 进一步约束预算、版本、上下文、工具权限和模型降级，并用 run_events 保存轻量 Trace。测试覆盖 90 条黄金集、70 条对抗集和 40 条回放种子，34 个单测与 200 个端到端用例通过，9 项指标均为 1.0；并发确认由 CAS 保证只提交一次。回放层目前是合成种子，我不会把它包装成真实线上质量。

## 11. 面试前自查清单

- 能画出新版请求链路。
- 能解释 LangGraph 的每个节点。
- 能说清楚 `interrupt` 和 `resume`。
- 能说清楚 PostgreSQL checkpoint 的意义。
- 能解释 `agent_runs` 与 `tool_calls`。
- 能讲清楚本地 session 映射 RAGFlow session。
- 能解释 `X-Dev-User` 只适合开发。
- 能说明旧项目硬编码密钥的风险。
- 能复述本次 bug 的根因和修复。
- 能运行 PowerShell 测试指令并看懂结果。
- 能主动说出项目不足和下一步优化。
