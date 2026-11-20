def test_create_task(client):
    resp = client.post("/tasks", json={"title": "Write report"})
    assert resp.status_code == 201
    assert resp.get_json()["title"] == "Write report"


def test_list_tasks_pagination(client):
    for i in range(3):
        client.post("/tasks", json={"title": f"task {i}"})
    resp = client.get("/tasks?page=1&per_page=2")
    items = resp.get_json()["items"]
    assert [t["title"] for t in items] == ["task 0", "task 1"]


def test_update_task_status(client):
    task_id = client.post("/tasks", json={"title": "t"}).get_json()["id"]
    resp = client.patch(f"/tasks/{task_id}", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "done"


def test_update_task_rejects_bad_status(client):
    task_id = client.post("/tasks", json={"title": "t"}).get_json()["id"]
    resp = client.patch(f"/tasks/{task_id}", json={"status": "bogus"})
    assert resp.status_code == 400
