"""Knowledge-graph construction tests."""

from app.services.graph.builder import GraphBuilder

from conftest import make_invoice, make_item


def build(batch):
    return GraphBuilder().build(batch)


class TestGraphBuilder:
    def test_empty_batch_returns_empty_graph(self):
        graph = build([])
        assert graph["nodes"] == []
        assert graph["edges"] == []
        assert graph["statistics"]["total_nodes"] == 0

    def test_node_and_edge_counts(self):
        batch = [make_invoice(number="11111111111111111111", items=[make_item(name="固态硬盘")])]
        graph = build(batch)
        types = {n["type"] for n in graph["nodes"]}
        assert types == {"invoice", "company", "product"}
        # 1 invoice + 2 companies + 1 product
        assert graph["statistics"]["total_nodes"] == 4
        relations = {e["relation"] for e in graph["edges"]}
        assert {"bought", "sold", "contains"} <= relations

    def test_same_company_deduplicated(self):
        batch = [
            make_invoice(number="11111111111111111111", seller_name="甲公司", seller_tax_id="91440183797370649Q"),
            make_invoice(number="22222222222222222222", seller_name="甲公司", seller_tax_id="91440183797370649Q"),
        ]
        graph = build(batch)
        companies = [n for n in graph["nodes"] if n["type"] == "company"]
        # 甲公司 + 同济大学 (buyer, deduplicated across both invoices)
        assert len(companies) == 2
        seller = next(n for n in companies if n["properties"].get("name") == "甲公司")
        assert seller["properties"]["invoice_count"] == 2

    def test_transaction_edge_between_companies(self):
        batch = [
            make_invoice(number="11111111111111111111", seller_name="甲公司", buyer_name="乙公司",
                         amount_including_tax=100.0),
            make_invoice(number="22222222222222222222", seller_name="甲公司", buyer_name="乙公司",
                         amount_including_tax=200.0),
        ]
        graph = build(batch)
        transactions = [e for e in graph["edges"] if e["relation"] == "transaction"]
        assert len(transactions) == 1
        assert transactions[0]["properties"]["transaction_count"] == 2
        assert transactions[0]["properties"]["total_amount"] == 300.0

    def test_deterministic_node_ids(self):
        b1 = build([make_invoice(number="11111111111111111111")])
        b2 = build([make_invoice(number="11111111111111111111")])
        assert {n["id"] for n in b1["nodes"]} == {n["id"] for n in b2["nodes"]}

    def test_statistics_totals(self):
        batch = [
            make_invoice(number="11111111111111111111", items=[make_item(name="A"), make_item(name="B")]),
            make_invoice(number="22222222222222222222", items=[make_item(name="A")]),
        ]
        graph = build(batch)
        stats = graph["statistics"]
        assert stats["total_edges"] == len(graph["edges"])
        assert stats["node_types"]["invoice"] == 2
        assert stats["edge_types"]["contains"] == 3

    def test_insights_available(self):
        batch = [make_invoice(number="11111111111111111111", amount_including_tax=100.0)]
        result = GraphBuilder().build_with_insights(batch)
        assert "top_companies_by_amount" in result["insights"]
        assert result["insights"]["top_companies_by_amount"][0]["amount"] == 100.0
