# CampusFlow LMS

A learning management system for engineering colleges (first year to final year). It covers the teacher's whole workflow, starting from the syllabus:

**syllabus → learning plan → timetable → class delivery (notes + attendance) → classwork & rubric grading → ISE / MSE / ESE exams → gradebook & CO attainment**

It connects teachers and students the way Google Classroom does: class codes, a stream, assignments, turn-in and grade return.

## Quick start

```bash

pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # optional demo data
python manage.py seed_institution --demo   # optional: FRCRCE college setup + demo students, payroll, punches
python manage.py runserver
```

Open http://127.0.0.1:8000. Demo logins: `teacher / teacher123` and students `s01`–`s12 / student123`.
After `seed_institution --demo` the super admin is `admin / admin123` (Admin console at `/admin-console/`) and demo students `demo-comp-2024-001` etc. use `student123`.
Create your own admin account with `python manage.py createsuperuser` (the Django admin site is at `/admin/`).

Run the tests with `python manage.py test`.

## Get a test URL (deploy to Render, free)

1. Sign in at [render.com](https://render.com) with GitHub.
2. Choose **New + → Blueprint** and pick this repository. Render reads `render.yaml`.
3. Click **Apply**. After a few minutes you get a public `https://campusflow-lms-….onrender.com` link with the demo data loaded.

This test setup uses SQLite on the instance disk, so data resets whenever the service redeploys or restarts. Free instances also sleep when idle. For real use, switch to PostgreSQL and object storage for uploads. A `Procfile` is included for Railway/Heroku-style hosts.

## Deploy on an OVHcloud VPS

`deploy/ovh/install.sh` installs everything on an Ubuntu 22.04/24.04 or Debian 12 VPS:
- gunicorn under systemd;
- Caddy, with an automatic Let's Encrypt certificate;
- a firewall;
- a nightly SQLite backup;
- demo and FRCRCE data on first install.

```bash
ssh ubuntu@<vps-ip>
curl -fsSL https://raw.githubusercontent.com/mayuraggarwal7/LMS/main/deploy/ovh/install.sh -o install.sh
sudo bash install.sh                          # → https://<ip-with-dashes>.sslip.io (no domain needed)
sudo DOMAIN=lms.yourcollege.edu bash install.sh   # own domain: point its A record at the VPS first
```

Options:
- `BRANCH=...` deploys a branch other than `main`.
- `REPO_URL=https://<token>@github.com/mayuraggarwal7/LMS.git` is needed if the repository is private.
- `DEMO=0` loads the FRCRCE data without demo users.

Check a deployment from any computer with `pip install requests && python deploy/smoke_test.py https://<your-host>`. It logs in as each demo role, crawls every page and checks HTTPS and access rules without changing any data.

Re-run the script to update; the database in `/var/lib/campusflow` is kept. Settings live in `/etc/campusflow.env`. Logs: `journalctl -u campusflow`.

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

### Bring your existing lesson plan
Upload the lesson plan you already submit to the department, as a Word `.doc` (Word 97–2003), `.docx`, PDF or text file. It needs a *Sr. No. / Proposed Date / Topics / Delivery Mode / CO / Actual Date / Remark* table. CampusFlow then:
- Reads the COs and the plan rows. `19/7 (2)` means two lectures that day.
- Maps `2.1`/`2.2` sub-sections to units, and puts case studies in their own unit.
- Takes holidays and MSE/ESE dates from rows such as "15/8 | Independence Day".
- Follows that order and CO mapping instead of generating a new plan.

This works both at course creation and later, from **Learning plan → Import lesson plan**.

**Learning plan → Lesson plan (dept. format)** exports the plan in the same institutional layout, as a printable page or `.docx`:
- Proposed date comes from the synced timetable.
- Actual date comes from the delivered class.
- Remark is Covered, Partially covered or Not covered.

### Terms
Each course belongs to an **academic term**: odd, even, re-exam or special exam. The term chip in the header opens **Switch term**. Dashboards, attendance and reports then show only the term you're viewing, and new courses are created in it. Administrators (staff) add terms on that page. Viewing a re-exam term also lists courses that hold re-exams in it.

### Attendance across classes
**Attendance** in the header shows, for each class, how many classes have attendance marked vs pending. It lists every pending class with **Take attendance** and **Delete** buttons, and you can delete in bulk. Deleting marks the class as not held, and the plan shifts to the next slots.

### Sudden holidays, schedule changes and supplementary days
- **Sudden holiday:** use **Attendance → Holiday / supplementary day**, or the course timetable. Pick a date and the classes it applies to. Administrators can apply it to every course in the term. That day's classes are cancelled with the reason recorded, and every affected plan shifts forward.
- **Supplementary teaching day:** for example, "Saturday follows Monday's timetable". The plan pulls forward onto it. Days after the last teaching day also count.
- **Reschedule one class:** from the class page, move it to another date and time. The original slot stays on record as "Rescheduled to …" and the new class keeps the planned topic.
- **Will the syllabus still fit?** Each course shows how many plan items are left vs how many classes are left, and warns when a holiday leaves the plan short.

### Re-exams and additional assessments
From any exam, create a **Re-exam**, or an **Additional / make-up assessment**. You can also create a make-up for an assignment.
- **Candidates are pre-selected:** students who were absent, not assessed, or below the component's pass mark.
- **Fresh paper:** a new paper is generated from the bank, avoiding questions already used.
- **It can run in the re-exam term.**
- **Marks entry lists only the registered students.**
- **How results count:** the result replaces the original score (usual for re-exams), or the better of the two is kept (usual for improvement tests). Either way it flows into the gradebook, the CIE report and the final grade, marked **RE** / **ADD**.
- **Visibility:** students see a re-exam only if they are registered for it, and see the marks only after release.

### Consolidated CIE and attendance report
**Gradebook → CIE & attendance report** has one row per student with:
- every internal item's marks (**NE** = not entered, **AB** = absent);
- the bucket marks for each component;
- total CIE;
- attendance.

Rows can be sorted by roll number, name or PRN, and the report exports to CSV.

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

## College administration (super admins)

Staff/superuser accounts get an **Admin console** with these modules.

### Institution, programmes and rules (AICTE → university → institute)
- Set up the institution, departments (with HOD) and programmes (UG/PG/PhD, intake, duration, credits, NBA status).
- Rules come in **regulations** with a scope: AICTE, the affiliating university, or the institute. For any rule, the more specific scope wins (institute > university > AICTE), and a programme-specific rule wins over a college-wide one.
- Rule keys include: minimum attendance, faculty-student ratio, cadre ratio, workload norms per designation, lateral-entry and TFWS quotas, minimum programme credits, pass percentage, maximum backlogs for promotion, maximum years to complete, PhD faculty share, class and lab batch size.
- The rules page flags any university or institute rule that is weaker than the AICTE norm. The **AICTE report** shows faculty-student ratio, cadre ratio, PhD share, intake per programme and every compliance issue.

### Students: admission to passing out
- Admit a student (CAP / institute / minority / NRI / TFWS / lateral entry; lateral entry starts in semester 3).
- Each semester gets a record: SGPA, credits, attendance and cumulative backlogs. It can be computed from the LMS gradebook or typed in.
- **Promote** applies the rules: low attendance → detained, too many backlogs → fail, otherwise pass/ATKT. A student passes out after the final semester with no backlogs.
- The student page shows a semester-by-semester timeline, CGPA, fees and campus days. Students see their own record under **My progress**.

### Teacher HR
- Teaching, lab (lab assistants and technicians) and non-teaching staff, with designation, qualification, PhD, experience and biometric id.
- Core competencies rated 1–5 per staff member. The **allocation board** suggests the best-matched faculty for each course from their competencies and spare hours.
- Workload allocation (theory, tutorial, lab, lab assistance, projects, admin) is checked against the AICTE weekly norms for each designation.
- Ratings from students, the HOD, peers and self, plus bulk import of feedback averages.

### Accounts (fees to payouts)
- Fee heads and fee structures per programme, year and admission type; concessions; TFWS waives tuition.
- Raise invoices in bulk, record payments with receipt numbers, and view each student's ledger.
- Salary structures and monthly payroll. Loss-of-pay days come from biometric presence; visiting faculty are paid per completed class hour.
- The finance dashboard shows fees billed, collected and outstanding against payroll.

### Biometric attendance
- Register devices (each gets an API key). Upload a CSV export, or have the device push to `POST /api/biometric/punches` with header `X-Device-Key` and body `{"punches":[{"biometric_id":"COMP001","timestamp":"2026-10-16T08:55:00+05:30","direction":"in"}]}`.
- People are matched by roll number / PRN / admission number (students) or biometric id / employee id (staff).
- Each course's attendance report has **Compare with biometric**. It flags students marked present who were not on campus, and students on campus who were marked absent. Staff reports show classes taught without a punch.

### FRCRCE data
`seed_institution` loads `samples/frcrce_public_data.json`: Fr. Conceicao Rodrigues College of Engineering, Bandra (Mumbai University, autonomous). It contains programmes and intake, departments and HODs, faculty, fees and academic rules gathered from public sources. The `_provenance` and `gaps` fields in the file say what was found and what still needs checking. `--demo` adds synthetic students, payments, payroll and punches, all clearly marked DEMO. The AICTE norm values in the default regulation are the commonly published ones; check them against the current AICTE Approval Process Handbook.

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
| `accounts` | User model (teacher/student role, department, year, roll no, PRN), sign-up, dashboards |
| `courses` | Course, COs, units/topics, learning plan, rubrics, enrolment; `syllabus.py` (PDF/DOCX/DOC extraction, parsing), `lessonplan.py` (lesson plan import/export), `planner.py` (plan generation), `setup.py` (auto-configuration), `terms.py` (academic terms), `ai.py` (optional Claude) |
| `delivery` | Timetable slots, holidays, supplementary days, class sessions, notes, attendance; `services.py` (sync, holidays, supplementary days, rescheduling, carry-over, capacity, ICS, stats) |
| `classroom` | Stream, assignments, submissions, rubric scores; `grading.py` (rubric⇄score, gradebook) |
| `institution` | Institution, departments, programmes, regulations and rules (AICTE/university/institute), compliance, AICTE report, `seed_institution` |
| `students` | Admission, semester records, course results, SGPA/CGPA, promotion and pass-out |
| `hr` | Staff profiles, competencies, workload allocation and norms, ratings, staffing metrics |
| `finance` | Fee structures, concessions, invoices, payments, salary structures, payroll |
| `biometric` | Devices, punches (CSV/API), presence, correlation with course attendance and teaching |
| `exams` | Assessment components, question bank, exams, marks; `services.py` (scheme, question generation, paper builder, re-exams / additional assessments), `analytics.py` (totals, CO attainment, question analysis) |
