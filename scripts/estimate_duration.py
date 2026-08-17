from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


BASELINE_VIDEOS = 1207
BASELINE_SECONDS = 4834
MINIMUM_SECONDS = 120


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = math.ceil(remainder / 60)
    if minutes == 60:
        hours += 1
        minutes = 0
    if hours and minutes:
        return f"约 {hours} 小时 {minutes} 分钟"
    if hours:
        return f"约 {hours} 小时"
    return f"约 {minutes} 分钟"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    with args.manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    count = len(rows)
    estimated = max(MINIMUM_SECONDS, round(count * BASELINE_SECONDS / BASELINE_VIDEOS))
    by_special = Counter(row.get("special", "未标注") or "未标注" for row in rows)
    payload = {
        "videos": count,
        "by_special": dict(by_special),
        "estimated_seconds": estimated,
        "estimated_minutes": math.ceil(estimated / 60),
        "display": format_duration(estimated),
        "likely_range_minutes": [max(2, math.floor(estimated * 0.8 / 60)), math.ceil(estimated * 1.2 / 60)],
        "baseline": {
            "videos": BASELINE_VIDEOS,
            "seconds": BASELINE_SECONDS,
            "seconds_per_video": round(BASELINE_SECONDS / BASELINE_VIDEOS, 3),
            "parameters": "并发3、20条/批、每视频最多100条评论、复用登录态",
        },
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
