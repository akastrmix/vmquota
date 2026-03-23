import argparse
import unittest

from vmquota.parsing import (
    format_bps,
    format_bytes,
    parse_anchor_day,
    parse_byte_size,
    parse_rate_bps,
    parse_vmid_ranges,
)


class ParsingTests(unittest.TestCase):
    def test_parse_byte_size(self) -> None:
        self.assertEqual(parse_byte_size("2TB"), 2_000_000_000_000)
        self.assertEqual(parse_byte_size("2TiB"), 2_199_023_255_552)

    def test_parse_rate_bps(self) -> None:
        self.assertEqual(parse_rate_bps("2mbit"), 2_000_000)
        self.assertEqual(parse_rate_bps("2mbps"), 16_000_000)

    def test_parse_ranges(self) -> None:
        ranges = parse_vmid_ranges(["101-110", "200"])
        self.assertEqual((ranges[0].start, ranges[0].end), (101, 110))
        self.assertEqual((ranges[1].start, ranges[1].end), (200, 200))

    def test_parse_anchor_day(self) -> None:
        self.assertEqual(parse_anchor_day("31"), 31)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_anchor_day("99")

    def test_format_helpers(self) -> None:
        self.assertEqual(format_bytes(2_000_000), "2.00 MB")
        self.assertEqual(format_bps(2_000_000), "2.00 mbit/s")


if __name__ == "__main__":
    unittest.main()
