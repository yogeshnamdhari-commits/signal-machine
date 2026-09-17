import sqlite3

from scanner.deployment_gate import DeploymentGate


def _seed_forward_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE forward_signals (id INTEGER PRIMARY KEY)")
    db.execute(
        """
        CREATE TABLE forward_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pnl REAL NOT NULL,
            fees REAL NOT NULL,
            funding REAL NOT NULL,
            slippage REAL NOT NULL,
            net_pnl REAL NOT NULL,
            outcome TEXT NOT NULL
        )
        """
    )
    db.executemany("INSERT INTO forward_signals (id) VALUES (?)", [(i,) for i in range(1, 501)])
    db.executemany(
        """
        INSERT INTO forward_trades (pnl, fees, funding, slippage, net_pnl, outcome)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (100.0, 3.0, 2.0, 4.0, 91.0, "win")
            for _ in range(100)
        ],
    )
    db.commit()
    db.close()


def test_deployment_gate_rejects_economic_decomposition_mismatch(tmp_path):
    db_path = tmp_path / "forward_test.db"
    _seed_forward_db(db_path)

    # Corrupt one stored trade: 100 - 3 - 2 - 4 = 91, not 90.
    db = sqlite3.connect(db_path)
    db.execute("UPDATE forward_trades SET net_pnl=90 WHERE id=1")
    db.commit()
    db.close()

    result = DeploymentGate(str(db_path)).evaluate()

    assert result["status"] == "DO NOT DEPLOY"
    assert "economic decomposition" in result["reason"].lower()
