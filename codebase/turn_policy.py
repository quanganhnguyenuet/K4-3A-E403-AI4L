"""Shared conversational intent and recovery policy for both product pipelines.

The language model may propose an intent, but these high-precision rules protect
conversation-control turns from being graded as learner knowledge.  Ambiguous
content deliberately falls through to ``teachback_answer`` so the semantic
assessor can make the final decision.
"""

from __future__ import annotations

import re
import unicodedata


INTENTS = (
    "teachback_answer",
    "request_help",
    "clarification_question",
    "change_topic",
    "out_of_scope",
    "authority_attack",
    "session_setup",
    "source_request",
    "social",
)

RECOVERY_STAGES = (
    "none",
    "socratic_question",
    "narrowed_question",
    "controlled_hint",
    "knowledge_recovery",
)


def requests_direct_answer(value: str) -> bool:
    """Detect an explicit opt-out from further Socratic prompting."""
    text = normalize_for_policy(value)
    patterns = (
        r"\b(cho|dua|noi|bat mi|hien|xem)\s+(minh|toi|em)?\s*(dap an|cau tra loi|loi giai)\b",
        r"\b(dap an|cau tra loi|loi giai)\s+(la gi|dung la gi|luon|di|nhe|duoc khong)\b",
        r"\b(giai|tra loi)\s+(thang|luon|truc tiep)\b",
        # Natural opt-out phrasing that does not contain the word "đáp án".
        # Keep the explicit second-person subject so ordinary statements such
        # as "LLM trả lời tôi..." are not mistaken for a help request.
        r"\bban\s+(co the\s+)?(tra loi|giai dap)\s+(cho\s+)?(minh|toi|em)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def normalize_for_policy(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower())
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", ascii_text.replace("đ", "d")).strip()


def classify_turn_intent(value: str) -> str:
    """Return a deterministic intent only for explicit, high-precision cases."""
    text = normalize_for_policy(value)

    # Prevent "cho mình đáp án được không?" from falling through to the
    # generic question branch and producing yet another coaching question.
    if requests_direct_answer(value):
        return "request_help"

    authority_patterns = (
        # Only a request to *reveal* the hidden prompt is an attack — citing
        # "system prompt" as a legitimate mitigation technique in an answer
        # (e.g. "dùng system prompt để giảm rủi ro") must not match.
        r"\b(cho|dua|hien|xem|lay|trich|tiet lo|noi ro|in ra|dua ra).{0,60}\b(system prompt|developer message|hidden prompt|prompt an|chi dan an)\b",
        r"\b(system prompt|developer message|hidden prompt|prompt an|chi dan an)\b.{0,20}\b(cua ban|ban dang dung|la gi)\b",
        r"\b(bo qua|ignore|quen).{0,30}\b(chi dan|quy tac|vai tro|system)\b",
        r"\b(doi vai|dong vai).{0,35}\b(giao vien|nguoi cham|admin)\b",
        r"\b(cho|tang|sua).{0,20}\b(diem|progress).{0,20}\b(100|toi da|day)\b",
        r"\b(danh dau|xac nhan).{0,25}\b(hoan thanh|da hoc xong)\b",
        r"\b(bang diem chinh thuc|tinh vao.{0,15}(diem|bang diem)|diem thi)\b",
    )
    if any(re.search(pattern, text) for pattern in authority_patterns):
        return "authority_attack"

    source_patterns = (
        r"\b(cho|dua|hien|xem|lay|trich).{0,35}\b(nguon|trich dan|transcript|doan goc|nguyen van)\b",
        r"\b(nguon o dau|theo transcript|locator nao|ma nguon nao)\b",
    )
    if any(re.search(pattern, text) for pattern in source_patterns):
        return "source_request"

    change_patterns = (
        r"\b(chuyen|doi|sang).{0,25}\b(chu de|bai|phan|hoc|on)\b",
        r"\b(khong hoc|khong on).{0,25}\b(nay|bai nay|phan nay)\b",
    )
    if any(re.search(pattern, text) for pattern in change_patterns):
        return "change_topic"

    outside_patterns = (
        r"\b(thoi tiet|du bao mua|bong da|nau an|mon an|viet cv|viet.{0,15}bai luan|bien doi khi hau|chung khoan|gia bitcoin|dat phong|du lich)\b",
        r"\b(ai|chatgpt|model).{0,35}\b(viet cv|lam tho|dat ve|dat phong|du bao thoi tiet)\b",
        r"\b(model nao|gpt-?4|gpt-?3\.5).{0,45}\b(xep hang|so sanh|it nhat|nhieu hon)\b",
        r"\b(ai|may).{0,30}\b(tu y thuc|co y thuc|cam xuc)\b",
        r"\b(sua|debug|fix).{0,25}\b(code|python|javascript|java)\b",
        r"\b(mang|wifi).{0,20}\b(cham|lag|loi|mat)\b",
    )
    if any(re.search(pattern, text) for pattern in outside_patterns):
        return "out_of_scope"

    if re.search(
        r"\b((em|minh|toi) )?(khong|k|ko|chua) (biet|hieu|nho|ro)|quen het|mat goc|bi roi|bi y"
        r"|chiu|chiu thua|bo cuoc|khong (nghi|doan) (ra|duoc)\b",
        text,
    ):
        return "request_help"

    session_setup_patterns = (
        r"^(chao ban[, ]*)?(toi|minh|em)?\s*(muon|can|nho|hay).{0,45}\b(hoc|on|day|giai thich|lam hoc vien)\b",
        r"\bban co the.{0,35}\b(hoc|on|day|giai thich|lam hoc vien)\b",
    )
    if any(re.search(pattern, text) for pattern in session_setup_patterns):
        return "session_setup"

    help_patterns = (
        r"\b(khong|chua|chang)\s+(biet|hieu|nho|ro)\b",
        r"\b(k biet|ko biet|quen het|mat goc|bi roi|bi y|hoc tu dau|bat dau tu dau)\b",
        r"\b(cho|minh can|giup minh|goi y|nhac lai|xem lai).{0,25}\b(gợi ý|goi y|bai|phan|kien thuc|cach)\b",
    )
    if any(re.search(pattern, text) for pattern in help_patterns):
        return "request_help"

    # A question can still be a knowledge claim (for example "RAG luôn đúng,
    # phải không?").  Absolute technical claims must reach the assessor.
    if re.search(
        r"\b(luon dung|chinh xac 100|100%|khong bao gio.{0,15}(bia|hallucinat)|het hallucinat|loai bo hoan toan)\b",
        text,
    ):
        return "teachback_answer"

    question_patterns = (
        r"\b(la gi|tai sao|vi sao|nhu the nao|the nao|khi nao|o dau|khac gi|co dung)\b",
        r"\b(cho|minh can|hay neu).{0,18}\bvi du\b",
    )
    if "?" in value or any(re.search(pattern, text) for pattern in question_patterns):
        return "clarification_question"

    if re.match(r"^(xin chao|chao|hello|hi|cam on|thanks)(\b|[!. ])", text):
        return "social"
    return "teachback_answer"


def recovery_action(attempt: int, *, misconception: bool = False) -> tuple[str, str]:
    """Map a per-gap failed attempt count to the locked recovery ladder."""
    if attempt <= 1:
        return (
            "SOCRATIC_CORRECTION" if misconception else "SOCRATIC_QUESTION",
            "socratic_question",
        )
    if attempt == 2:
        return "NARROW_QUESTION", "narrowed_question"
    if attempt == 3:
        return "CONTROLLED_HINT", "controlled_hint"
    return "SHOW_RECOVERY", "knowledge_recovery"
