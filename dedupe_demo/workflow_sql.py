"""Run the exact SQL that a workflow's Postgres node runs, from the exported JSON.

Tests use this so they check the query that ships in workflows/*.json, not a copy.
n8n's Postgres node binds $1, $2, ... client-side; here they map to psycopg params.
"""

from __future__ import annotations

import json
import re

from .config import ROOT


def node_query(workflow_file: str, node_name: str) -> str:
    wf = json.loads((ROOT / "workflows" / workflow_file).read_text(encoding="utf-8"))
    for node in wf["nodes"]:
        if node["name"] == node_name:
            return node["parameters"]["query"]
    raise KeyError(f"{node_name!r} not in {workflow_file}")


def to_psycopg(query: str) -> str:
    """'$1' -> '%(p1)s' (and escape literal % signs)."""
    return re.sub(r"\$(\d+)", r"%(p\1)s", query.replace("%", "%%"))


def run_node_query(conn, workflow_file: str, node_name: str, *params):
    sql = to_psycopg(node_query(workflow_file, node_name))
    return conn.execute(sql, {f"p{i}": v for i, v in enumerate(params, start=1)}).fetchall()
