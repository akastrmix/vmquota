import unittest

from vmquota.shaping import TrafficShaper


class ShapingTests(unittest.TestCase):
    def test_rate_tokens_accept_mbit_and_kbit_forms(self) -> None:
        tokens = TrafficShaper._rate_tokens(2000)
        self.assertIn("rate 2000Kbit", tokens)
        self.assertIn("rate 2Mbit", tokens)


if __name__ == "__main__":
    unittest.main()
