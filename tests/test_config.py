from pathlib import Path
import tempfile
import unittest

from vmquota.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "missing.toml"

            with self.assertRaisesRegex(FileNotFoundError, "config file not found"):
                load_config(config_path)

    def test_load_config_accepts_toml_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                """
[general]
timezone = "Asia/Shanghai"
state_db = "/tmp/vmquota.sqlite"
enforce_shaping = true

[api]
bind_host = "127.0.0.1"
bind_port = 9527

[scope]
vmid_ranges = ["101-110"]

[defaults]
limit_bytes = 2000000000000
throttle_rate = "2mbit"
auto_enroll = false
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.enforce_shaping)
            self.assertFalse(config.auto_enroll)
            self.assertEqual(config.api_bind_port, 9527)
            self.assertEqual(config.api_access_log, Path("/var/lib/vmquota/api-access.jsonl"))
            self.assertEqual(config.api_access_log_max_entries, 1000)
            self.assertEqual(config.default_limit_bytes, 2_000_000_000_000)

    def test_load_config_accepts_api_access_log_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            access_log = Path(tempdir) / "access.jsonl"
            config_path.write_text(
                f"""
[general]

[api]
access_log = "{access_log.as_posix()}"
access_log_max_entries = 25

[scope]
vmid_ranges = ["101-110"]

[defaults]
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.api_access_log, access_log)
            self.assertEqual(config.api_access_log_max_entries, 25)

    def test_load_config_rejects_string_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                """
[general]
enforce_shaping = "false"

[api]

[scope]
vmid_ranges = ["101-110"]

[defaults]
auto_enroll = "false"
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "general.enforce_shaping must be a TOML boolean"):
                load_config(config_path)

    def test_load_config_rejects_implicit_type_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                """
[general]

[api]
bind_port = "9527"

[scope]
vmid_ranges = ["101-110"]

[defaults]
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "api.bind_port must be a TOML integer"):
                load_config(config_path)

    def test_load_config_rejects_wrong_section_type(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text('general = "invalid"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "general must be a TOML table"):
                load_config(config_path)

    def test_load_config_rejects_invalid_ranges_type(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                """
[general]

[api]

[scope]
vmid_ranges = [101, 102]

[defaults]
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "scope.vmid_ranges must be a non-empty TOML string array"):
                load_config(config_path)

    def test_load_config_rejects_missing_required_section(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required TOML table: general"):
                load_config(config_path)

    def test_load_config_uses_boolean_defaults_when_sections_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.toml"
            config_path.write_text(
                """
[general]

[api]

[scope]
vmid_ranges = ["101-110"]

[defaults]
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertFalse(config.enforce_shaping)
            self.assertTrue(config.auto_enroll)


if __name__ == "__main__":
    unittest.main()
