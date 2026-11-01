#!/usr/bin/env python3
"""Refresh GlobalNavigator's public MOFCOM policy and Customs trade data feed."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import re
from pathlib import Path
from time import sleep
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "trade-updates.json"
POLICY_URL = "https://wms.mofcom.gov.cn/zcfb/"
STATS_URL = "https://data.mofcom.gov.cn/datamofcom/front/totalmonth/query"
STATS_PAGE = "https://data.mofcom.gov.cn/hwmy/imexmonth.shtml"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

GLOBAL_REGIONS = {
    "north-america": {
        "countries": ["unitedstates", "canada", "mexico"],
        "names": {"united states", "united states of america", "canada", "mexico"},
    },
    "south-america": {
        "countries": ["brazil", "argentina", "chile", "colombia", "peru", "uruguay", "ecuador"],
        "names": {"brazil", "argentina", "chile", "colombia", "peru", "uruguay", "ecuador", "venezuela"},
    },
    "europe": {
        "countries": ["unitedkingdom", "france", "germany", "italy", "spain", "netherlands", "poland", "sweden", "norway"],
        "names": {"united kingdom", "great britain", "france", "germany", "italy", "spain", "netherlands", "poland", "sweden", "norway", "portugal", "belgium", "switzerland", "ireland", "ukraine", "romania"},
    },
    "africa": {
        "countries": ["southafrica", "nigeria", "egypt", "kenya", "ghana", "morocco", "ethiopia", "tanzania"],
        "names": {"south africa", "nigeria", "egypt", "kenya", "ghana", "morocco", "ethiopia", "tanzania", "algeria", "uganda", "senegal", "rwanda", "zambia"},
    },
    "asia": {
        "countries": ["china", "japan", "india", "southkorea", "singapore", "indonesia", "malaysia", "thailand", "vietnam", "philippines", "uae", "saudiarabia"],
        "names": {"china", "japan", "india", "south korea", "republic of korea", "singapore", "indonesia", "malaysia", "thailand", "vietnam", "philippines", "pakistan", "bangladesh", "united arab emirates", "saudi arabia", "israel", "hong kong", "taiwan", "turkey"},
    },
    "oceania": {
        "countries": ["australia", "newzealand", "fiji", "papuanewguinea"],
        "names": {"australia", "new zealand", "fiji", "papua new guinea"},
    },
}

REGIONAL_RSS = {
    "north-america": ("US", "US:en", "United States Canada Mexico trade tariff export import"),
    "south-america": ("BR", "BR:en", "Brazil Argentina Chile Colombia trade tariff export import"),
    "europe": ("GB", "GB:en", "European Union UK Germany France trade tariff export import"),
    "africa": ("ZA", "ZA:en", "Africa South Africa Nigeria Kenya trade tariff export import"),
    "asia": ("SG", "SG:en", "China Japan India ASEAN trade tariff export import"),
    "oceania": ("AU", "AU:en", "Australia New Zealand Pacific trade tariff export import"),
}


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


def global_category(title):
    text = title.casefold()
    if any(word in text for word in ("tariff", "anti-dumping", "anti dumping", "countervailing", "trade remedy", "export control", "import quota", "customs duty")):
        return "tariff"
    if any(word in text for word in ("supply chain", "shipping", "logistics", "port", "freight", "container", "maritime", "cargo", "rail freight")):
        return "supply"
    return "policy"


def gdelt_date(value):
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", str(value or ""))
    if not match:
        return ""
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}+00:00"


def gdelt_items(previous_items):
    """Fetch English-language international trade stories and label publisher regions."""
    previous_global = [item for item in previous_items if item.get("continent") in GLOBAL_REGIONS]
    fetched = {region: [] for region in GLOBAL_REGIONS}
    api_throttled = False

    for region, config in GLOBAL_REGIONS.items():
        source_filter = " OR ".join(f"sourcecountry:{country}" for country in config["countries"])
        query = f"(trade OR tariff OR export OR import OR customs OR shipping OR logistics) ({source_filter}) sourcelang:english"
        params = urlencode({"query": query, "mode": "ArtList", "format": "json", "maxrecords": 25, "sort": "DateDesc", "timespan": "30d"})
        try:
            payload = json.loads(get(f"{GDELT_URL}?{params}").decode("utf-8"))
        except HTTPError as error:
            print(f"GDELT {region} request failed: HTTP {error.code}")
            api_throttled = error.code == 429
            if api_throttled:
                break
            continue
        except Exception as error:
            print(f"GDELT {region} request failed: {error}")
            continue

        articles = payload.get("articles", []) if isinstance(payload, dict) else []
        for article in articles:
            title = re.sub(r"\s+", " ", str(article.get("title", ""))).strip()
            link = str(article.get("url", "")).strip()
            country = re.sub(r"\s+", " ", str(article.get("sourcecountry", ""))).strip()
            pub_date = gdelt_date(article.get("seendate"))
            if not title or not link.startswith("http") or not pub_date:
                continue
            if country.casefold() not in config["names"]:
                continue
            domain = str(article.get("domain", "")).strip()
            fetched[region].append({
                "title": title,
                "description": f"International trade news from {country}. Open the publisher's article for details.",
                "link": link,
                "author": domain or country,
                "sourceCountry": country,
                "continent": region,
                "pubDate": pub_date,
                "category": global_category(title),
            })
        print(f"GDELT {region}: {len(fetched[region])} matching stories")
        sleep(2)

    # Keep the last successful regional snapshot if the public index is briefly unavailable.
    cached_by_region = {region: [] for region in GLOBAL_REGIONS}
    for item in previous_global:
        cached_by_region[item["continent"]].append(item)
    combined = []
    for region in GLOBAL_REGIONS:
        fresh = fetched[region]
        preserved = cached_by_region[region]
        selected = fresh if len(fresh) >= 4 or not preserved else fresh + [item for item in preserved if item.get("link") not in {entry.get("link") for entry in fresh}]
        combined.extend(selected[:25])
    if api_throttled and not previous_global:
        print("GDELT is rate-limited and no prior international snapshot exists yet.")
    return combined


def regional_rss_items(previous_items):
    """Fetch region-focused international headlines from public Google News RSS editions."""
    previous_global = [item for item in previous_items if item.get("continent") in GLOBAL_REGIONS]
    fetched = {region: [] for region in GLOBAL_REGIONS}
    for region, (market, edition, terms) in REGIONAL_RSS.items():
        params = urlencode({
            # 1/2/3 month archive filters need a 90-day source snapshot.
            "q": f"{terms} when:90d",
            "hl": "en-US",
            "gl": market,
            "ceid": edition,
        })
        try:
            root = ElementTree.fromstring(get(f"{GOOGLE_NEWS_RSS}?{params}"))
            for row in root.findall(".//item"):
                title = re.sub(r"\s+", " ", row.findtext("title", "")).strip()
                link = row.findtext("link", "").strip()
                source = re.sub(r"\s+", " ", row.findtext("source", "")).strip()
                raw_date = row.findtext("pubDate", "").strip()
                if not title or not link.startswith("http") or not raw_date:
                    continue
                pub_date = parsedate_to_datetime(raw_date).isoformat()
                fetched[region].append({
                    "title": title,
                    "description": f"Regional trade coverage for {region.replace('-', ' ')}. Open the publisher's article for details.",
                    "link": link,
                    "author": source or "Google News",
                    "continent": region,
                    "pubDate": pub_date,
                    "category": global_category(title),
                })
                if len(fetched[region]) >= 45:
                    break
        except Exception as error:
            print(f"Regional RSS {region} request failed: {error}")
        print(f"Regional RSS {region}: {len(fetched[region])} stories")
        sleep(1)

    cached_by_region = {region: [] for region in GLOBAL_REGIONS}
    for item in previous_global:
        cached_by_region[item["continent"]].append(item)
    combined = []
    for region in GLOBAL_REGIONS:
        fresh = fetched[region]
        cached = cached_by_region[region]
        # Keep the last known stories if a public feed is temporarily empty or incomplete.
        selected = fresh if len(fresh) >= 4 or not cached else fresh + [item for item in cached if item.get("link") not in {entry.get("link") for entry in fresh}]
        combined.extend(selected[:45])
    return combined


def main():
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    official_items = policy_items() + customs_items()
    international_items = regional_rss_items(previous.get("items", []))
    items = official_items + international_items
    items.sort(key=lambda item: item["pubDate"], reverse=True)
    updated_at = datetime.now(timezone.utc).isoformat()
    if previous.get("items") == items and previous.get("updatedAt"):
        updated_at = previous["updatedAt"]
    result = {
        "source": "商务部政策发布 · 海关总署统计 · Google News国际贸易资讯",
        "updatedAt": updated_at,
        "items": items,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(items)} official items to {OUTPUT}")


if __name__ == "__main__":
    main()

