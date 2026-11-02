import json
from pathlib import Path
import tempfile
import unittest

from inference_settings import load_limits


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def test_missing_file_uses_sixty_minutes(self):
        self.assertEqual(load_limits(self.path), {"max_runtime_seconds": 3600, "max_wait_seconds": 7200})

    def test_custom_limits_and_utf8_bom(self):
        self.path.write_text(json.dumps({"max_response_minutes": 90, "max_queue_wait_minutes": 180}), encoding="utf-8-sig")
        self.assertEqual(load_limits(self.path), {"max_runtime_seconds": 5400, "max_wait_seconds": 10800})

    def test_invalid_limits_fail_instead_of_silently_using_fifteen_minutes(self):
        for value in ([], {"typo": 60}, {"max_response_minutes": True},
                      {"max_response_minutes": "60"}, {"max_response_minutes": 1.5},
                      {"max_response_minutes": 0}, {"max_queue_wait_minutes": 1441},
                      {"max_response_minutes": 120, "max_queue_wait_minutes": 60}):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_limits(self.path)
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_limits(self.path)
