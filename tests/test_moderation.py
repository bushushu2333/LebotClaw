"""内容守护（违禁词 + 心理守护）引擎测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lebotclaw.core import moderation as m  # noqa: E402


class TestCategories(unittest.TestCase):
    def test_abuse_body(self):
        r = m.check("你这个傻逼")
        self.assertEqual(r.category, "abuse")
        self.assertFalse(r.blocked)  # 温和级放行

    def test_abuse_homophone(self):
        self.assertEqual(m.check("你这个煞笔").category, "abuse")

    def test_abuse_pinyin_abbr(self):
        self.assertEqual(m.check("你真是个 sb").category, "abuse")

    def test_abuse_symbol_mask(self):
        self.assertEqual(m.check("傻*逼").category, "abuse")
        self.assertEqual(m.check("傻 b").category, "abuse")

    def test_abuse_repeat(self):
        self.assertEqual(m.check("傻逼逼逼").category, "abuse")

    def test_nsfw_blocked(self):
        r = m.check("我想看色情视频")
        self.assertEqual(r.category, "nsfw")
        self.assertTrue(r.blocked)

    def test_politics_blocked(self):
        r = m.check("支持台独")
        self.assertEqual(r.category, "politics")
        self.assertTrue(r.blocked)

    def test_mental_not_blocked(self):
        r = m.check("我不想活了")
        self.assertEqual(r.category, "mental")
        self.assertFalse(r.blocked)  # 绝不拦截
        self.assertTrue(r.priority_high)
        self.assertEqual(r.hotline, "12356")

    def test_mental_hurt_others(self):
        r = m.check("我想弄死他")
        self.assertEqual(r.category, "mental")
        self.assertTrue(r.priority_high)


class TestFalsePositives(unittest.TestCase):
    """反误伤：口语夸张、学科内容不应命中。"""

    def test_colloquial_miss(self):
        self.assertEqual(m.check("想死你了，好久不见").category, "")

    def test_lei_si(self):
        self.assertEqual(m.check("今天累死了").category, "")

    def test_xiao_si(self):
        self.assertEqual(m.check("笑死我了哈哈哈").category, "")

    def test_subject_physics(self):
        self.assertEqual(m.check("光合作用是怎么回事").category, "")

    def test_subject_history(self):
        self.assertEqual(m.check("辛亥革命是哪一年").category, "")

    def test_math(self):
        self.assertEqual(m.check("3.14乘以2.5等于多少").category, "")

    def test_clean_greeting(self):
        self.assertEqual(m.check("你好呀，我是小明").category, "")


class TestPriority(unittest.TestCase):
    """同时命中多类，取最高优先级（mental > nsfw > politics > abuse）。"""

    def test_mental_over_abuse(self):
        r = m.check("我是个废物不想活了")
        self.assertEqual(r.category, "mental")


class TestOutputGate(unittest.TestCase):
    def test_output_nsfw_replaced(self):
        safe, r = m.check_output("来我教你做爱")
        self.assertEqual(r.category, "nsfw")
        self.assertNotEqual(safe, "来我教你做爱")

    def test_output_clean(self):
        safe, r = m.check_output("光合作用是植物制造养分的过程")
        self.assertFalse(r.hit)


class TestMask(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(m._mask("傻逼"), "傻*")
        self.assertEqual(m._mask("a"), "a*")


if __name__ == "__main__":
    unittest.main()
