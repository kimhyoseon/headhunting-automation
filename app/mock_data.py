from __future__ import annotations

from app.models import Candidate


def build_mock_candidates() -> list[Candidate]:
    raw = [
        ("rmbr_8801", "정O진", "토스", "시니어 프로덕트 디자이너", "6년 3개월", "서울 강남구", ["Figma", "Design System", "Fintech", "B2B"]),
        ("rmbr_8802", "이O민", "핀다", "Senior Product Designer", "5년 8개월", "서울 서초구", ["Figma", "UX Research", "SaaS", "Amplitude"]),
        ("rmbr_8803", "박O준", "두나무", "Product Designer", "7년", "서울 송파구", ["Fintech", "Dashboard", "Data UX", "Mentoring"]),
        ("rmbr_8804", "최O서", "비바리퍼블리카", "Lead Designer", "8년", "서울 강남구", ["Design System", "Leadership", "Payment", "Research"]),
        ("rmbr_8805", "김O연", "카카오뱅크", "시니어 프로덕트 디자이너", "7년 4개월", "서울 강남구", ["Figma", "정산", "디자인시스템", "리드"]),
        ("rmbr_8806", "한O찬", "뱅크샐러드", "Product Designer", "6년", "서울 마포구", ["B2B", "Fintech", "Amplitude", "Research"]),
        ("rmbr_8807", "문O희", "네이버파이낸셜", "UX Designer", "4년 10개월", "서울 분당", ["UX", "Payment", "Prototype"]),
        ("rmbr_8808", "오O원", "카카오페이", "Product Designer", "9년 2개월", "경기 성남", ["Figma", "금융", "리서치", "리더십"]),
        ("rmbr_8809", "장O우", "쿠팡페이", "UX Lead", "10년", "서울 잠실", ["Commerce", "Payment", "Team Lead"]),
        ("rmbr_8810", "서O림", "라인플러스", "Product Designer", "5년 2개월", "서울 강남구", ["Design System", "Global", "Figma"]),
        ("rmbr_8811", "윤O아", "당근페이", "Product Designer", "6년 7개월", "서울 서초구", ["Fintech", "Local", "Research"]),
        ("rmbr_8812", "강O혁", "현대카드", "UX Designer", "8년 1개월", "서울 여의도", ["Finance", "Mobile", "Data"]),
        ("rmbr_8813", "임O나", "마이리얼트립", "Product Designer", "5년 5개월", "서울 강남구", ["Travel", "B2C", "Figma"]),
        ("rmbr_8814", "노O현", "무신사", "UX Designer", "6년 9개월", "서울 성수", ["Commerce", "Design System", "Research"]),
        ("rmbr_8815", "백O수", "아임웹", "Product Designer", "4년 6개월", "서울 강남구", ["SaaS", "Builder", "Figma"]),
        ("rmbr_8816", "신O경", "채널톡", "Product Designer", "7년 8개월", "서울 강남구", ["B2B", "SaaS", "Dashboard", "Design System"]),
        ("rmbr_8817", "홍O표", "센드버드", "Senior Designer", "8년", "서울 강남구", ["B2B", "Global", "Messaging"]),
        ("rmbr_8818", "권O비", "토스페이먼츠", "Product Designer", "6년 1개월", "서울 강남구", ["Payment", "정산", "Figma", "Research"]),
        ("rmbr_8819", "남O준", "스포카", "UX Designer", "5년", "서울 마포구", ["POS", "SMB", "SaaS"]),
        ("rmbr_8820", "유O선", "페이히어", "Product Designer", "7년 3개월", "서울 성동구", ["POS", "Payment", "B2B", "Design System"]),
        ("rmbr_8821", "조O민", "직방", "Product Designer", "5년 11개월", "서울 강남구", ["Proptech", "Mobile", "Research"]),
        ("rmbr_8822", "성O훈", "야놀자", "UX Lead", "9년", "서울 강남구", ["Travel", "Leadership", "Data"]),
        ("rmbr_8823", "차O영", "뤼튼", "Product Designer", "4년 2개월", "서울 강남구", ["AI", "SaaS", "Figma"]),
        ("rmbr_8824", "마O진", "업스테이지", "Product Designer", "6년 4개월", "서울 강남구", ["AI", "Enterprise", "Research"]),
        ("rmbr_8825", "전O호", "레몬베이스", "Product Designer", "5년 9개월", "서울 강남구", ["HR SaaS", "B2B", "Design System"]),
        ("rmbr_8826", "고O라", "플렉스", "Senior Designer", "8년 5개월", "서울 강남구", ["HR SaaS", "B2B", "Leadership"]),
        ("rmbr_8827", "진O석", "오늘의집", "Product Designer", "6년 2개월", "서울 서초구", ["Commerce", "Community", "Research"]),
        ("rmbr_8828", "배O정", "에이블리", "UX Designer", "5년 1개월", "서울 강남구", ["Commerce", "Mobile", "Figma"]),
        ("rmbr_8829", "민O재", "리디", "Product Designer", "7년", "서울 강남구", ["Content", "Design System", "Data"]),
        ("rmbr_8830", "하O윤", "원티드랩", "Product Designer", "6년 6개월", "서울 송파구", ["HR", "Matching", "B2B"]),
    ]

    candidates: list[Candidate] = []
    for cid, name, company, role, exp, location, skills in raw:
        resume = (
            f"{company}에서 {role}로 근무하며 {', '.join(skills[:3])} 중심의 제품 경험을 쌓았습니다. "
            f"경력은 {exp}이며 {location} 근무가 가능합니다. "
            "Figma 기반 프로토타이핑, 사용자 인터뷰, 데이터 기반 개선 실험을 수행했습니다. "
            "디자인시스템 운영과 개발 조직 협업 경험이 있으며 주요 지표 개선 프로젝트에 참여했습니다."
        )
        candidates.append(
            Candidate(
                id=cid,
                name=name,
                company=company,
                role=role,
                experience=exp,
                location=location,
                skills=skills,
                resume_text=resume,
            )
        )
    return candidates
