"""校准聚合引擎的单元测试。

零第三方依赖，可直接用标准库 unittest 运行：

    python -m unittest discover -s tests -v
    # 或
    python tests/test_calibration.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from wb_quota_guard import calibration  # noqa: E402


def make_sample(contributor="alice", model="hy3", tokens=20.0, credit=None,
                requests=None, kind="auto-inferred", samples=5,
                window=24.0, observed="2026-10-01", ckind="inferred"):
    limits = {}
    if tokens is not None:
        limits["tokens_M"] = tokens
    if credit is not None:
        limits["credit"] = credit
    if requests is not None:
        limits["requests"] = requests
    return {
        "schema_version": 1,
        "contributor": {"id": contributor, "kind": ckind},
        "model": model,
        "observed_at": observed,
        "window_hours": window,
        "limits": limits,
        "method": {"kind": kind, "samples": samples},
    }


class TestValidate(unittest.TestCase):
    def test_valid_sample_passes(self):
        s = calibration.validate_sample(make_sample(), "t.json")
        self.assertEqual(s["model"], "hy3")
        self.assertEqual(s["limits"]["tokens_M"], 20.0)

    def test_rejects_unknown_field(self):
        s = make_sample()
        s["bogus"] = 1
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_bad_schema_version(self):
        s = make_sample()
        s["schema_version"] = 99
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_bad_contributor_id(self):
        s = make_sample(contributor="Alice_123")  # 大写与下划线不允许
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_empty_limits(self):
        s = make_sample(tokens=0, credit=None, requests=None)
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_bad_date(self):
        s = make_sample(observed="2026/10/01")
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_windows_path(self):
        s = make_sample()
        s["method"]["notes"] = "见 /Users/jason/notes"
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_email(self):
        s = make_sample()
        s["method"]["notes"] = "联系我 a@b.com"
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_uuid(self):
        s = make_sample()
        s["method"]["notes"] = "id 550e8400-e29b-41d4-a716-446655440000"
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")

    def test_rejects_windows_absolute_path(self):
        s = make_sample()
        s["method"]["notes"] = r"C:\Users\Jason\data"
        with self.assertRaises(calibration.SampleError):
            calibration.validate_sample(s, "t.json")


class TestAggregate(unittest.TestCase):
    def test_single_contributor_not_adopted(self):
        """核心规则：单一贡献者的样本不足以定论。"""
        result = calibration.aggregate([make_sample(contributor="alice", tokens=20.0)])
        self.assertEqual(result["adopted_count"], 0)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertIn("独立贡献者", result["conflicts"][0]["reason"])

    def test_same_contributor_twice_not_enough(self):
        """同一人提交两条也不算交叉验证。"""
        samples = [
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="alice", tokens=21.0),
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 0)

    def test_two_consistent_contributors_adopted(self):
        samples = [
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="bob", tokens=21.0),
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 1)
        entry = result["reference"]["hy3@24h"]
        self.assertEqual(entry["contributor_count"], 2)
        self.assertEqual(entry["limits"]["tokens_M"], 20.5)
        self.assertGreater(entry["confidence"], 0)

    def test_conflicting_samples_not_adopted(self):
        """两个贡献者差距过大 → 判冲突，不进参考表。"""
        samples = [
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="bob", tokens=60.0),  # 相对离散度 2.0 > 0.25
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 0)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertIn("离散度", result["conflicts"][0]["reason"])

    def test_outlier_contributor_is_excluded(self):
        """三人一致 + 一人离群 → 剔除离群者，其余采纳。

        这是社区池的关键鲁棒性：单个异常提交不应让该模型对所有人不可用。
        """
        samples = [
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="bob", tokens=20.5),
            make_sample(contributor="carol", tokens=20.2),
            make_sample(contributor="dave", tokens=80.0),  # 明显离群
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 1, result["conflicts"])
        entry = result["reference"]["hy3@24h"]
        self.assertIn("dave", entry.get("excluded_contributors", []))
        self.assertNotIn("dave", entry["contributors"])
        # 取值应贴近多数派而非离群值
        self.assertLess(entry["limits"]["tokens_M"], 25)

    def test_majority_outlier_not_dropped(self):
        """若多数派才是离群的那个 → 不剔除，判为整体冲突。"""
        samples = [
            make_sample(contributor="alice", tokens=80.0),
            make_sample(contributor="bob", tokens=80.0),
            make_sample(contributor="carol", tokens=80.0),
            make_sample(contributor="dave", tokens=20.0),
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 0)
        self.assertEqual(len(result["conflicts"]), 1)

    def test_one_contributor_many_samples_cannot_outvote(self):
        """一人提交多条样本，仍只算一个独立贡献者。"""
        samples = [
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="alice", tokens=20.1),
            make_sample(contributor="alice", tokens=20.2),
            make_sample(contributor="bob", tokens=25.0),
        ]
        result = calibration.aggregate(samples)
        # 只有 2 个独立贡献者，且离散度 25% 恰好在容差边界
        self.assertLessEqual(result["adopted_count"], 1)
        if result["adopted_count"] == 1:
            entry = result["reference"]["hy3@24h"]
            self.assertEqual(entry["contributor_count"], 2)

    def test_different_models_are_independent(self):
        samples = [
            make_sample(contributor="alice", model="hy3", tokens=20.0),
            make_sample(contributor="bob", model="hy4-preview", tokens=50.0),
        ]
        result = calibration.aggregate(samples)
        # 两个模型各自只有 1 个贡献者，都不采纳
        self.assertEqual(result["adopted_count"], 0)
        self.assertEqual(len(result["conflicts"]), 2)

    def test_different_windows_are_independent(self):
        samples = [
            make_sample(contributor="alice", tokens=20.0, window=24.0),
            make_sample(contributor="bob", tokens=20.0, window=12.0),
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 0)

    def test_manual_anchor_gets_higher_weight(self):
        """人工锚点权重更高，取值应更靠近锚点。

        注意：两条样本的差距必须落在容差内，否则会先被判为冲突而无法采纳。
        这里取 24（锚点）与 26（反推）：中位数 25，锚点权重 3 : 反推 1，
        结果应明显偏向 24 一侧（小于 25）。
        """
        samples = [
            make_sample(contributor="alice", tokens=24.0, kind="manual-anchor"),
            make_sample(contributor="bob", tokens=26.0, kind="auto-inferred"),
        ]
        result = calibration.aggregate(samples)
        self.assertEqual(result["adopted_count"], 1, result["conflicts"])
        entry = result["reference"]["hy3@24h"]
        # 加权中位数应偏向 24 而非等权时的 25
        self.assertLess(entry["limits"]["tokens_M"], 25)
        self.assertGreaterEqual(entry["limits"]["tokens_M"], 24)
        self.assertTrue(entry["has_manual_anchor"])

    def test_anchor_raises_confidence(self):
        with_anchor = calibration.aggregate([
            make_sample(contributor="alice", tokens=20.0, kind="manual-anchor"),
            make_sample(contributor="bob", tokens=20.0, kind="auto-inferred"),
        ])
        without = calibration.aggregate([
            make_sample(contributor="alice", tokens=20.0, kind="auto-inferred"),
            make_sample(contributor="bob", tokens=20.0, kind="auto-inferred"),
        ])
        self.assertGreater(
            with_anchor["reference"]["hy3@24h"]["confidence"],
            without["reference"]["hy3@24h"]["confidence"],
        )

    def test_confidence_bounds(self):
        result = calibration.aggregate([
            make_sample(contributor="alice", tokens=20.0),
            make_sample(contributor="bob", tokens=20.0),
        ])
        c = result["reference"]["hy3@24h"]["confidence"]
        self.assertGreaterEqual(c, 0.0)
        self.assertLessEqual(c, 1.0)

    def test_credit_and_requests_dimensions(self):
        samples = [
            make_sample(contributor="alice", tokens=None, credit=100.0, requests=500),
            make_sample(contributor="bob", tokens=None, credit=102.0, requests=510),
        ]
        result = calibration.aggregate(samples)
        entry = result["reference"]["hy3@24h"]
        self.assertEqual(entry["limits"]["credit"], 101.0)
        self.assertEqual(entry["limits"]["requests"], 505)


class TestMerge(unittest.TestCase):
    def test_reference_overrides_local(self):
        local = {"per_model_limits": {"hy3": {"median_tokens_M": 20.0, "samples": 5}}}
        reference = {"reference": {"hy3@24h": {
            "model": "hy3", "limits": {"tokens_M": 25.0}, "confidence": 0.8,
            "contributor_count": 3, "sample_count": 5}}}
        merged, report = calibration.merge_reference(local, reference)
        self.assertEqual(merged["hy3"]["limits"]["tokens_M"], 25.0)
        self.assertEqual(merged["hy3"]["source"], "community-reference")
        self.assertIn("hy3", report["from_reference"])

    def test_local_fallback_when_no_reference(self):
        local = {"per_model_limits": {"hy3": {"median_tokens_M": 20.0, "samples": 5}}}
        merged, report = calibration.merge_reference(local, {"reference": {}})
        self.assertEqual(merged["hy3"]["source"], "local-inferred")
        self.assertEqual(merged["hy3"]["limits"]["tokens_M"], 20.0)
        self.assertIn("hy3", report["from_local"])

    def test_conflicted_model_marked(self):
        local = {"per_model_limits": {"hy3": {"median_tokens_M": 20.0, "samples": 5}}}
        reference = {"reference": {}, "conflicts": [{"model": "hy3", "reason": "x"}]}
        merged, report = calibration.merge_reference(local, reference)
        self.assertIn("conflicted", merged["hy3"]["source"])
        self.assertIn("hy3", report["conflicted"])

    def test_merge_does_not_mutate_input(self):
        local = {"per_model_limits": {"hy3": {"median_tokens_M": 20.0}}}
        before = json.dumps(local, sort_keys=True)
        calibration.merge_reference(local, {"reference": {}})
        self.assertEqual(json.dumps(local, sort_keys=True), before)


class TestLoadSamples(unittest.TestCase):
    def test_load_and_report_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "good.json"), "w", encoding="utf-8") as f:
                json.dump(make_sample(), f, ensure_ascii=False)
            with open(os.path.join(d, "bad.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            samples, errors = calibration.load_samples(d)
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("bad.json", errors[0])

    def test_missing_dir_returns_empty(self):
        samples, errors = calibration.load_samples("/nonexistent/path/xyz")
        self.assertEqual(samples, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
