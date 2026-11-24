"""Short instructions for the owner's AI; every answer stays grounded in Firra facts."""

from typing import Any

from mcp.server.mcpserver import MCPServer


def register(server: MCPServer[Any], *, processing_tools: bool = False) -> None:
    from firra.local.mcp.server import RULES

    @server.prompt()
    def morning_review() -> str:
        return "Call get_clients_needing_attention, get_recent_changes and get_decisions. " + RULES

    @server.prompt()
    def prepare_for_meeting(client: str) -> str:
        return (
            "Use list_clients to find "
            + client
            + "; call prepare_for_client and get_client_context. "
            + RULES
        )

    @server.prompt()
    def weekly_review() -> str:
        return "Call get_recent_changes for 7 days, get_decisions and get_opportunities. " + RULES

    if processing_tools:

        @server.prompt()
        def process_new_mail() -> str:
            return (
                "Process exactly ONE packet: call get_pending_mail once, submit packet-bound "
                "facts with submit_extractions once, report records_left to the owner, then stop "
                "and ask the owner before fetching another packet. " + RULES
            )

    @server.prompt()
    def draft_reply(client: str, topic: str) -> str:
        return (
            "Find "
            + client
            + " with list_clients; call get_client_context and search_evidence for "
            + topic
            + ". Draft for the owner, never send. "
            + RULES
        )
