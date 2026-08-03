"""
来源可信度评分

基于 URL 域名模式和来源类型，给每条证据赋予可信度等级 S/A/B/C/UNKNOWN。

用法:
    from app.evidence.source_reliability import score_source_reliability, ReliabilityTier
    tier = score_source_reliability("https://www.ccgp.gov.cn/xxx")
    # → ReliabilityTier.S
"""
import logging
from enum import Enum
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ReliabilityTier(str, Enum):
    """来源可信度等级"""
    S = "S"           # 官方权威（.gov.cn, 政府采购网, 官方公告平台）
    A = "A"           # 高可信（权威媒体、行业权威机构）
    B = "B"           # 中等可信（一般媒体、企业官网）
    C = "C"           # 低可信（个人博客、论坛、社交平台）
    UNKNOWN = "UNKNOWN"  # 无法判断


# ── 域名模式表 ──────────────────────────────────────────────────────────

# S 级：政府域 + 政府采购平台
_S_TIER_SUFFIXES = (
    ".gov.cn",
)

_S_TIER_DOMAINS = {
    # 政府采购平台
    "ccgp.gov.cn",          # 中国政府采购网
    "zfcg.shandong.gov.cn", # 山东政府采购
    "zfcg.hebei.gov.cn",   # 河北政府采购
    "zfcg.hunan.gov.cn",   # 湖南政府采购
    # 明确核验过的非 gov.cn 招采平台
    "ebnew.com",
    "jxsggzy.cn",
}

# A 级：权威媒体 + 行业权威
_A_TIER_DOMAINS = {
    # 官方新闻
    "xinhuanet.com",
    "people.com.cn",
    "china.com.cn",
    "chinadaily.com.cn",
    "cctv.com",
    "gmw.cn",
    "youth.cn",
    "cnr.cn",
    # 经济/财经权威
    "caixin.com",
    "eeo.com.cn",
    "yicai.com",
    "cls.cn",
    "21jingji.com",
    "ce.cn",
    "jjckb.cn",
    # 行业权威
    "hbr.org",
    "mckinsey.com",
    "deloitte.com",
    "pwc.com",
    "ey.com",
    "kpmg.com",
    "gartner.com",
    "idc.com",
    # 学术/标准
    "cnki.net",
    "wanfangdata.com.cn",
    "std.gov.cn",
    "sac.gov.cn",
}

# C 级：低可信来源
_C_TIER_DOMAINS = {
    "zhihu.com",
    "tieba.baidu.com",
    "douban.com",
    "weibo.com",
    "xiaohongshu.com",
    "douyin.com",
    "kuaishou.com",
    "csdn.net",
    "jianshu.com",
    "juejin.cn",
    "segmentfault.com",
    "blog.csdn.net",
    "medium.com",
    "bidcenter.com.cn",
    "wondercv.com",
    "liepin.com",
    "zhipin.com",
    "51job.com",
    "zhaopin.com",
}

# 常见企业信息平台 → B 级
_B_TIER_KEYWORDS = (
    "tianyancha",
    "qichacha",
    "qixin",
    "企查查",
)


def score_source_reliability(
    url: str,
    source_type: str = "",
    *,
    official_domains: tuple[str, ...] = (),
) -> ReliabilityTier:
    """根据 URL 域名和来源类型返回可信度等级。

    规则优先级（先匹配先返回）：
    S:  .gov.cn 后缀 → S；明确核验的政府采购/公共资源域名 → S
    A:  权威媒体/行业权威域名 → A
    B:  企业信息平台 keyword → B; source_type="official_site" → B
    C:  论坛/博客/社交域名 → C
    其他: UNKNOWN

    Args:
        url: 来源 URL（可含或不含协议头）
        source_type: 来源类型提示（如 "official_site", "web_scrape"）

    Returns:
        可信度等级
    """
    if not url or not url.strip():
        return ReliabilityTier.UNKNOWN

    # 解析域名
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower()

    if not hostname:
        return ReliabilityTier.UNKNOWN

    normalized_official_domains = tuple(
        domain.strip().lower().removeprefix("www.")
        for domain in official_domains
        if isinstance(domain, str) and domain.strip()
    )
    if any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in normalized_official_domains
    ):
        return ReliabilityTier.A

    # ── S 级 ──────────────────────────────────────────────────────
    # gov.cn 后缀
    if hostname.endswith(_S_TIER_SUFFIXES):
        return ReliabilityTier.S

    # 仅接受明确域名及其子域，禁止凭 ccgp/ggzy 等字符串片段提权。
    for domain in _S_TIER_DOMAINS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return ReliabilityTier.S

    # ── A 级 ──────────────────────────────────────────────────────
    # 精确匹配 A 级域名
    if hostname in _A_TIER_DOMAINS:
        return ReliabilityTier.A

    # 匹配权威域名后缀（如子域名）
    for domain in _A_TIER_DOMAINS:
        if hostname.endswith("." + domain) or hostname == domain:
            return ReliabilityTier.A

    # ── C 级 ──────────────────────────────────────────────────────
    if hostname in _C_TIER_DOMAINS:
        return ReliabilityTier.C
    for domain in _C_TIER_DOMAINS:
        if hostname.endswith("." + domain) or hostname == domain:
            return ReliabilityTier.C

    # ── B 级 ──────────────────────────────────────────────────────
    # source_type 提示
    if source_type and source_type.lower() in ("official_site", "official"):
        return ReliabilityTier.A

    # 企业信息平台
    for kw in _B_TIER_KEYWORDS:
        if kw in hostname:
            return ReliabilityTier.B

    # ── 默认 ──────────────────────────────────────────────────────
    return ReliabilityTier.UNKNOWN
