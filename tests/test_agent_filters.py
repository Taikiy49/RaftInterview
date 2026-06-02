import os
import time
import unittest
from unittest.mock import patch

import main


ORDERS = [
    "Order 1001: Buyer=John Davis, Location=Columbus, OH, Total=$742.10, Items: laptop, hdmi cable",
    "Order 1002: Buyer=Sarah Liu, Location=Austin, TX, Total=$156.55, Items: headphones",
    "Order 1003: Buyer=Mike Turner, Location=Cleveland, OH, Total=$1299.99, Items: gaming pc, mouse",
    "Order 1004: Buyer=Rachel Kim, Location=Seattle, WA, Total=$89.50, Items: coffee maker",
    "Order 1005: Buyer=Chris Myers, Location=Cincinnati, OH, Total=$512.00, Items: monitor, desk lamp",
]


def run_local_query(request_text):
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
        state = {
            "request": request_text,
            "started_at": time.time(),
            "raw_records": ORDERS,
            "chunks": [ORDERS],
        }
        state = main.parse_orders(state)
        state = main.parse_request(state)
        state = main.filter_orders(state)
        return state["result"]["orders"], state["filters"]


class AgentFilterTests(unittest.TestCase):
    def assert_order_ids(self, request_text, expected_ids):
        orders, _ = run_local_query(request_text)
        self.assertEqual([order["orderId"] for order in orders], expected_ids)

    def test_item_query(self):
        self.assert_order_ids("People who bought HDMI cables", ["1001"])

    def test_misspelled_item_query(self):
        self.assert_order_ids("people who bought hmdi cabels", ["1001"])

    def test_all_items_query(self):
        orders, filters = run_local_query("orders with laptop and hdmi cable")
        self.assertEqual([order["orderId"] for order in orders], ["1001"])
        self.assertEqual(filters["item_match_mode"], "all")

    def test_any_items_query(self):
        orders, filters = run_local_query("orders with mouse or headphones")
        self.assertEqual([order["orderId"] for order in orders], ["1002", "1003"])
        self.assertEqual(filters["item_match_mode"], "any")

    def test_singular_item_alias(self):
        self.assert_order_ids("orders with headphone", ["1002"])

    def test_city_typo(self):
        self.assert_order_ids("orders in Clevland", ["1003"])

    def test_state_and_total(self):
        self.assert_order_ids("orders in Ohio over 500", ["1001", "1003", "1005"])

    def test_total_range(self):
        self.assert_order_ids("orders between 100 and 600", ["1002", "1005"])

    def test_buyer_typo(self):
        self.assert_order_ids("buyer Jon Davis", ["1001"])

    def test_rank_filter(self):
        self.assert_order_ids("cheapest order", ["1004"])

    def test_exact_total(self):
        self.assert_order_ids("orders where total is 512", ["1005"])


if __name__ == "__main__":
    unittest.main()
