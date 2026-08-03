"""
WBS-6 source_reliability 单元测试

测试来源可信度评分函数 score_source_reliability。
"""
import pytest

from app.evidence.source_reliability import (
    score_source_reliability,
    ReliabilityTier,
)


class TestTierS:
    """S 级：官方权威来源"""

    def test_gov_cn_is_s(self):
        assert score_source_reliability("https://www.mof.gov.cn/article/123") == ReliabilityTier.S

    def test_local_gov_cn_is_s(self):
        assert score_source_reliability("http://shandong.gov.cn/notice/456") == ReliabilityTier.S

    def test_ccgp_is_s(self):
        """中国政府采购网"""
        assert score_source_reliability("https://www.ccgp.gov.cn/notice/789") == ReliabilityTier.S

    def test_zfcg_keyword_is_s(self):
        """政府采购关键词"""
        assert score_source_reliability("http://zfcg.suzhou.gov.cn/bid/123") == ReliabilityTier.S

    def test_ggzy_keyword_is_s(self):
        """公共资源交易"""
        assert score_source_reliability("https://ggzy.hebei.gov.cn/tender/456") == ReliabilityTier.S

    def test_ebnew_keyword_is_s(self):
        """中国采购与招标网"""
        assert score_source_reliability("https://www.ebnew.com/bid/789") == ReliabilityTier.S


class TestTierA:
    """A 级：权威媒体和行业权威"""

    def test_xinhuanet_is_a(self):
        assert score_source_reliability("http://www.xinhuanet.com/politics/123") == ReliabilityTier.A

    def test_people_is_a(self):
        assert score_source_reliability("https://people.com.cn/n1/2026/0704/456") == ReliabilityTier.A

    def test_caixin_is_a(self):
        assert score_source_reliability("https://www.caixin.com/2026-07-04/123") == ReliabilityTier.A

    def test_deloitte_is_a(self):
        assert score_source_reliability("https://www2.deloitte.com/cn/zh/pages/xxx") == ReliabilityTier.A

    def test_cnki_is_a(self):
        assert score_source_reliability("https://kns.cnki.net/kcms2/article/xxx") == ReliabilityTier.A

    def test_confirmed_enterprise_official_domain_is_a(self):
        assert score_source_reliability(
            "https://life.cpic.com.cn/c/2026-07-01/notice.shtml",
            official_domains=("cpic.com.cn",),
        ) == ReliabilityTier.A


class TestTierB:
    """B 级：中等可信来源"""

    def test_official_site_source_type_is_a(self):
        """明确标注的企业官网原文 → A"""
        assert score_source_reliability(
            "https://some-company.com/news", source_type="official_site"
        ) == ReliabilityTier.A

    def test_tianyancha_is_b(self):
        assert score_source_reliability("https://www.tianyancha.com/company/123") == ReliabilityTier.B


class TestTierC:
    """C 级：低可信来源"""

    def test_zhihu_is_c(self):
        assert score_source_reliability("https://www.zhihu.com/question/123") == ReliabilityTier.C

    def test_csdn_is_c(self):
        assert score_source_reliability("https://blog.csdn.net/user/article/123") == ReliabilityTier.C

    def test_tieba_is_c(self):
        assert score_source_reliability("https://tieba.baidu.com/p/123456") == ReliabilityTier.C

    def test_weibo_is_c(self):
        assert score_source_reliability("https://weibo.com/u/1234567890") == ReliabilityTier.C

    def test_procurement_aggregator_is_c(self):
        assert score_source_reliability(
            "https://www.bidcenter.com.cn/newscontent-123.html"
        ) == ReliabilityTier.C

    def test_recruitment_aggregator_is_c(self):
        assert score_source_reliability(
            "https://www.wondercv.com/jobs/example.html"
        ) == ReliabilityTier.C


class TestUnknown:
    """UNKNOWN：无法判断"""

    def test_general_commercial_domain_is_unknown(self):
        """一般商业网站 → UNKNOWN（不在任何名单）"""
        assert score_source_reliability("https://www.random-company.com/page") == ReliabilityTier.UNKNOWN

    def test_empty_url_is_unknown(self):
        assert score_source_reliability("") == ReliabilityTier.UNKNOWN

    def test_none_url_is_unknown(self):
        assert score_source_reliability("") == ReliabilityTier.UNKNOWN

    def test_whitespace_url_is_unknown(self):
        assert score_source_reliability("   ") == ReliabilityTier.UNKNOWN

    def test_url_without_protocol(self):
        """无协议头的 URL"""
        assert score_source_reliability("www.mof.gov.cn/article") == ReliabilityTier.S

    def test_url_with_port(self):
        """带端口的 URL"""
        assert score_source_reliability("https://www.ccgp.gov.cn:8443/notice") == ReliabilityTier.S


class TestPriority:
    """优先级：S > A > C > UNKNOWN"""

    def test_gov_cn_overrides_all(self):
        """域名以 .gov.cn 结尾 → S（必须是后缀匹配，而非前缀匹配）"""
        assert score_source_reliability("https://www.example.gov.cn/blog") == ReliabilityTier.S

    def test_authoritative_not_misclassified(self):
        """人民网不应被 C 级名单影响"""
        assert score_source_reliability("https://people.com.cn/article") == ReliabilityTier.A

    @pytest.mark.parametrize(
        "url",
        (
            "https://fake-ccgp.example.com/notice",
            "https://ggzy-news.example.net/tender",
            "https://ebnew.example.org/bid",
        ),
    )
    def test_authority_keywords_inside_untrusted_hostname_are_not_s(self, url):
        assert score_source_reliability(url) == ReliabilityTier.UNKNOWN
