# Heuristic Prep: NEET & JEE Exam Preparation Platform (Hackathon Prototype)

Working prototype of the VIT Vellore Hackathon proposal. One command starts the API and the web app together.

## Run it (about 2 minutes)

Requires Python 3.9+.

```bash
cd backend
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                   # optional: enables the live AI tutor (Windows: set ANTHROPIC_API_KEY=...)
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000. The database (`app.db`) is created and seeded automatically on first start.
Interactive API docs (Swagger, as promised in the proposal): http://localhost:8000/docs

Without `ANTHROPIC_API_KEY` the chatbot still answers, using the stored explanation, so the demo never breaks.

## Optional Render demo deployment

This repository includes `render.yaml` for a public demo service. Push the project to a GitHub repository, create a Render account, then choose **New + → Blueprint** and connect that repository. Render reads the deployment settings from `render.yaml`; the app will start on the assigned port and exposes `/healthz` for health checks. Add `ANTHROPIC_API_KEY` in the Render service's Environment settings only if you want live AI replies. Never commit API keys or `.env` files.

The included configuration uses Render's Free plan and a temporary SQLite database so it does not upload your local `app.db`. It is suitable only for a short demo: Free services can spin down after 15 minutes idle, and their local database is erased on spin-down, restart, or redeploy. User accounts, OTPs, and scores on that hosted demo therefore are not durable. Render persistent disks require a paid web service; choose that only if you want persistent hosted data and accept the associated cost. Use fake demo accounts, not real student records.

## What is implemented (mapped to the proposal)

| Proposal section | Prototype |
|---|---|
| 2.1 Exam track (NEET / JEE / both) | Chosen at sign-up, switchable from the header for "both" students; filters subjects, chapters, tests |
| 2.1 Learning mode | Subject and chapter-wise MCQs, instant correct/wrong + NCERT-style explanation |
| 2.1 Test mode | Exam-pattern and subject-wise tests, plus year-wise mock practice sets for 2024 and 2025; question palette, per-question time tracking, score and review |
| 2.2 AI doubt chatbot | Per-question chat with follow-ups (Claude API), aware of exam, subject, correct answer and explanation |
| 2.2 Voice assistant | Browser speech-to-text (🎤) and text-to-speech ("Speak replies") |
| 2.3 Leaderboards | Exam-wise (NEET and JEE separate), highlights you |
| 2.5 OTP login + roles | Email OTP (shown on screen in demo mode), student vs teacher roles, JWT-style bearer sessions |
| 2.5 Teacher view | Class table: attempts, accuracy, tests, average score, filtered by exam |
| 3.6 AI analytics | Subject accuracy, average time per question, weak-chapter recommendations |
| 3.2 / 3.4 Stack | Python + FastAPI, REST, Swagger. SQLite here, MySQL in production |

## Demo shortcuts

- Teacher sign-up code: `VIT2026` (change with the `TEACHER_CODE` env var)
- The leaderboard is pre-seeded with 6 demo students (3 NEET, 3 JEE)
- Sample bank: 84 questions (36 initial questions plus 48 original additions). The additions make a 2024 and a 2025 practice set for each exam. They are mock questions, not official previous-year questions.
- The app adds missing seed questions and paper fields to an existing `app.db` on startup. Keep the database to preserve your accounts and scores.

## Suggested 5-minute demo flow

1. Sign up as a student, track = **Both**. Show the exam switch in the header.
2. **Learn** → Physics → answer a question wrong → read the explanation → ask the AI "why not option A?" → use 🎤.
3. **Tests** → full test (shows timer, negative marking, palette) → submit → score and review.
4. **Dashboard** → subject bars and AI recommendations. **Leaderboard** → switch NEET/JEE.
5. Log out, sign up as a teacher with code `VIT2026` → **Teacher** view.

## Honest scope notes (say these confidently to judges)

Prototype stand-ins for production components in the proposal:

- SQLite → Amazon RDS MySQL; in-process sessions → JWT + Redis
- OTP shown on screen → Amazon SES/SNS delivery
- Chatbot grounds on the question's explanation → full RAG over NCERT embeddings (Pinecone/Qdrant)
- 84 sample questions → 30,000+ via a verified bulk CSV/Excel import
- Year-wise paper mode currently uses original mock questions. Import verified, licensed PYQs before calling these official previous-year papers.
- Not built yet: push notifications, real-time WebSocket leaderboard, JEE Advanced numerical/multi-correct formats, Celery jobs, S3/CloudFront images
