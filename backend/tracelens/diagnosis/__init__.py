"""Workbench diagnosis modes and engine provenance."""

from typing import Literal

DiagnosisMode = Literal["fast", "standard", "deep", "trajdebug", "agentdebugx", "chief", "dual", "dual_chief"]
DEFAULT_MODE: DiagnosisMode = "standard"
LEGACY_DEFAULT_MODE: DiagnosisMode = "trajdebug"
MODE_ENGINES: dict[DiagnosisMode, tuple[str, ...]] = {
    "fast": ("agentdebugx",),
    "standard": ("agentdebugx", "chief"),
    "deep": ("trajdebug", "agentdebugx", "chief"),
    "trajdebug": ("trajdebug",),
    "agentdebugx": ("agentdebugx",),
    "chief": ("chief",),
    "dual": ("trajdebug", "agentdebugx"),
    "dual_chief": ("trajdebug", "agentdebugx", "chief"),
}
TRAJDEBUG_MODES = {mode for mode, engines in MODE_ENGINES.items() if "trajdebug" in engines}

AGENTDEBUGX_VERSION = "0.5.2"
AGENTDEBUGX_COMMIT = "e180ac2e46e4c1e468336526f67ad122ecaf34ff"
AGENTDEBUGX_URL = "https://github.com/AgentDebugX/AgentDebugX"

CHIEF_COMMIT = "2b47170c41913c8bdea34397f7d52c65360dcef9"
CHIEF_URL = "https://github.com/Mr-Capybara/CHIEF"
CHIEF_VERSION = f"chief-{CHIEF_COMMIT[:7]}"
