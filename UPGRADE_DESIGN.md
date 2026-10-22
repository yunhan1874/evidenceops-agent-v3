# EvidenceOps Agent v3 升级设计与面试拆解

## 1. 项目定位

一句话：这是一个把“答案可信度”和“副作用一致性”提升为一等公民的企业知识 Agent。

普通 Agent 项目通常止步于意图分类、RAG 检索、工具调用。本项目多了一条明确的信任边界：
RAGFlow 返回的文本只是 `candidate_answer`，只有通过 Evidence Guard 后才能成为 `final_answer`。

```text
query -> intent -> retrieval -> candidate_answer
                              -> Evidence Guard -> grounded -> answer
                                                -> review   -> warning + answer
                                                -> weak     -> abstain + repair plan
                                                -> attack   -> quarantine + security review
```

## 2. 独特亮点：Evidence Passport

每次知识问答或文档对比都生成一份可持久化、可评测的证据护照：

- `verdict`：`grounded / review_required / insufficient / blocked`
- `confidence`：由引用相关度、数量、来源多样性组成的确定性分数
- `coverage`：引用数、来源数、有效分数数、平均分和最高分
- `risk_flags`：无引用、低相关度、元数据缺失、来源不足、高风险弱证据、间接提示注入
- `evidence`：稳定指纹、来源、页码、分数、截断片段、隔离状态
- `recovery_plan`：重写查询、限定文档、分别检索、知识库负责人复核或安全隔离

为什么先做确定性门禁：低延迟、低成本、可解释、可回归。它是第一道门，不冒充完整的事实蕴含模型；
后续可以在高风险分支再接 NLI/LLM judge，形成按风险分层的成本模型。

## 3. 被显式处理的边界

| 环节 | 失败/攻击 | 处理策略 | 可讲知识点 |
| --- | --- | --- | --- |
| HTTP 入口 | 超长或换行 Request ID 污染日志 | 字符白名单；不合法则生成 UUID | 日志注入、trace 边界 |
| 创建运行 | 客户端超时重放 | `Idempotency-Key` 按用户哈希并加唯一约束 | 幂等、唯一索引、竞争条件 |
| 创建运行 | 同一幂等键换了 query/文档 | 全请求指纹不一致返回 409 | 幂等语义不是“去重” |
| RAGFlow | timeout/429/4xx/5xx/非法 JSON | 稳定错误码；不回传内部 URL/异常 | 错误契约、脱敏 |
| RAGFlow | POST 盲目重试导致远端重复消息 | 不自动重试无幂等保证的调用 | at-least-once 副作用 |
| 检索 | 空引用仍生成流畅答案 | `insufficient` 并拒答 | fail-closed、幻觉控制 |
| 检索 | 低相似度/缺分数 | 拒答或带警告回答 | 阈值、降级策略 |
| 文档对比 | 只检索到一个文档 | 要求至少两个独立来源 | 任务特定证据契约 |
| 高风险问题 | 食安/合规证据不强 | 使用更高阈值并要求人工复核 | 风险分层 |
| 知识库 | 文档包含恶意自然语言指令 | 引用隔离、阻断候选答案、记录安全事件 | 间接 Prompt Injection |
| 审批 | 两个请求同时 resume | 条件 `UPDATE ... WHERE status=pending_approval` 抢占 | CAS、并发一致性 |
| 审批 | 抢占后执行异常，副作用状态不明 | 标记 `recovery_required`，禁止自动重试 | 对账、saga 思维 |
| 部署 | 生产误开 dev auth/mock | 启动时 fail-fast | 安全默认值、配置治理 |

## 4. 为什么审批不是“先查再改”

错误写法是两个请求分别读取 `pending_approval`，随后都执行提交。读取检查与状态更新之间存在竞态窗口。

本项目使用单条条件更新：

```sql
UPDATE agent_runs
SET status = 'approval_processing'
WHERE id = :run_id
  AND user_id = :user_id
  AND status = 'pending_approval'
RETURNING id;
```

只有一个请求能拿到返回行。另一个请求得到 409，从数据库层关闭重复副作用窗口。

## 5. 为什么异常后是 recovery_required

审批恢复失败不等于“什么都没发生”。进程可能在写入反馈后、更新 run 前崩溃。此时自动重试可能重复提交。
因此状态进入 `recovery_required`，由管理员核对本地反馈或真实知识库维护系统后再决定补偿。这体现了副作用系统中
“未知状态”与普通失败的区别。

## 6. 三层评测不是“多写几条 case”

评测资产由 `evaluation/manifest.json` 固定版本和 SHA-256，共 200 条：90 条黄金业务样本、70 条
对抗/边界样本、40 条无 PII 回放种子。原有 9 条仍作为快速 smoke 集，不与全量回归混用。

评测器同时验证意图、完整工具序列、最终状态、审批前状态、`pending_approval`、工具状态、禁止工具、
反馈提交次数、反馈 schema、知识目标定位门禁、Evidence verdict、风险标签和安全拒答；报告按 layer/tag
拆分通过率并给出 p50/p95 延迟。Mock 新增双来源、缺少分数、百分制分数和空答案，连同无证据、低分、
提示注入构成可重复故障注入矩阵。

生产回放通过 `prepare_replay_dataset.py` 进入 staging：脚本采用字段白名单、常见 PII/凭证遮盖和加盐
trace 哈希，不复制答案、用户 ID 与原始引用；所有生产标签默认 `unreviewed`，未经人工复标不能进入评测。
当前 40 条仅是合成回放种子，因此不能宣称已覆盖真实线上分布。

Docker 实测结果：34/34 单元测试、90/90 黄金集、70/70 对抗集、40/40 回放种子集；200 条总体
case pass rate、意图、工具序列、工作流、证据判定、反馈定位门禁和安全拒答均为 1.0。这个结果证明当前
确定性 Mock 契约回归通过，不等价于真实模型质量或统计显著性。

## 7. Execution Harness：六个最新面试考点的最小闭环

1. **运行预算**：API 用 `asyncio.timeout` 限制总时长，用 LangGraph `recursion_limit` 限制步数；
   Tool Policy 限制工具总次数；输入在运行前计算估算 Token，超限时以明确 termination reason 失败。
2. **轻量 Trace**：新增 append-only `run_events`，记录 HTTP、LLM、工具策略、上下文与终止 span；
   每条事件包含状态、耗时、父级名称和结构化 payload，可由 `/runs/{id}/harness` 查询。
3. **版本指纹**：每个 run 启动时固化 app、prompt、tool schema、evidence policy 与 dataset 版本，
   避免线上问题无法回答“当时究竟运行了哪套配置”。
4. **上下文工程**：只选最近有限轮次，做去重、字符预算和注入模式隔离；RAG 引用同时去重、限量、截断。
5. **Tool Contract / 权限沙箱**：未知工具默认拒绝；契约声明输入输出、风险、角色、超时、输出上限和 egress；
   `submit_feedback` 是高风险写操作，未获人工批准不会执行。
6. **模型路由与降级**：意图、普通生成和高风险反馈可选不同模型；连续失败触发进程内熔断，
   最终降级到确定性规则，并把选模原因、fallback 和估算 Token 写入 Trace。

这是可运行的面试级实现，不宣称等同于完整 OpenTelemetry、跨副本分布式熔断、真实 token/cost 计费、
容器级 seccomp 沙箱或生产模型网关。下一步可把 `run_events` 导出到 OTel/Prometheus，把熔断状态放进 Redis，
并用真实 usage 字段和价格表替换字符估算。

## 8. 面试时主动承认的边界

1. 相关度不是事实蕴含度；v3 是 serving-time 第一层门禁，后续应做逐 claim 引用对齐。
2. 规则检测无法覆盖所有提示注入；生产应叠加内容分类器、摄入侧扫描、最小权限与安全告警。
3. 文档对比应真正按 document filter 分别召回；当前通过来源多样性门禁避免假装比较成功。
4. `create_all` 仅用于新环境演示；生产 schema 变更应使用 Alembic 并演练回滚。
5. 对接真实知识库维护系统时要传下游幂等键；当前 CAS 只关闭本服务的并发恢复窗口。
6. 200/200 是版本化 Mock 回归结果；上线前仍需真实 RAGFlow 黄金标注、生产回放、漂移监控和置信区间。

## 9. 参考依据

- [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)：RAG 不能消除提示注入，需要输入/输出过滤、高风险人工审批和对抗测试。
- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)：需要覆盖摄入、检索、生成、输出与 Agent 工具的完整信任链。
- [Google Cloud Check Grounding](https://cloud.google.com/generative-ai-app-builder/docs/check-grounding)：通过 support score、claim/citation 和阈值在推理时筛除不可靠回答。
- [Google Cloud RAG reference architecture](https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-vertex-ai)：将 serving 与 quality evaluation 作为独立但联动的子系统。
