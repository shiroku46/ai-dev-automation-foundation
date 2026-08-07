import unittest

from src.access import can_enter
from src.policy import is_active


class AccessTests(unittest.TestCase):
    def test_policy_normalizes_case_and_outer_whitespace(self):
        self.assertTrue(is_active("  ACTIVE "))

    def test_access_reuses_policy_behavior(self):
        self.assertTrue(can_enter(" Active "))

    def test_inactive_state_is_rejected(self):
        self.assertFalse(can_enter("disabled"))


if __name__ == "__main__":
    unittest.main()
