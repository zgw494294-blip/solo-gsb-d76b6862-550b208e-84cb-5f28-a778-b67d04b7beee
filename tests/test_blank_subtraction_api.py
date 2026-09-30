"""空白扣除 HTTP 接口测试（TestClient 走真实 ASGI 栈），含与定量接口的衔接。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 共用参考峰表：A/S1/B/S2（S1、S2 为内标）
REFS = [
    {"id": "A", "time_ms": 20},
    {"id": "S1", "time_ms": 60},
    {"id": "B", "time_ms": 70},
    {"id": "S2", "time_ms": 80},
]

# 样本运行：与 /v1/integrate 文档示例相同，
# 峰顶时间 20/60/80，整数面积 115/70/40，area_scale = 1
def sample_run():
    return {
        "samples": [
            {"time_ms": t, "intensity": y}
            for t, y in zip(
                range(0, 101, 10), [2, 5, 9, 5, 3, 4, 8, 2, 5, 3, 2]
            )
        ],
        "start_index": 0,
        "end_index": 10,
        "apex_indices": [2, 6, 8],
        "is_meas_idx_1": 1,
        "is_meas_idx_2": 2,
    }


def blank_run(apex1_intensity=5):
    """空白运行：采样时间 0..8（与样本不同的采样网格），
    峰顶时间 1/4/7，段面积 9/2、5、4，area_scale = 2，
    整数面积 9/10/8；归属 S1/B/S2（无 A）。"""
    return {
        "samples": [
            {"time_ms": t, "intensity": y}
            for t, y in zip(
                range(0, 9), [0, apex1_intensity, 1, 1, 6, 1, 1, 4, 0]
            )
        ],
        "start_index": 0,
        "end_index": 8,
        "apex_indices": [1, 4, 7],
        "is_meas_idx_1": 0,
        "is_meas_idx_2": 2,
    }


def payload(**over):
    body = {
        "reference_peaks": REFS,
        "is_ref_id_1": "S1",
        "is_ref_id_2": "S2",
        "tolerance_ms": 0,
        "sample": sample_run(),
        "blank": blank_run(),
    }
    body.update(over)
    return body


def test_blank_subtraction_happy_path():
    r = client.post("/v1/blank-subtraction", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    # A：空白无该参考峰，不扣；S1：70 - 9/2 = 131/2；S2：40 - 4 = 36；
    # 空白独有的 B 峰（面积 5）不输出
    assert data["corrected_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 115, "d": 1}},
        {"ref_id": "S1", "corrected_area": {"n": 131, "d": 2}},
        {"ref_id": "S2", "corrected_area": {"n": 36, "d": 1}},
    ]
    assert data["area_scale"] == 2
    assert data["peaks"] == [
        {"time_ms": 20, "area": 230},
        {"time_ms": 60, "area": 131},
        {"time_ms": 80, "area": 72},
    ]


def test_negative_difference_clamped_to_zero():
    # 空白 S1 峰面积 299/2 > 70，校正后截为零且保留在输出中
    r = client.post(
        "/v1/blank-subtraction", json=payload(blank=blank_run(150))
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["corrected_peaks"] == [
        {"ref_id": "A", "corrected_area": {"n": 115, "d": 1}},
        {"ref_id": "S1", "corrected_area": {"n": 0, "d": 1}},
        {"ref_id": "S2", "corrected_area": {"n": 36, "d": 1}},
    ]
    assert data["area_scale"] == 1
    assert data["peaks"] == [
        {"time_ms": 20, "area": 115},
        {"time_ms": 60, "area": 0},
        {"time_ms": 80, "area": 36},
    ]


def test_unassigned_sample_peak_keeps_area():
    # 参考峰表去掉 A：样本 20ms 峰未归属，保持原面积，ref_id 为 null
    r = client.post(
        "/v1/blank-subtraction", json=payload(reference_peaks=REFS[1:])
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["corrected_peaks"][0] == {
        "ref_id": None,
        "corrected_area": {"n": 115, "d": 1},
    }
    assert data["peaks"][0] == {"time_ms": 20, "area": 230}


def test_422_on_sample_integration_failure():
    body = payload(sample={**sample_run(), "start_index": 11})
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("sample: ")


def test_422_on_blank_integration_failure():
    body = payload(blank={**blank_run(), "apex_indices": [4, 1, 7]})
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("blank: ")


def test_422_on_blank_assignment_failure():
    body = payload(blank={**blank_run(), "is_meas_idx_2": 5})
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith("blank: ")
    assert "is_meas_idx_2" in detail


def test_422_on_shared_reference_errors():
    dup = [dict(p) for p in REFS] + [{"id": "A", "time_ms": 90}]
    r = client.post("/v1/blank-subtraction", json=payload(reference_peaks=dup))
    assert r.status_code == 422
    assert "unique" in r.json()["detail"]
    r = client.post("/v1/blank-subtraction", json=payload(is_ref_id_2="S1"))
    assert r.status_code == 422


def test_422_on_wrong_types():
    body = payload()
    body["sample"]["samples"][0]["intensity"] = True  # 布尔不能冒充整数
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    body = payload()
    body["blank"]["apex_indices"][0] = 1.5  # 浮点不能被截断为整数
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422
    body = payload()
    del body["blank"]  # 缺字段
    r = client.post("/v1/blank-subtraction", json=body)
    assert r.status_code == 422


def test_blank_subtraction_output_chains_into_standard_addition():
    """扣除后的整数峰数组直接作为定量接口的 measured_peaks。"""
    r = client.post("/v1/blank-subtraction", json=payload())
    assert r.status_code == 200, r.text
    peaks = r.json()["peaks"]  # (20,230) (60,131) (80,72)

    injections = []
    for conc in (0, 5, 10):
        measured = [dict(p) for p in peaks]
        measured[0]["area"] += 100 * conc  # 目标峰 A 面积随加标浓度线性增长
        injections.append(
            {
                "added_concentration": conc,
                "measured_peaks": measured,
                "is_meas_idx_1": 1,
                "is_meas_idx_2": 2,
            }
        )
    r = client.post(
        "/v1/standard-addition",
        json={
            "reference_peaks": REFS,
            "target_ref_id": "A",
            "is_ref_id_1": "S1",
            "is_ref_id_2": "S2",
            "tolerance_ms": 0,
            "injections": injections,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # 面积比 = (230 + 100c) / 131 = (100/131)c + 230/131
    assert data["slope"] == {"n": 100, "d": 131}
    assert data["intercept"] == {"n": 230, "d": 131}
    assert data["sample_concentration"] == {"n": 23, "d": 10}
