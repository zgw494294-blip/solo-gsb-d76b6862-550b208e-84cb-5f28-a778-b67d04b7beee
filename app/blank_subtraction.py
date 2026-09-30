"""样本/空白两次色谱运行的空白扣除（纯函数，全部使用精确有理数运算）。

对样本与空白两组原始强度采样分别复用 ``integration.integrate_peaks`` 积分，
再按各自峰顶时间复用 ``assignment.assign_peaks`` 归属到共用参考峰表
（两组可有不同的采样时间与面积放大倍数）。仅当样本峰与空白峰归属同一
参考 id 时，用各自整数面积除以各自 area_scale 后精确相减，负值截为零；
空白缺少该参考峰则不扣，样本未归属峰保持原面积，空白独有峰不输出。
校正面积取全部约分分母的最小公倍数统一放大为整数峰数组，
可直接供标准加入法定量接口使用。
"""

from __future__ import annotations

from fractions import Fraction
from math import lcm
from typing import Any

from .assignment import AssignmentError, assign_peaks
from .integration import integrate_peaks


def _frac_out(value: Fraction) -> dict[str, int]:
    """把约分有理数序列化为 {n, d}，分母恒为正。"""
    return {"n": value.numerator, "d": value.denominator}


def blank_subtraction(
    ref_peaks: list[dict[str, Any]],
    is_ref_id_1: Any,
    is_ref_id_2: Any,
    tolerance_ms: int,
    sample: dict[str, Any],
    blank: dict[str, Any],
) -> dict[str, Any]:
    """空白扣除主流程，返回可直接序列化的结果字典。"""

    # —— 共用参考峰表层面的校验（各组归属也会检查，这里先给出
    #     与组别无关的明确报错）——
    ref_ids = [p["id"] for p in ref_peaks]
    ref_times = [p["time_ms"] for p in ref_peaks]
    if len(set(ref_ids)) != len(ref_ids):
        raise AssignmentError("reference peak ids must be unique")
    if any(ref_times[i] >= ref_times[i + 1] for i in range(len(ref_times) - 1)):
        raise AssignmentError("reference times must be strictly increasing")
    if is_ref_id_1 not in ref_ids:
        raise AssignmentError("is_ref_id_1 not found in reference peaks")
    if is_ref_id_2 not in ref_ids:
        raise AssignmentError("is_ref_id_2 not found in reference peaks")
    if is_ref_id_1 == is_ref_id_2:
        raise AssignmentError("the two reference internal standards must differ")

    # —— 两组各自积分 + 归属；任一组失败都在消息中指出 sample/blank ——
    runs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for label, run in (("sample", sample), ("blank", blank)):
        try:
            integ = integrate_peaks(
                samples=run["samples"],
                start_index=run["start_index"],
                end_index=run["end_index"],
                apex_indices=run["apex_indices"],
            )
            assigned = assign_peaks(
                ref_peaks=ref_peaks,
                meas_times=[p["time_ms"] for p in integ["peaks"]],
                is_ref_id_1=is_ref_id_1,
                is_meas_idx_1=run["is_meas_idx_1"],
                is_ref_id_2=is_ref_id_2,
                is_meas_idx_2=run["is_meas_idx_2"],
                tolerance_ms=tolerance_ms,
            )
        except AssignmentError as exc:
            raise AssignmentError(f"{label}: {exc}") from exc
        runs[label] = (integ, assigned)

    sample_integ, sample_assign = runs["sample"]
    blank_integ, blank_assign = runs["blank"]

    # 空白：参考 id -> 真实面积（整数面积 / 本组面积放大倍数）；
    # 归属为一对一，同一参考 id 至多出现一次
    blank_area_by_ref: dict[Any, Fraction] = {}
    for ri, mj in enumerate(blank_assign["assignments"]):
        if mj is not None:
            blank_area_by_ref[ref_ids[ri]] = Fraction(
                blank_integ["peaks"][mj]["area"], blank_integ["area_scale"]
            )

    # 样本：实测下标 -> 参考 id
    sample_ref_by_meas: dict[int, Any] = {}
    for ri, mj in enumerate(sample_assign["assignments"]):
        if mj is not None:
            sample_ref_by_meas[mj] = ref_ids[ri]

    # —— 按样本峰顶顺序逐峰扣除；空白独有峰不进入输出 ——
    corrected: list[dict[str, Any]] = []
    for k, peak in enumerate(sample_integ["peaks"]):
        area = Fraction(peak["area"], sample_integ["area_scale"])
        ref_id = sample_ref_by_meas.get(k)
        if ref_id is not None and ref_id in blank_area_by_ref:
            area -= blank_area_by_ref[ref_id]
            if area < 0:
                area = Fraction(0)
        corrected.append(
            {"ref_id": ref_id, "time_ms": peak["time_ms"], "area": area}
        )

    # —— 用全部校正面积分母的最小公倍数统一放大为整数面积 ——
    scale = lcm(*(c["area"].denominator for c in corrected))

    return {
        "corrected_peaks": [
            {"ref_id": c["ref_id"], "corrected_area": _frac_out(c["area"])}
            for c in corrected
        ],
        "area_scale": scale,
        # 峰顶下标严格递增且采样时间严格递增，峰数组天然按峰顶时间排序；
        # 零面积的峰保留在数组中。
        "peaks": [
            {"time_ms": c["time_ms"], "area": int(c["area"] * scale)}
            for c in corrected
        ],
    }
