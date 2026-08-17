from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


HEADERS = ["发布日期", "达人昵称", "发布链接", "机构", "专项", "水评举例"]


def load_detector(root: Path):
    module_path = root / "tools" / "water_comment_detector.py"
    spec = importlib.util.spec_from_file_location("water_comment_detector", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载检测器: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.detect_comments


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> tuple[list[dict], int, int]:
    rows: list[dict] = []
    malformed = 0
    total_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            total_lines += 1
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
                else:
                    malformed += 1
            except json.JSONDecodeError:
                malformed += 1
    return rows, malformed, total_lines


def decode_log(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def discover_aliases(log_root: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    pattern = re.compile(r"Parsed aweme ID:\s*([\d\s]+?)\s+from\s+([^\s]+)")
    for path in log_root.glob("*.log"):
        for match in pattern.finditer(decode_log(path)):
            aweme_id = re.sub(r"\s+", "", match.group(1))
            source = re.sub(r"\s+", "", match.group(2)).lower()
            if aweme_id.isdigit():
                aliases[source] = aweme_id
    return aliases


def duration_text(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {secs} 秒"
    return f"{minutes} 分 {secs} 秒"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--detector-root", type=Path, default=Path(r"E:\豆包创作线Agent\MediaCrawler"))
    parser.add_argument("--elapsed-seconds", type=int, default=0)
    parser.add_argument("--threshold", type=int, default=3)
    args = parser.parse_args()

    detect_comments = load_detector(args.detector_root)
    manifest_rows = read_manifest(args.manifest)
    aliases = discover_aliases(args.log_root)
    manifest_by_id: dict[str, dict[str, str]] = {}
    for row in manifest_rows:
        url_key = re.sub(r"\s+", "", row.get("video_url", "")).lower()
        aweme_id = str(row.get("aweme_id", "")).strip() or aliases.get(url_key, "")
        if aweme_id:
            row["aweme_id"] = aweme_id
            manifest_by_id.setdefault(aweme_id, row)

    raw_comments: list[dict] = []
    malformed_comment_rows = 0
    comment_jsonl_lines = 0
    for path in args.data_root.rglob("detail_comments_*.jsonl"):
        rows, malformed, total = read_jsonl(path)
        raw_comments.extend(rows)
        malformed_comment_rows += malformed
        comment_jsonl_lines += total

    unique_comments: list[dict] = []
    seen_comments: set[str] = set()
    for index, row in enumerate(raw_comments):
        comment_id = str(row.get("comment_id") or row.get("cid") or "").strip()
        key = comment_id or f"__row_{index}"
        if key in seen_comments:
            continue
        seen_comments.add(key)
        unique_comments.append(row)

    detail_ids: set[str] = set()
    malformed_content_rows = 0
    for path in args.data_root.rglob("detail_contents_*.jsonl"):
        rows, malformed, _ = read_jsonl(path)
        malformed_content_rows += malformed
        for row in rows:
            aweme_id = str(row.get("aweme_id") or "").strip()
            if aweme_id:
                detail_ids.add(aweme_id)

    comments_by_video: dict[str, list[dict]] = defaultdict(list)
    for comment in unique_comments:
        aweme_id = str(comment.get("aweme_id") or comment.get("video_id") or "").strip()
        if aweme_id:
            comments_by_video[aweme_id].append(comment)

    problem_rows: list[dict[str, str]] = []
    problem_details: list[dict] = []
    for aweme_id, comments in comments_by_video.items():
        meta = manifest_by_id.get(aweme_id)
        if not meta:
            continue
        result = detect_comments(comments, threshold=max(1, args.threshold))
        if result["summary"]["decision"] != "water_comment_review":
            continue
        examples = [
            str(item.get("content") or item.get("text") or "").strip()
            for item in result["candidates"][:5]
        ]
        row = {
            "发布日期": meta.get("publish_date", ""),
            "达人昵称": meta.get("creator", ""),
            "发布链接": meta.get("video_url", ""),
            "机构": meta.get("institution", "未标注") or "未标注",
            "专项": meta.get("special", ""),
            "水评举例": " ｜ ".join(examples),
        }
        problem_rows.append(row)
        problem_details.append({"aweme_id": aweme_id, "summary": result["summary"], "examples": examples})

    problem_rows.sort(key=lambda row: (row["发布日期"], row["专项"], row["达人昵称"]), reverse=True)
    creators_by_institution: dict[str, set[str]] = defaultdict(set)
    videos_by_institution: Counter[str] = Counter()
    for row in problem_rows:
        creators_by_institution[row["机构"]].add(row["达人昵称"])
        videos_by_institution[row["机构"]] += 1
    institution_unique_creators = dict(
        sorted(((name, len(creators)) for name, creators in creators_by_institution.items()), key=lambda item: (-item[1], item[0]))
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "problem_videos.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(problem_rows)
    workbook = {
        "sheets": [{
            "name": "问题视频",
            "columns": HEADERS,
            "data": [[row[column] for column in HEADERS] for row in problem_rows],
        }]
    }
    (args.output_dir / "feishu-workbook-payload.json").write_text(
        json.dumps(workbook, ensure_ascii=False), encoding="utf-8"
    )

    output_ids = detail_ids | set(comments_by_video)
    matched_output_ids = output_ids & set(manifest_by_id)
    stats = {
        "manifest_videos": len(manifest_rows),
        "manifest_resolved_ids": len(manifest_by_id),
        "videos_with_detail_or_comments": len(matched_output_ids),
        "videos_with_comments": len(set(comments_by_video) & set(manifest_by_id)),
        "raw_valid_comment_rows": len(raw_comments),
        "valid_unique_comments": len(unique_comments),
        "duplicate_comment_rows_removed": len(raw_comments) - len(unique_comments),
        "malformed_comment_jsonl_rows": malformed_comment_rows,
        "comment_jsonl_nonempty_lines": comment_jsonl_lines,
        "malformed_content_jsonl_rows": malformed_content_rows,
        "problem_videos": len(problem_rows),
        "institution_unique_creators": institution_unique_creators,
        "institution_problem_videos": dict(videos_by_institution),
        "elapsed_seconds": args.elapsed_seconds,
        "elapsed_display": duration_text(args.elapsed_seconds),
        "threshold": args.threshold,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps({
            "stats": stats,
            "problem_details": problem_details,
            # These stable ID lists let the 24-hour follow-up distinguish a
            # genuinely cleared video from a video that could not be fetched.
            "observed_video_ids": sorted(matched_output_ids),
            "comment_video_ids": sorted(set(comments_by_video) & set(manifest_by_id)),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
