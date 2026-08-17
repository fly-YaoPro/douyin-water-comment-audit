from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

COLOR_TO_INSTITUTION = {
    "FBBFBC": "萌兽",
    "FED4A4": "闪光点",
    "FAF1D1": "纽微特",
    "EEF6C6": "志森",
    "7EDAFB": "睿立",
    "DEE0E3": "金铲子",
    "FFF258": "筑石",
    "34C724": "山恒",
    "AD82F7": "杭杭",
    "F54A45": "肆野",
}


def read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return list(csv.reader(raw.decode(enc).splitlines()))
        except UnicodeDecodeError:
            pass
    raise ValueError(f"无法识别 CSV 编码: {path}")


def parse_date(value: str, start: dt.date, end: dt.date) -> dt.date | None:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        number = float(text)
        if 20000 < number < 60000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(number))
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    full = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if full:
        try:
            return dt.date(int(full.group(1)), int(full.group(2)), int(full.group(3)))
        except ValueError:
            return None
    short = re.fullmatch(r"(\d{1,2})-(\d{1,2})", normalized)
    if not short:
        return None
    month, day = int(short.group(1)), int(short.group(2))
    if start.year == end.year:
        try:
            return dt.date(start.year, month, day)
        except ValueError:
            return None
    candidates = []
    for year in range(start.year, end.year + 1):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            continue
        if start <= candidate <= end:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def normalize_color(value: str | None) -> str:
    color = (value or "").upper().replace("#", "")
    if len(color) == 8:
        color = color[-6:]
    return color if len(color) == 6 else ""


def sheet_xml_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    sheets_node = workbook.find("m:sheets", NS)
    for sheet in list(sheets_node) if sheets_node is not None else []:
        if sheet.attrib.get("name") != sheet_name:
            continue
        target = targets[sheet.attrib[REL_ID]].lstrip("/")
        return target if target.startswith("xl/") else f"xl/{target}"
    available = [sheet.attrib.get("name", "") for sheet in list(sheets_node) if sheets_node is not None]
    raise ValueError(f"XLSX 中找不到 Sheet {sheet_name!r}; 可用: {available}")


def read_ar_cells(xlsx: Path, sheet_name: str) -> dict[int, tuple[str, str]]:
    with zipfile.ZipFile(xlsx) as zf:
        styles = ET.fromstring(zf.read("xl/styles.xml"))
        fills_node = styles.find("m:fills", NS)
        fill_colors: list[str] = []
        for fill in list(fills_node) if fills_node is not None else []:
            pattern = fill.find("m:patternFill", NS)
            foreground = pattern.find("m:fgColor", NS) if pattern is not None else None
            fill_colors.append(normalize_color(foreground.attrib.get("rgb")) if foreground is not None else "")
        xfs_node = styles.find("m:cellXfs", NS)
        style_colors: list[str] = []
        for xf in list(xfs_node) if xfs_node is not None else []:
            fill_id = int(xf.attrib.get("fillId", "0"))
            style_colors.append(fill_colors[fill_id] if fill_id < len(fill_colors) else "")

        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("m:si", NS):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{NS['m']}}}t")))

        worksheet = ET.fromstring(zf.read(sheet_xml_path(zf, sheet_name)))
        output: dict[int, tuple[str, str]] = {}
        for cell in worksheet.findall(".//m:c", NS):
            match = re.fullmatch(r"AR(\d+)", cell.attrib.get("r", ""))
            if not match:
                continue
            style_id = int(cell.attrib.get("s", "0"))
            color = style_colors[style_id] if style_id < len(style_colors) else ""
            value = ""
            value_node = cell.find("m:v", NS)
            if value_node is not None and value_node.text:
                value = value_node.text
                if cell.attrib.get("t") == "s":
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError):
                        pass
            if cell.attrib.get("t") == "inlineStr":
                inline = cell.find("m:is", NS)
                if inline is not None:
                    value = "".join(node.text or "" for node in inline.iter(f"{{{NS['m']}}}t"))
            output[int(match.group(1))] = (color, value)
        return output


def institution(ar_text: str, color: str) -> str:
    text = (ar_text or "").strip()
    if "自建" in text:
        return "自建"
    for name in COLOR_TO_INSTITUTION.values():
        if name in text:
            return name
    return COLOR_TO_INSTITUTION.get(color, "未标注")


def clean(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def process_source(
    csv_path: Path,
    xlsx_path: Path,
    sheet_name: str,
    special: str,
    start: dt.date,
    end: dt.date,
    all_rows: bool,
) -> tuple[list[dict], dict]:
    rows = read_csv(csv_path)
    ar_cells = read_ar_cells(xlsx_path, sheet_name)
    stats = {
        "source_rows": max(0, len(rows) - 1),
        "outside_range": 0,
        "invalid_date": 0,
        "missing_link": 0,
        "unmapped_institution": 0,
        "self_built": 0,
        "selected": 0,
    }
    records: list[dict] = []
    for row_no, original in enumerate(rows[1:], start=2):
        row = original + [""] * max(0, 44 - len(original))
        published = parse_date(row[10], start, end)
        if not all_rows:
            if not published:
                stats["invalid_date"] += 1
                continue
            if not start <= published <= end:
                stats["outside_range"] += 1
                continue
        elif not published:
            # Full-table mode still requires a usable published date in output.
            stats["invalid_date"] += 1
            continue
        url = clean(row[11])
        if not url:
            stats["missing_link"] += 1
            continue
        aweme_id = clean(row[12])
        if not re.fullmatch(r"\d+", aweme_id):
            match = re.search(r"/video/(\d+)", url)
            aweme_id = match.group(1) if match else ""
        color, xlsx_text = ar_cells.get(row_no, ("", ""))
        ar_text = row[43].strip() or xlsx_text
        org = institution(ar_text, color)
        if org == "未标注":
            stats["unmapped_institution"] += 1
        if org == "自建":
            stats["self_built"] += 1
        records.append({
            "source_row": row_no,
            "publish_date": published.isoformat(),
            "creator": row[7].strip(),
            "video_url": url,
            "aweme_id": aweme_id,
            "institution": org,
            "special": special,
            "ar_raw": ar_text,
            "ar_color": color,
        })
    stats["selected"] = len(records)
    return records, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-csv", type=Path, required=True)
    parser.add_argument("--work-xlsx", type=Path, required=True)
    parser.add_argument("--creation-csv", type=Path, required=True)
    parser.add_argument("--creation-xlsx", type=Path, required=True)
    parser.add_argument("--work-sheet-name", default="视频发布（一口价）")
    parser.add_argument("--creation-sheet-name", default="创作skill-视频发布")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--work-all", action="store_true")
    parser.add_argument("--creation-all", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end 不能早于 --start")
    work, work_stats = process_source(
        args.work_csv, args.work_xlsx, args.work_sheet_name, "工作任务", start, end, args.work_all
    )
    creation, creation_stats = process_source(
        args.creation_csv, args.creation_xlsx, args.creation_sheet_name, "创作线", start, end, args.creation_all
    )

    dedup: dict[str, dict] = {}
    for record in work + creation:
        key = record["aweme_id"] or record["video_url"].lower()
        if key not in dedup:
            dedup[key] = record
            continue
        existing = dedup[key]
        specials = list(dict.fromkeys((existing["special"] + "、" + record["special"]).split("、")))
        existing["special"] = "、".join(specials)
    records = list(dedup.values())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_row", "publish_date", "creator", "video_url", "aweme_id",
        "institution", "special", "ar_raw", "ar_color",
    ]
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    summary = {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "work": work_stats,
        "creation": creation_stats,
        "manifest_rows": len(records),
        "duplicates_combined": len(work) + len(creation) - len(records),
        "output": str(args.output.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
