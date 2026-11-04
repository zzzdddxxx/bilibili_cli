"""批量获取 UP 主视频字幕，文件命名包含 BV 号和发布时间。

用法：
  python fetch_subtitles.py                获取默认 UP 主（玄离199）最近 5 个视频字幕
  python fetch_subtitles.py 玄离199 -n 10   按名字，获取最近 10 个
  python fetch_subtitles.py 67079745 -n 8   按 UID

输出：
  data/subtitles/{up名}_{发布时间}_{BV号}.txt  纯文本字幕
  data/subtitles/{up名}_{发布时间}_{BV号}.srt  SRT 时间轴字幕
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
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


def _parse_date_to_ts(date_str: str | None, *, end_of_day: bool = False) -> int | None:
    """解析 YYYY-MM-DD 为 unix 时间戳。end_of_day=True 时取当天 23:59:59。

    支持 YYYY-M-D（如 2026-11-4）和 YYYY-MM-DD（如 2026-11-04）。
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s:
        return None
    try:
        parts = s.split("-")
        if len(parts) != 3:
            raise ValueError
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        dt = datetime(y, m, d)
    except (ValueError, TypeError):
        print(f"错误：日期格式应为 YYYY-MM-DD：{date_str}", file=sys.stderr)
        raise SystemExit(2)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _parse_sleep_range(sleep_range: object) -> tuple[float, float] | None:
    """解析 sleep_range 配置为 (下限, 上限) 秒。None 或非法返回 None（表示不休息）。"""
    if not sleep_range:
        return None
    if isinstance(sleep_range, (list, tuple)) and len(sleep_range) == 2:
        try:
            lo = float(sleep_range[0])  # type: ignore[arg-type]
            hi = float(sleep_range[1])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if lo < 0:
            lo = 0.0
        if hi < lo:
            hi = lo
        if hi <= 0:
            return None
        return lo, hi
    return None


def _short_name(name: str, max_len: int = 6) -> str:
    """截断名称到最多 max_len 个字符，用于文件名。"""
    name = (name or "").strip()
    if not name:
        return "up"
    return name[:max_len]


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


def resolve_up(up: int | str | None = None) -> tuple[int, str]:
    """解析 up 为 (uid, 名称)。名称用于文件命名。"""
    if isinstance(up, str) and up.strip() and not up.strip().isdigit():
        # 用户直接给了名字
        return resolve_uid(up), up.strip()
    # UID 或默认：通过 API 获取名称
    uid = resolve_uid(up)
    name = ""
    try:
        info = asyncio.run(client.get_user_info(uid))
        name = info.get("name", "") or ""
    except Exception:  # noqa: BLE001
        name = ""
    return uid, name


async def run(
    uid: int,
    count: int,
    cred,
    up_name: str = "",
    begin_date: str | None = None,
    end_date: str | None = None,
    sleep_range: object = None,
) -> int:
    begin_ts = _parse_date_to_ts(begin_date, end_of_day=False)
    end_ts = _parse_date_to_ts(end_date, end_of_day=True)
    sleep = _parse_sleep_range(sleep_range)
    videos = await client.get_user_videos_filtered(
        uid, count=count, credential=cred, begin_ts=begin_ts, end_ts=end_ts
    )
    if not videos:
        print("未获取到视频列表。")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    up_short = _short_name(up_name)
    print(f"UP 主 UID {uid}（{up_short}），最近 {len(videos)} 个视频：\n")

    ok = 0
    skipped = 0
    unavailable = 0
    meta_list: list[dict] = []
    for i, v in enumerate(videos, 1):
        # 反爬：每个视频处理前随机休息（第一个不用）
        if sleep is not None and i > 1:
            delay = random.uniform(sleep[0], sleep[1])
            print(f"    （防反爬，休息 {delay:.1f}s）")
            await asyncio.sleep(delay)

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

        base = f"{up_short}_{date}_{bvid}"
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
    parser.add_argument("--begin", dest="begin_date", default=None, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", dest="end_date", default=None, help="结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--sleep-range", dest="sleep_range", nargs=2, type=float, default=None, help="反爬随机休息秒数范围，如 0.5 2.5")
    args = parser.parse_args(argv)

    uid, up_name = resolve_up(args.up)

    cred = auth.get_credential(mode="read")
    if not cred:
        print("错误：未登录。字幕接口需要登录凭证，请先运行 bili login。", file=sys.stderr)
        return 2

    return asyncio.run(run(uid, args.count, cred, up_name, args.begin_date, args.end_date, args.sleep_range))


if __name__ == "__main__":
    raise SystemExit(main())
