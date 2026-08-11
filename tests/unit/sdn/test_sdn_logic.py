"""Unit tests for the SDN inspection logic mirrored from the YAML inline code.

The YAML ``python`` nodes carry inline code; these equivalent functions pin
the same semantics at unit granularity (regex parsing with interface-name
aliases, queue seeding + vendor command mapping, fault detection, report
header assembly). The end-to-end wiring of the real inline code is covered
by tests/integration/sdn/test_sdn_workflow.py. Zero network / zero LLM.
"""

import re

import pytest

pytestmark = pytest.mark.unit

# --- mirrors of parse_result inline code (faithful port of the source agent) ---

_IFACE_PATTERN = re.compile(
    r"^(\S+)\s+(\*?down|up)\s+(down|up(?:\(s\))?)\s+(\S+)\s+(\S+)\s*(.*)$",
    re.MULTILINE,
)

_VENDOR_COMMAND_MAP = {"H3C": "dis ip in br", "Huawei": "dis ip in br"}
_DEFAULT_COMMAND = "dis ip in br"


def _extract_interface_number(name: str) -> str:
    m = re.search(r"(\d+/\d+/\d+(?:\.\d+)?)$", name)
    return m.group(1) if m else name


def _find_interface_status(raw_output: str, interface_name: str) -> str | None:
    target = _extract_interface_number(interface_name)
    for match in _IFACE_PATTERN.finditer(raw_output):
        iface = match.group(1)
        if iface == interface_name or _extract_interface_number(iface) == target:
            physical, protocol = match.group(2), match.group(3)
            ip_addr, description = match.group(4), match.group(6).strip()
            status = f"Interface: {iface}, Physical: {physical}, Protocol: {protocol}, IP: {ip_addr}"
            if description and description != "--":
                status += f", Description: {description}"
            return status
    return None


# --- mirrors of prepare_check inline code ---


def _prepare_check(state: dict) -> dict:
    alerts = state.get("remaining_alerts")
    if not alerts:
        response = (state.get("get_alerts_result") or {}).get("response") or {}
        alerts = list(response.get("data") or [])
    current = {"current_device": "", "current_interface": "", "current_down_time": ""}
    rest = alerts
    if alerts:
        first, rest = alerts[0], alerts[1:]
        current = {
            "current_device": first.get("source", ""),
            "current_interface": first.get("component", ""),
            "current_down_time": first.get("time", ""),
        }
    return {"remaining_alerts": rest, **current}


# --- mirrors of report inline code ---


def _detect_fault(check_result: list[str]) -> bool:
    for summary in check_result:
        m = re.search(r"Physical:\s*(\S+),\s*Protocol:\s*(\S+)", summary)
        if m and ("down" in m.group(1).lower() or "down" in m.group(2).lower()):
            return True
    return False


class TestInterfaceStatusParsing:
    """Regex parsing of `dis ip in br` style command output."""

    _RAW = (
        "GE0/4/9  up  up  10.0.0.1  --  to_hw104\n"
        "XGE3/2/20.1  *down  down  --  --  \n"
        "Eth-Trunk1  up  up(s)  172.16.0.1  --  core-link\n"
    )

    def test_exact_name_hit(self) -> None:
        """Full-name match returns the formatted status line."""
        status = _find_interface_status(self._RAW, "GE0/4/9")
        assert status is not None and "Physical: up" in status and "Description: to_hw104" in status

    def test_alias_hit_gigabit_ethernet(self) -> None:
        """GigabitEthernet0/4/9 resolves via the numeric suffix to GE0/4/9."""
        status = _find_interface_status(self._RAW, "GigabitEthernet0/4/9")
        assert status is not None and status.startswith("Interface: GE0/4/9")

    def test_alias_hit_xge(self) -> None:
        """XGE alias matches the *down subinterface line."""
        status = _find_interface_status(self._RAW, "Ten-GigabitEthernet3/2/20.1")
        assert status is not None and "Physical: *down" in status and "Protocol: down" in status

    def test_eth_trunk_hit_keeps_description(self) -> None:
        """Eth-Trunk keeps its full name and non-placeholder description."""
        raw = "Eth-Trunk1  up  up(s)  172.16.0.1  --  core-link"
        status = _find_interface_status(raw, "Eth-Trunk1")
        assert status is not None and "Protocol: up(s)" in status and "Description: core-link" in status

    def test_miss_returns_none(self) -> None:
        """Unknown interfaces return None (skipped in the summary)."""
        assert _find_interface_status(self._RAW, "GE9/9/9") is None

    def test_placeholder_description_dropped(self) -> None:
        """'--' descriptions are not appended."""
        raw = "XGE3/2/20.1  *down  down  --  --  "
        status = _find_interface_status(raw, "XGE3/2/20.1")
        assert status is not None and "Description" not in status


class TestPrepareCheck:
    """Queue seeding (first round) and head-popping (later rounds)."""

    def test_first_round_seeds_and_pops(self) -> None:
        """An empty queue seeds from get_alerts_result and pops the head."""
        alerts = [
            {"source": "NJ-SCT-R02", "component": "GigabitEthernet0/4/9", "time": "2026-07-24 04:08:35"},
            {"source": "NJ-SCT-R03", "component": "XGE1/0/1", "time": "2026-07-24 04:10:00"},
        ]
        out = _prepare_check({"remaining_alerts": [], "get_alerts_result": {"response": {"data": alerts}}})
        assert out["current_device"] == "NJ-SCT-R02"
        assert out["current_interface"] == "GigabitEthernet0/4/9"
        assert len(out["remaining_alerts"]) == 1

    def test_later_round_pops_only(self) -> None:
        """A non-None queue is popped without re-seeding."""
        queue = [{"source": "NJ-SCT-R03", "component": "XGE1/0/1", "time": "t"}]
        out = _prepare_check({"remaining_alerts": queue})
        assert out["current_device"] == "NJ-SCT-R03"
        assert out["remaining_alerts"] == []

    def test_empty_seed_short_circuits(self) -> None:
        """Zero alerts leave current_device empty (drives the == '' short-circuit edge)."""
        out = _prepare_check({"remaining_alerts": [], "get_alerts_result": {"response": {"data": []}}})
        assert out["current_device"] == ""
        assert out["remaining_alerts"] == []


class TestVendorCommandMapping:
    """VENDOR_COMMAND_MAP with default fallback (mirror of pick_vendor)."""

    def test_known_vendor_maps(self) -> None:
        """Known vendors resolve to their mapped command."""
        assert _VENDOR_COMMAND_MAP.get("H3C", _DEFAULT_COMMAND) == "dis ip in br"

    def test_unknown_vendor_falls_back(self) -> None:
        """Unknown vendors fall back to the default command."""
        assert _VENDOR_COMMAND_MAP.get("Cisco", _DEFAULT_COMMAND) == _DEFAULT_COMMAND


class TestFaultDetectionAndReport:
    """_detect_fault over summaries + report header semantics."""

    def test_fault_when_physical_down(self) -> None:
        """A Physical: down summary marks the report faulty."""
        assert _detect_fault(["x Physical: *down, Protocol: down, IP: --"]) is True

    def test_fault_when_protocol_down(self) -> None:
        """A Protocol-only down also counts as a fault."""
        assert _detect_fault(["x Physical: up, Protocol: down, IP: --"]) is True

    def test_no_fault_when_all_up(self) -> None:
        """All-up summaries yield a normal report."""
        assert _detect_fault(["x Physical: up, Protocol: up, IP: 10.0.0.1"]) is False

    def test_no_fault_on_empty(self) -> None:
        """Empty check results mean no fault detected."""
        assert _detect_fault([]) is False

    def test_header_contains_fixed_parts(self) -> None:
        """Header carries the fixed title, unit and status line (mirror of report code)."""
        overall = (
            "⚠️ 异常检测 (Fault Detected)" if _detect_fault(["Physical: down, Protocol: down,"]) else "✅ 正常 (Normal)"
        )
        header = f"# 网络智能巡检报告\n| 巡检单位 | 智能运维中心 (AIOps Center) |\n| 整体状态 | {overall} |"
        assert "# 网络智能巡检报告" in header
        assert "⚠️ 异常检测 (Fault Detected)" in header
