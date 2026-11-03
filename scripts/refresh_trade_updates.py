#!/usr/bin/env python3
"""Refresh public China trade policy, Customs statistics, and news sources."""

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
CGTN_FEEDS = {
    "cgtn-world": ("CGTN·World", "https://www.cgtn.com/subscribe/rss/section/world.xml"),
    "cgtn-business": ("CGTN·Business", "https://www.cgtn.com/subscribe/rss/section/business.xml"),
}
CGTN_REGION_PAGES = {
    "africa": ("africa", "https://www.cgtn.com/africa"),
    "europe": ("europe", "https://www.cgtn.com/europe"),
    "americas": ("americas", "https://www.cgtn.com/america"),
    "asia-pacific": ("asia-pacific", "https://www.cgtn.com/world/asia-pacific.html"),
}
ALLOWED_NEWS_DOMAINS = ("chinanews.com.cn", "cgtn.com")

TRADE_TERMS = (
    "贸易", "关税", "出口", "进口", "外贸", "供应链", "航运", "港口", "物流", "海关",
    "自贸", "贸易协定", "贸易谈判", "贸易壁垒", "贸易救济", "反倾销", "反补贴", "市场准入",
    "跨境电商", "产业链", "货运", "集装箱", "商业", "商务", "投资", "市场", "企业", "产业",
    "制造", "经济", "金融", "能源", "农业", "采购", "贸易额", "生产", "消费", "融资",
    "tariff", "trade", "export", "import", "customs", "supply chain", "shipping", "logistics",
    "port", "freight", "cargo", "business", "market", "investment", "economy", "economic",
    "company", "companies", "industry", "manufacturing", "commodity", "commercial", "retail",
    "energy", "financial", "finance", "trade ties", "trade war", "trade deal", "trade talks",
    "agreement", "sanction", "oil", "gas", "automotive", "consumer", "production", "sales",
    "supplier", "agriculture", "mining", "resource", "investor", "growth", "bank", "factory",
)

REGION_TERMS = {
    "north-america": ("美国", "加拿大", "墨西哥", "北美", "united states", "u.s.", "usa", "american", "canada", "canadian", "mexico", "mexican", "north america", "washington", "new york", "toronto", "ottawa"),
    "south-america": ("巴西", "阿根廷", "智利", "哥伦比亚", "秘鲁", "乌拉圭", "厄瓜多尔", "委内瑞拉", "巴拿马", "玻利维亚", "巴拉圭", "南美", "拉美", "latin america", "south america", "brazil", "brazilian", "argentina", "argentine", "chile", "chilean", "colombia", "colombian", "peru", "peruvian", "uruguay", "ecuador", "venezuela", "panama", "paraguay", "bolivia"),
    "europe": ("欧盟", "欧洲", "英国", "法国", "德国", "意大利", "西班牙", "荷兰", "比利时", "瑞士", "瑞典", "挪威", "波兰", "葡萄牙", "爱尔兰", "乌克兰", "罗马尼亚", "俄罗斯", "united kingdom", "britain", "british", "france", "french", "germany", "german", "italy", "italian", "spain", "spanish", "netherlands", "dutch", "european union", "europe", "european", "ukraine", "russia", "russian", "switzerland", "swiss", "sweden", "norway", "poland", "portugal", "ireland", "romania"),
    "africa": ("非洲", "南非", "尼日利亚", "埃及", "肯尼亚", "加纳", "摩洛哥", "埃塞俄比亚", "坦桑尼亚", "阿尔及利亚", "乌干达", "塞内加尔", "卢旺达", "赞比亚", "刚果", "安哥拉", "马里", "south africa", "nigeria", "egypt", "kenya", "ghana", "morocco", "ethiopia", "tanzania", "algeria", "uganda", "senegal", "rwanda", "zambia", "congo", "angola", "mali", "africa", "african"),
    "asia": ("亚洲", "中国", "日本", "印度", "韩国", "新加坡", "东盟", "印度尼西亚", "马来西亚", "泰国", "越南", "菲律宾", "巴基斯坦", "孟加拉国", "阿联酋", "沙特", "以色列", "土耳其", "伊朗", "卡塔尔", "阿曼", "asia", "asian", "china", "chinese", "japan", "japanese", "india", "indian", "south korea", "korean", "asean", "singapore", "vietnam", "indonesia", "malaysia", "thailand", "philippines", "pakistan", "bangladesh", "uae", "saudi", "israel", "turkey", "iran", "qatar", "oman", "middle east"),
    "oceania": ("大洋洲", "澳大利亚", "澳洲", "澳中", "悉尼", "墨尔本", "堪培拉", "新西兰", "斐济", "巴布亚新几内亚", "oceania", "australia", "australian", "new zealand", "fiji", "papua new guinea", "sydney", "melbourne", "canberra", "pacific islands"),
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


def is_trade_story(text):
    folded = text.casefold()
    return any(term.casefold() in folded for term in TRADE_TERMS)


def allowed_news_link(link):
    parsed = urlparse(link)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_NEWS_DOMAINS)


def clean_news_link(link):
    parsed = urlparse(link.strip())
    return parsed._replace(query="", fragment="").geturl()


def rss_text(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def story_region(text):
    folded = text.casefold()
    for region, terms in REGION_TERMS.items():
        if any(term.casefold() in folded for term in terms):
            return region
    return ""


class CGTNPageParser(HTMLParser):
    """Extract dated CGTN news-card links from its official regional pages."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        link = urljoin("https://www.cgtn.com", attrs.get("href", ""))
        title = rss_text(attrs.get("data-label", ""))
        stamp = attrs.get("data-time", "")
        key = clean_news_link(link)
        if not title or "/news/" not in urlparse(link).path or not stamp or key in self.seen:
            return
        try:
            pub_date = datetime.fromtimestamp(int(stamp) / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return
        self.seen.add(key)
        self.items.append({"title": title, "link": key, "pubDate": pub_date})


def cgtn_page_region(page_id, title):
    tagged_region = story_region(title)
    if page_id in ("africa", "europe"):
        return page_id
    if page_id == "asia-pacific":
        return "oceania" if tagged_region == "oceania" else "asia"
    if page_id == "americas" and tagged_region in ("north-america", "south-america"):
        return tagged_region
    return ""


def chinese_news_items(previous_items):
    """Fetch Chinese News Service and CGTN feeds, plus CGTN regional story pages."""
    previous_global = [
        item for item in previous_items
        if item.get("continent") in GLOBAL_REGIONS
        and allowed_news_link(item.get("link", ""))
    ]
    fetched = {region: [] for region in GLOBAL_REGIONS}
    fetched_global = []
    seen_links = set()
    seen_titles = {}

    def add_story(item, region=""):
        key = clean_news_link(item.get("link", ""))
        title_key = item.get("title", "").casefold()
        if not key or not title_key:
            return
        existing = seen_titles.get(title_key)
        if existing:
            if region in GLOBAL_REGIONS and existing.get("continent") != region:
                old_region = existing.get("continent")
                if old_region in fetched:
                    fetched[old_region] = [row for row in fetched[old_region] if row is not existing]
                existing["continent"] = region
                fetched[region].append(existing)
            return
        if key in seen_links:
            return
        seen_links.add(key)
        seen_titles[title_key] = item
        item["link"] = key
        if region in GLOBAL_REGIONS:
            item["continent"] = region
            fetched[region].append(item)
        fetched_global.append(item)

    for feed_id, (feed_name, feed_url) in {**CHINANEWS_FEEDS, **CGTN_FEEDS}.items():
        try:
            root = ElementTree.fromstring(get(feed_url))
            for row in root.findall(".//item"):
                title = rss_text(row.findtext("title", ""))
                description = rss_text(row.findtext("description", ""))
                link = row.findtext("link", "").strip()
                source = rss_text(row.findtext("source", ""))
                raw_date = row.findtext("pubDate", "").strip()
                if not title or not allowed_news_link(link) or not raw_date:
                    continue
                if not is_trade_story(f"{title} {description}"):
                    continue
                try:
                    pub_date = parsedate_to_datetime(raw_date).isoformat()
                except (TypeError, ValueError, OverflowError):
                    continue
                region = story_region(f"{title} {description}")
                source_name = "CGTN" if feed_id in CGTN_FEEDS else "中国新闻网"
                item = {
                    "title": title,
                    "description": description or f"{feed_name}发布的国际市场与贸易资讯。打开原文查看详情。",
                    "link": link,
                    "author": source or source_name,
                    "sourceCountry": source_name,
                    "pubDate": pub_date,
                    "category": global_category(f"{title} {description}"),
                }
                if feed_id == "asean" and not region:
                    region = "asia"
                add_story(item, region)
        except Exception as error:
            print(f"News RSS {feed_id} request failed: {error}")
        print(f"News RSS {feed_id}: {len(fetched_global)} matching stories collected")
        sleep(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    for page_id, (region_hint, page_url) in CGTN_REGION_PAGES.items():
        try:
            parser = CGTNPageParser()
            parser.feed(get(page_url).decode("utf-8", "replace"))
            page_count = 0
            for row in parser.items:
                if not allowed_news_link(row["link"]) or not is_trade_story(row["title"]):
                    continue
                published = datetime.fromisoformat(row["pubDate"].replace("Z", "+00:00"))
                if published < cutoff:
                    continue
                region = cgtn_page_region(region_hint, row["title"])
                item = {
                    **row,
                    "description": f"CGTN {region_hint.replace('-', ' ').title()} · International market and trade coverage.",
                    "author": "CGTN",
                    "sourceCountry": "CGTN",
                    "category": global_category(row["title"]),
                }
                add_story(item, region)
                page_count += 1
            print(f"CGTN regional page {region_hint}: {page_count} recent trade stories collected")
        except Exception as error:
            print(f"CGTN regional page {region_hint} request failed: {error}")
        sleep(1)

    cached_by_region = {region: [] for region in GLOBAL_REGIONS}
    for item in previous_global:
        cached_by_region[item["continent"]].append(item)
    combined = []
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

