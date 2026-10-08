"""Inventory CSV and confidence distribution use the same scan assets."""
import csv
import io

from backend.models.asset import CryptoAssetDB
from backend.models.scan_job import ScanJobDB


def test_asset_csv_all_and_selected(client, session_factory):
    with session_factory() as db:
        scan = ScanJobDB(repo_path="/test/repo", status="completed")
        db.add(scan)
        db.flush()
        db.add_all([
            CryptoAssetDB(scan_job_id=scan.id, algorithm=f"RSA-{i}", category="encryption",
                          source=["rule"] if i == 0 else [],
                          location=f"src/file,{i}.py", confidence=0.9 if i < 2 else 0.5,
                          priority_label="HIGH" if i < 2 else "LOW", priority_score=60 - i)
            for i in range(3)
        ])
        db.commit()
        scan_id = scan.id

    listed = client.get(f"/api/assets?scan_job_id={scan_id}")
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["items"]]
    response = client.get(f"/api/assets.csv?scan_job_id={scan_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "ecdat-assets.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [int(row["id"]) for row in rows] == ids
    assert rows[0]["location"] == "src/file,0.py"

    for selected in ([ids[1]], [ids[0], ids[2]]):
        response = client.get("/api/assets.csv", params=[("scan_job_id", scan_id)] +
                              [("ids", asset_id) for asset_id in selected])
        selected_rows = list(csv.DictReader(io.StringIO(response.text)))
        assert {int(row["id"]) for row in selected_rows} == set(selected)

    summary = client.get(f"/api/dashboard/summary?scan_id={scan_id}").json()
    assert summary["total_assets"] == 3
    assert sum(summary["confidence_distribution"].values()) == 3
    assert summary["confidence_distribution"]["41-60"] == 1
    assert summary["confidence_distribution"]["81-100"] == 2

    graph = client.get(f"/api/evidence-graph?scan_id={scan_id}&asset_id={ids[0]}")
    assert graph.status_code == 200
    assert [node["id"] for node in graph.json()["nodes"] if node["type"] == "asset"] == [f"asset:{ids[0]}"]
    assert graph.json()["edges"] == [{"source": f"source:{ids[0]}:rule", "target": f"asset:{ids[0]}", "relation": "supports"}]
    assert graph.json()["truncation"]["total_assets"] == 1
    assert client.get(f"/api/evidence-graph?scan_id={scan_id}&asset_id=999999").status_code == 404
