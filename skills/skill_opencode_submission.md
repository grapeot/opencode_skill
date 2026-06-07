# OpenCode Session 交互技能

本 skill 提供三种操作，走同一个 CLI package。**用哪个取决于你能不能说出 session ID**：

| 你的场景 | 用哪个命令 | 关键参数 |
|---------|-----------|---------|
| 新建一个任务 | `submit` | `--prompt-file`, `--title` |
| 往已有 session 追加消息 | `append` | `--session-id`, `--prompt-file` |
| 批量创建 session | `batch submit` / `batch qa` | `--template`, `--specs` |

**禁止用 `submit` 假装 `append`**：`submit` 会创建新 session。即使你的意图是跟已有对话继续，`submit` 也只会给你一个全新的 session。你有 session ID 就用 `append`，没有 session ID 但知道对话存在就先从 SQLite 查出来再用 `append`。

以下触发词指向 `append`，不要在这组词下执行 `submit`："发消息给 session"、"回复 session"、"往 session 里追加"、"给这个对话继续"、"通知这个 session"。

## Prerequisites

项目根目录含 `pyproject.toml`，Python 环境用 `uv` 创建的 `.venv`。

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

HTTP 提交配置通过 `.env` 或 CLI 显式参数。prompt、session ID、路径视为私密数据，不要输出到日志或 repo。

## 操作一：新建 session (`submit`)

### 基础流程

```bash
# dry-run 必须先跑
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "任务标题" --dry-run --json

# 确认 assistant 返回 OK 后正式提交
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --title "任务标题"
```

`submit` 默认创建 session、发送 prompt、立即返回。session 保留不删，方便后续审计或用 `append` 追加。

### 更多选项

```bash
# 阻塞等待 OpenCode 完成
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --wait

# 完成后删除 session（仅当用户明确要求不可审计的临时任务）
.venv/bin/python -m opencode_skill submit --prompt-file prompt.md --delete-session
```

### 定时提交

把 prompt 文件放在 `prompts/` 目录（稳定、不会被 tmp 清理），通过 Process Launcher 调度。流程与下面 `append` 的定时流程完全相同，只需把命令换成 `submit`。

---

## 操作二：往已有 session 追加消息 (`append`)

### 基础流程

```bash
# dry-run 必须先跑
.venv/bin/python -m opencode_skill append --session-id ses_xxx --prompt-file followup.md --dry-run --json

# 正式追加
.venv/bin/python -m opencode_skill append --session-id ses_xxx --prompt-file followup.md
```

dry-run 不会真的往目标 session 写内容。它会创建一个临时 session 发一条内置 OK prompt 来验证路由通畅，然后删除临时 session。`--json` 输出里 `target_session_id` 是目标 session，`session_id` 是临时的 dry-run session。

### 我不知道 session ID 怎么办

查 OpenCode SQLite 的 `session` 表。用当前工作目录过滤，按最近更新时间降序，取顶部候选。用 `append --dry-run` 验证，通过再正式追加。如果同目录下多个活跃 session 或 title/time 信号不一致，不要猜，问用户要明确的 session ID。

```sql
SELECT id, title, directory, time_updated
FROM session
WHERE directory = '<当前工作目录>'
ORDER BY time_updated DESC
LIMIT 8;
```

### 定时追加（`append` + Process Launcher）

不要在 shell 里裸写 `sleep 7200 && append`。用 Process Launcher 的 durable scheduler：任务写入 SQLite，launcher 重启后恢复 `pending` 任务。

```bash
# 1. 把 prompt 写入 prompts/ 目录下的稳定文件
# 2. 跑 dry-run 验证
.venv/bin/python -m opencode_skill append --session-id ses_xxx --prompt-file prompts/reminder.md --dry-run --json

# 3. 通过 Process Launcher 调度
curl -X POST http://localhost:7997/run \
  -H 'Content-Type: application/json' \
  -d '{
    "command": ["/absolute/path/to/opencode_skill/.venv/bin/python", "-m", "opencode_skill", "append", "--session-id", "ses_xxx", "--prompt-file", "/absolute/path/to/prompts/reminder.md", "--send-timeout", "5"],
    "cwd": "/absolute/path/to/opencode_skill",
    "label": "opencode_append_reminder",
    "delay_seconds": 7200,
    "timeout": 300
  }'
```

---

## 操作三：批量 submit (`batch submit` / `batch qa`)

`batch submit` 从模板 + 规格文件批量创建 session。`batch qa` 对已创建的 batch slug 做 QA 分组。

```bash
# dry-run 先
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --dry-run

# smoke test 一个 slug
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs --smoke-slug sample

# 全量提交
.venv/bin/python -m opencode_skill batch submit --template template.md --specs specs/ --output-root tmp/batch_runs

# QA 分组
.venv/bin/python -m opencode_skill batch qa --slugs alpha,beta --output-root tmp/batch_runs --group-size 2 --dry-run
```

Batch session 标题必须以 `batch-` 开头，方便后续 archive selector 审计。生成的 manifest 和 rendered prompt 是运行时产物，不入 git。

---

## 输出格式

`submit` 输出 session ID、状态、删除状态。`--json` 输出版本相同的 JSON。`submit --dry-run` 成功标准是 assistant 返回 `OK`。

`append` 输出格式相同。对于真实追加，`session_id` 是目标 session。对于 `append --dry-run`，`session_id` 是临时 dry-run session，`target_session_id` 是被验证的目标 session。

`batch` 操作在输出根目录下写 `batch_manifest.json` 和 rendered prompt 文件。

---

## 安全规则

- prompt 内容用 `--prompt-file` 或 `--stdin`，不要裸写在命令行里
- 每次操作前必须先跑对应命令的 `--dry-run`
- 定时任务的 prompt 文件放 `prompts/` 目录，不要放 `tmp/`
- 不要提交 `.env`、日志、manifest、rendered prompt、导出 session、真实操作报告到 git
- privacy review 时报告文件路径和类别，不打印 prompt 或消息正文
- 不要把私有 endpoint、模型名、agent 名、模板、session ID、本地路径写进这个 public repo

## DeepSeek 模型注意项

以下规则针对 DeepSeek 模型的认知特征。如果你不是 DeepSeek，跳过本节。

**能力表必须在文件顶部显式列出。** DeepSeek 读 skill 时高度信任文件的 framing。如果标题暗示"这是提交技能"、前几段只出现 `submit`，DeepSeek 会锁定"这个 package 只能 submit"的认知框架。之后即使在 CLI help 里看到 `append` 这个词也不会触发警觉，因为框架已闭合。所以本文件开头就是能力速查表和触发词映射。

**不要用"详见 X 文件"做主要交接。** DeepSeek 不会主动跟随手动链接。所有操作的命令和参数必须 inline 出现在本文件中。`skill_opencode_periodic_job.md` 和 `skill_opencode_data.md` 是对应场景的 canonical reference，但它们的内容不影响 DeepSeek 对当前文件的理解。

**dry-run 是协议，不是建议。** DeepSeek 有"用户说做就直接做"的倾向。每次操作前，必须先跑对应命令的 `--dry-run`，确认返回 OK 或验证通过后，再跑正式命令。这不是可选的，是执行流程的硬前置。

**当多个命令同时出现时，按"任务匹配度"选择，不按"出现频率"选择。** 本文件开头的能力表列出了三种操作。DeepSeek 容易因为 `submit` 出现次数多而默认用它，忽略了 `append` 才是当前任务的正确选择。选命令的方法是：先读触发词映射，找不到匹配再看能力表的"你的场景"列逐行对照。
