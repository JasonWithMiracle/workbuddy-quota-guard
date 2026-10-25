#!/usr/bin/env node
/**
 * workbuddy-quota-guard —— SessionStart 配额预检 Hook
 *
 * 作用：会话开始时检查各模型配额状态，把高风险模型提前告知用户，
 *       避免任务跑到一半才撞 429。
 *
 * 安装：编辑 ~/.workbuddy/settings.json
 *   {
 *     "hooks": {
 *       "SessionStart": [
 *         {
 *           "type": "command",
 *           "command": "node /absolute/path/to/hooks/quota-precheck.js",
 *           "timeout": 20
 *         }
 *       ]
 *     }
 *   }
 *
 * 行为约定：
 *   - 只读。不修改、不删除任何 WorkBuddy 数据。
 *   - 失败时静默退出（exit 0），绝不阻断会话启动。
 *   - 通过环境变量 WB_QUOTA_GUARD_HOME 指定 WorkBuddy 数据根目录。
 */

'use strict';

const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const SKILL_DIR = path.resolve(__dirname, '..');
const SCRIPT = path.join(SKILL_DIR, 'skill', 'scripts', 'quota_guard.py');
const PYTHON = process.env.WB_QUOTA_GUARD_PYTHON || findPython();
const HOME_DIR = process.env.WB_QUOTA_GUARD_HOME || path.join(os.homedir(), '.workbuddy');

function findPython() {
  const candidates = ['python', 'python3', 'py'];
  for (const c of candidates) {
    try {
      const r = spawnSync(c, ['--version'], { encoding: 'utf8' });
      if (r.status === 0) return c;
    } catch (_) { /* ignore */ }
  }
  return 'python';
}

function main() {
  if (!PYTHON || !fs.existsSync(SCRIPT)) {
    process.exit(0);
  }
  if (!fs.existsSync(HOME_DIR)) {
    process.exit(0);
  }

  let out;
  try {
    const r = spawnSync(PYTHON, [SCRIPT, 'status', '--json', '--quiet'], {
      encoding: 'utf8',
      timeout: 15000,
      windowsHide: true,
    });
    if (r.status !== 0 || !r.stdout) process.exit(0);
    out = JSON.parse(r.stdout);
  } catch (_) {
    process.exit(0);
  }

  const models = (out && out.models) || [];
  const high = models.filter((m) => m.risk === 'high');
  if (!high.length) process.exit(0);

  const lines = [];
  lines.push('');
  lines.push('⚠️ 配额预警：以下模型当前窗口用量已超估计限额，撞 429 风险较高');
  lines.push('');
  for (const m of high) {
    const ratio = typeof m.usage_ratio === 'number' ? `${(m.usage_ratio * 100).toFixed(0)}%` : '未知';
    lines.push(`   🔴 ${m.model.padEnd(24)} 已用 ${ratio.padStart(5)} | 重置 ${m.next_reset} | 历史 429 ${m.hits_429} 次`);
  }
  lines.push('');
  lines.push('   建议：新任务换用额度更充裕的模型，或等待下次窗口重置。');
  lines.push('   详情：python ' + SCRIPT + ' status');
  lines.push('');

  process.stdout.write(lines.join('\n'));
  process.exit(0);
}

main();
