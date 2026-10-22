# EvidenceOps Agent v3 三层评测指南

## 1. 目标与边界

这套评测回答的是“当前版本是否满足已声明的工程契约”，而不是用少量 Mock 样本证明大模型能力。
评测对象包括意图路由、工具编排、证据门禁、知识反馈定位、人工审批和副作用安全。

当前版本：`evaluation-case/v2`，数据版本 `2026-08-05.v2`。

## 2. 三层数据集

| 层级 | 数量 | 主要用途 | 当前数据来源 |
| --- | ---: | --- | --- |
| Gold | 90 | 稳定业务契约和正常边界 | 人工设计的确定性业务样本 |
| Adversarial | 70 | 失败、攻击、格式和意图边界 | 确定性故障注入与对抗样本 |
| Replay | 40 | 验证回放格式和流水线 | 无 PII 合成种子，不是生产日志 |

`manifest.json` 记录每个文件的数量与 SHA-256。评测器加载分层数据时会校验摘要，避免文件被修改后仍沿用
旧报告。`cases.jsonl` 的 9 条用例继续作为开发期快速 smoke 集。

## 3. 关键断言

单个 case 可以声明：

- `expected_intent`：预期意图；
- `expected_tool_sequence`：完整、有序的工具调用，而非“出现过某工具”；
- `expected_initial_status` / `expected_final_status`：审批前后状态；
- `expected_pending_approval`：是否真的进入人工确认；
- `expected_tool_statuses`：如模糊反馈的 `create_feedback_draft=needs_input`；
- `forbidden_tools` / `expected_submit_count`：拒绝、取消或模糊反馈不得产生写副作用；
- `expected_feedback`：问题类型、优先级、目标定位状态和知识条目片段；
- `expected_evidence_verdict` / `expected_risk_flags`：证据策略结果；
- `expected_safe_abstention`：阻断时不得泄露 Mock 候选答案。

## 4. 指标

- `case_pass_rate`：样本全部断言通过率；
- `intent_accuracy`、`tool_selection_accuracy`、`task_completion_rate`；
- `workflow_accuracy`：初始状态、审批状态、工具状态和禁止调用是否正确；
- `evidence_verdict_accuracy`、`unsafe_answer_block_rate`；
- `feedback_target_gate_accuracy`、`feedback_schema_accuracy`；
- `approval_side_effect_safety`：批准只提交一次，拒绝和模糊反馈提交零次；
- layer/tag 细分通过率与端到端 latency p50/p95/max。

## 5. 运行方式

```bash
# 只校验 schema、唯一 case_id 和数据集摘要
python scripts/evaluate.py --suite all --validate-only

# 9 条快速回归
python scripts/evaluate.py --base-url http://localhost:8000 --suite smoke

# 200 条全量回归，并生成可审计 JSON 报告
python scripts/evaluate.py --base-url http://localhost:8000 --suite all \
  --report evaluation/reports/layered-evaluation-v2.json --fail-under 1.0
```

当前 Docker 验证：26/26 单元测试、200/200 全量 case、9/9 smoke；并发审批保持 `[200, 409]`，
`submit_feedback` 只出现一次。

## 6. 生产回放接入

原始线上日志必须先经过数据授权、最小化和脱敏，禁止直接提交到仓库：

```bash
export REPLAY_HASH_SALT='at-least-16-private-characters'
python scripts/prepare_replay_dataset.py raw-runs.jsonl \
  --output evaluation/replay_staging.jsonl
```

转换器只复制评测所需白名单字段，对常见邮箱、手机号、身份证号、工号和凭证做遮盖，并用本地盐生成
不可直接反查的 trace hash。它不会复制用户 ID、最终答案或原始引用。自动脱敏不能替代组织级 DLP；
人工审核者仍需检查残留专有名词和上下文身份信息、纠正观测标签，并把 `review_status` 改为 `approved`。
评测器会拒绝未审核的生产回放标签。

## 7. 防止指标自欺

1. Mock 标记只用于验证控制流，不能衡量真实检索和生成质量。
2. Replay 种子只验证工程管道，不代表线上分布。
3. 调整规则后必须运行原数据集，不能删除失败样本来“修复”指标。
4. 真实上线应冻结测试集、隔离训练集，并报告置信区间、分业务切片指标和版本间差异。
5. 生产阶段还应增加逐 claim 忠实度、引用召回率、人工复核一致性、漂移与成本指标。
