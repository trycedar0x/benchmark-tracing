"""Guards for the tau3 agents, independent of any agent framework.

Each guard watches the conversation (customer messages, tool results) and decides whether a
proposed tool call or reply may go ahead. The retail agent runs them as OpenAI Agents SDK tool
guardrails; the banking agent runs them in a LangGraph guard node. A blocked step is never sent
to the benchmark: the model gets the reason and tries again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# After this many blocks in one conversation, guards stop blocking so a stuck model cannot loop
# forever. Every block is still recorded.
MAX_BLOCKS = 8


@dataclass
class Verdict:
    blocked: bool
    guard: str = ""
    reason: str = ""


ALLOW = Verdict(False)


class Guards:
    """Base class: counts blocks and fails open after MAX_BLOCKS."""

    def __init__(self) -> None:
        self.blocks: dict[str, int] = {}

    def on_customer_message(self, text: str) -> None:
        pass

    def on_tool_result(self, name: str, args: dict[str, Any], result: str) -> None:
        pass

    def tool_call(self, name: str, args: dict[str, Any]) -> Verdict:
        return self._count(self._tool_call(name, args))

    def reply(self, text: str) -> Verdict:
        return self._count(self._reply(text))

    def _tool_call(self, name: str, args: dict[str, Any]) -> Verdict:
        return ALLOW

    def _reply(self, text: str) -> Verdict:
        return ALLOW

    def _count(self, verdict: Verdict) -> Verdict:
        if not verdict.blocked:
            return verdict
        if sum(self.blocks.values()) >= MAX_BLOCKS:
            return ALLOW
        self.blocks[verdict.guard] = self.blocks.get(verdict.guard, 0) + 1
        return verdict


def is_error(result: str) -> bool:
    head = result.strip().lower()[:200]
    return head.startswith("error") or "not found" in head or "failed" in head


AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|yup|correct|confirm(ed)?|go ahead|proceed|please do|do it|sounds good|that'?s right|"
    r"that works|ok(ay)?|sure)\b",
    re.IGNORECASE,
)
HEDGED = re.compile(r"\b(no|not|don'?t|do not|wait|hold on|instead|actually|but)\b", re.IGNORECASE)


class RetailGuards(Guards):
    """Authenticate before account access; confirm before every database change."""

    AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
    OPEN_TOOLS = AUTH_TOOLS | {"get_product_details", "list_all_product_types", "calculate", "transfer_to_human_agents"}
    WRITE_TOOLS = {
        "cancel_pending_order",
        "exchange_delivered_order_items",
        "modify_pending_order_address",
        "modify_pending_order_items",
        "modify_pending_order_payment",
        "modify_user_address",
        "return_delivered_order_items",
    }

    def __init__(self) -> None:
        super().__init__()
        self.user_id: str | None = None
        self.last_customer_message = ""
        self.confirmation_used = True  # nothing to confirm until the customer replies

    def on_customer_message(self, text: str) -> None:
        self.last_customer_message = text
        self.confirmation_used = False

    def confirmed(self) -> bool:
        """An explicit yes, not hedged in a long message ("yes, but actually...")."""
        text = self.last_customer_message
        return bool(AFFIRMATIVE.search(text)) and not (HEDGED.search(text) and len(text) > 80)

    def _tool_call(self, name: str, args: dict[str, Any]) -> Verdict:
        if name not in self.OPEN_TOOLS and self.user_id is None:
            return Verdict(
                True,
                "authenticate_first",
                "Blocked: authenticate the customer first with find_user_id_by_email or "
                "find_user_id_by_name_zip (required even if they gave a user id).",
            )
        if name in self.WRITE_TOOLS and (self.confirmation_used or not self.confirmed()):
            return Verdict(
                True,
                "confirm_first",
                "Blocked: this changes the database. Send the customer the exact action details and ask them "
                "to reply yes. Call the tool only after their reply confirms.",
            )
        return ALLOW

    def on_tool_result(self, name: str, args: dict[str, Any], result: str) -> None:
        if name in self.AUTH_TOOLS and result.strip() and not is_error(result):
            self.user_id = result.strip().strip('"')
        if name in self.WRITE_TOOLS:
            self.confirmation_used = True  # one database change per confirmation


GIVE_UP = re.compile(
    r"(unable to (help|assist)|can(not|'t) (help|assist)|not able to (help|assist)|transfer you|"
    r"human agent|outside (of )?(my|our) (scope|capabilities)|don'?t have (any )?(information|access))",
    re.IGNORECASE,
)


class BankingGuards(Guards):
    """Search before giving up or transferring; hold account changes once before identity is logged.

    Also keeps the case notes (verified identity, unlocked tools, tools given to the customer)
    that the banking agent pins to its prompt.
    """

    ACCOUNT_CHANGES = {"call_discoverable_agent_tool", "change_user_email"}

    def __init__(self) -> None:
        super().__init__()
        self.verified: str | None = None
        self.unlocked: dict[str, str] = {}
        self.given: list[str] = []
        self.searches_since_customer = 0
        self.searches_total = 0
        self.held: set[str] = set()

    def on_customer_message(self, text: str) -> None:
        self.searches_since_customer = 0

    def _unsearched(self) -> bool:
        return self.searches_since_customer == 0 and self.searches_total < 2

    def _tool_call(self, name: str, args: dict[str, Any]) -> Verdict:
        if name == "transfer_to_human_agents" and self._unsearched():
            return Verdict(
                True,
                "search_before_transfer",
                "Blocked: search the knowledge base (KB_search) for this situation before transferring. Many "
                "cases have a documented procedure or a specific transfer tool.",
            )
        if name in self.ACCOUNT_CHANGES and not self.verified and name not in self.held:
            # A speed bump, not a wall: some flows legitimately skip verification.
            self.held.add(name)
            return Verdict(
                True,
                "verify_identity",
                "Held: no identity verification has been logged in this conversation. If this action touches "
                "the customer's account data, verify 2 of 4 identity fields and call log_verification first. "
                "If the knowledge base says no verification is needed, call the tool again.",
            )
        return ALLOW

    def _reply(self, text: str) -> Verdict:
        if GIVE_UP.search(text) and self._unsearched():
            return Verdict(
                True,
                "search_before_giving_up",
                "Your draft tells the customer you cannot help or will hand them off, but you have not searched "
                "the knowledge base for this request. Search it with KB_search first, then answer.",
            )
        return ALLOW

    def on_tool_result(self, name: str, args: dict[str, Any], result: str) -> None:
        if name == "KB_search":
            self.searches_since_customer += 1
            self.searches_total += 1
        elif name == "log_verification" and "logged successfully" in result.lower():
            self.verified = f"{args.get('name', '?')} (user {args.get('user_id', '?')})"
        elif name == "unlock_discoverable_agent_tool" and result.startswith("Tool unlocked"):
            description = next((ln for ln in result.splitlines() if ln.startswith("Description:")), "")
            self.unlocked[str(args.get("agent_tool_name"))] = description.removeprefix("Description:").strip()[:200]
        elif name == "give_discoverable_user_tool" and result.startswith("Tool given"):
            self.given.append(str(args.get("discoverable_tool_name")))

    def case_notes(self) -> str | None:
        if not (self.verified or self.unlocked or self.given):
            return None
        lines = ["Case notes (kept by the system from this conversation's tool results):"]
        if self.verified:
            lines.append(f"- Identity verified and logged: {self.verified}")
        lines += [f"- Unlocked tool {tool}: {summary}" for tool, summary in self.unlocked.items()]
        lines += [f"- Tool given to the customer: {tool}" for tool in self.given]
        return "\n".join(lines)
