"""demo 总入口：从配置文件读取参数，串联「拉字幕 → 本地模型分析」全流程。

用法：
  python main.py                   读取 config.yaml 执行全流程
  python main.py -c 其他配置.yaml    指定配置文件

配置文件 config.yaml 字段说明见文件内注释。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bili_cli import auth  # noqa: E402

import analyze  # noqa: E402
import fetch_subtitles  # noqa: E402

CONFIG_PATH = HERE / "config.yaml"

DEFAULT_CONFIG: dict = {
    "up": None,              # UP 主：名字（如"玄离199"）或 UID 数字
    "count": 8,              # 拉取最近 N 个视频字幕
    "model": "qwen3.5:4b",   # Ollama 模型名
    "temperature": None,     # 采样温度（null=用模型默认值）
    "analyze_mode": "all",   # all=全部 / single=单个 / bvids=指定多个
    "bvids": [],             # analyze_mode 为 single/bvids 时生效
    "skip_fetch": False,     # 跳过拉字幕步骤
    "skip_analyze": False,   # 跳过分析步骤
    "clear_subtitles": False,  # 运行前清空字幕目录（避免文件堆积）
    "clear_output": False,     # 运行前清空输出目录（避免文件堆积）
    "begin_date": None,        # 发布时间过滤：起始日期 "2026-08-01"（含）
    "end_date": None,          # 发布时间过滤：结束日期 "2026-10-31"（含）
    "sleep_range": [0.5, 2.5],  # 反爬：每个视频处理前的随机休息秒数范围（null 表示不休息）
}


def load_config(path: Path) -> dict:
    """读取 YAML 配置文件，缺失字段用默认值补齐。"""
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            print(f"错误：配置文件解析失败：{e}", file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(data, dict):
            print("错误：配置文件顶层必须是键值对。", file=sys.stderr)
            raise SystemExit(2)
        cfg.update(data)
    return cfg


def _clear_dir(path: Path) -> None:
    """清空目录（删除后重建）。"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def do_fetch(cfg: dict) -> int:
    """步骤 1：批量拉取字幕。"""
    print("=" * 60)
    print("步骤 1/2：拉取字幕")
    print("=" * 60)

    uid, up_name = fetch_subtitles.resolve_up(cfg.get("up"))
    cred = auth.get_credential(mode="read")
    if not cred:
        print("错误：未登录。字幕接口需要登录凭证，请先运行 bili login。", file=sys.stderr)
        return 2
    return asyncio.run(
        fetch_subtitles.run(
            uid,
            cfg.get("count", 8),
            cred,
            up_name,
            cfg.get("begin_date"),
            cfg.get("end_date"),
            cfg.get("sleep_range"),
        )
    )


def do_analyze(cfg: dict) -> int:
    """步骤 2：本地模型分析。"""
    print()
    print("=" * 60)
    print("步骤 2/2：本地模型分析")
    print("=" * 60)

    meta = analyze.load_meta()
    mode = cfg.get("analyze_mode", "all")
    bvids = cfg.get("bvids") or []

    if mode == "all":
        targets = analyze.collect_targets(meta, all_flag=True)
    elif mode == "single":
        targets = analyze.collect_targets(meta, bvids=bvids[:1])
    else:  # bvids
        targets = analyze.collect_targets(meta, bvids=bvids)

    if not targets:
        print("没有可分析的字幕。请先确保拉字幕步骤已生成字幕。", file=sys.stderr)
        return 2

    out_path = analyze.run_analyze(targets, cfg.get("model", "qwen3.5:4b"), cfg.get("temperature"))
    return 0 if out_path else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo 总入口：拉字幕 + 本地模型分析")
    parser.add_argument("-c", "--config", type=Path, default=CONFIG_PATH, help="配置文件路径")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    print(f"配置文件：{args.config}")

    if cfg.get("clear_subtitles"):
        _clear_dir(fetch_subtitles.OUTPUT_DIR)
        print(f"已清空字幕目录：{fetch_subtitles.OUTPUT_DIR}")
    if cfg.get("clear_output"):
        _clear_dir(analyze.OUTPUT_DIR)
        print(f"已清空输出目录：{analyze.OUTPUT_DIR}")

    code = 0
    if not cfg.get("skip_fetch"):
        code = do_fetch(cfg)
        if code != 0:
            return code
    else:
        print("（已跳过拉字幕步骤）")

    if not cfg.get("skip_analyze"):
        code = do_analyze(cfg)
    else:
        print("（已跳过分析步骤）")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
