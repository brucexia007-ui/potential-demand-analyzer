import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict):
    task_id: str
    company_name: str
    demand_direction: str
    findings: Annotated[dict[str, Any], operator.ior]
    evidences: Annotated[list[dict[str, Any]], operator.add]
    logs: Annotated[list[dict[str, str]], operator.add]
