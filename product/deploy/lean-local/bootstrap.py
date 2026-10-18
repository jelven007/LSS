"""Provision the isolated test tenant using the production administration path."""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from app.cell.admin import provision_tenant


def main() -> None:
    with (
        psycopg.connect(os.environ["TSP_CONTROL_ADMIN_DSN"], autocommit=True,
                         row_factory=dict_row) as control,
        psycopg.connect(os.environ["TSP_DATA_ADMIN_DSN"], autocommit=True,
                         row_factory=dict_row) as data,
    ):
        if control.info.dbname != "tsp_control" or data.info.dbname != "tsp_data_local":
            raise SystemExit("Local bootstrap requires the isolated test databases.")
        admin = control.execute(
            "SELECT id,default_tenant_id FROM control.users WHERE username='admin'",
        ).fetchone()
        if not admin:
            raise SystemExit("Bootstrap the system administrator first.")
        provision_tenant(control, data, "lean-test", "本地集成测试", "local", admin["id"])
        data.execute("SELECT business.initialize_tenant(%s,%s)", ("lean-test", 1))
        control.execute(
            """UPDATE control.users SET default_tenant_id=%s
               WHERE id=%s AND default_tenant_id IS NULL""",
            ("lean-test", admin["id"]),
        )
    print('{"tenant":"lean-test","initialized":true}')


if __name__ == "__main__":
    main()
