#!/usr/bin/env bash
# 一键安装 Skill 到 ~/.workbuddy/skills/workbuddy-quota-guard/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="${WORKBUDDY_SKILLS_DIR:-$HOME/.workbuddy/skills}"
TARGET="$SKILLS_DIR/workbuddy-quota-guard"

echo "仓库根目录：$REPO_ROOT"
echo "安装目标：$TARGET"

mkdir -p "$TARGET"
cp -r "$REPO_ROOT/skill/." "$TARGET/"

echo ""
echo "✅ Skill 已安装。验证："
echo "   python \"$TARGET/scripts/quota_guard.py\" status"
echo ""
echo "如需同时启用 Hook 自动预警，编辑 ~/.workbuddy/settings.json："
echo '  { "hooks": { "SessionStart": ['
echo "    { \"type\": \"command\", \"command\": \"node $REPO_ROOT/hooks/quota-precheck.js\", \"timeout\": 20 }"
echo "  ] } }"
