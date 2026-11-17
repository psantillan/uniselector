"""The copy of a web application for the SentinelBench tasks: which tables one answer of the application gives."""
from uniselector_tasks.sentinel import cell, reference, tables


def test_an_answer_of_the_application_gives_one_table_for_each_list_and_one_for_its_plain_values():
    assert tables("stocks", {"stocks": [{"symbol": "AAA"}, {"symbol": "BBB"}]}) == {"stocks": [{"symbol": "AAA"}, {"symbol": "BBB"}]}
    assert tables("portfolio", {"buying_power": 10.5, "total_gain": 2}) == {"portfolio": [{"buying_power": 10.5, "total_gain": 2}]}
    assert tables("repository", {"repository": {"stars": 7}}) == {"repository": [{"stars": 7}]}
    assert tables("papers", {"papers": [{"id": 1}], "total": 1}) == {"papers_papers": [{"id": 1}], "papers_values": [{"total": 1}]}
    assert tables("alerts", {"alerts": []}) == {}   # a list with no rows has no columns, so it has no table yet
    assert cell(["x", {"y": 1}]) == '["x", {"y": 1}]' and cell(None) == "" and cell(3) == 3


def test_the_lists_to_copy_are_the_get_paths_of_one_application_that_have_no_parameter_in_the_path():
    api = {"paths": {"/data/shop-stocks": {"get": {"parameters": [{"name": "search", "in": "query"}]}},
                     "/data/shop-stocks/{symbol}": {"get": {}},
                     "/data/shop-stocks/{symbol}/order": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}}}},
                     "/data/other-things": {"get": {}}},
           "components": {"schemas": {"Order": {"required": ["action"], "properties": {"action": {"type": "string"}, "quantity": {"type": "integer"}}}}}}
    lists, text = reference(api, "shop")
    assert lists == ["/data/shop-stocks"]
    assert text.splitlines() == ["GET /data/shop-stocks query: search", "GET /data/shop-stocks/{symbol}",
                                 "POST /data/shop-stocks/{symbol}/order body: {action: string, quantity?: integer}"]
