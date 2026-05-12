from nodalarc.proto import node_agent_pb2
from node_agent.command_contract import worst_error_code


def test_worst_error_code_returns_highest_severity_code() -> None:
    assert (
        worst_error_code(
            [
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                node_agent_pb2.NODE_AGENT_OK,
                node_agent_pb2.NODE_AGENT_DIRTY_KERNEL,
                node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
            ]
        )
        == node_agent_pb2.NODE_AGENT_DIRTY_KERNEL
    )


def test_worst_error_code_returns_ok_when_all_entries_ok() -> None:
    assert (
        worst_error_code([node_agent_pb2.NODE_AGENT_OK, node_agent_pb2.NODE_AGENT_OK])
        == node_agent_pb2.NODE_AGENT_OK
    )
