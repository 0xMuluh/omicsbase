"""CAS browser saves and durable edit-journal/undo API coverage."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.project import Project
from app.services.edit_engine import EditOperation, apply_transaction, sha256_bytes


@pytest.fixture
def workspace(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_local()
    project = Project(
        id=str(uuid.uuid4()),
        name="Edit journal test",
        question="test",
        status="completed",
        tenant_id="default_tenant",
        owner_id="default_user",
        project_dir=str(tmp_path),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    (tmp_path / "code").mkdir()
    file_path = tmp_path / "code" / "analysis.R"
    file_path.write_text("value <- 1\n")

    def override_get_db():
        scoped = session_local()
        try:
            yield scoped
        finally:
            scoped.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield project, tmp_path, file_path
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_edit_journal_lists_and_reverts_transaction(workspace):
    project, _root, file_path = workspace
    original = file_path.read_bytes()
    committed = apply_transaction(
        project.project_dir,
        [
            EditOperation(
                path="code/analysis.R",
                kind="rewrite",
                content="value <- 2\n",
                base_sha256=sha256_bytes(original),
            )
        ],
        origin="test",
        summary="Update tested value",
    )
    client = TestClient(app)

    listed = client.get(f"/api/projects/{project.id}/edits")
    assert listed.status_code == 200
    transaction = next(item for item in listed.json()["transactions"] if item["transaction_id"] == committed.transaction_id)
    assert transaction["status"] == "committed"
    assert transaction["files"][0]["path"] == "code/analysis.R"

    detail = client.get(f"/api/projects/{project.id}/edits/{committed.transaction_id}")
    assert detail.status_code == 200
    assert detail.json()["summary"] == "Update tested value"

    reverted = client.post(f"/api/projects/{project.id}/edits/{committed.transaction_id}/revert")
    assert reverted.status_code == 200
    assert file_path.read_bytes() == original

    listed_again = client.get(f"/api/projects/{project.id}/edits")
    assert next(item for item in listed_again.json()["transactions"] if item["transaction_id"] == committed.transaction_id)["status"] == "reverted"


def test_browser_save_requires_and_checks_if_match(workspace):
    project, _root, file_path = workspace
    client = TestClient(app)
    path = f"/api/projects/{project.id}/files/content/code/analysis.R"

    fetched = client.get(path)
    assert fetched.status_code == 200
    digest = fetched.json()["sha256"]
    assert fetched.headers["etag"] == f'"{digest}"'

    missing = client.patch(path, json={"content": "value <- 2\n"})
    assert missing.status_code == 428

    stale = client.patch(
        path,
        headers={"If-Match": '"' + "0" * 64 + '"'},
        json={"content": "value <- 2\n"},
    )
    assert stale.status_code == 409
    assert file_path.read_text() == "value <- 1\n"

    saved = client.patch(
        path,
        headers={"If-Match": f'"{digest}"'},
        json={"content": "value <- 2\n"},
    )
    assert saved.status_code == 200
    assert file_path.read_text() == "value <- 2\n"
    assert saved.json()["transaction_id"]



def test_edit_review_prepare_and_approve(workspace):
    project, _root, file_path = workspace
    client = TestClient(app)
    prepared = client.post(
        f"/api/projects/{project.id}/edit-reviews",
        json={
            "summary": "Review API edit",
            "operations": [
                {
                    "path": "code/analysis.R",
                    "kind": "replace",
                    "search": "value <- 1",
                    "replace": "value <- 3",
                }
            ],
        },
    )
    assert prepared.status_code == 200, prepared.text
    review = prepared.json()
    assert review["status"] == "pending"
    assert file_path.read_text() == "value <- 1\n"
    approved = client.post(f"/api/projects/{project.id}/edit-reviews/{review['review_id']}/approve")
    assert approved.status_code == 200, approved.text
    assert file_path.read_text() == "value <- 3\n"


def test_execution_provenance_endpoint_is_empty_before_a_run(workspace):
    project, _root, _file_path = workspace
    client = TestClient(app)
    response = client.get(f"/api/projects/{project.id}/execution-runs")
    assert response.status_code == 200
    assert response.json() == {"runs": []}


def _tree_nodes(nodes):
    result = []
    for node in nodes:
        result.append(node)
        result.extend(_tree_nodes(node.get("children") or []))
    return result


def test_file_tree_exposes_the_project_root_and_editability(workspace):
    project, root, _file_path = workspace
    (root / "data").mkdir()
    (root / "data" / "input.csv").write_text("sample,value\nA,1\n")
    (root / "preprocessing").mkdir()
    (root / "preprocessing" / "prepare.R").write_text("message(\"prepare\")\n")
    (root / "output").mkdir()
    (root / "output" / "index.html").write_text("<html></html>\n")
    (root / "report_pack.yaml").write_text("schema_version: \"1.0\"\n")

    response = TestClient(app).get(f"/api/projects/{project.id}/files/tree")

    assert response.status_code == 200
    nodes = _tree_nodes(response.json())
    by_path = {node["path"]: node for node in nodes}
    assert {"code", "data", "preprocessing", "output", "report_pack.yaml"}.issubset(by_path)
    assert by_path["code/analysis.R"]["editable"] is True
    assert by_path["preprocessing/prepare.R"]["editable"] is True
    assert by_path["data/input.csv"]["editable"] is False
    assert by_path["output/index.html"]["editable"] is False
    assert by_path["report_pack.yaml"]["editable"] is False


def test_browser_cannot_edit_project_inputs_or_contracts(workspace):
    project, root, _file_path = workspace
    data_file = root / "data" / "input.csv"
    data_file.parent.mkdir()
    data_file.write_text("sample,value\nA,1\n")
    contract = root / "execution_contract.json"
    contract.write_text("{}\n")
    client = TestClient(app)

    for relative_path in ("data/input.csv", "execution_contract.json"):
        response = client.patch(
            f"/api/projects/{project.id}/files/content/{relative_path}",
            headers={"If-Match": "*"},
            json={"content": "changed\n"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "protected_project_file"

    assert data_file.read_text() == "sample,value\nA,1\n"
    assert contract.read_text() == "{}\n"
