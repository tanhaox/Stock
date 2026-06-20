# Stock 智能备份使用指南

> **创建日期**: 2026-06-21
> **适用版本**: Stock v4.5+
> **作者**: Claude (用户委托)

---

## 📌 三层备份策略

| 层 | 方式 | 何时触发 | 存储位置 |
|---|------|---------|---------|
| **L1: GitHub 远程** | `git push` (SSH) | 推送成功时 | `https://github.com/tanhaox/Stock` (Private) |
| **L2: 本地 git** | `.git/` 目录 | 每次 commit 后自动 | `Stock/.git/` |
| **L3: 离线快照** | zip 打包 `.git/` | push 失败时兜底 | `Stock/.backup-queue/` |

> **核心理念**: 即使 GitHub 挂了、SSH 不通、VPN 受限，你的代码**永远完整**——只是最后那次推送会延迟到网络恢复。

---

## 🚀 快速使用

### 方式 1：手动触发（最简单）

```bash
# Bash (Git Bash / WSL / macOS)
cd /c/AI-Agent-Local/Stock
bash backup.sh

# 或 Windows 双击
backup.cmd
```

### 方式 2：自然语言调用 AI

直接在 Claude Code 里说：

| 你说的 | AI 做什么 |
|--------|---------|
| "备份 Stock" | `bash backup.sh` |
| "同步 Stock" | `bash backup.sh push` |
| "离线备份" | `bash backup.sh snapshot` |
| "推送队列" | `bash backup.sh flush` |
| "检查 Stock 状态" | `bash backup.sh check` |

### 方式 3：会话开始自动检查

每次 Claude Code 新会话，AI 会自动：
1. 检测 `Stock/.backup-needed` 是否存在
2. 如果有，提示你"Stock 有 N 个修改未备份，是否备份？"
3. 你说"是" → 自动 `bash backup.sh push`

---

## 📖 四种模式详解

### `default` (默认) — 检查 + 智能推送

```bash
bash backup.sh
```

执行流程：
1. 检查 `git status`
2. 有未提交修改 → 生成 `.backup-needed` 标记
3. 有未推送 commit 或队列里有快照 → 尝试 `git push`
4. push 失败 → 自动打包快照到 `.backup-queue/`

### `check` — 只检查状态

```bash
bash backup.sh check
```

只看不动。适合：
- 想知道本地和远程是否同步
- 不想在不该 commit 的时候自动 commit

### `push` — 强制推送

```bash
bash backup.sh push
```

跳过状态检查，直接尝试 `git push`。适合：
- 刚刚手动 commit 了
- 想强制覆盖远程（谨慎！）

### `snapshot` — 强制打离线快照

```bash
bash backup.sh snapshot
```

跳过 push，直接打包 `.git/` 到 `.backup-queue/`。适合：
- 出门前想留个本地备份
- 准备做危险操作前（如大重构）

### `flush` — 推送队列里的快照

```bash
bash backup.sh flush
```

网络恢复后，把 `.backup-queue/` 里的快照手动恢复并 push。流程：
1. 解压最新 zip 到临时目录
2. 对比 `git log` 差异
3. 手动 cherry-pick 或合并
4. 确认后删除对应 zip

---

## 🛡️ VPN / 网络受限怎么办？

### 问题场景

| 现象 | 原因 |
|------|------|
| `ssh: connect to host github.com port 22: Connection timed out` | SSH 被防火墙挡 |
| `fatal: unable to access 'https://github.com/...': Connection reset` | HTTPS 443 被墙 |
| `Could not resolve hostname github.com` | DNS 污染 |

### 自动兜底

脚本**自动检测**网络状态：

```bash
# 网络不通时, 自动进入 snapshot 模式
[INFO] fetch 失败 (可能 VPN/网络问题)
[INFO] ===== 离线快照模式 =====
[INFO] 打包 .git 到 stock_snapshot_20260621_153022.zip ...
[OK] 快照已保存: .backup-queue/stock_snapshot_20260621_153022.zip (3.4M)
[WARN] 网络恢复后请运行: bash backup.sh flush
```

### 手动绕过方案

**方案 A：换 SSH 端口 (走代理)**
```bash
# ~/.ssh/config 加入
Host github.com
    HostName ssh.github.com
    Port 443
    ProxyCommand connect-proxy -S 127.0.0.1:1080 %h %p
```

**方案 B：用 HTTPS + Personal Access Token**
```bash
git remote set-url origin https://tanhaox:TOKEN@github.com/tanhaox/Stock.git
# ⚠️ 不推荐: token 会暴露在 .git/config
```

**方案 C：双轨同步到本地 NAS / 移动硬盘**
```bash
# 备份到 D 盘
rsync -av --delete --exclude=node_modules --exclude=.venv \
  C:/AI-Agent-Local/Stock/ D:/Backup/Stock/

# 或用 robocopy (Windows)
robocopy C:\AI-Agent-Local\Stock D:\Backup\Stock /MIR /XD node_modules .venv
```

**方案 D：用 GitHub Actions 自动备份**
- 在 GitHub 上设 workflow，定时 `git pull` 触发备份到其他位置
- 但 GitHub 也可能受 VPN 影响，**仅适合纯境外 VPS**

---

## 🧹 维护建议

### 定期清理

```bash
# 删除超过 30 天的旧快照
find .backup-queue -name "*.zip" -mtime +30 -delete

# 删除已推送的备份 (.git.backup-before-filter 是 7.4 GB)
rm -rf .git.backup-before-filter   # 父仓库的备份, 不影响 Stock
```

### 何时删除 `.backup-queue/` 里的 zip？

满足以下**全部条件**才能删除：
- [ ] 远程 `https://github.com/tanhaox/Stock` 已经有更新的 commit
- [ ] `git ls-remote` 和本地 HEAD 一致
- [ ] 你已确认 push 成功

### 添加更多忽略规则

如果新文件类型需要忽略，编辑 `.gitignore`：
```gitignore
# 在末尾添加
*.bak
*.tmp
*.swp
```

---

## ❓ 常见问题

### Q1: 推送失败但脚本没生成快照？

检查 `.git` 目录是否损坏：
```bash
git fsck
```

### Q2: 我手动 commit 了，脚本能 push 吗？

✅ 可以。`do_push()` 会自动检测本地 HEAD 领先远程。

### Q3: 脚本会修改我的代码吗？

❌ **绝对不会**。脚本只做：
- `git fetch` / `git push` (只读/推送，不改代码)
- 打包 `.git/` (只读)

### Q4: AI (Claude) 怎么调用这个脚本？

按 CLAUDE.md 的 Backup Agent 模式：
1. 新会话开始 → AI 读 `Stock/.backup-needed`
2. 如果存在 → 提示用户
3. 用户确认 → AI 执行 `bash backup.sh`

---

## 📞 故障排查

| 错误信息 | 原因 | 解决 |
|---------|------|------|
| `bash: command not found` | Windows 没装 Git Bash | 安装 Git for Windows |
| `Permission denied` | backup.sh 无执行权限 | `chmod +x backup.sh` |
| `fatal: not a git repository` | 不在 Stock 目录 | `cd C:\AI-Agent-Local\Stock` |
| `Could not resolve hostname` | DNS 问题 | 改用 SSH 或换网络 |

---

**最后更新**: 2026-06-21 | **作者**: Claude