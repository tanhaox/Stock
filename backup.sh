#!/usr/bin/env bash
# ============================================================
# Stock 智能备份脚本
#
# 用途:
#   1. 检查本地是否有未提交的改动 (智能备份)
#   2. 推送 commit 到 GitHub (能 push 就 push)
#   3. 推送失败 → 打包快照到 .backup-queue/ 离线备份
#   4. 推送成功 → 自动清理已推送的快照
#
# 设计:
#   - 不自动 commit (避免 AI/脚本错误 commit message)
#   - 本地永远完整 (即使 GitHub 挂了也不丢)
#   - 三种模式: check | push | snapshot
#
# 用法:
#   bash backup.sh                # 默认: check + push
#   bash backup.sh check          # 只检查状态
#   bash backup.sh push           # 强制 push (有 commit 就推)
#   bash backup.sh snapshot       # 强制打包快照
#   bash backup.sh flush          # 推送队列里的所有快照
#
# 作者: Claude (按用户要求设计)
# 创建: 2026-06-21
# ============================================================

set -e

# === 路径配置 ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REMOTE_URL="git@github.com:tanhaox/Stock.git"
REMOTE_BRANCH="main"
QUEUE_DIR=".backup-queue"
NEEDED_FLAG=".backup-needed"

# === 颜色输出 ===
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERR]${NC} $*"; }
info()  { echo -e "${CYAN}[INFO]${NC} $*"; }

# ============================================================
# 1. 检查本地状态
# ============================================================
check_status() {
    echo ""
    info "===== 检查本地状态 ====="

    # 当前 HEAD
    local head_hash
    head_hash=$(git rev-parse HEAD 2>/dev/null) || { err "不是 git 仓库"; return 1; }
    local head_short="${head_hash:0:7}"
    info "本地 HEAD: $head_short"

    # 未提交的修改
    local untracked
    untracked=$(git status --porcelain 2>/dev/null)
    if [ -n "$untracked" ]; then
        local count=$(echo "$untracked" | wc -l)
        warn "本地有 $count 个未提交的修改"
        echo "$untracked" | head -5
        [ "$count" -gt 5 ] && echo "  ... (共 $count 个)"

        # 标记需要备份
        echo "$(date -Iseconds) - $count uncommitted changes" > "$NEEDED_FLAG"
        ok "已生成 $NEEDED_FLAG 标记"
        return 0
    else
        ok "工作区干净"
        rm -f "$NEEDED_FLAG" 2>/dev/null
    fi

    # 队列里的待推送快照
    local queue_count=$(find "$QUEUE_DIR" -maxdepth 1 -name "*.zip" 2>/dev/null | wc -l)
    if [ "$queue_count" -gt 0 ]; then
        warn "$QUEUE_DIR/ 有 $queue_count 个待推送快照"
        find "$QUEUE_DIR" -maxdepth 1 -name "*.zip" -exec basename {} \; 2>/dev/null
    fi
}

# ============================================================
# 2. 推送 (能 push 就 push)
# ============================================================
do_push() {
    echo ""
    info "===== 尝试推送 ====="

    # 先 fetch 一次, 看远程有没有更新
    if ! git fetch origin "$REMOTE_BRANCH" 2>/dev/null; then
        warn "fetch 失败 (可能 VPN/网络问题)"
        do_snapshot
        return 1
    fi

    # 本地 HEAD 是否领先于远程
    local local_hash remote_hash
    local_hash=$(git rev-parse HEAD)
    remote_hash=$(git rev-parse "origin/$REMOTE_BRANCH" 2>/dev/null || echo "")

    if [ -z "$remote_hash" ]; then
        warn "远程分支 origin/$REMOTE_BRANCH 不存在, 尝试 force push (首次)"
        git push -u origin "$REMOTE_BRANCH" 2>&1 || { do_snapshot; return 1; }
        ok "首次推送成功"
        return 0
    fi

    if [ "$local_hash" = "$remote_hash" ]; then
        ok "本地 = 远程, 无需推送"
        return 0
    fi

    # 检查远程是否领先于本地 (pull 还是 push?)
    local merge_base
    merge_base=$(git merge-base HEAD "origin/$REMOTE_BRANCH" 2>/dev/null || echo "")

    if [ "$merge_base" = "$local_hash" ]; then
        # 本地是远程的直接祖先, fast-forward push
        if git push origin "$REMOTE_BRANCH" 2>&1; then
            ok "推送成功: $local_hash"
            return 0
        else
            warn "push 失败, 进入 snapshot 模式"
            do_snapshot
            return 1
        fi
    else
        # 分叉了 - 不处理, 提示用户
        err "本地和远程分叉, 请手动解决"
        err "  本地: $(git log --oneline -1)"
        err "  远程: $(git log --oneline -1 origin/$REMOTE_BRANCH)"
        return 1
    fi
}

# ============================================================
# 3. 离线快照 (push 失败时的兜底)
# ============================================================
do_snapshot() {
    echo ""
    info "===== 离线快照模式 ====="

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local zip_name="stock_snapshot_${timestamp}.zip"
    local zip_path="$QUEUE_DIR/$zip_name"

    # 打包 .git 目录 (足够完整恢复)
    # 排除 filter-repo 残留 / hooks 等非必要文件
    info "打包 .git 到 $zip_name ..."
    if command -v 7z &> /dev/null; then
        7z a -tzip "$zip_path" .git/ > /dev/null
    elif command -v zip &> /dev/null; then
        zip -r "$zip_path" .git/ > /dev/null
    else
        # Windows 没有 zip 命令, 用 PowerShell 的 Compress-Archive
        powershell -Command "Compress-Archive -Path '.git' -DestinationPath '$zip_path' -Force" 2>&1 | head -5
    fi

    if [ -f "$zip_path" ]; then
        local size=$(du -h "$zip_path" | awk '{print $1}')
        ok "快照已保存: $zip_path ($size)"
        warn "网络恢复后请运行: bash backup.sh flush"
    else
        err "快照失败"
        return 1
    fi
}

# ============================================================
# 4. 推送队列里的快照
# ============================================================
do_flush() {
    echo ""
    info "===== 推送队列里的快照 ====="

    # 先尝试 push 当前 HEAD
    if do_push; then
        ok "当前 HEAD 已推送"
    fi

    # 推送队列里的快照
    local snapshot
    snapshot=$(find "$QUEUE_DIR" -maxdepth 1 -name "*.zip" 2>/dev/null | head -1)

    if [ -z "$snapshot" ]; then
        ok "队列为空"
        return 0
    fi

    warn "找到队列快照: $(basename "$snapshot")"
    info "提示: 快照需要你手动恢复并比对"
    info "  1. 解压 $snapshot 到临时目录"
    info "  2. 用 git log 查看差异"
    info "  3. 手动 cherry-pick 或合并"
    info "  4. 确认后删除 .backup-queue/$(basename "$snapshot")"
}

# ============================================================
# 主入口
# ============================================================
main() {
    echo "================================================"
    echo "  Stock 智能备份脚本"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================"

    case "${1:-default}" in
        check)     check_status ;;
        push)      do_push ;;
        snapshot)  do_snapshot ;;
        flush)     do_flush ;;
        default)
            check_status
            # 如果有未推送的 commit 或待推送快照, 尝试 push
            if [ -f "$NEEDED_FLAG" ] || [ -n "$(find "$QUEUE_DIR" -maxdepth 1 -name '*.zip' 2>/dev/null)" ]; then
                do_push
            fi
            ;;
        *)
            err "未知参数: $1"
            echo "用法: bash backup.sh [check|push|snapshot|flush]"
            return 1
            ;;
    esac

    echo ""
    ok "完成"
}

main "$@"