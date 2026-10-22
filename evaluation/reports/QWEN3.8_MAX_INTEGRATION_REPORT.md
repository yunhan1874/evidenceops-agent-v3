# qwen3.8-max 接入与 Harness 完整测试报告

测试日期：2026-08-19  
项目：EvidenceOps Agent v3.1 Harness  
模型入口：阿里云百炼 OpenAI-compatible Chat Completions  
实际服务端返回模型：`qwen3.8-max`

## 1. 测试结论

`qwen3.8-max` 已成功接入意图识别与知识反馈草稿生成链路。真实模型 Harness 集成套件、原有 smoke
回归、幂等与并发审批测试全部通过。测试密钥未写入 `.env`、源码或报告；测试完成后从运行容器移除。

## 2. 真实模型最小探测

| 请求模型 | HTTP | 服务端返回 model | 结果 |
| --- | ---: | --- | --- |
| `qwen3.8-max` | 200 | `qwen3.8-max` | 可用 |
| `qwen3.7-max` | 200 | `qwen3.7-max` | 可用 |

公开模型文档在测试时仍主要列出 `qwen3.7-max`，但在线服务端已经确认 `qwen3.8-max` 可用。因此报告以
服务端响应为准，同时保留精确 model ID 检查，未把相近名称误判为成功。

## 3. 真实 Qwen Harness 套件

结果文件：`evaluation/reports/qwen3.8-max-harness-report.json`

- 8 个端到端阶段全部通过；
- 10 条 LLM route 均由 `qwen3.8-max` 实际完成；
- `fallback_count = 0`；
- Provider 实报总 Token：2626；
- Harness 事件：57 条；
- 事件类型：`lifecycle`、`context`、`policy`、`span`、`termination`；
- 5 个 Tool Contract 均可查询，`deny_by_default = true`。

覆盖路径：

1. 知识问答与 Evidence Passport；
2. 带历史摘要的上下文追问；
3. 双来源文档对比；
4. 间接 Prompt Injection 阻断；
5. 模糊知识反馈定位门禁；
6. 明确知识反馈进入 `pending_approval`；
7. 人工批准后执行 `submit_feedback`；
8. unsupported 请求拒绝执行。

## 4. 兼容与并发回归

真实模型 smoke：9/9 通过。

| 指标 | 结果 |
| --- | ---: |
| Intent accuracy | 1.0 |
| Task completion | 1.0 |
| Tool selection | 1.0 |
| Workflow accuracy | 1.0 |
| Evidence verdict | 1.0 |
| P50 | 4386.27 ms |
| P95 | 12086.81 ms |
| Max | 12086.81 ms |

并发和幂等：

- 相同幂等键与相同请求返回同一 run；
- 相同幂等键与不同请求返回 409；
- 双并发审批状态码为 `[200, 409]`；
- `submit_feedback` 只执行一次；
- 最终状态为 `completed`。

## 5. 测试发现并修复的问题

### 5.1 模型为模糊反馈虚构具体目标

第一次真实测试中，用户只输入“知识库有问题，请提交反馈”，模型生成了 `knowledge_item=内部知识库`，
导致流程错误进入审批。修复后，副作用资格必须同时通过原始用户文本的确定性定位门禁；模型不能通过补全
一个貌似具体的名称绕过 `needs_input`。

### 5.2 短暂上游错误触发 fallback

连续请求中曾出现一次 HTTP 错误，系统安全降级到规则路由。随后增加仅适用于无业务副作用 LLM 调用的
受控重试：只重试网络异常、429 和 5xx，默认最多两次，并把 `retry_count`、最终状态码和稳定错误码写入
Harness。RAGFlow 的有状态 completion 仍不盲目重试。

## 6. 单元测试与边界

- 34 个单元测试、6 个 subtests 通过；
- 新增 Provider model/usage、模糊反馈防模型虚构目标、429 受控重试测试；
- 本次真实模型测试使用真实 Qwen + Mock RAGFlow + 真实 PostgreSQL/checkpoint；
- 不把该结果包装成真实知识库检索质量；真实 RAGFlow 还需独立黄金集和线上脱敏回放；
- 未用真实 Max 模型跑 200 条全量集，避免无必要费用；200 条确定性工程基线在移除密钥后单独执行。

