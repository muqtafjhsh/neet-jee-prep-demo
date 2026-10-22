import json
import os
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from seed import DEMO_STUDENTS, QUESTIONS

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DATABASE_PATH", os.path.join(BASE, "app.db"))
os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
FRONT = os.path.abspath(os.path.join(BASE, "..", "frontend"))
TEACHER_CODE = os.getenv("TEACHER_CODE", "VIT2026")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MARK_CORRECT, MARK_WRONG = 4, -1  # NEET and JEE Main MCQ marking

app = FastAPI(title="Heuristic Prep - NEET & JEE API", version="0.1")


@app.get("/healthz")
def healthz():
    return {"ok": True}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, name TEXT, role TEXT,
  exam_track TEXT, class_level TEXT, created REAL);
CREATE TABLE IF NOT EXISTS otps(email TEXT PRIMARY KEY, code TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, exam TEXT, subject TEXT, chapter TEXT, text TEXT,
  options TEXT, answer INTEGER, explanation TEXT, source TEXT, paper_year INTEGER, paper_label TEXT);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, question_id INTEGER, selected INTEGER,
  correct INTEGER, time_taken REAL, mode TEXT, created REAL);
CREATE TABLE IF NOT EXISTS tests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exam TEXT, mode TEXT, subject TEXT,
  qids TEXT, started REAL, duration_sec INTEGER, submitted INTEGER DEFAULT 0,
  score INTEGER DEFAULT 0, max_score INTEGER DEFAULT 0, correct INTEGER DEFAULT 0,
  wrong INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0);
"""


@contextmanager
def get_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with get_db() as c:
        c.executescript(SCHEMA)
        # Migrate existing demo databases without deleting user progress.
        columns = {r[1] for r in c.execute("PRAGMA table_info(questions)")}
        if "paper_year" not in columns:
            c.execute("ALTER TABLE questions ADD COLUMN paper_year INTEGER")
        if "paper_label" not in columns:
            c.execute("ALTER TABLE questions ADD COLUMN paper_label TEXT")
        for item in QUESTIONS:
            exam, subj, chap, text, opts, ans, expl, src = item[:8]
            year, label = item[8:10] if len(item) > 8 else (None, None)
            exists = c.execute("SELECT 1 FROM questions WHERE exam=? AND text=?", (exam, text)).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO questions(exam,subject,chapter,text,options,answer,explanation,source,paper_year,paper_label) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (exam, subj, chap, text, json.dumps(opts, ensure_ascii=False), ans, expl, src, year, label),
                )
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            now = time.time()
            for name, email, cls, track, scores in DEMO_STUDENTS:
                cur = c.execute(
                    "INSERT INTO users(email,name,role,exam_track,class_level,created) VALUES(?,?,?,?,?,?)",
                    (email, name, "student", track, cls, now),
                )
                for s in scores:
                    c.execute(
                        "INSERT INTO tests(user_id,exam,mode,subject,qids,started,duration_sec,submitted,score,max_score) "
                        "VALUES(?,?,?,?,?,?,?,1,?,72)",
                        (cur.lastrowid, track, "exam_pattern", None, "[]", now, 1080, s),
                    )


init_db()


# ---------------------------------------------------------------- auth
def auth(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not logged in")
    with get_db() as c:
        row = c.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (authorization[7:],),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Session expired")
    return dict(row)


def teacher_only(u: dict = Depends(auth)) -> dict:
    if u["role"] != "teacher":
        raise HTTPException(403, "Teacher access only")
    return u


class OtpRequest(BaseModel):
    email: str


class OtpVerify(BaseModel):
    email: str
    otp: str
    name: Optional[str] = None
    role: str = "student"
    exam_track: str = "NEET"  # NEET | JEE | BOTH
    class_level: str = "12"
    teacher_code: Optional[str] = None


@app.post("/api/auth/request-otp")
def request_otp(body: OtpRequest):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email")
    code = f"{random.randint(0, 999999):06d}"
    with get_db() as c:
        c.execute("REPLACE INTO otps(email,code,expires) VALUES(?,?,?)", (email, code, time.time() + 300))
    print(f"[OTP] {email} -> {code}")
    # DEMO MODE: OTP is returned so the prototype works without SES/SNS. Production: send via Amazon SES/SNS.
    with get_db() as c:
        user = c.execute("SELECT exam_track, role, class_level FROM users WHERE email=?", (email,)).fetchone()
    return {"sent": True, "dev_otp": code,
            "existing_user": dict(user) if user else None}


@app.post("/api/auth/verify-otp")
def verify_otp(b: OtpVerify):
    email = b.email.strip().lower()
    with get_db() as c:
        row = c.execute("SELECT * FROM otps WHERE email=?", (email,)).fetchone()
        if not row or row["code"] != b.otp.strip() or row["expires"] < time.time():
            raise HTTPException(400, "Invalid or expired OTP")
        if b.exam_track not in ("NEET", "JEE", "BOTH"):
            raise HTTPException(400, "Invalid exam track")
        user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            if b.role == "teacher" and b.teacher_code != TEACHER_CODE:
                raise HTTPException(403, "Invalid teacher access code")
            cur = c.execute(
                "INSERT INTO users(email,name,role,exam_track,class_level,created) VALUES(?,?,?,?,?,?)",
                (email, b.name or email.split("@")[0], "teacher" if b.role == "teacher" else "student",
                 b.exam_track, b.class_level, time.time()),
            )
            user = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        else:
            c.execute("UPDATE users SET exam_track=?, class_level=? WHERE id=?",
                      (b.exam_track, b.class_level, user["id"]))
            user = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        c.execute("DELETE FROM otps WHERE email=?", (email,))
        token = secrets.token_hex(24)
        c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)", (token, user["id"]))
    return {"token": token, "user": dict(user)}

@app.get("/api/me")
def me(u: dict = Depends(auth)):
    return u


class TrackUpdate(BaseModel):
    exam_track: str


@app.post("/api/me/track")
def set_track(b: TrackUpdate, u: dict = Depends(auth)):
    if b.exam_track not in ("NEET", "JEE", "BOTH"):
        raise HTTPException(400, "Invalid exam track")
    with get_db() as c:
        c.execute("UPDATE users SET exam_track=? WHERE id=?", (b.exam_track, u["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- content
@app.get("/api/meta")
def meta(u: dict = Depends(auth)):
    out: Dict[str, Dict[str, List[str]]] = {}
    with get_db() as c:
        for r in c.execute("SELECT DISTINCT exam,subject,chapter FROM questions ORDER BY exam,subject,chapter"):
            out.setdefault(r["exam"], {}).setdefault(r["subject"], []).append(r["chapter"])
    return out


def public_q(r) -> dict:
    return {"id": r["id"], "exam": r["exam"], "subject": r["subject"], "chapter": r["chapter"],
            "text": r["text"], "options": json.loads(r["options"])}


@app.get("/api/questions")
def questions(exam: str, subject: Optional[str] = None, chapter: Optional[str] = None, u: dict = Depends(auth)):
    sql, args = "SELECT * FROM questions WHERE exam=?", [exam]
    if subject:
        sql += " AND subject=?"; args.append(subject)
    if chapter:
        sql += " AND chapter=?"; args.append(chapter)
    with get_db() as c:
        return [public_q(r) for r in c.execute(sql + " ORDER BY id", args)]


class AnswerReq(BaseModel):
    question_id: int
    selected: int
    time_taken: float = 0


@app.post("/api/answer")
def answer(b: AnswerReq, u: dict = Depends(auth)):
    with get_db() as c:
        q = c.execute("SELECT * FROM questions WHERE id=?", (b.question_id,)).fetchone()
        if not q:
            raise HTTPException(404, "Question not found")
        ok = int(b.selected == q["answer"])
        c.execute("INSERT INTO attempts(user_id,question_id,selected,correct,time_taken,mode,created) VALUES(?,?,?,?,?,?,?)",
                  (u["id"], q["id"], b.selected, ok, b.time_taken, "learn", time.time()))
    return {"correct": bool(ok), "answer": q["answer"], "explanation": q["explanation"], "source": q["source"]}


# ---------------------------------------------------------------- tests
class StartTest(BaseModel):
    exam: str
    mode: str  # chapter | exam_pattern | previous_year
    subject: Optional[str] = None
    chapter: Optional[str] = None
    paper_year: Optional[int] = None


@app.get("/api/papers")
def papers(exam: str, u: dict = Depends(auth)):
    with get_db() as c:
        return [dict(r) for r in c.execute(
            "SELECT paper_year AS year, paper_label AS label, COUNT(*) AS questions "
            "FROM questions WHERE exam=? AND paper_year IS NOT NULL GROUP BY paper_year,paper_label ORDER BY paper_year DESC",
            (exam,))]


@app.post("/api/tests/start")
def start_test(b: StartTest, u: dict = Depends(auth)):
    sql, args = "SELECT * FROM questions WHERE exam=?", [b.exam]
    if b.exam not in ("NEET", "JEE"):
        raise HTTPException(400, "Choose NEET or JEE")
    if b.mode not in ("chapter", "exam_pattern", "previous_year"):
        raise HTTPException(400, "Unknown test mode")
    if b.mode == "previous_year":
        if b.paper_year not in (2024, 2025):
            raise HTTPException(400, "Choose an available practice set")
        sql += " AND paper_year=?"; args.append(b.paper_year)
    if b.mode == "chapter":
        if not b.subject:
            raise HTTPException(400, "Choose a subject")
        sql += " AND subject=?"; args.append(b.subject)
        if b.chapter:
            sql += " AND chapter=?"; args.append(b.chapter)
    with get_db() as c:
        rows = c.execute(sql, args).fetchall()
        if not rows:
            raise HTTPException(404, "No questions available")
        random.shuffle(rows)
        rows = rows[:30]
        # Prototype pattern: 1 minute per question. Real pattern: NEET 180Q/3h, JEE Main 90Q/3h (same 4/-1 marking).
        duration = len(rows) * 60
        cur = c.execute(
            "INSERT INTO tests(user_id,exam,mode,subject,qids,started,duration_sec,max_score) VALUES(?,?,?,?,?,?,?,?)",
            (u["id"], b.exam, b.mode, b.subject, json.dumps([r["id"] for r in rows]), time.time(), duration,
             MARK_CORRECT * len(rows)),
        )
    return {"test_id": cur.lastrowid, "duration_sec": duration, "questions": [public_q(r) for r in rows],
            "marking": {"correct": MARK_CORRECT, "wrong": MARK_WRONG}}


class SubmitTest(BaseModel):
    answers: Dict[str, Optional[int]]
    times: Dict[str, float] = {}


@app.post("/api/tests/{test_id}/submit")
def submit_test(test_id: int, b: SubmitTest, u: dict = Depends(auth)):
    with get_db() as c:
        t = c.execute("SELECT * FROM tests WHERE id=? AND user_id=?", (test_id, u["id"])).fetchone()
        if not t:
            raise HTTPException(404, "Test not found")
        if t["submitted"]:
            raise HTTPException(400, "Already submitted")
        qids = json.loads(t["qids"])
        correct = wrong = skipped = 0
        by_subject: Dict[str, dict] = {}
        review = []
        for qid in qids:
            q = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
            sel = b.answers.get(str(qid))
            s = by_subject.setdefault(q["subject"], {"correct": 0, "wrong": 0, "skipped": 0, "score": 0})
            if sel is None:
                skipped += 1; s["skipped"] += 1
            elif sel == q["answer"]:
                correct += 1; s["correct"] += 1; s["score"] += MARK_CORRECT
            else:
                wrong += 1; s["wrong"] += 1; s["score"] += MARK_WRONG
            if sel is not None:
                c.execute("INSERT INTO attempts(user_id,question_id,selected,correct,time_taken,mode,created) VALUES(?,?,?,?,?,?,?)",
                          (u["id"], qid, sel, int(sel == q["answer"]), b.times.get(str(qid), 0), "test", time.time()))
            review.append({"id": qid, "text": q["text"], "options": json.loads(q["options"]), "selected": sel,
                           "answer": q["answer"], "explanation": q["explanation"], "subject": q["subject"],
                           "chapter": q["chapter"], "time": round(b.times.get(str(qid), 0), 1)})
        score = correct * MARK_CORRECT + wrong * MARK_WRONG
        c.execute("UPDATE tests SET submitted=1,score=?,correct=?,wrong=?,skipped=? WHERE id=?",
                  (score, correct, wrong, skipped, test_id))
    return {"score": score, "max_score": t["max_score"], "correct": correct, "wrong": wrong, "skipped": skipped,
            "by_subject": by_subject, "review": review}


# ---------------------------------------------------------------- leaderboard / analytics
@app.get("/api/leaderboard")
def leaderboard(exam: str, u: dict = Depends(auth)):
    with get_db() as c:
        rows = c.execute(
            """SELECT u.name, u.class_level, COUNT(t.id) tests, SUM(t.score) total, MAX(t.score) best
               FROM tests t JOIN users u ON u.id=t.user_id
               WHERE t.submitted=1 AND t.exam=? AND u.role='student'
               GROUP BY u.id ORDER BY total DESC LIMIT 20""", (exam,)).fetchall()
    return [{"rank": i + 1, **dict(r), "you": r["name"] == u["name"]} for i, r in enumerate(rows)]


@app.get("/api/analytics")
def analytics(exam: Optional[str] = None, u: dict = Depends(auth)):
    exam = exam or ("NEET" if u["exam_track"] == "BOTH" else u["exam_track"])
    with get_db() as c:
        subj = c.execute(
            """SELECT q.subject, COUNT(*) n, SUM(a.correct) k FROM attempts a JOIN questions q ON q.id=a.question_id
               WHERE a.user_id=? AND q.exam=? GROUP BY q.subject""", (u["id"], exam)).fetchall()
        chap = c.execute(
            """SELECT q.subject, q.chapter, COUNT(*) n, SUM(a.correct) k FROM attempts a JOIN questions q ON q.id=a.question_id
               WHERE a.user_id=? AND q.exam=? GROUP BY q.chapter""", (u["id"], exam)).fetchall()
        tests = c.execute(
            "SELECT id,mode,score,max_score,started FROM tests WHERE user_id=? AND submitted=1 AND exam=? ORDER BY id DESC LIMIT 5",
            (u["id"], exam)).fetchall()
        avg_t = c.execute(
            "SELECT AVG(a.time_taken) FROM attempts a JOIN questions q ON q.id=a.question_id WHERE a.user_id=? AND q.exam=? AND a.time_taken>0",
            (u["id"], exam)).fetchone()[0]
    total = sum(r["n"] for r in subj)
    right = sum(r["k"] or 0 for r in subj)
    weak = sorted([r for r in chap if (r["k"] or 0) / r["n"] < 0.6], key=lambda r: (r["k"] or 0) / r["n"])[:3]
    recs = [f"Revise {r['chapter']} ({r['subject']}): only {round(100 * (r['k'] or 0) / r['n'])}% correct so far" for r in weak]
    if total == 0:
        recs = ["Attempt a few questions in Learning Mode or take a test to unlock personalised insights."]
    elif not recs:
        recs = ["Great accuracy so far. Try a full exam-pattern test to build speed and stamina."]
    return {
        "exam": exam, "attempted": total, "accuracy": round(100 * right / total) if total else 0,
        "avg_time": round(avg_t or 0, 1),
        "subjects": [{"subject": r["subject"], "attempted": r["n"], "accuracy": round(100 * (r["k"] or 0) / r["n"])} for r in subj],
        "recommendations": recs,
        "tests": [dict(t) for t in tests],
    }


@app.get("/api/teacher/overview")
def teacher_overview(exam: Optional[str] = None, u: dict = Depends(teacher_only)):
    with get_db() as c:
        students = c.execute("SELECT * FROM users WHERE role='student'").fetchall()
        out = []
        for s in students:
            if exam and s["exam_track"] not in (exam, "BOTH"):
                continue
            a = c.execute("SELECT COUNT(*) n, SUM(correct) k FROM attempts WHERE user_id=?", (s["id"],)).fetchone()
            t = c.execute("SELECT COUNT(*) n, AVG(1.0*score/NULLIF(max_score,0)) p FROM tests WHERE user_id=? AND submitted=1",
                          (s["id"],)).fetchone()
            out.append({"name": s["name"], "class": s["class_level"], "track": s["exam_track"],
                        "attempted": a["n"], "accuracy": round(100 * (a["k"] or 0) / a["n"]) if a["n"] else None,
                        "tests": t["n"], "avg_score_pct": round(100 * t["p"]) if t["p"] is not None else None})
    return {"students": out, "count": len(out)}


# ---------------------------------------------------------------- AI doubt chatbot
class ChatReq(BaseModel):
    question_id: int
    message: str
    history: List[dict] = []


@app.post("/api/chat")
def chat(b: ChatReq, u: dict = Depends(auth)):
    with get_db() as c:
        q = c.execute("SELECT * FROM questions WHERE id=?", (b.question_id,)).fetchone()
    if not q:
        raise HTTPException(404, "Question not found")
    opts = json.loads(q["options"])
    system = (
        f"You are a friendly, precise {q['exam']} tutor for Class 11-12 students in India. "
        f"The student is asking about this {q['exam']} {q['subject']} question (chapter: {q['chapter']}).\n"
        f"Question: {q['text']}\nOptions: " + " | ".join(f"({chr(65 + i)}) {o}" for i, o in enumerate(opts)) +
        f"\nCorrect answer: ({chr(65 + q['answer'])}) {opts[q['answer']]}\nReference explanation: {q['explanation']}\n"
        "Explain step by step, keep it concise, align with the NCERT syllabus, and for JEE-style numerical or "
        "Mathematics questions show the working. Answer in the language the student uses. Use plain text, no markdown tables."
    )
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {"reply": "(AI offline: set ANTHROPIC_API_KEY to enable the live tutor.)\n\n" + q["explanation"], "live": False}
    msgs = [m for m in b.history[-8:] if m.get("role") in ("user", "assistant") and m.get("content")]
    msgs.append({"role": "user", "content": b.message})
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 700, "system": system, "messages": msgs},
            timeout=60,
        )
        r.raise_for_status()
        return {"reply": r.json()["content"][0]["text"], "live": True}
    except Exception as e:  # keep the demo alive if the network or key fails
        return {"reply": f"(AI temporarily unavailable: {type(e).__name__}.)\n\n" + q["explanation"], "live": False}


app.mount("/", StaticFiles(directory=FRONT, html=True), name="frontend")
