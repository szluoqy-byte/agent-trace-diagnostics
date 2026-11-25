"""Workbench diagnosis modes and engine provenance."""

from typing import Literal

DiagnosisMode = Literal["trajdebug", "agentdebugx", "dual"]
DEFAULT_MODE: DiagnosisMode = "trajdebug"

AGENTDEBUGX_VERSION = "0.5.2"
AGENTDEBUGX_COMMIT = "e180ac2e46e4c1e468336526f67ad122ecaf34ff"
AGENTDEBUGX_URL = "https://github.com/AgentDebugX/AgentDebugX"
