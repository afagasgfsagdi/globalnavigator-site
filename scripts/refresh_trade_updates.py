#!/usr/bin/env python3
"""Refresh GlobalNavigator's public MOFCOM policy and Customs trade data feed."""

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from html import unescape
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
CHINANEWS_FEEDS = {
    "world": ("中国新闻网·国际", "https://www.chinanews.com.cn/rss/world.xml"),
    "finance": ("中国新闻网·财经", "https://www.chinanews.com.cn/rss/finance.xml"),
    "belt-road": ("中国新闻网·一带一路", "https://www.chinanews.com.cn/rss/ydyl.xml"),
    "asean": ("中国新闻网·中国—东盟商贸", "https://www.chinanews.com.cn/rss/aseaninfo.xml"),
}

TRADE_TERMS = (
    "贸易", "关税", "出口", "进口", "外贸", "供应链", "航运", "港口", "物流", "海关",
    "自贸", "贸易协定", "贸易谈判", "贸易壁垒", "贸易救济", "反倾销", "反补贴", "市场准入",
    "跨境电商", "产业链", "货运", "集装箱", "关税", "tariff", "trade", "export", "import",
    "customs", "supply chain", "shipping", "logistics", "port", "freight", "cargo",
)

REGION_TERMS = {
    "north-america": ("美国", "加拿大", "墨西哥", "北美", "united states", "u.s.", "usa", "canada", "mexico", "north america"),
    "south-america": ("巴西", "阿根廷", "智利", "哥伦比亚", "秘鲁", "乌拉圭", "厄瓜多尔", "委内瑞拉", "南美", "拉美", "brazil", "argentina", "chile", "colombia", "peru", "south america", "latin america"),
    "europe": ("欧盟", "欧洲", "英国", "法国", "德国", "意大利", "西班牙", "荷兰", "比利时", "瑞士", "瑞典", "挪威", "波兰", "葡萄牙", "爱尔兰", "乌克兰", "罗马尼亚", "united kingdom", "france", "germany", "italy", "spain", "netherlands", "european union", "europe"),
    "africa": ("非洲", "南非", "尼日利亚", "埃及", "肯尼亚", "加纳", "摩洛哥", "埃塞俄比亚", "坦桑尼亚", "阿尔及利亚", "乌干达", "塞内加尔", "卢旺达", "赞比亚", "south africa", "nigeria", "egypt", "kenya", "africa"),
    "asia": ("亚洲", "中国", "日本", "印度", "韩国", "新加坡", "东盟", "印度尼西亚", "马来西亚", "泰国", "越南", "菲律宾", "巴基斯坦", "孟加拉国", "阿联酋", "沙特", "以色列", "土耳其", "asia", "china", "japan", "india", "south korea", "asean", "singapore", "vietnam", "indonesia"),
    "oceania": ("大洋洲", "澳大利亚", "新西兰", "斐济", "巴布亚新几内亚", "oceania", "australia", "new zealand", "fiji"),
}

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
    if any(word in text for word in ("关税", "反倾销", "反补贴", "贸易救济", "出口管制", "进口配额", "反制措施", "tariff", "anti-dumping", "anti dumping", "countervailing", "trade remedy", "export control", "import quota", "customs duty")):
        return "tariff"
    if any(word in text for word in ("供应链", "航运", "物流", "港口", "货运", "集装箱", "海运", "中欧班列", "supply chain", "shipping", "logistics", "port", "freight", "container", "maritime", "cargo", "rail freight")):
        return "supply"
    return "policy"


def rss_text(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def story_region(text):
    folded = text.casefold()
    for region, terms in REGION_TERMS.items():
        if any(term.casefold() in folded for term in terms):
            return region
    return ""


def chinese_news_items(previous_items):
    """Fetch Chinese News Service RSS items and map trade stories to regions."""
    previous_global = [
        item for item in previous_items
        if item.get("continent") in GLOBAL_REGIONS
        and urlparse(item.get("link", "")).hostname
        and urlparse(item.get("link", "")).hostname.endswith("chinanews.com.cn")
    ]
    fetched = {region: [] for region in GLOBAL_REGIONS}
    fetched_global = []
    seen = set()
    for feed_id, (feed_name, feed_url) in CHINANEWS_FEEDS.items():
        try:
            root = ElementTree.fromstring(get(feed_url))
            for row in root.findall(".//item"):
                title = rss_text(row.findtext("title", ""))
                description = rss_text(row.findtext("description", ""))
                link = row.findtext("link", "").strip()
                source = rss_text(row.findtext("source", ""))
                raw_date = row.findtext("pubDate", "").strip()
                host = urlparse(link).hostname or ""
                if not title or not link.startswith("https://") or not host.endswith("chinanews.com.cn") or not raw_date or link in seen:
                    continue
                if not any(term.casefold() in f"{title} {description}".casefold() for term in TRADE_TERMS):
                    continue
                try:
                    pub_date = parsedate_to_datetime(raw_date).isoformat()
                except (TypeError, ValueError, OverflowError):
                    continue
                seen.add(link)
                region = story_region(f"{title} {description}")
                item = {
                    "title": title,
                    "description": description or f"{feed_name}发布的国际贸易相关资讯。打开原文查看详情。",
                    "link": link,
                    "author": source or "中国新闻网",
                    "sourceCountry": "中国新闻网",
                    "pubDate": pub_date,
                    "category": global_category(f"{title} {description}"),
                }
                if region:
                    item["continent"] = region
                    fetched[region].append(item)
                elif feed_id == "asean":
                    item["continent"] = "asia"
                    fetched["asia"].append(item)
                fetched_global.append(item)
        except Exception as error:
            print(f"China News Service RSS {feed_id} request failed: {error}")
        print(f"China News Service RSS {feed_id}: {len(fetched_global)} matching stories collected")
        sleep(1)

    cached_by_region = {region: [] for region in GLOBAL_REGIONS}
    for item in previous_global:
        cached_by_region[item["continent"]].append(item)
    combined = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    for region in GLOBAL_REGIONS:
        fresh = fetched[region]
        cached = cached_by_region[region]
        # Merge recent cached stories so a short RSS response does not shrink the archive.
        seen = set()
        selected = []
        for item in fresh + cached:
            link = item.get("link")
            if not link or link in seen:
                continue
            try:
                published = datetime.fromisoformat(item.get("pubDate", "").replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if published < cutoff:
                continue
            seen.add(link)
            selected.append(item)
        selected.sort(key=lambda item: item["pubDate"], reverse=True)
        combined.extend(selected[:90])
    # Include relevant items that could not be mapped confidently to one continent
    # in the all-region view, without duplicating them in a filtered region.
    mapped = {item.get("link") for item in combined}
    for item in fetched_global:
        if item.get("link") in mapped:
            continue
        try:
            published = datetime.fromisoformat(item.get("pubDate", "").replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if published >= cutoff:
            combined.append(item)
    return combined


def main():
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_items = previous.get("items", [])
    cached_policy = [item for item in previous_items if item.get("author") == "商务部" and not item.get("continent")]
    cached_customs = [item for item in previous_items if "海关总署" in item.get("author", "") and not item.get("continent")]
    try:
        fresh_policy = policy_items()
    except Exception as error:
        print(f"MOFCOM policy refresh failed; keeping the previous policy snapshot: {error}")
        fresh_policy = cached_policy
    try:
        fresh_customs = customs_items()
    except Exception as error:
        print(f"Customs statistics refresh failed; keeping the previous statistics snapshot: {error}")
        fresh_customs = cached_customs
    international_items = chinese_news_items(previous_items)
    items = fresh_policy + fresh_customs + international_items
    if not items:
        raise RuntimeError("All trade sources failed and no previous items are available")
    items.sort(key=lambda item: item["pubDate"], reverse=True)
    updated_at = datetime.now(timezone.utc).isoformat()
    if previous.get("items") == items and previous.get("updatedAt"):
        updated_at = previous["updatedAt"]
    result = {
        "source": "中国新闻网（国际、财经、一带一路、东盟商贸）· 商务部 · 海关总署",
        "updatedAt": updated_at,
        "items": items,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(items)} official items to {OUTPUT}")


if __name__ == "__main__":
    main()

