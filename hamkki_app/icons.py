"""
함끼(Hamkki) - 화면에 쓰는 선 아이콘 모음

이모지는 휴대폰 기종마다 모양·색이 달라서 사이트 색감과 따로 놀았다.
모든 아이콘을 같은 굵기(2)의 선으로 직접 그려서 글자색(currentColor)을 따라가게 했다.
템플릿에서 {{ icon("pin") }} 처럼 쓴다. (app.py가 Jinja 전역 함수로 등록)
"""

from markupsafe import Markup

_THUMB = ('<path d="M7 11v9H4v-9zM7 11l4-7a2 2 0 0 1 2.5 2.4L12.8 10H18a2 2 0 0 1 2 2.3l-1.1 6'
          'A2 2 0 0 1 17 20H7"/>')

ICONS = {
    # 위치
    "pin": '<path d="M12 21s-6.5-6.2-6.5-11.2a6.5 6.5 0 0 1 13 0C18.5 14.8 12 21 12 21z"/><circle cx="12" cy="9.8" r="2.4"/>',
    # 상황
    "date": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    "family": '<path d="M3.5 11 12 4l8.5 7M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
    "friends": ('<circle cx="9" cy="8" r="3"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/>'
                '<circle cx="16.5" cy="9" r="2.4"/><path d="M15.8 14.1a4.6 4.6 0 0 1 4.7 5.4"/>'),
    "team": ('<path d="M4 4h6l-.7 6.5a2.3 2.3 0 0 1-4.6 0z"/><path d="M7 13v6.5M5 19.5h4"/>'
             '<path d="M14 4h6l-.7 6.5a2.3 2.3 0 0 1-4.6 0z"/><path d="M17 13v6.5M15 19.5h4"/>'
             '<path d="M12 2.5V1M10.2 2.8 9.5 1.6M13.8 2.8l.7-1.2"/>'),
    "solo": '<path d="M3.5 12h17a8.5 8.5 0 0 1-17 0z"/><path d="M9 20.5h6"/><path d="M13 9.5 19 3.5M10.5 9.5 15.5 3"/>',
    # 결과·투표
    "sparkle": '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 16v4M16.5 18h4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "up": _THUMB,
    "down": f'<g transform="rotate(180 12 12)">{_THUMB}</g>',
    "refresh": '<path d="M19.5 11a7.5 7.5 0 0 0-13.6-4.4L4 8.5M4 4v4.5h4.5"/><path d="M4.5 13a7.5 7.5 0 0 0 13.6 4.4l1.9-1.9M20 20v-4.5h-4.5"/>',
    "users": ('<circle cx="9" cy="8" r="3"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/>'
              '<circle cx="16.5" cy="9" r="2.4"/><path d="M15.8 14.1a4.6 4.6 0 0 1 4.7 5.4"/>'),
    "ballot": '<path d="M4 13h16v7H4z"/><path d="M8 13V4.5h8V13"/><path d="M10.2 8.6l1.4 1.4 2.4-2.6"/>',
    "check": '<circle cx="12" cy="12" r="8.5"/><path d="M8.3 12.4l2.5 2.5 4.9-5"/>',
    "party": '<path d="M4 20 8 9l7 7z"/><path d="M14 3.5v2.5M18.5 9H21M16.8 5.2l1.7-1.7M12.5 8.5c1-1 2.6-1 3.5 0"/>',
    "crown": '<path d="M4 18h16M5 18 4 8l5 4 3-6.5L15 12l5-4-1 10"/>',
    "chart": '<path d="M4 20h16M7.5 16.5v-5M12 16.5V6.5M16.5 16.5V9"/>',
}


def icon(name, cls=""):
    """선 아이콘 하나를 SVG로. 없는 이름이면 빈 문자열."""
    body = ICONS.get(name)
    if not body:
        return Markup("")
    extra = f" {cls}" if cls else ""
    return Markup(f'<svg class="hk-ic{extra}" viewBox="0 0 24 24" aria-hidden="true">{body}</svg>')
