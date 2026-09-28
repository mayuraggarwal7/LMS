# CampusFlow LMS

A learning management system for engineering colleges (first year to final year). It covers the teacher's whole workflow, starting from the syllabus:

**syllabus → learning plan → timetable → class delivery (notes + attendance) → classwork & rubric grading → ISE / MSE / ESE exams → gradebook & CO attainment**

It connects teachers and students the way Google Classroom does: class codes, a stream, assignments, turn-in and grade return.

## Quick start

```bash

pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # optional demo data
python manage.py runserver
```

Open http://127.0.0.1:8000. Demo logins: `teacher / teacher123` and students `s01`–`s12 / student123`.
Create an admin account with `python manage.py createsuperuser` (the admin site is at `/admin/`).

Run the tests with `python manage.py test`.

## What the teacher does and what the system does

| Step | Teacher | CampusFlow |
|---|---|---|
| 1. Upload syllabus | Drop in a PDF, DOCX or text file (or paste it), then pick the year, semester and dates | Pulls out the course code, title, credits, L-T-P, course outcomes, units with hours and topics, textbooks and lab experiments |
| 2. Review | Fix anything in a simple text editor view | Builds the course in one click: learning plan, assessment scheme, rubric library, question bank, draft assignments per unit, and draft MSE/ESE papers |
| 3. Timetable | Add weekly slots, semester dates and holidays | **Sync** creates every dated class and places the plan on it in order. Cancelling a class moves the plan forward. Delivered classes are never changed. Exports an `.ics` calendar |
| 4. Teach | Open "Take class", write notes, upload slides, take attendance (one-tap all present, or open a 6-digit code so students check in themselves) | Tracks planned vs delivered sessions and pace (how far behind). If a topic was only partly covered, the rest carries over to the next class |
| 5. Classwork | Publish assignments, grade with rubric levels or type a total | Rubric scores roll up to the total. A typed or CSV-imported total is split back across the rubric criteria, so both directions work |
| 6. Exams | Adjust the ISE/MSE/TW/ESE weights and generate papers from a blueprint | Builds papers from the question bank, balancing units, Bloom levels and COs and avoiding questions used before. Marks go in per question or as a total (the total is split across questions). Supports CSV import, absentees and releasing marks |
| 7. Results | Check the gradebook and CO attainment, export CSV | Rolls scores up into weighted components (average, sum or best-N), a final %, and a 10-point grade with component minimum-pass rules. Computes NBA-style CO attainment levels (internal vs ESE) and per-question analysis |

### Tuned to the year of study
The learning plan climbs Bloom's levels within each unit, up to each CO's level. Teaching methods change with the year:
- **First year:** worked examples, demos, peer instruction.
- **Second year:** think-pair-share, flipped classroom.
- **Third year:** PBL (problem-based learning), simulation, jigsaw.
- **Final year:** case studies, project-based learning, research-paper discussion. Final-year courses also get a mini-project with its own rubric.

### Digital tools in each session
Each plan item suggests tools that match its topic, for example:
- Colab/Jupyter, Python Tutor, GitHub Classroom
- MATLAB/Simulink, Falstad, LTspice, Tinkercad, Wokwi
- Packet Tracer, PhET, Virtual Labs (vlab.co.in), GeoGebra, Fusion 360
- plus an engagement tool: Mentimeter, Kahoot or Padlet

### For students
- Join a class with its code.
- See to-dos and missing work.
- Turn in text, files or links, and unsubmit before grading.
- See returned grades with the rubric breakdown and feedback.
- Check in to class with a code and track attendance %.
- Read shared class notes.
- See a running grade and their own CO profile. Unreleased exam marks and draft grades are hidden.

## AI assistance (optional)

Set `ANTHROPIC_API_KEY` to have Claude parse unusual syllabus layouts and write exam questions with marking schemes. The results use structured JSON output and are checked before use. The default model is `claude-opus-5` (change it with `LMS_AI_MODEL`). Server-side refusal fallback is turned on. Without a key, or if a call fails, the built-in parser and template question generator are used, so every feature works offline.

## Configuration

`campusflow/settings.py` reads these environment variables:
- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
- `LMS_TIME_ZONE` (default `Asia/Kolkata`)

It also holds these LMS settings:
- attendance threshold (75%)
- CO attainment level bands (70/60/50)
- ESE weight in overall CO attainment (0.6)

The default assessment schemes are in `exams/services.py`:
- **Theory courses:** ISE-1 10%, MSE 20%, ISE-2 10%, ESE 60%.
- **Courses with a lab:** ISE-1 10%, MSE 20%, ISE-2 10%, TW 10%, ESE 50%.

The ESE and TW minimum pass is 40%. Each course can change these on its **Exams → Assessment scheme** page.

## Code map

| App | Responsibility |
|---|---|
| `accounts` | User model (teacher/student role, department, year, roll no), sign-up, dashboards |
| `courses` | Course, COs, units/topics, learning plan, rubrics, enrolment; `syllabus.py` (extract/parse), `planner.py` (plan generation), `setup.py` (auto-configuration), `ai.py` (optional Claude) |
| `delivery` | Timetable slots, holidays, class sessions, notes, attendance; `services.py` (sync, carry-over, ICS, stats) |
| `classroom` | Stream, assignments, submissions, rubric scores; `grading.py` (rubric⇄score, gradebook) |
| `exams` | Assessment components, question bank, exams, marks; `services.py` (scheme, question generation, paper builder), `analytics.py` (totals, CO attainment, question analysis) |
