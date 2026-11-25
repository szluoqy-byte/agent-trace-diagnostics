"""Load the pinned AgentDebugX source tree in its worker process."""

from pathlib import Path
import sys

from ..upstream import verify_source_tree
from . import AGENTDEBUGX_COMMIT, AGENTDEBUGX_URL, AGENTDEBUGX_VERSION


VENDOR = Path(__file__).resolve().parents[1] / "_vendor" / "agentdebugx"


def verify_agentdebugx():
    manifest = verify_source_tree(VENDOR, "AgentDebugX")
    if (manifest["commit"], manifest["version"], manifest["repository"]) != (
        AGENTDEBUGX_COMMIT, AGENTDEBUGX_VERSION, AGENTDEBUGX_URL
    ):
        raise ValueError("AgentDebugX 源码版本与工作台配置不一致")
    return manifest


def load_vendored_agentdebugx():
    verify_agentdebugx()
    sys.path.insert(0, str(VENDOR))
    import agentdebug

    if not Path(agentdebug.__file__).resolve().is_relative_to(VENDOR):
        raise RuntimeError("AgentDebugX 未从仓库内置源码加载")
