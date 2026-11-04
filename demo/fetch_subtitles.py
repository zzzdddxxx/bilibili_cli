"""批量获取 UP 主视频字幕，文件命名包含 BV 号和发布时间。

用法：
  python fetch_subtitles.py                获取默认 UP 主（玄离199）最近 5 个视频字幕
  python fetch_subtitles.py 玄离199 -n 10   按名字，获取最近 10 个
  python fetch_subtitles.py 67079745 -n 8   按 UID

输出：
  data/subtitles/{BV号}_{发布时间}.txt  纯文本字幕
  data/subtitles/{BV号}_{发布时间}.srt  SRT 时间轴字幕
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bili_cli import auth, client  # noqa: E402

OUTPUT_DIR = HERE / "data" / "subtitles"

DEFAULT_UID = 67079745  # 玄离199


def _ts_to_date(ts: object) -> str:
    """把 unix 时间戳转成 YYYY-MM-DD，失败返回 unknown。"""
    try:
        ts_int = int(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    if ts_int <= 0:
        return "unknown"
    return datetime.fromtimestamp(ts_int).strftime("%Y-%m-%d")


def resolve_uid(up: int | str | None = None) -> int:
    """确定 UID：up 可以是数字 UID，也可以是用户名（字符串）。

    - None / 空字符串 -> 默认 UID
    - 纯数字（int 或 "12345"）-> 直接作为 UID
    - 其他字符串 -> 按用户名搜索
    """
    if up is None or up == "":
        return DEFAULT_UID
    if isinstance(up, int):
        return up
    s = str(up).strip()
    if s.isdigit():
        return int(s)
    users = asyncio.run(client.search_user(s))
    if not users:
        print(f"错误：未搜索到用户：{s}", file=sys.stderr)
        raise SystemExit(2)
    mid = users[0].get("mid")
    if mid is None:
        print(f"错误：搜索结果缺少 mid：{s}", file=sys.stderr)
        raise SystemExit(2)
    print(f"匹配到用户：{users[0].get('uname', s)} (UID: {mid})")
    return int(mid)


async def run(uid: int, count: int, cred) -> int:
    videos = await client.get_user_videos(uid, count=count, credential=cred)
    if not videos:
        print("未获取到视频列表。")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"UP 主 UID {uid}，最近 {len(videos)} 个视频：\n")

    ok = 0
    skipped = 0
    unavailable = 0
    meta_list: list[dict] = []
    for i, v in enumerate(videos, 1):
        bvid = v.get("bvid", "")
        title = v.get("title", "")
        date = _ts_to_date(v.get("created", 0))
        print(f"[{i}/{len(videos)}] {bvid}  {date}  {title}")

        # 校验视频可用性（充电/付费/异常等）
        try:
            info = await client.get_video_info(bvid)
            avail = client.video_availability(info)
        except Exception as e:  # noqa: BLE001
            print(f"    获取视频信息失败：{e}")
            skipped += 1
            continue

        if not avail["available"]:
            reasons = "、".join(avail["reasons"])
            print(f"    （不可用：{reasons}，跳过）")
            meta_list.append(
                {
                    "bvid": bvid,
                    "title": title,
                    "date": date,
                    "available": False,
                    "reasons": avail["reasons"],
                }
            )
            unavailable += 1
            continue

        try:
            text, raw = await client.get_video_subtitle(bvid, credential=cred)
        except Exception as e:  # noqa: BLE001
            print(f"    获取字幕失败：{e}")
            skipped += 1
            continue

        if not raw:
            print("    （无字幕，跳过）")
            meta_list.append(
                {
                    "bvid": bvid,
                    "title": title,
                    "date": date,
                    "available": True,
                    "subtitle": False,
                }
            )
            skipped += 1
            continue

        base = f"{bvid}_{date}"
        txt_path = OUTPUT_DIR / f"{base}.txt"
        srt_path = OUTPUT_DIR / f"{base}.srt"
        if txt_path.exists() and srt_path.exists():
            print("    （已存在，跳过）")
        else:
            txt_path.write_text(text, encoding="utf-8")
            srt_path.write_text(client.format_subtitle_timeline(raw, output_format="srt"), encoding="utf-8")
            print(f"    -> {base}.txt / {base}.srt  ({len(raw)} 条)")
            ok += 1

        meta_list.append(
            {
                "bvid": bvid,
                "title": title,
                "date": date,
                "txt": f"{base}.txt",
                "srt": f"{base}.srt",
                "available": True,
            }
        )

    meta_path = OUTPUT_DIR / "meta.json"
    meta_path.write_text(json.dumps(meta_list, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：成功 {ok} 个，无字幕/失败 {skipped} 个，不可用 {unavailable} 个，保存到 {OUTPUT_DIR}")
    print(f"元数据已保存到 {meta_path}（共 {len(meta_list)} 条）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量获取 UP 主视频字幕")
    parser.add_argument("up", nargs="?", default=None, help="UP 主名字或 UID（默认 67079745）")
    parser.add_argument("-n", "--count", type=int, default=5, help="获取最近 N 个视频（默认 5）")
    args = parser.parse_args(argv)

    uid = resolve_uid(args.up)

    cred = auth.get_credential(mode="read")
    if not cred:
        print("错误：未登录。字幕接口需要登录凭证，请先运行 bili login。", file=sys.stderr)
        return 2

    return asyncio.run(run(uid, args.count, cred))


if __name__ == "__main__":
    raise SystemExit(main())
