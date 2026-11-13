# 坚果云同步协议 v1

本文档冻结 `src/memo_app/sync/` 的线上协议，供跨版本兼容与审计使用。
实现：`crypto.py` `protocol.py` `transport.py` `engine.py` `vault_setup.py` `store.py`。

## 1. 远端布局

```
<base>/memo-sync/<vault_id>/vault.json          # 加密库描述（明文 JSON，内含包裹主密钥）
<base>/memo-sync/<vault_id>/heads/<writer_id>.enc   # 该 writer 最新批次 seq 指针
<base>/memo-sync/<vault_id>/commits/<writer_id>/<seq>.enc  # 批次密文（seq 从 1 递增）
```

- `vault_id` / `writer_id`：UUID，`heads`/`commits` 目录按 writer 分文件，避免共享索引覆盖。
- 业务 SQLite 文件**永不**进入同步目录；同步的是业务实体修订，不是数据库文件。

## 2. 密码学

- 业务载荷：AES-256-GCM，随机 96-bit nonce，AAD =
  `memo-batch:{v}:{vault_id}:{writer_id}:{seq}` / `memo-head:{v}:{vault_id}:{writer_id}`。
- 库主密钥 `master_key`(32B 随机)：由用户库密码经 scrypt 派生 KEK 包裹存于 `vault.json`。
- KDF 参数随 vault.json 下发但**不可信**：接收端强制 n∈[2^10,2^17] 且为 2 的幂、r∈[1,16]、p∈[1,4]、128·n·r≤256MiB。
- scrypt 参数：n=2^15, r=8, p=1。

## 3. 批次信封（解密后 JSON）

```json
{"v":1,"batch_id":"<uuid>","writer_id":"<uuid>","seq":<int>,
 "created_utc":"<iso>","ops":[<op>...]}
```

op 信封（= `sync_outbox.payload`）：

```json
{"v":1,"entity":"memo|category","entity_uuid":"<uuid>","op":"upsert|delete",
 "rev_ts":<float>,"rev_seq":<int>,"writer_id":"<uuid>","data":{...}}
```

- `rev_ts`：写入时刻 Unix 时间；`rev_seq`：同 writer 单调序号（sqlite_sequence），
  用于同 `rev_ts` 因果平局。
- LWW 比较键：`(rev_ts, writer_id, rev_seq)` 字典序，严格大于才应用。
- memo `data`：content/is_pinned/is_done/created_at/updated_at/deleted_at/due_ts/
  priority/recur/remind_advance/category_uuids[]；`notified` 与本机状态不同步。
- category `data`：name/created_at。

## 4. 上限与校验

- 批次明文 ≤ 4 MiB，ops ≤ 500/批；发送端按 ~3 MiB 估算裁剪。
- 远端文件 GET ≤ 16 MiB；PROPFIND XML 拒绝 DTD/ENTITY。
- 所有远端 JSON 视为不可信：版本、UUID、类型（拒 bool/int 别名）、NaN/Inf、
  路径穿越、writer 一致性均严格校验。
- 单条 op 应用失败（缺字段/类型错/约束冲突）→ 记修订状态跳过（bad_ops），
  不阻塞日志；所有端确定性地忽略同一 op。

## 5. 状态机与崩溃恢复

- 拉取：PROPFIND `heads/` → 逐 writer GET head → 从 `applied_seq+1` 到 `head.seq`
  顺序 GET commit → 解密 → 校验 → 每批一个 `BEGIN IMMEDIATE` 事务应用
  （业务写 + entity_state + peer_state.applied_seq 原子提交）。
- head 回退（`seq < 已观察最大`）视为异常中止，不回退不跳过。
- 发布：`sync_batches` 先持久化密文 → PUT commit（If-None-Match，已存在则
  GET 逐字节比对）→ 全部 commit 成功后 PUT head（最大 seq）→ 标记 published
  + outbox sent=2。中途崩溃由 `unpublished_batches` 下轮补齐。
- 首次同步 bootstrap：只回填"本机 writer 来源且从未入过 outbox"的实体，
  避免远端应用数据被回声发布。

## 6. 传输

- WebDAV：PROPFIND(Depth 0/1)/GET/PUT/MKCOL，标准库 urllib。
- HTTPS 强制（http 仅允许 loopback 测试）；Basic Auth（账号+第三方应用密码）；
  跨主机/HTTPS→HTTP 重定向剥离 Authorization；响应体上限。
- 错误分类：AuthError(401/403)、NotFoundError(404)、RateLimitedError(429+Retry-After)、
  PreconditionFailedError(412)、ServerError(5xx)、QuotaError(507)。

## 7. 已知限制（v1 有意取舍）

- LWW：两端同时编辑同一实体，后写覆盖先写（用户已确认接受）。
- 无附件同步、无实时协作、单用户单 vault。
- writer 克隆检测（复制数据目录）未实现——复制 DB 会产生两个同 writer_id
  的写入者，日志 seq 可能冲突（save_batch digest 校验会响亮失败）。
- `If-None-Match` 支持度依赖服务端；create_vault 用 exists 预检+写后回读兜底。
