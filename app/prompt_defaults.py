DEFAULT_MATCH_SYSTEM_PROMPT = (
    "너는 채용 후보자 매칭 점수를 보수적으로 산출하는 평가자다."
)

DEFAULT_MATCH_USER_PROMPT = """JD와 후보자 이력서 텍스트를 비교해서 후보자의 적합도를 JSON으로 평가해줘.

반드시 JSON 객체만 반환하고, 키는 total_score, reason만 사용해.
total_score는 0~100 사이의 정수야.
reason은 한국어 20자 이내의 짧은 사유로 작성해.

JD:
{jd_text}

이력서:
{resume_text}"""

DEFAULT_PROMPTS = {
    "match_system_prompt": DEFAULT_MATCH_SYSTEM_PROMPT,
    "match_user_prompt": DEFAULT_MATCH_USER_PROMPT,
}
