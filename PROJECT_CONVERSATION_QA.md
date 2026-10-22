# EvidenceOps Agent 项目迭代问答整理

本文整理项目升级过程中提出的核心问题与回答，用于秋招项目复盘、简历修改和面试深挖。比较基线始终是用户最早提供的 `app.zip`，升级代码位于独立目录 `agent_app_evidence_v3`，没有覆盖最早版本。

项目的核心结论是：它不是把多个工具接到大模型上的通用 Agent Demo，而是围绕企业知识场景建立了三条可信边界——候选答案必须通过证据门禁，写操作必须通过人工审批和数据库并发控制，整个运行必须受到 Harness 的预算、权限、版本和可观测性约束。

## 一 项目升级目标与比较基线

### 问题一 如何把最早版项目升级为更适合大厂秋招的项目

**回答：** 最早版已经打通飞书身份、Excel 白名单、RAGFlow 问答、会话、历史、反馈和日志，因此升级不应只增加一个 Agent 框架。真正有面试价值的方向是补齐企业系统最容易出错的边界：答案没有证据却仍然返回、前端依赖上游 Session、同步调用阻塞异步接口、重复请求产生重复副作用、双并发审批同时成功、模型越权选择工具，以及系统无法解释某次运行使用了什么版本和为何降级。

升级后的项目以 EvidenceOps 为定位，将 LangGraph 状态编排、Evidence Guard、人工审批、幂等、CAS、恢复状态和 Execution Harness 组合成一个可信知识 Agent。亮点不在工具数量，而在答案可信度与副作用一致性。

### 问题二 为什么必须在新目录生成而不是覆盖原项目

**回答：** 新目录可以保留可验证的演进证据，便于面试时说明升级前后的差异，也避免破坏已有可运行版本。升级代码统一位于 `app_v2/agent_app_evidence_v3`，原始压缩包和最早版源码不被修改。Docker 使用独立 Compose project name、端口和 named volume，降低与其他本地项目冲突的风险。

### 问题三 升级对比应该和哪个版本比较

**回答：** 对比基线是最早提供的 `app.zip`，不是中间形成的 `app_v2`。对比文档应回答最早版已经解决了什么、在生产并发和安全场景下会暴露什么，以及 v3 用什么机制修复。这样才能展示真实演进，而不是拿两个中间版本做自我比较。

### 问题四 这个项目最独特的亮点是什么

**回答：** 第一，RAGFlow 返回的是候选答案，不是可直接对外的最终答案；Evidence Guard 根据引用覆盖、相关度、来源多样性、任务风险和间接 Prompt Injection 决定能否输出。第二，知识库问题反馈不是普通表单，而是带确定性目标定位、LangGraph interrupt、人工审批、数据库 CAS 和恢复状态的高风险写流程。第三，Execution Harness 对预算、上下文、工具权限、版本和模型降级进行统一治理。

## 二 业务语义与 Agent 工作流

### 问题五 为什么把创建工单改成提出知识库问题反馈

**回答：** 当前业务语义不是通用客服工单，而是指出知识库中哪一条知识存在错误、过期、不完整或表述不清。反馈草稿包含 `knowledge_item`、`issue_type`、`description`、`priority` 和 `target_status`。只有用户明确指出制度、条款、流程节点或具体知识点后，系统才允许进入审批；空泛的“知识库有问题”只返回 `needs_input`。

### 问题六 新版整体运行逻辑是什么

**回答：** 请求首先完成认证、会话所有权校验和幂等处理，然后 Context Manager 清洗历史，Harness 固化预算和版本。LangGraph 根据意图进入知识问答、文档对比、知识反馈或不支持分支。所有工具调用前都经过 Tool Policy；知识问答和文档对比的候选结果进入 Evidence Guard；知识反馈在目标明确后进入人工审批。最后统一保存答案、引用、工具轨迹、Harness 事件和终止原因。

### 问题七 嵌入大模型后的完整 Agent 运行逻辑是什么

**回答：** Qwen 只参与受约束的意图分类和明确反馈的结构化草稿生成。知识问答的候选答案由 RAGFlow 工具返回，并由 Evidence Guard 决定能否成为最终答案。LangGraph 管理业务状态转移，PostgreSQL 保存 Run、Tool Call、事件和 Checkpoint。模型不能直接调用任意工具、决定写权限或提交反馈。

不同请求的 Provider 调用情况如下：模糊知识反馈为零次；明确知识反馈通常为两次，分别用于意图分类和草稿生成；知识问答与文档对比通常有一次 Agent 侧意图分类，再调用一次 RAGFlow 工具；不支持请求通常只有意图分类。模型未配置或熔断时改用确定性 fallback。

### 问题八 模糊知识反馈为什么要在模型调用前拦截

**回答：** “知识库有问题，请提交反馈”本身就能被规则识别为反馈意图且目标未定位。如果先调用模型，模型可能补出“内部知识库”等貌似具体的目标，同时增加延迟和 Token 成本。当前实现先执行 `feedback_target.preflight`，直接得到 `knowledge_feedback + unresolved`，跳过意图模型；进入反馈工具后再执行 `feedback_target.precheck`，仍未定位时跳过草稿模型并返回 `needs_input`。

对于明确目标，系统也不会让模型替换 `knowledge_item`。目标字段绑定到原始用户文本的确定性抽取结果，模型只负责问题类型、描述和优先级。单元测试用 MockTransport 断言模糊输入的 HTTP handler 调用次数为零。

## 三 Execution Harness 与最新面试考点

### 问题九 Harness 和 Agent Framework 有什么区别

**回答：** LangGraph 是 Framework，解决节点、条件边、共享状态、Checkpoint 和 interrupt 如何编排。Harness 是运行控制层，解决一次执行允许使用多少时间、步骤、Token 和工具，哪些工具和外连目标可以访问，使用了什么模型和版本，以及如何记录失败与终止原因。只画状态图而没有预算、权限和可复现信息，仍然只是 Demo。

### 问题十 当前实现了哪些 Harness 能力

**回答：** 当前实现了六项核心能力：运行预算、轻量 Trace、版本指纹、Context Engineering、Tool Contract 与权限策略、模型路由与熔断。它们均进入真实请求链路，而不是只存在于配置或架构图中。

| 能力 | 当前实现 | 主要边界 |
| --- | --- | --- |
| 运行预算 | 总时限、LangGraph 步数、工具次数、输入 Token 估算 | Token 仍是字符估算 |
| Trace | `run_events` 记录生命周期、策略、LLM、工具和终止事件 | 尚未导出 OpenTelemetry |
| 版本指纹 | App、Prompt、Tool Schema、Policy、Dataset 版本 | 还可加入镜像 Digest 和 Git SHA |
| Context | 限轮次、限字符、去重、注入隔离 | 当前不是语义记忆系统 |
| Tool Contract | 角色、风险、超时、输出、Egress、审批 | 尚非容器级沙箱 |
| 模型路由 | 任务与风险路由、受控重试、熔断、确定性降级 | 熔断状态仅在进程内 |

### 问题十一 Harness 如何限制失控 Agent

**回答：** 图启动前检查估算输入 Token；`asyncio.timeout` 限制总运行时间；LangGraph `recursion_limit` 限制最大步骤；每个工具调用前检查累计次数；每个工具还有独立 timeout 和最大输出长度。超限不会静默返回普通答案，而是保存稳定的 `termination_reason`，例如 `run_timeout`、`step_budget_exceeded` 或 `tool_call_budget_exceeded`。

### 问题十二 Tool Contract 为什么不只是参数 Schema

**回答：** Schema 只说明参数形状，Contract 还必须说明允许角色、读写风险、是否需要审批、超时、调用次数、最大输出和外连目标。项目只注册 `knowledge_search`、`compare_documents`、`create_feedback_draft`、`evidence_guard` 和 `submit_feedback` 五个工具，未知工具默认拒绝；`submit_feedback` 被标记为高风险，没有人工批准不能执行。

## 四 测试集与完整测试逻辑

### 问题十三 是否应该构建测试集来评测 Agent

**回答：** 必须构建。Agent 不能只测答案文本，因为一次任务还包含意图、工具选择、工具顺序、状态迁移、审批、副作用和安全拒答。项目将评测拆成单元测试、真实模型 Harness、小型 Smoke、三层数据集和并发韧性测试。

### 问题十四 三层评测体系是什么

**回答：** 黄金集覆盖标准业务路径；对抗集覆盖空召回、低相关度、Prompt Injection、边界分数、复合故障和模糊反馈；回放种子覆盖更接近线上表达的合成样本。当前数据规模为 90 条黄金集、70 条对抗集和 40 条回放种子。回放层必须明确是合成种子，不能包装成真实线上日志。

### 问题十五 一条评测用例如何完整执行

**回答：** 评测器先校验数据文件 SHA-256，再创建测试会话并提交 Run。若初始状态是 `pending_approval`，评测器继续调用 resume。随后同时断言意图、初始和最终状态、完整工具序列、禁止工具、Evidence verdict、风险标志、反馈目标门禁、反馈 Schema、安全拒答以及 `submit_feedback` 次数，最后聚合分层通过率和 P50/P95 延迟。

### 问题十六 如何测试幂等和并发审批

**回答：** 相同用户、相同 Idempotency-Key 和相同参数必须返回同一 Run；相同 Key 配不同参数必须返回 409。审批测试先创建一个 `pending_approval` Run，再并发发送两个 resume。数据库使用条件更新把状态从 `pending_approval` 抢占为 `approval_processing`，因此结果应为一个 200、一个 409，且 `submit_feedback` 只能记录一次。

### 问题十七 当前有哪些可验证结果

**回答：** 最新代码状态下，36 个单元测试通过，Ruff 静态检查通过；无密钥 Docker 端到端验证确认模糊反馈返回 `completed + needs_input`，没有审批、没有 `llm.classify_intent` 和 `llm.feedback_draft` 事件，Provider Route 数量为零。

历史基线报告记录了 200 条确定性三层评测全部通过，以及 Qwen3.8 Max 真实 Harness 8 个业务阶段和 9 条 Smoke 兼容用例通过。由于随后增加了模型前置门禁，历史报告应作为当时版本的证据，不应冒充最新代码的重新实测结果。

### 问题十八 测试结果能证明模型质量好吗

**回答：** 不能。确定性 Mock 的 200 条评测主要证明控制流、安全策略、状态机和副作用约束可回归。真实 Qwen Harness 使用真实模型、真实 PostgreSQL 和真实 Checkpoint，但 RAGFlow 仍为 Mock，因此它证明模型接入和 Agent 编排，不证明真实企业知识库的检索与回答质量。生产评测还需要人工黄金集、真实 RAGFlow、脱敏线上回放、逐 Claim 忠实度、置信区间和漂移监控。

## 五 Qwen 接入与故障处理

### 问题十九 如何接入 Qwen3.8 Max

**回答：** 项目通过 OpenAI Compatible Chat Completions 接口接入，Base URL、API Key、Router Model、Primary Model 和 High Risk Model 均由环境变量注入。密钥不写入源码、镜像或报告。客户端记录 Provider 返回的实际模型、usage、request ID 和重试次数，避免只根据本地配置宣称接入成功。

### 问题二十 模型失败后如何重试和降级

**回答：** 无副作用的 LLM 推理只对网络异常、429 和部分 5xx 进行受控重试，默认最多两次，并写入 `retry_count`。连续失败达到阈值后打开进程内熔断器，路由到确定性 fallback。普通业务 4xx 不重试；RAGFlow completion 和反馈提交可能产生状态或副作用，在没有下游幂等键时不能照搬 LLM 重试策略。

### 问题二十一 真实模型测试发现了什么问题

**回答：** 最重要的问题是模型会为模糊反馈补出一个具体目标，导致错误进入审批。第一次修复是在模型调用后基于原文拦截；进一步复盘后将门禁提前到所有模型调用之前，并把明确目标绑定到原文抽取结果。这个过程体现了真实模型评测的价值：Mock 能证明流程，但不容易暴露模型为了完成任务而补全缺失字段的行为。

### 问题二十二 Harness 汇总指标有什么统计边界

**回答：** 历史 Qwen 测试脚本曾在 `pending_approval` 响应时汇总一次事件，resume 后又汇总该 Run 的完整累计事件，因此 `llm_route_count` 和 `harness_event_count` 是响应快照相加，不能当作唯一事件数。Case 通过、工具顺序、状态和 Provider 模型断言仍然有效，但面试中不应引用重复累计的 10 条 Route 和 57 条 Event 作为唯一调用数。更好的实现应按 Event ID 去重，或只采集 resume 后新增事件。

## 六 证据安全与副作用一致性

### 问题二十三 Evidence Guard 能完全解决幻觉吗

**回答：** 不能。当前 Evidence Guard 是确定性的第一层服务时门禁，可以阻止无引用、低相关度、单来源对比和明显间接 Prompt Injection，但不等价于逐 Claim 事实蕴含。后续可以加入 Claim 拆分、NLI 或 LLM Judge、知识时效元数据和新旧策略 Shadow 对比。

### 问题二十四 为什么审批失败后需要 recovery_required

**回答：** 如果进程在外部副作用完成后、本地成功状态写入前崩溃，系统无法知道反馈是否已经提交。此时自动重试可能重复写入，所以将 Run 标记为 `recovery_required`，等待对账。端到端 exactly once 还需要下游幂等键、外部反馈编号和 Outbox 或对账 Worker。

### 问题二十五 为什么 Trace 和 Tool Call 要分开

**回答：** Tool Call 是业务审计，评测需要知道 Agent 实际选择了哪些业务工具。Trace 还包含 HTTP、LLM、Context、Policy 和 Termination。如果把所有内部事件塞进 Tool Call，会污染工具选择指标。项目使用 `tool_calls` 保存业务工具轨迹，使用 `run_events` 保存 Harness 控制面事件，两者通过 `run_id` 关联。

## 七 简历表达

### 问题二十六 如何把项目写进简历

**回答：** 推荐项目名称为“EvidenceOps 企业级 RAG 知识 Agent 后端”，技术栈写 Python、FastAPI、LangGraph、RAGFlow、PostgreSQL 和 Qwen。描述应避免“Agent 化优化包装”这类空泛措辞，改成可被追问和验证的技术动作。

1. 基于 FastAPI、RAGFlow 与 PostgreSQL 构建企业知识库问答后端，完成飞书鉴权、Excel 白名单、本地会话映射、流式与非流式问答、引用标准化、历史管理和软删除，并通过用户资源所有权校验防止越权访问。
2. 基于 LangGraph 编排知识问答、多文档对比和知识库问题反馈，使用 PostgreSQL Checkpoint、`interrupt/resume`、幂等键、请求指纹和审批 CAS，实现跨请求人工确认及并发场景下反馈最多提交一次。
3. 设计 Evidence Guard 与 Evidence Passport，对引用覆盖、相关度、来源多样性、任务风险和间接 Prompt Injection 进行服务时门禁，实现无证据安全拒答、双文档单来源阻断和高风险分层控制。
4. 建设 Agent Execution Harness，落地运行预算、Context 清洗、版本指纹、Tool Contract、Egress 白名单、模型路由与熔断，并构建三层评测数据和自动化测试覆盖关键边界。

### 问题二十七 面试时如何用一句话介绍项目

**回答：** 我将一个具备鉴权、会话和反馈闭环的 RAGFlow 后端，升级为以 LangGraph 编排、Evidence Guard 控制答案可信度、Execution Harness 约束模型和工具运行、数据库 CAS 保障审批副作用安全的企业知识 Agent。

### 问题二十八 简历中应避免哪些过度表述

**回答：** 不要写“彻底解决大模型幻觉”，应写“通过弱证据拒答和候选答案门禁降低不可信输出”；不要写“保证 Exactly Once”，应写“正常并发下最多提交一次，未知副作用进入对账状态”；不要把合成回放种子写成线上数据，也不要把 Mock 回归指标包装成真实知识库质量。

## 八 面试复习速查

| 面试官追问 | 回答抓手 |
| --- | --- |
| 为什么需要 LangGraph | 状态、Checkpoint、interrupt 和跨请求恢复，而不是为了替代简单 if else |
| 为什么答案不能直接返回 | RAGFlow 输出是候选答案，必须通过 Evidence Guard |
| 如何防止模型越权 | 意图白名单、Tool Contract、deny by default、确定性门禁和人工审批 |
| 如何防止重复提交 | 用户域幂等键、请求指纹、数据库唯一约束和审批 CAS |
| 为什么不能盲目重试 | 超时后的副作用状态可能未知，重试必须建立在幂等语义上 |
| Harness 的核心价值 | 预算、权限、版本、观测、终止和复现实验 |
| 测试为什么不只看答案 | 还要断言意图、工具序列、工作流、证据、安全拒答和副作用次数 |
| 当前最大边界是什么 | 真实 RAGFlow 质量、逐 Claim 忠实度、跨副本熔断和自动对账尚未完成 |

## 九 代码与报告索引

| 能力 | 位置 |
| --- | --- |
| Agent API 与幂等入口 | `app/api/routes/agent.py` |
| LangGraph 状态图 | `app/agent/graph.py` |
| Qwen 客户端与前置门禁 | `app/clients/llm.py` |
| Evidence Guard | `app/services/evidence.py` |
| Execution Harness | `app/services/harness.py` |
| Context 管理 | `app/services/context_manager.py` |
| Tool Contract | `app/services/tool_policy.py` |
| 模型路由与熔断 | `app/services/model_routing.py` |
| 三层评测器 | `scripts/evaluate.py` |
| Qwen Harness 测试 | `scripts/test_qwen_harness.py` |
| 并发与幂等测试 | `scripts/test_resilience.py` |
| 历史 Qwen 接入报告 | `evaluation/reports/QWEN3.8_MAX_INTEGRATION_REPORT.md` |

