from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_DB = Path(__file__).resolve().parent / "data" / "a_stock_chain.db"
DEFAULT_DB = Path(os.environ.get("A_STOCK_CHAIN_DB", str(LOCAL_DB)))


def available() -> bool:
    return DEFAULT_DB.exists()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DEFAULT_DB)
    con.row_factory = sqlite3.Row
    return con


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not available():
        return []
    with closing(_connect()) as con:
        return [dict(row) for row in con.execute(sql, params).fetchall()]


def search(q: str, limit: int = 20) -> dict[str, Any]:
    q = str(q or "").strip()
    if not q:
        return {"available": available(), "query": q, "companies": [], "industries": [], "products": []}
    like = f"%{q}%"
    limit = max(1, min(int(limit or 20), 50))
    return {
        "available": available(),
        "query": q,
        "companies": _rows(
            """
            SELECT code6 AS code, name, sw_industry_lv1, sw_industry_lv2, sw_industry_lv3, market_value
            FROM companies
            WHERE code6 = ? OR name LIKE ? OR full_name LIKE ?
            ORDER BY CASE WHEN code6 = ? THEN 0 ELSE 1 END, market_value DESC
            LIMIT ?
            """,
            (q, like, like, q, limit),
        ),
        "industries": _rows(
            """
            SELECT id, name, code, level
            FROM industries
            WHERE name LIKE ?
            ORDER BY level, name
            LIMIT ?
            """,
            (like, limit),
        ),
        "products": _rows(
            """
            SELECT id, name
            FROM products
            WHERE name LIKE ?
            ORDER BY length(name), name
            LIMIT ?
            """,
            (like, limit),
        ),
    }


def stock_chain(code: str) -> dict[str, Any]:
    code = str(code or "").strip()
    companies = _rows(
        """
        SELECT id, code6 AS code, name, full_name, akshare_industry, sw_industry_lv1, sw_industry_lv2, sw_industry_lv3
        FROM companies
        WHERE code6 = ? OR name = ?
        LIMIT 1
        """,
        (code, code),
    )
    if not companies:
        return {"available": available(), "stock": None, "industries": [], "products": []}
    company = companies[0]
    company_id = company["id"]
    industries = _rows(
        """
        SELECT ci.industry_name AS name, i.id AS id, ci.rel
        FROM company_industry ci
        LEFT JOIN industries i ON ci.industry_id = i.id
        WHERE ci.company_id = ?
        ORDER BY ci.id
        """,
        (company_id,),
    )
    products = _rows(
        """
        SELECT cp.product_name AS name, p.id AS id, cp.rel, cp.rel_weight AS weight
        FROM company_product cp
        LEFT JOIN products p ON cp.product_id = p.id
        WHERE cp.company_id = ?
        ORDER BY cp.rel_weight DESC
        LIMIT 20
        """,
        (company_id,),
    )
    industry_chains = [_industry_chain(industry) for industry in industries]
    product_chains = [_product_chain(product) for product in products]
    return {
        "available": available(),
        "stock": {k: v for k, v in company.items() if k != "id"},
        "industries": industry_chains,
        "products": product_chains,
        "graph": _stock_graph(company, industry_chains, product_chains),
    }


def industry_chain(name: str, limit: int = 30) -> dict[str, Any]:
    name = str(name or "").strip()
    industry = _rows("SELECT id, name, code, level FROM industries WHERE name = ? LIMIT 1", (name,))
    if not industry:
        industry = _rows("SELECT id, name, code, level FROM industries WHERE name LIKE ? ORDER BY level, name LIMIT 1", (f"%{name}%",))
    if not industry:
        return {"available": available(), "industry": None, "upstream": [], "downstream": []}
    links = _industry_links(industry[0]["id"], limit)
    return {"available": available(), "industry": industry[0], **links, "graph": _industry_graph(industry[0], links)}


def industry_nav() -> dict[str, Any]:
    rows = _rows("SELECT id, code, name FROM industries ORDER BY code, name")
    lv1 = [row for row in rows if _sw_level(row.get("code")) == 1]
    lv2 = [row for row in rows if _sw_level(row.get("code")) == 2]
    lv3 = [row for row in rows if _sw_level(row.get("code")) == 3]
    by_lv2: dict[str, list[dict[str, Any]]] = {}
    for row in lv3:
        by_lv2.setdefault(str(row.get("code") or "")[:4], []).append(row)
    by_lv1: dict[str, list[dict[str, Any]]] = {}
    for row in lv2:
        children = by_lv2.get(str(row.get("code") or "")[:4], [])
        by_lv1.setdefault(str(row.get("code") or "")[:2], []).append({**row, "children": children, "count": len(children)})
    items = []
    for row in lv1:
        children = by_lv1.get(str(row.get("code") or "")[:2], [])
        items.append({**row, "children": children, "count": sum(child["count"] for child in children)})
    return {
        "available": available(),
        "items": items,
        "count": sum(item["count"] for item in items),
    }


def _sw_level(code: Any) -> int:
    code = str(code or "")
    if len(code) != 6 or not code.isdigit():
        return 0
    if code[2:] == "0000":
        return 1
    if code[4:] == "00":
        return 2
    return 3


def _industry_chain(industry: dict[str, Any]) -> dict[str, Any]:
    if not industry.get("id"):
        return {**industry, "upstream": [], "downstream": []}
    return {**industry, **_industry_links(industry["id"], 20)}


def _industry_links(industry_id: int, limit: int) -> dict[str, list[dict[str, Any]]]:
    return {
        "upstream": _rows(
            """
            SELECT i.name, iu.weight
            FROM industry_up iu
            LEFT JOIN industries i ON iu.to_industry_id = i.id
            WHERE iu.from_industry_id = ?
            ORDER BY iu.weight DESC
            LIMIT ?
            """,
            (industry_id, limit),
        ),
        "downstream": _rows(
            """
            SELECT i.name, iu.weight
            FROM industry_up iu
            LEFT JOIN industries i ON iu.from_industry_id = i.id
            WHERE iu.to_industry_id = ?
            ORDER BY iu.weight DESC
            LIMIT ?
            """,
            (industry_id, limit),
        ),
    }


def _product_chain(product: dict[str, Any]) -> dict[str, Any]:
    if not product.get("id"):
        return {**product, "upstream": [], "downstream": []}
    return {
        **product,
        "upstream": _rows(
            """
            SELECT p.name, pp.rel
            FROM product_product pp
            LEFT JOIN products p ON pp.to_product_id = p.id
            WHERE pp.from_product_id = ? AND pp.rel = '上游材料'
            ORDER BY p.name
            LIMIT 20
            """,
            (product["id"],),
        ),
        "downstream": _rows(
            """
            SELECT p.name, pp.rel
            FROM product_product pp
            LEFT JOIN products p ON pp.to_product_id = p.id
            WHERE pp.from_product_id = ? AND pp.rel = '下游产品'
            ORDER BY p.name
            LIMIT 20
            """,
            (product["id"],),
        ),
    }


def _node_key(kind: str, label: str) -> str:
    return f"{kind}:{label}"


def _graph_add_node(nodes: dict[str, dict[str, Any]], kind: str, label: str, column: str, weight: Any = None) -> str:
    key = _node_key(kind, str(label or ""))
    if label and key not in nodes:
        nodes[key] = {"id": key, "label": label, "kind": kind, "column": column, "weight": weight}
    return key


def _graph_add_edge(edges: list[dict[str, str]], source: str, target: str, label: str = "") -> None:
    if source and target and source != target:
        edge = {"source": source, "target": target, "label": label}
        if edge not in edges:
            edges.append(edge)


def _stock_graph(company: dict[str, Any], industries: list[dict[str, Any]], products: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    center = _graph_add_node(nodes, "company", company["name"], "center")

    mids = products[:8] or industries[:8]
    for item in mids:
        kind = "product" if item in products else "industry"
        mid = _graph_add_node(nodes, kind, item.get("name"), "center", item.get("weight"))
        _graph_add_edge(edges, mid, center, kind)
        for up in (item.get("upstream") or [])[:8]:
            source = _graph_add_node(nodes, "upstream", up.get("name"), "upstream", up.get("weight"))
            _graph_add_edge(edges, source, mid, "upstream")
        for down in (item.get("downstream") or [])[:8]:
            target = _graph_add_node(nodes, "downstream", down.get("name"), "downstream", down.get("weight"))
            _graph_add_edge(edges, mid, target, "downstream")

    return {"nodes": list(nodes.values()), "edges": edges}


def _industry_graph(industry: dict[str, Any], links: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    center = _graph_add_node(nodes, "industry", industry["name"], "center")
    for up in links.get("upstream", [])[:12]:
        source = _graph_add_node(nodes, "upstream", up.get("name"), "upstream", up.get("weight"))
        _graph_add_edge(edges, source, center, "upstream")
    for down in links.get("downstream", [])[:12]:
        target = _graph_add_node(nodes, "downstream", down.get("name"), "downstream", down.get("weight"))
        _graph_add_edge(edges, center, target, "downstream")
    return {"nodes": list(nodes.values()), "edges": edges}
