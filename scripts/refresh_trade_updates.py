#!/usr/bin/env python3
"""Refresh GlobalNavigator's public MOFCOM policy and Customs trade data feed."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "trade-updates.json"
POLICY_URL = "https://wms.mofcom.gov.cn/zcfb/"
STATS_URL = "https://data.mofcom.gov.cn/datamofcom/front/totalmonth/query"
STATS_PAGE = "https://data.mofcom.gov.cn/hwmy/imexmonth.shtml"


def get(url, data=None, content_type=None):
    headers = {"User-Agent": "GlobalNavigator/1.0 (public trade information display)"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=30) as response:
        return response.read()


class PolicyParser(HTMLParser):
    """Collect list-item anchors and dates from the MOFCOM policy index."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.current = {"link": "", "title": "", "date": ""}
        elif self.current is not None and tag == "a" and attrs.get("href", "").startswith("/zcfb/"):
            self.current["link"] = urljoin(POLICY_URL, attrs["href"])
            self.capture = "title"
        elif self.current is not None and tag == "span":
            self.capture = "date"

    def handle_data(self, data):
        if self.current is not None and self.capture in ("title", "date"):
            self.current[self.capture] += data

    def handle_endtag(self, tag):
        if tag in ("a", "span"):
            self.capture = None
        elif tag == "li" and self.current is not None:
            item = {key: re.sub(r"\s+", " ", value).strip() for key, value in self.current.items()}
            if item["title"] and item["link"] and re.search(r"\d{4}-\d{2}-\d{2}", item["date"]):
                self.items.append(item)
            self.current = None
            self.capture = None


def category_for(title):
    if any(word in title for word in ("关税", "反倾销", "反补贴", "进口配额", "出口配额", "贸易救济", "保障措施")):
        return "tariff"
    if any(word in title for word in ("供应链", "航运", "港口", "物流", "保税维修", "加工贸易", "中欧班列")):
        return "supply"
    return "policy"


def policy_items():
    html = get(POLICY_URL).decode("utf-8", "replace")
    parser = PolicyParser()
    parser.feed(html)
    items = []
    for row in parser.items[:80]:
        date = re.search(r"\d{4}-\d{2}-\d{2}", row["date"]).group(0)
        items.append({
            "title": row["title"],
            "description": "商务部外贸政策公告。请查看官方文件原文，核对适用范围与实施时间。",
            "link": row["link"],
            "author": "商务部",
            "sourceCountry": "China",
            "pubDate": f"{date}T00:00:00+08:00",
            "category": category_for(row["title"]),
        })
    if not items:
        raise RuntimeError("No dated policy announcements could be parsed from the MOFCOM index")
    return items


def customs_items():
    payload = json.loads(get(STATS_URL, data=b"", content_type="application/x-www-form-urlencoded").decode("utf-8"))
    records = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else payload
    if not isinstance(records, list) or not records:
        raise RuntimeError("The MOFCOM data center returned no Customs trade records")
    items = []
    for row in records[:12]:
        period = str(row.get("trade_date", ""))
        if not re.fullmatch(r"\d{6}", period):
            continue
        year, month = int(period[:4]), int(period[4:])
        total, total_yoy = row.get("total_value"), row.get("total_per")
        export, export_yoy = row.get("export_value"), row.get("export_per")
        imports, import_yoy = row.get("import_value"), row.get("import_per")
        if None in (total, total_yoy, export, export_yoy, imports, import_yoy):
            continue
        def signed(value):
            value = float(value)
            return f"+{value:g}" if value > 0 else f"{value:g}"
        description = (
            f"进出口总值 {float(total):,.2f} 亿美元（同比 {signed(total_yoy)}%）；"
            f"出口 {float(export):,.2f} 亿美元（{signed(export_yoy)}%），"
            f"进口 {float(imports):,.2f} 亿美元（{signed(import_yoy)}%）。统计月份：{year}年{month}月。"
        )
        items.append({
            "title": f"海关总署 {year}年{month}月进出口统计",
            "description": description,
            "link": STATS_PAGE,
            "author": "海关总署统计（商务部数据中心）",
            "sourceCountry": "China",
            "pubDate": f"{year:04d}-{month:02d}-01T00:00:00+08:00",
            "category": "supply",
        })
    if not items:
        raise RuntimeError("No valid monthly Customs records could be parsed")
    return items


def main():
    items = policy_items() + customs_items()
    items.sort(key=lambda item: item["pubDate"], reverse=True)
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    updated_at = datetime.now(timezone.utc).isoformat()
    if previous.get("items") == items and previous.get("updatedAt"):
        updated_at = previous["updatedAt"]
    result = {
        "source": "商务部政策发布 · 海关总署统计",
        "updatedAt": updated_at,
        "items": items,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(items)} official items to {OUTPUT}")


if __name__ == "__main__":
    main()

