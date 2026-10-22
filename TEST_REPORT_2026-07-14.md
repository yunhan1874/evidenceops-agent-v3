# agent_app_v2 测试报告与旧 app 项目差异总结

测试日期：2026-07-14  
修复复测日期：2026-07-15  
业务语义校正：后续已将早期“工单”原型统一替换为“知识库问题反馈”；本文以下名称按校正后的 v2 表达。  
测试项目：`C:\Users\HP\vsocde\app_v2\agent_app_v2`  
对比项目：`C:\Users\HP\Desktop\app.zip`，已解压至 `C:\Users\HP\vsocde\_uploaded_app_compare\app`  
认证方式：开发模式请求头 `X-Dev-User: demo-user`  
上游模式：`RAG_MOCK_MODE=true`，未连接真实 RAGFlow / 飞书环境

## 0. 修复复测记录

2026-07-15 已修复审批恢复接口返回旧状态的问题。修复后：

| 指标 | 修复后结果 |
| --- | --- |
| 意图识别准确率 | 100% |
| 工具选择准确率 | 100% |
| 任务完成率 | 100% |
| 知识反馈同意 `resume` 响应 | 直接返回 `completed`，工具包含 `create_feedback_draft` 与 `submit_feedback` |
| 知识反馈拒绝 `resume` 响应 | 直接返回 `completed`，工具仅包含 `create_feedback_draft` |

修复方式：`RunRepository.get_owned` 增加强制刷新查询能力，`POST /api/v2/agent/runs/{run_id}/resume` 在返回响应前使用刷新读取，避免 SQLAlchemy 当前会话缓存返回审批前状态。

## 1. 测试结论

`agent_app_v2` 可以通过 Docker Compose 正常启动，API 服务与 PostgreSQL 均可用。健康检查、会话创建、会话列表、知识问答、文档对比、知识反馈确认后的实际提交、用户隔离和未认证拦截均通过。

原始测试发现 1 个问题：`POST /api/v2/agent/runs/{run_id}/resume` 在审批后会触发后续执行，但接口响应仍返回审批前的旧状态 `pending_approval`。该问题已在 2026-07-15 修复，修复后 `resume` 响应会直接返回最终状态 `completed`。

## 2. 环境与启动情况

| 项目 | 结果 |
| --- | --- |
| Docker 构建 | 通过，使用缓存完成构建 |
| API 容器 | `agent_app_v2-api-1` 正常运行，端口 `8000` |
| PostgreSQL 容器 | `agent_app_v2-postgres-1` 正常运行，健康状态 healthy |
| `/health/live` | 通过，返回 `{"status":"ok"}` |
| `/health/ready` | 通过，返回 `{"status":"ready"}` |
| 服务端日志 | 未发现 500 错误 |

说明：容器当前仍处于运行状态，便于继续复测。

## 3. 自动评测结果

执行项目自带离线评测脚本，使用 `demo-user`：

| 指标 | 结果 |
| --- | --- |
| 测试用例数 | 4 |
| 意图识别准确率 | 100% |
| 工具选择准确率 | 75% |
| 任务完成率 | 50% |

逐项结果：

| 用例 | 意图 | 工具 | 脚本看到的状态 | 结论 |
| --- | --- | --- | --- | --- |
| knowledge_001 | `knowledge_qa` | `knowledge_search` | `completed` | 通过 |
| compare_001 | `document_compare` | `compare_documents` | `completed` | 通过 |
| feedback_approve_001 | `knowledge_feedback` | `create_feedback_draft` | `pending_approval` | 被 `resume` 旧响应影响，脚本误判 |
| feedback_reject_001 | `knowledge_feedback` | `create_feedback_draft` | `pending_approval` | 被 `resume` 旧响应影响，脚本误判 |

自动评测的失败并不代表审批执行没有发生，而是 `resume` 返回值没有刷新到最新数据库状态。

## 4. 手工接口测试结果

| 测试项 | 结果 | 说明 |
| --- | --- | --- |
| 创建会话 | 通过 | `POST /api/v2/sessions` 返回本地 session UUID |
| 查询会话列表 | 通过 | `GET /api/v2/sessions` 能列出 `demo-user` 的会话 |
| 知识问答 | 通过 | 状态 `completed`，意图 `knowledge_qa`，工具 `knowledge_search`，返回 1 条 mock 引用 |
| 文档对比 | 通过 | 状态 `completed`，意图 `document_compare`，工具 `compare_documents`，返回 1 条 mock 引用 |
| 知识反馈，确认前 | 通过 | 状态 `pending_approval`，工具 `create_feedback_draft` |
| 知识反馈，同意后立即响应 | 异常 | `resume` 响应仍是 `pending_approval`，仅包含 `create_feedback_draft` |
| 知识反馈，同意后再查详情 | 通过 | `GET` 返回 `completed`，工具包含 `create_feedback_draft` 与 `submit_feedback` |
| 知识反馈，拒绝后立即响应 | 异常 | `resume` 响应仍是 `pending_approval` |
| 知识反馈，拒绝后再查详情 | 通过 | `GET` 返回 `completed`，未执行 `submit_feedback` |
| 其他用户读取运行记录 | 通过 | `other-user` 读取 `demo-user` 的 run 返回 404 |
| 不带认证头访问会话 | 通过 | 返回 401 |
| 意图分类轻量检查 | 通过 | 容器内校验知识问答、文档对比、知识反馈三类规则均通过 |

未完成项：主机 Python 环境缺少 `pytest`，因此没有在主机上执行完整 `pytest tests`。已改用容器运行环境做了同等核心逻辑检查。

## 5. 发现的问题与修复状态

### P1：审批恢复接口返回旧状态，已修复

现象：`POST /api/v2/agent/runs/{run_id}/resume` 返回 `pending_approval`，但随后 `GET /api/v2/agent/runs/{run_id}` 返回 `completed`。

影响：

- 前端审批成功后可能仍显示“待审批”。
- 自动评测脚本直接使用 `resume` 响应计算完成率，会把已完成任务误判为未完成。
- 用户可能重复点击审批，产生 409 或重复操作困惑。

推测原因：`resume_run` 先用当前数据库会话读取了 run，再由 LangGraph 内部使用新的数据库会话更新状态；最后 `_load_response` 复用原来的 SQLAlchemy 会话，可能读到了 identity map 中的旧对象。

建议修复：

- 在 `resume_run` 调用 `_load_response` 前刷新或过期当前数据库会话，例如 `await db.expire_all()` 不可直接 await，应使用同步方法，或对目标对象执行 `await db.refresh(run)`。
- 更稳妥的方式是让 `_load_response` 使用新的数据库会话查询，或者在查询语句加上 `populate_existing=True`。
- 修复后同步更新 `scripts/evaluate.py`，确保审批后以最终状态计算完成率。

修复结果：已采用 `populate_existing=True` 的刷新查询方案，并仅在审批恢复响应中启用刷新读取。复测后自动评测三项指标均为 100%。

### P2：容器镜像没有包含测试目录和开发依赖

现象：API 镜像只复制了 `app`、`scripts`、`evaluation`，没有复制 `tests`，且只安装 `requirements.txt`。因此容器内无法直接运行完整单元测试。

建议：

- 增加单独的测试镜像或 CI 步骤安装 `requirements-dev.txt`。
- 或在 Dockerfile 中增加可选 test stage，避免生产镜像膨胀。

### P3：README 在当前终端显示为乱码

现象：PowerShell 默认读取时出现中文乱码。源码本身多处中文正常，可能是终端编码或文档编码不一致。

建议：

- 统一 README 为 UTF-8。
- 在仓库中增加 `.editorconfig` 或文档说明，避免跨平台阅读乱码。

## 6. 新版 agent_app_v2 与旧 app 项目的主要区别

| 维度 | 旧 app 项目 | 新版 agent_app_v2 |
| --- | --- | --- |
| 架构 | 单文件 `app.py`，约 110KB，接口、认证、数据库、RAG 代理、日志、定时任务混在一起 | 模块化拆分为 `api`、`agent`、`clients`、`db`、`repositories`、`schemas`、`services` |
| API 路径 | 主要为 `/api/rag/*`、`/api/feishu/*`、`/api/diagnostics/*` | 统一新增 `/api/v2/*`，并保留独立健康检查 |
| 核心能力 | RAGFlow 代理、会话、历史、反馈、消息删除、诊断缓存 | LangGraph Agent，支持知识问答、文档对比、知识反馈草稿与人工确认 |
| 认证方式 | 依赖飞书 Bearer token，带本地 token 缓存和黑名单 | 支持飞书正式登录，也支持开发模式 `X-Dev-User` |
| 会话设计 | 前端需要传真实 `chat_id` 和 RAGFlow `session_id` | 后端维护本地 session UUID 与 RAGFlow session 映射，前端只保存本地 UUID |
| 上游依赖 | 强依赖真实 RAGFlow、飞书和白名单 Excel | 默认可用 mock RAGFlow 离线测试，真实配置留到生产环境 |
| 数据库 | psycopg2 同步连接池，表偏向反馈和交互日志 | SQLAlchemy async，包含 users、agent_sessions、agent_runs、tool_calls、knowledge_feedbacks 等运行轨迹 |
| 审计追踪 | 有请求日志和用户交互日志 | 明确记录 agent_runs、tool_calls、request_id、latency、citations、审批状态 |
| 工作流 | 请求直接代理或流式转发 RAGFlow | 显式意图分类，再路由到不同工具，知识反馈使用 interrupt 人工确认 |
| 部署 | Dockerfile 使用 Python 3.10，`uvicorn app:app --workers 2` | Dockerfile 使用 Python 3.11，`uvicorn app.main:app --workers 1`，配 LangGraph checkpoint |
| 配置安全 | compose 和源码默认值里包含真实上游地址、应用凭据、RAGFlow Key、数据库密码 | `.env.example` 留空敏感项，README 明确要求生产环境关闭开发认证并轮换旧密钥 |
| 测试资产 | 未看到独立测试/评测脚本 | 有 `tests/test_llm_fallback.py`、`evaluation/cases.jsonl` 和 `scripts/evaluate.py` |
| 迁移定位 | 旧版生产代理服务 | 新版 MVP/迁移目标，README 明确建议旧服务先作为 legacy 保留 |

## 7. 总体评价

新版 `agent_app_v2` 已经从“RAGFlow 代理服务”升级成“企业知识 Agent MVP”：结构更清晰，安全边界更好，前端不再接触真实 RAGFlow 会话 ID，并且有运行轨迹、工具调用记录和人工审批机制。它更适合作为后续可维护、可评测、可扩展的主线版本。

当前最值得优先处理的是审批 `resume` 响应旧状态问题。修复后，自带评测的任务完成率应能从当前的 50% 恢复到更符合实际执行结果的水平。
