# 针对原 app.py 的实际修改顺序

## 第一步：立即处理

- 将原 `app.py` 和 `docker-compose.yml` 中的所有密钥废弃并轮换；
- 删除默认密钥，全部从 `.env` 读取；
- 原 Docker `--workers 2` 先改为 `--workers 1`，因为原代码使用进程内 Token Cache、黑名单和队列；
- 删除 `add_request_id()` 第一个 `return response` 后的不可达代码；
- `rag_feedback()` 在保存失败时返回 503，不再返回伪成功；
- 将所有 `requests.get/post/delete` 迁移到共享 `httpx.AsyncClient`。

## 第二步：前端字段变化

旧请求：

```json
{"query":"...","session_id":"RAGFlow ID","chat_id":"RAGFlow Chat ID"}
```

新请求：

```json
{"query":"...","session_id":"本系统 UUID"}
```

`chat_id` 与 RAGFlow Session ID 不再由前端掌握，避免越权。

## 第三步：保留旧服务并行迁移

不要把 2600 行原文件一次性重写。先让新服务独立运行，通过 Mock 模式验证 Agent Graph、审批、轨迹和评测；再接真实 RAGFlow，最后迁移飞书登录。
