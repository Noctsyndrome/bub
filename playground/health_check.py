#!/usr/bin/env python3
"""
Bub 运行状态检查脚本。

检查内容：
1. 是否存在 Bub 相关运行进程（chat / message / idle）
2. 核心环境变量是否已配置（BUB_MODEL + 至少一种 LLM API Key）
3. 渠道开关是否启用（Telegram / Discord）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _prepare_stdout() -> None:
    """避免 Windows cmd(GBK) 下输出 emoji 时崩溃。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # 某些环境（被重定向/旧解释器）可能不支持 reconfigure
        pass


def _repo_root() -> Path:
    # 当前文件位于 <repo>/playground/health_check.py
    return Path(__file__).resolve().parents[1]


def _env_file() -> Path:
    return _repo_root() / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def _merged_env() -> dict[str, str]:
    merged = _parse_env_file(_env_file())
    # 进程环境覆盖 .env（更接近运行时真实状态）
    for key, value in os.environ.items():
        merged[key] = value
    return merged


def _process_lines_windows() -> list[str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,Name,CommandLine | "
            "ForEach-Object { "
            "$cl = if ($_.CommandLine) { $_.CommandLine } else { '' }; "
            "\"$($_.ProcessId)`t$($_.Name)`t$cl\" }"
        ),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return result.stdout.splitlines()


def _process_lines_unix() -> list[str]:
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    return result.stdout.splitlines()


def check_process() -> tuple[bool, list[str]]:
    """检查 Bub 相关进程是否在运行。"""
    try:
        lines = _process_lines_windows() if os.name == "nt" else _process_lines_unix()
        matched: list[str] = []
        for line in lines:
            lower = line.lower()
            if "health_check.py" in lower:
                continue
            if "bub" not in lower:
                continue
            if any(mode in lower for mode in (" bub chat", " bub message", " bub idle", "uv run bub chat", "uv run bub message", "uv run bub idle")):
                matched.append(line.strip())
        return (len(matched) > 0, matched)
    except Exception as exc:
        return (False, [f"进程检查异常: {exc}"])


def detect_running_modes(processes: list[str]) -> list[str]:
    """从进程命令行中推断 Bub 运行模式。"""
    modes: list[str] = []
    for line in processes:
        lower = line.lower()
        for mode in ("chat", "message", "idle"):
            token1 = f" bub {mode}"
            token2 = f"uv run bub {mode}"
            if token1 in lower or token2 in lower:
                if mode not in modes:
                    modes.append(mode)
    return modes


def check_env_vars() -> dict[str, bool]:
    """检查关键环境变量（按 Bub 常见配置规则）。"""
    env = _merged_env()
    return {
        "BUB_MODEL": bool(env.get("BUB_MODEL")),
        "OPENROUTER_API_KEY": bool(env.get("OPENROUTER_API_KEY")),
        "LLM_API_KEY": bool(env.get("LLM_API_KEY")),
    }


def check_channels_enabled() -> list[str]:
    """检查渠道开关是否启用。"""
    env = _merged_env()
    enabled_channels: list[str] = []

    if env.get("BUB_TELEGRAM_ENABLED", "").strip().lower() == "true":
        enabled_channels.append("telegram")
    if env.get("BUB_DISCORD_ENABLED", "").strip().lower() == "true":
        enabled_channels.append("discord")

    return enabled_channels


def run_health_check() -> None:
    """执行完整检查并输出中文结果。"""
    print("Bub 运行状态检查")
    print("=" * 50)
    print(f"仓库目录: {_repo_root()}")
    print(f".env 路径: {_env_file()}")

    # 1) 进程状态
    print("\n1. 检查进程状态...")
    is_running, processes = check_process()
    if is_running:
        print("通过 - 检测到 Bub 运行进程")
        for proc in processes:
            print(f"  {proc}")
    else:
        print("错误 - 未检测到 Bub 运行进程（chat / message / idle）")
        if processes:
            for item in processes[:3]:
                print(f"  提示: {item}")

    # 2) 环境变量
    print("\n2. 检查环境变量...")
    found_vars = check_env_vars()
    for var in ("BUB_MODEL", "OPENROUTER_API_KEY", "LLM_API_KEY"):
        if found_vars.get(var):
            print(f"通过 - {var} 已配置")
        else:
            print(f"警告 - {var} 未配置")

    llm_key_ok = found_vars["OPENROUTER_API_KEY"] or found_vars["LLM_API_KEY"]
    if llm_key_ok:
        print("通过 - 已配置至少一种 LLM API Key（OPENROUTER_API_KEY / LLM_API_KEY）")
    else:
        print("错误 - 未配置任何 LLM API Key（OPENROUTER_API_KEY / LLM_API_KEY）")

    # 3) 渠道配置
    print("\n3. 检查渠道配置...")
    enabled_channels = check_channels_enabled()
    if enabled_channels:
        print(f"通过 - 已启用渠道: {', '.join(enabled_channels)}")
    else:
        print("信息 - 未启用 Telegram/Discord 渠道（.env 或环境变量）")

    # 汇总
    print("\n汇总:")
    print("通过 - 进程状态正常" if is_running else "错误 - 进程未运行")
    print("通过 - BUB_MODEL 已配置" if found_vars["BUB_MODEL"] else "警告 - BUB_MODEL 未配置")
    print("通过 - LLM API Key 满足要求（至少一个）" if llm_key_ok else "错误 - LLM API Key 不满足要求")
    if enabled_channels:
        print(f"通过 - 已启用 {len(enabled_channels)} 个渠道: {', '.join(enabled_channels)}")
    else:
        print("信息 - 当前未启用渠道（若你只跑 chat 模式这是正常的）")


def build_channel_status_text() -> str:
    """构建适合 Discord/Telegram 频道的简洁状态文本（单行）。"""
    is_running, processes = check_process()
    found_vars = check_env_vars()
    enabled_channels = check_channels_enabled()
    modes = detect_running_modes(processes if is_running else [])

    model_ok = found_vars.get("BUB_MODEL", False)
    llm_key_ok = found_vars.get("OPENROUTER_API_KEY", False) or found_vars.get("LLM_API_KEY", False)

    run_part = (
        f"🟢运行中({','.join(modes)})"
        if is_running and modes
        else ("🟢运行中" if is_running else "🔴未运行")
    )
    model_part = "🧠模型OK" if model_ok else "🧠模型缺失"
    key_part = "🔑密钥OK" if llm_key_ok else "🔑密钥缺失"
    channel_part = f"📡渠道:{','.join(enabled_channels)}" if enabled_channels else "📡渠道:未启用"

    # 单行、短句、带 emoji，适合频道展示与日志回帖
    return f"🤖 Bub状态 | {run_part} | {model_part} | {key_part} | {channel_part}"


if __name__ == "__main__":
    _prepare_stdout()
    # 默认输出频道友好文本；如需排查细节，执行: python playground/health_check.py --verbose
    if "--verbose" in sys.argv or "-v" in sys.argv:
        run_health_check()
    else:
        print(build_channel_status_text())
