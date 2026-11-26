"""Workbench diagnosis modes and engine provenance."""

from typing import Literal

DiagnosisMode = Literal["trajdebug", "agentdebugx", "chief", "dual", "dual_chief"]
DEFAULT_MODE: DiagnosisMode = "trajdebug"
TRAJDEBUG_MODES = {"trajdebug", "dual", "dual_chief"}

AGENTDEBUGX_VERSION = "0.5.2"
AGENTDEBUGX_COMMIT = "e180ac2e46e4c1e468336526f67ad122ecaf34ff"
AGENTDEBUGX_URL = "https://github.com/AgentDebugX/AgentDebugX"

CHIEF_COMMIT = "2b47170c41913c8bdea34397f7d52c65360dcef9"
CHIEF_URL = "https://github.com/Mr-Capybara/CHIEF"
CHIEF_VERSION = f"chief-{CHIEF_COMMIT[:7]}"
