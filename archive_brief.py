"""텔레그램에 올라온 그날의 '최종' Daily Tech Brief 를 보관한다.

사람이 검토를 끝낸 최종본은 매 평일 낮(10~12시경) 텔레그램 방에 게시된다.
그 원문을 두 곳에 저장한다.

  1) GitHub  hijsh994-maker/slack-trend-backdata  의  brief_final/YYYYMMDD.md
     - 링크·매체·요약이 그대로 살아있는 마크다운 원문 + 제목 목록.
     - 백데이터 저장소이므로 입력 로그(logs/)와 폴더를 분리한다. 파이프라인이
       자기 출력을 다시 입력으로 먹는 사고를 원천 차단하기 위함.
  2) 로컬  D:\\업무\\TIR\\news-crawler\\brief-YYYY-MM-DD.txt
     - compare.py 가 읽는 '정답지'(제목 한 줄씩). 지금까지 사람이 직접 만들어
       주던 파일이라 며칠치밖에 없었다. 이걸 자동화하면 매일 재현율·임계통과율을
       스스로 측정하는 루프가 돈다.

수집 대상 방과 메시지 형식(2026-07 기준):
    방: '테크 뉴스 동향 테스트방' (id -1004441111734)
    첫 줄: **[7/30(목), Daily Tech Brief]**
    분류:  **■ AI Model**
    항목:  **1. 제목** (매체, [원문보기](URL))
           📌 **주요 내용:** ...
           📌 **시사점:** ...

날짜 판별은 게시 시각이 아니라 '헤더에 적힌 날짜'를 기준으로 한다. 게시가 늦어
자정을 넘기거나 다음날 정정 재게시되는 경우에도 올바른 파일에 들어가게 하기 위함.

사용:
    python archive_brief.py                      # 오늘(KST)치
    python archive_brief.py --date 2026-07-28    # 특정일 백필
    python archive_brief.py --no-push            # 커밋만, 푸시 안 함
    python archive_brief.py --truth-dir truth    # 정답지도 저장소 안(truth/)에 써서 함께 커밋

클라우드 루틴(Claude Code routine)에서 실행할 때:
    - 이 파일을 저장소 루트에 두면 --repo 기본값이 그 저장소가 된다.
    - 세션 파일 대신 환경변수 TELEGRAM_SESSION(StringSession), TELEGRAM_API_ID,
      TELEGRAM_API_HASH 를 읽는다. 문자열은 로컬에서 export_session.py 로 발급.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TG_DIR = os.path.join(os.environ.get("USERPROFILE", ""), ".telegram-sync")
HERE = os.path.dirname(os.path.abspath(__file__))
# 클라우드 샌드박스는 UTC 라서 로컬 tz 에 기대면 날짜가 밀린다. 항상 KST 로 계산.
KST = datetime.timezone(datetime.timedelta(hours=9))

DEFAULT_CHAT_ID = -1004441111734          # 테크 뉴스 동향 테스트방
# 스크립트가 저장소 안에 있으면(클라우드 루틴) 그 저장소를, 아니면 옆의 로컬 클론을 쓴다.
DEFAULT_REPO = HERE if os.path.isdir(os.path.join(HERE, ".git")) \
    else os.path.join(os.path.dirname(HERE), "backdata-repo")
REPO_URL = "https://github.com/hijsh994-maker/slack-trend-backdata.git"
REPO_SUBDIR = "brief_final"
GIT_USER = ("hijsh994-maker", "hijsh994@gmail.com")

# **[7/30(목), Daily Tech Brief]**  — 굵게 표시가 없거나 공백이 달라도 잡히게 느슨히.
RE_HEADER = re.compile(r"^\**\s*\[\s*(\d{1,2})\s*/\s*(\d{1,2})\s*(?:\([^)]*\))?\s*,\s*Daily\s*Tech\s*Brief\s*\]")
# **1. 제목** (매체, [원문보기](URL))  — 굵게 표시 위치는 날짜별로 흔들린다(아래 split_item 참고)
# 번호 뒤 공백은 있을 때도 없을 때도 있다("2.마이크로소프트..."처럼). \s* 로 둘 다 받는다.
RE_ITEM = re.compile(r"^\*{0,2}(\d+)\.\s*(.*)$")
RE_CATEGORY = re.compile(r"^\*{0,2}\s*■\s*(.+?)\s*\*{0,2}\s*$")
RE_URL = re.compile(r"\[원문보기\]\s*\((.*?)\)")
MARK = "[원문보기]"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 텔레그램


def connect():
    from telethon.sync import TelegramClient

    # 클라우드: 세션 파일이 없으므로 환경변수의 StringSession 을 쓴다(export_session.py 로 발급).
    sess_str = os.environ.get("TELEGRAM_SESSION")
    if sess_str:
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(sess_str),
                                int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
    else:
        cfg_path = os.path.join(TG_DIR, "config.json")
        if not os.path.exists(cfg_path):
            log("ERROR: %s 없음. login.py 를 먼저 실행하세요." % cfg_path)
            sys.exit(2)
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        client = TelegramClient(os.path.join(TG_DIR, "adot_session"), cfg["api_id"], cfg["api_hash"])
    # 하이퍼링크(text_url 엔티티)를 [label](url) 로 보존. 이게 없으면 원문 링크가 통째로 날아간다.
    client.parse_mode = "md"
    client.connect()
    if not client.is_user_authorized():
        log("ERROR: 텔레그램 세션 만료. %s 재실행 필요"
            % ("TELEGRAM_SESSION 재발급(export_session.py)" if sess_str else TG_DIR + "\\login.py"))
        client.disconnect()
        sys.exit(2)
    return client


def collect_messages(client, chat_id, target, lookback):
    """target 날짜 전후를 훑어 (id 오름차순) 메시지 목록을 만든다.

    헤더 날짜로 판별하므로 게시일이 하루이틀 밀린 경우도 잡으려면 target 이후
    lookback 일까지 봐야 한다. 300건이면 이 방의 몇 달치라 충분하다.
    """
    entity = client.get_entity(chat_id)
    stop_before = target - datetime.timedelta(days=1)
    out = []
    for m in client.iter_messages(entity, limit=300):
        if m.date is None:
            continue
        local = m.date.astimezone(KST)
        if local.date() > target + datetime.timedelta(days=lookback):
            continue
        if local.date() < stop_before:
            break  # 최신순이므로 더 볼 필요 없음
        text = (m.text or m.message or "").strip()
        out.append((m.id, local, text, m))
    out.reverse()
    return out


def find_brief(msgs, target):
    """헤더 날짜가 target 과 같은 브리프를 찾아 (게시시각, 본문) 반환.

    같은 날짜 브리프가 여러 번 올라왔으면(정정 재게시) 가장 나중 것을 쓴다.
    텔레그램 4096자 제한으로 쪼개져 올라온 경우를 대비해, 헤더 메시지 직후
    10분 이내에 이어 붙은 '자체 헤더가 없는' 메시지들을 본문에 이어 붙인다.
    """
    hit = None
    for idx, (mid, when, text, _m) in enumerate(msgs):
        head = RE_HEADER.match(text)
        if not head:
            continue
        mm, dd = int(head.group(1)), int(head.group(2))
        if (mm, dd) == (target.month, target.day):
            hit = idx
    if hit is None:
        return None

    mid, when, text, _m = msgs[hit]
    parts = [text]
    for mid2, when2, text2, _m2 in msgs[hit + 1:]:
        if RE_HEADER.match(text2):
            break
        if (when2 - when).total_seconds() > 600:
            break
        if not text2:
            continue
        parts.append(text2)
    return {"msg_id": mid, "posted": when, "body": "\n".join(parts)}


# ---------------------------------------------------------------- 파싱


def split_item(rest):
    """'제목** (매체, [원문보기](url))' 꼬리를 (제목, 매체, 링크)로 가른다.

    굵게(**) 표시 위치가 날짜마다 다르다. 7/23 이후는 `**제목** (매체, [원문보기](url))`
    이지만 7/22 에는 `**제목 (매체, **[원문보기](url)**)**` 처럼 별표가 꼬리 안까지
    들어와 있었다. 그래서 정규식으로 통째 매칭하지 않고 '[원문보기]' 마커를 기준으로
    자른 뒤, 그 앞의 마지막 여는 괄호를 매체명 시작으로 본다. 제목 안의 괄호
    (예: '엑사원(EXAONE)')는 마지막 괄호가 아니므로 안전하다.
    """
    url_m = RE_URL.search(rest)
    idx = rest.find(MARK)
    if not url_m or idx < 0:
        return clean(rest), "", ""
    head = rest[:idx].rstrip().rstrip("*").rstrip()
    open_at = head.rfind("(")
    if open_at < 0:
        return clean(head), "", url_m.group(1)
    outlet = clean(head[open_at + 1:]).rstrip(",").strip()
    return clean(head[:open_at]), outlet, url_m.group(1)


def clean(s):
    return s.replace("**", "").strip().strip("*").strip()


def extract_items(body):
    """브리프 본문에서 (분류, 제목, 매체, 링크) 목록을 뽑는다."""
    items = []
    category = ""
    for line in body.splitlines():
        line = line.strip()
        cat = RE_CATEGORY.match(line)
        if cat:
            category = clean(cat.group(1))
            continue
        if line.startswith("📌"):       # 주요 내용 / 시사점 줄은 항목이 아니다
            continue
        it = RE_ITEM.match(line)
        if not it:
            continue
        title, outlet, url = split_item(it.group(2))
        if len(title) < 6:              # 번호만 있는 빈 줄 방어
            continue
        items.append({"category": category, "title": title, "outlet": outlet, "url": url})
    return items


# ---------------------------------------------------------------- 출력


def render_repo_md(day, brief, items):
    lines = [
        "# Daily Tech Brief - %s" % day.isoformat(),
        "",
        "- 출처: 텔레그램 '테크 뉴스 동향 테스트방' (message id %d)" % brief["msg_id"],
        "- 게시: %s KST" % brief["posted"].strftime("%Y-%m-%d %H:%M"),
        "- 수록 기사: %d건" % len(items),
        "",
        "## 수록 기사(제목만)",
        "",
    ]
    for it in items:
        lines.append("- [%s] %s" % (it["category"], it["title"]))
    lines += ["", "## 원문", "", brief["body"], ""]
    return "\n".join(lines)


def render_ground_truth(day, items):
    lines = ["# %s 실제 Daily Tech Brief 수록 기사 (텔레그램 최종본 자동 보관)" % day.isoformat()]
    lines += [it["title"] for it in items]
    return "\n".join(lines) + "\n"


def render_truth_json(day, items):
    """compare.py 가 링크로 대조할 수 있게 제목+매체+링크를 함께 남긴다.

    제목만 있는 brief-*.txt 로는 '같은 기사인가'를 어휘 유사도로 추정할 수밖에
    없어 오매칭이 생긴다. 링크가 있으면 그 판정이 확정이 된다.
    """
    return json.dumps({"date": day.isoformat(), "items": items},
                      ensure_ascii=False, indent=1) + "\n"


def write_if_changed(path, content, force_note=""):
    """내용이 같으면 건드리지 않는다(불필요한 커밋 방지). 반환: 'new'|'update'|'same'"""
    if os.path.exists(path):
        old = open(path, encoding="utf-8").read()
        if old == content:
            return "same"
        state = "update"
    else:
        state = "new"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    if force_note:
        log(force_note)
    return state


# ---------------------------------------------------------------- git


def git(repo, *args, **kw):
    r = subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 and not kw.get("allow_fail"):
        log("ERROR: git %s 실패\n%s\n%s" % (" ".join(args), r.stdout, r.stderr))
        sys.exit(3)
    return r


def ensure_repo(repo):
    if not os.path.isdir(os.path.join(repo, ".git")):
        log("저장소 클론: %s" % repo)
        os.makedirs(os.path.dirname(repo), exist_ok=True)
        r = subprocess.run(["git", "clone", REPO_URL, repo], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            log("ERROR: clone 실패\n%s\n%s" % (r.stdout, r.stderr))
            sys.exit(3)
        git(repo, "config", "user.name", GIT_USER[0])
        git(repo, "config", "user.email", GIT_USER[1])
    else:
        git(repo, "pull", "--rebase", "origin", "main")
    # 클라우드 샌드박스는 매번 새 clone 이라 커밋 작성자가 비어 있다. 매번 넣어도 무해.
    git(repo, "config", "user.name", GIT_USER[0])
    git(repo, "config", "user.email", GIT_USER[1])


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--chat-id", type=int, default=DEFAULT_CHAT_ID)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="백데이터 저장소 로컬 클론 경로")
    ap.add_argument("--truth-dir", default=HERE,
                    help="정답지(brief-*.txt, truth-*.json) 저장 폴더. 저장소 안이면 함께 커밋한다")
    ap.add_argument("--lookback", type=int, default=2,
                    help="헤더 날짜가 target 인 브리프를 target+N일까지 뒤에서도 찾는다")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-repo", action="store_true", help="정답지만 쓰고 저장소는 건드리지 않음")
    ap.add_argument("--force-truth", action="store_true",
                    help="사람이 만든 기존 brief-*.txt 도 덮어씀(기본은 보존)")
    a = ap.parse_args()

    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.datetime.now(KST).date())
    log("=== Daily Tech Brief 보관: %s" % day.isoformat())

    client = connect()
    try:
        msgs = collect_messages(client, a.chat_id, day, a.lookback)
    finally:
        client.disconnect()
    log("메시지 %d건 조회" % len(msgs))

    brief = find_brief(msgs, day)
    if not brief:
        log("NO_BRIEF: %s 자 브리프가 아직 방에 없습니다. 아무 것도 저장하지 않고 종료." % day.isoformat())
        sys.exit(1)

    items = extract_items(brief["body"])
    log("브리프 발견: msg %d, 게시 %s, 기사 %d건"
        % (brief["msg_id"], brief["posted"].strftime("%m-%d %H:%M"), len(items)))
    if not items:
        log("ERROR: 제목을 하나도 못 뽑았습니다. 브리프 형식이 바뀌었을 수 있으니 파서(RE_ITEM) 점검 필요.")
        sys.exit(4)

    truth_dir = os.path.realpath(a.truth_dir)
    repo_abs = os.path.realpath(a.repo)
    truth_in_repo = (not a.no_repo) and (truth_dir == repo_abs or truth_dir.startswith(repo_abs + os.sep))
    if truth_in_repo:
        ensure_repo(a.repo)  # 정답지를 저장소에 쓰기 전에 최신 상태로 맞춘다

    # 1) 정답지 (compare.py 용)
    changed = []
    truth_path = os.path.join(truth_dir, "brief-%s.txt" % day.isoformat())
    if os.path.exists(truth_path) and not a.force_truth:
        log("정답지 유지: %s 이미 존재(사람이 만든 파일일 수 있어 덮어쓰지 않음)"
            % os.path.basename(truth_path))
    else:
        st = write_if_changed(truth_path, render_ground_truth(day, items))
        log("정답지 %s: %s" % (st, os.path.basename(truth_path)))
        if st != "same":
            changed.append(truth_path)
    # 링크가 든 정답지는 사람 파일과 겹치지 않으므로 항상 갱신한다.
    json_path = os.path.join(truth_dir, "truth-%s.json" % day.isoformat())
    st = write_if_changed(json_path, render_truth_json(day, items))
    log("링크 정답지 %s: truth-%s.json" % (st, day.isoformat()))
    if st != "same":
        changed.append(json_path)

    if a.no_repo:
        log("--no-repo: 저장소 반영 생략")
        return

    # 2) 백데이터 저장소
    if not truth_in_repo:
        ensure_repo(a.repo)
        changed = []  # 정답지는 저장소 밖이므로 커밋 대상이 아니다
    md_path = os.path.join(a.repo, REPO_SUBDIR, "%s.md" % day.strftime("%Y%m%d"))
    st = write_if_changed(md_path, render_repo_md(day, brief, items))
    rel = "%s/%s.md" % (REPO_SUBDIR, day.strftime("%Y%m%d"))
    if st != "same":
        changed.append(md_path)
    if not changed:
        log("저장소 변경 없음(%s 이미 동일). 커밋하지 않음." % rel)
        return
    log("저장소 %s: %s" % (st, rel) if st != "same" else "저장소: %s 동일, 정답지만 커밋" % rel)

    git(a.repo, "add", *[os.path.relpath(p, repo_abs) for p in changed])
    verb = "Add" if st == "new" else "Update"
    git(a.repo, "commit", "-m", "%s final Daily Tech Brief for %s" % (verb, day.isoformat()))
    if a.no_push:
        log("--no-push: 커밋만 하고 종료")
        return
    git(a.repo, "push", "origin", "main")
    log("푸시 완료: https://github.com/hijsh994-maker/slack-trend-backdata/blob/main/%s" % rel)


if __name__ == "__main__":
    main()
