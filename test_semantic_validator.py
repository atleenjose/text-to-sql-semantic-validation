
import unittest

import sqlglot

from semantic_validator import (
    _has_order_count_grain_issue,
    validate_sql,
)


class TestAggregationGrain(unittest.TestCase):

    def check_grain(self, sql, expected):
        tree = sqlglot.parse_one(sql, read="duckdb")
        self.assertEqual(
            _has_order_count_grain_issue(tree),
            expected
        )

    def test_direct_join(self):
        sql = """
        SELECT COUNT(*)
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        """
        self.check_grain(sql, True)

    def test_reversed_join(self):
        sql = """
        SELECT COUNT(*)
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        """
        self.check_grain(sql, True)

    def test_distinct_order_count(self):
        sql = """
        SELECT COUNT(DISTINCT o.order_id)
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        """
        self.check_grain(sql, False)

    def test_exists_query(self):
        sql = """
        SELECT COUNT(*)
        FROM orders o
        WHERE EXISTS (
            SELECT 1
            FROM order_items oi
            WHERE oi.order_id = o.order_id
        )
        """
        self.check_grain(sql, False)

    def test_grouped_subquery(self):
        sql = """
        SELECT COUNT(*)
        FROM (
            SELECT o.order_id
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            GROUP BY o.order_id
        ) grouped_orders
        """
        self.check_grain(sql, False)

    def test_f08_integration(self):
        question = "How many completed orders contained Sports products?"

        sql = """
        SELECT COUNT(*)
        FROM orders
        JOIN order_items
            ON orders.order_id = order_items.order_id
        JOIN products
            ON order_items.product_id = products.product_id
        WHERE orders.order_status = 'completed'
          AND products.category = 'Sports'
        """

        result = validate_sql(question, sql)

        self.assertFalse(result["passed"])
        self.assertIn(
            "Order count may be inflated by duplicate order items",
            result["issues"]
        )


if __name__ == "__main__":
    unittest.main()
