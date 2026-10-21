"""LangChain adapter that rewrites a follow-up into a standalone search question."""

from __future__ import annotations

import re
from typing import Any, Sequence

from langchain_core.prompts import ChatPromptTemplate

from src.adapters.langchain_answer_generator import usage_from_message
from src.application.question_rewriting import RewriteResult
from src.domain.query import ConversationTurn


_MAX_QUESTION_CHARS = 300
_PREFIX = re.compile(r"^\s*(?:改写后的问题|改写后|检索问题|问题)\s*[:：]\s*")
_QUOTES = "\"'“”‘’「」『』"


def _transcript(history: Sequence[ConversationTurn], answer_chars: int) -> str:
    lines: list[str] = []
    for turn in history:
        lines.append(f"用户：{turn.question}")
        lines.append(f"助手：{(turn.answer or '（资料不足，未作回答）')[:answer_chars]}")
    return "\n".join(lines)


class LangChainQuestionRewriter:
    """Rewrite with the same chat model the answers use.

    History goes in as a transcript inside one message, so the model edits a
    question instead of continuing the conversation.
    """

    def __init__(
        self,
        *,
        model: Any,
        answer_chars: int = 400,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        cost_currency: str = "CNY",
    ) -> None:
        self._answer_chars = answer_chars
        self._input_cost_per_million = input_cost_per_million
        self._output_cost_per_million = output_cost_per_million
        self._cost_currency = cost_currency
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你负责把用户在对话中的最新问题改写成检索用的问题。资料库是排水领域的法规、"
                    "标准和政策文件。\n"
                    "- 结合对话历史，补全最新问题里省略或用代词指代的对象、方法、文件名、条款号，"
                    "让它不看历史也能读懂。\n"
                    "- 最新问题本身已经完整，或者和历史无关时，原样输出。\n"
                    "- 不回答问题，不加入历史里没有的信息。\n"
                    "- 只输出改写后的问题这一句话。",
                ),
                ("human", "对话历史：\n{transcript}\n\n最新问题：{question}"),
            ]
        )
        self._chain = prompt | model

    async def rewrite(
        self, *, question: str, history: Sequence[ConversationTurn]
    ) -> RewriteResult:
        raw_message = await self._chain.ainvoke(
            {"transcript": _transcript(history, self._answer_chars), "question": question}
        )
        text = str(raw_message.content).strip().splitlines()[0] if str(raw_message.content).strip() else ""
        text = _PREFIX.sub("", text).strip().strip(_QUOTES).strip()
        if not text or len(text) > _MAX_QUESTION_CHARS:
            text = question
        return RewriteResult(
            question=text,
            usage=usage_from_message(
                raw_message,
                input_cost_per_million=self._input_cost_per_million,
                output_cost_per_million=self._output_cost_per_million,
                cost_currency=self._cost_currency,
            ),
        )
