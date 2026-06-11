from typing import Dict, List, Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models import (
    ApprovalNode,
    ApprovalTemplate,
    BookingApplication,
    BookingRule,
    BookingWorkflowSnapshot,
    TimeoutStrategy,
    UserRole,
)

if TYPE_CHECKING:
    pass


def serialize_template_nodes(template: ApprovalTemplate) -> List[Dict]:
    sorted_nodes = sorted(template.nodes, key=lambda n: n.order_index)
    return [
        {
            "id": node.id,
            "node_name": node.node_name,
            "order_index": node.order_index,
            "auditor_role": node.auditor_role.value,
            "timeout_minutes": node.timeout_minutes,
            "timeout_strategy": node.timeout_strategy.value,
            "transfer_to_user_id": node.transfer_to_user_id,
        }
        for node in sorted_nodes
    ]


def deserialize_snapshot_nodes(snapshot: BookingWorkflowSnapshot) -> List[ApprovalNode]:
    nodes_data = snapshot.nodes_json
    result = []
    for data in nodes_data:
        node = ApprovalNode(
            id=data.get("id", 0),
            template_id=snapshot.template_id,
            node_name=data["node_name"],
            order_index=data["order_index"],
            auditor_role=UserRole(data["auditor_role"]),
            timeout_minutes=data["timeout_minutes"],
            timeout_strategy=TimeoutStrategy(data["timeout_strategy"]),
            transfer_to_user_id=data.get("transfer_to_user_id"),
        )
        result.append(node)
    return sorted(result, key=lambda n: n.order_index)


def get_booking_nodes(db: Session, booking: BookingApplication) -> List[ApprovalNode]:
    if booking.workflow_snapshot:
        return deserialize_snapshot_nodes(booking.workflow_snapshot)

    rule = db.query(BookingRule).filter(BookingRule.id == booking.rule_id).first()
    if not rule:
        return []
    template = db.query(ApprovalTemplate).filter(
        ApprovalTemplate.id == rule.approval_template_id
    ).first()
    if not template:
        return []
    return sorted(template.nodes, key=lambda n: n.order_index)


def get_booking_current_node(
    db: Session, booking: BookingApplication
) -> Optional[ApprovalNode]:
    nodes = get_booking_nodes(db, booking)
    total_nodes = len(nodes)
    if total_nodes == 0:
        return None

    if booking.current_node_index >= total_nodes:
        return None

    if 0 <= booking.current_node_index < total_nodes:
        return nodes[booking.current_node_index]
    return None


def has_booking_finished_all_nodes(
    db: Session, booking: BookingApplication
) -> bool:
    nodes = get_booking_nodes(db, booking)
    total_nodes = len(nodes)
    if total_nodes == 0:
        return True
    return booking.current_node_index >= total_nodes
