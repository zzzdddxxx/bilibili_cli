"""用本地 Ollama 模型分析视频字幕（支持单个/多个视频）。

用法：
  python analyze.py --all                       分析 data/subtitles 下所有字幕，输出一个合并文件
  python analyze.py BV1Avx16TEZo                分析单个视频
  python analyze.py BV1xxx BV2xxx BV3xxx        分析指定的多个视频，输出一个合并文件
  python analyze.py -f 文件1.txt 文件2.txt      按文件路径分析
  python analyze.py -m qwen3.5:4b               指定模型
  python analyze.py -t 0.3                      指定采样温度

输出：
  单个视频 -> output/{BV号}_{发布时间}_summary.md
  多个视频 -> output/summaries_{时间戳}.md（一个文件，每个视频注明标题/BV号/发布时间）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from ollama_client import DEFAULT_MODEL, OllamaError, chat

HERE = Path(__file__).resolve().parent
SUBTITLE_DIR = HERE / "data" / "subtitles"
OUTPUT_DIR = HERE / "output"

SYSTEM_PROMPT = (
    "你是一名专业的视频内容总结助手。请根据提供的视频字幕（语音识别文本）"
    "输出结构化总结。要求：\n"
    "1. 使用中文。\n"
    "2. 先写一句话概述（这个视频讲了什么）。\n"
    "3. 再用编号列表提炼主要内容要点（3~8 条，每条一句话）。\n"
    "4. 只依据字幕内容，不要编造字幕中没有的信息。"
)

_FILENAME_RE = re.compile(r"^(BV[0-9A-Za-z]{10})_(\d{4}-\d{2}-\d{2})$")


def load_meta() -> dict[str, dict]:
    """读取 meta.json，返回 {bvid: meta} 映射。"""
    meta_path = SUBTITLE_DIR / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    result: dict[str, dict] = {}
    for item in data:
        if isinstance(item, dict) and item.get("bvid"):
            result[item["bvid"]] = item
    return result


def parse_bvid_date(stem: str) -> tuple[str, str] | None:
    """从文件名 stem（如 BV1xxx_2026-10-31）解析 BV 号和日期。"""
    m = _FILENAME_RE.match(stem)
    if m:
        return m.group(1), m.group(2)
    return None


def find_txt_by_bvid(bvid: str) -> Path | None:
    """在字幕目录中查找指定 BV 号的 .txt 文件。"""
    for p in SUBTITLE_DIR.glob(f"{bvid}_*.txt"):
        return p
    return None


def _build_target(bvid: str, meta: dict) -> dict:
    """根据 meta 构造目标。"""
    txt_name = meta.get("txt", "") if isinstance(meta, dict) else ""
    txt_path = SUBTITLE_DIR / txt_name if txt_name else find_txt_by_bvid(bvid)
    return {
        "bvid": bvid,
        "title": meta.get("title", "") if isinstance(meta, dict) else "",
        "date": meta.get("date", "") if isinstance(meta, dict) else "",
        "txt_path": txt_path,
    }


def collect_targets(
    meta: dict[str, dict],
    *,
    all_flag: bool = False,
    files: list[str] | None = None,
    bvids: list[str] | None = None,
) -> list[dict]:
    """根据参数确定待分析目标。"""
    if all_flag:
        targets = []
        for bvid, m in meta.items():
            t = _build_target(bvid, m)
            if t["txt_path"] and t["txt_path"].exists():
                targets.append(t)
        return targets

    if files:
        targets = []
        for f in files:
            p = Path(f)
            if not p.exists():
                print(f"警告：文件不存在：{f}", file=sys.stderr)
                continue
            bvid, date = parse_bvid_date(p.stem) or ("", "")
            m = meta.get(bvid, {})
            targets.append(
                {"bvid": bvid, "title": m.get("title", ""), "date": date, "txt_path": p}
            )
        return targets

    # 按 BV 号列表
    targets = []
    for bvid in bvids or []:
        m = meta.get(bvid, {})
        t = _build_target(bvid, m)
        if t["txt_path"] and t["txt_path"].exists():
            targets.append(t)
        else:
            print(f"警告：未找到 {bvid} 的字幕文件", file=sys.stderr)
    return targets


def summarize(text: str, model: str, temperature: float | None = None) -> str:
    """调用 Ollama 对字幕做总结，返回总结文本。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是视频字幕文本，请总结：\n\n{text}"},
    ]
    options = {"temperature": temperature} if temperature is not None else None
    resp = chat(model, messages, options=options)
    return resp.get("message", {}).get("content", "").strip()


def _single_output_path(target: dict) -> Path:
    bvid = target.get("bvid", "")
    date = target.get("date", "")
    stem = target["txt_path"].stem
    if bvid and date:
        return OUTPUT_DIR / f"{bvid}_{date}_summary.md"
    return OUTPUT_DIR / f"{stem}_summary.md"


def write_single(path: Path, target: dict, model: str) -> None:
    lines = [
        f"# 字幕总结",
        "",
        f"- 标题：{target.get('title', '')}",
        f"- BV号：{target.get('bvid', '')}",
        f"- 发布时间：{target.get('date', '')}",
        f"- 模型：{model}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        target["summary"],
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_multi(path: Path, targets: list[dict], model: str) -> None:
    lines = [
        "# 视频字幕批量分析",
        "",
        f"- 模型：{model}",
        f"- 分析视频数：{len(targets)}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for i, t in enumerate(targets, 1):
        lines += [
            "---",
            "",
            f"## {i}. {t.get('title', '(无标题)')}",
            "",
            f"- BV号：{t.get('bvid', '')}",
            f"- 发布时间：{t.get('date', '')}",
            "",
            t["summary"],
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_analyze(targets: list[dict], model: str, temperature: float | None = None) -> Path | None:
    """执行批量分析并输出，返回输出文件路径（无结果返回 None）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"模型：{model}")
    print(f"待分析视频：{len(targets)} 个\n")

    results: list[dict] = []
    for i, t in enumerate(targets, 1):
        title = t.get("title", "")
        bvid = t.get("bvid", "")
        date = t.get("date", "")
        print(f"[{i}/{len(targets)}] {bvid}  {date}  {title}")

        text = t["txt_path"].read_text(encoding="utf-8").strip()
        if not text:
            print("    （字幕为空，跳过）")
            continue
        try:
            summary = summarize(text, model, temperature=temperature)
        except OllamaError as e:
            print(f"    （分析失败：{e}）", file=sys.stderr)
            continue
        results.append({**t, "summary": summary})
        print(f"    （{len(summary)} 字）")

    if not results:
        print("\n没有成功分析出任何结果。", file=sys.stderr)
        return None

    if len(results) == 1:
        out_path = _single_output_path(results[0])
        write_single(out_path, results[0], model)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"summaries_{ts}.md"
        write_multi(out_path, results, model)

    print(f"\n完成：成功分析 {len(results)} 个，输出到 {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用本地 Ollama 模型分析视频字幕")
    parser.add_argument("bvids", nargs="*", help="BV 号列表（单个或多个）")
    parser.add_argument("--all", dest="all_flag", action="store_true", help="分析所有已获取的字幕")
    parser.add_argument("-f", "--file", dest="files", nargs="+", help="字幕文件路径（可多个）")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help="Ollama 模型名")
    parser.add_argument("-t", "--temperature", type=float, default=None, help="采样温度")
    args = parser.parse_args(argv)

    meta = load_meta()
    targets = collect_targets(meta, all_flag=args.all_flag, files=args.files, bvids=args.bvids)

    if not targets:
        print("没有可分析的字幕。请先运行 fetch_subtitles.py 获取字幕。", file=sys.stderr)
        return 2

    out_path = run_analyze(targets, args.model, args.temperature)
    return 0 if out_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
