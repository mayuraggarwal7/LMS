"""Seed a college account from a JSON file of its published data (default: Fr. CRCE public data).

    python manage.py seed_institution                       # published data only
    python manage.py seed_institution --demo                # + clearly labelled synthetic students, salaries, punches
    python manage.py seed_institution --file other.json

Published data = institution, departments, programmes, faculty & lab staff (name, designation,
qualification, specialisation), FRA fees and the autonomous academic rules. Anything not
published (students, salaries, biometric logs) is only created with --demo, always marked DEMO.
"""

import json
import random
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from biometric.models import Device, Punch
from finance.models import FeeHead, FeeLine, FeeStructure, SalaryStructure
from finance.services import raise_invoice, record_payment
from hr.models import Competency, FacultyCompetency, FacultyProfile, FacultyRating, WorkloadAllocation
from notifications.models import Deadline
from institution.models import Department, Institution, Program, Regulation, Rule, create_default_aicte_regulation
from students.models import SemesterRecord, StudentProfile

DEFAULT_FILE = Path(settings.BASE_DIR) / "samples" / "frcrce_public_data.json"
TITLES = re.compile(r"^(dr|mr|mrs|ms|prof|fr|rev)\.?\s+", re.I)


def designation_of(text):
    t = (text or "").lower()
    if "principal" in t:
        return "principal"
    if "practice" in t or "visiting" in t or "adjunct" in t:
        return "adjunct"
    if "associate" in t:
        return "associate"
    if "assistant professor" in t:
        return "assistant"
    if "professor" in t:
        return "professor"
    if "technician" in t:
        return "lab_technician"
    if "lab" in t or "attendant" in t or "supporting" in t:
        return "lab_assistant"
    return "assistant"


def split_name(full):
    name = full.strip()
    while TITLES.match(name):
        name = TITLES.sub("", name, count=1)
    parts = name.split()
    return (parts[0], " ".join(parts[1:])) if parts else (full, "")


def year_of(text):
    m = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(m.group(0)) if m else None


class Command(BaseCommand):
    help = "Seed an institution account (departments, programmes, rules, staff, fees) from published data"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE))
        parser.add_argument("--demo", action="store_true", help="Add labelled synthetic students, salaries and punches")

    @transaction.atomic
    def handle(self, *args, **opts):
        path = Path(opts["file"])
        if not path.exists():
            raise CommandError(f"No data file at {path}")
        data = json.loads(path.read_text())
        inst_data = data["institution"]
        name = inst_data.get("official_name") or inst_data.get("name")
        inst, created = Institution.objects.get_or_create(name=name)
        inst.short_name = inst_data.get("short_name") or ""
        inst.address = inst_data.get("address") or ""
        inst.city = inst_data.get("city") or ""
        inst.established = inst_data.get("year_established")
        inst.management = (inst_data.get("trust_management") or "")[:200]
        inst.affiliating_university = inst_data.get("affiliating_university") or ""
        inst.autonomous_since = year_of(inst_data.get("autonomous_since"))
        inst.aicte_id = inst_data.get("aicte_permanent_id") or inst_data.get("aicte_application_no_2024_25") or ""
        naac = inst_data.get("naac_grade") or ""
        if inst_data.get("naac_cgpa"):
            naac = f"{naac} ({inst_data['naac_cgpa']})"
        inst.naac_grade = naac[:20]
        inst.website = inst_data.get("website") or ""
        inst.data_source_note = (f"Seeded from {path.name} (as of {data.get('as_of')}). "
                                 + str(data.get("_provenance") or "") + " Gaps: " + "; ".join(map(str, data.get("gaps", []))))[:4000]
        inst.save()

        self.seed_rules(inst, data)
        depts = self.seed_departments(inst, data)
        self.seed_programs(inst, data, depts)
        staff = self.seed_staff(inst, data, depts)
        self.seed_fees(inst, data)
        self.stdout.write(self.style.SUCCESS(
            f"{inst.name}: {inst.departments.count()} departments, {inst.programs.count()} programmes, "
            f"{len(staff)} staff, {Rule.objects.filter(regulation__institution=inst).count()} rules, "
            f"{FeeStructure.objects.filter(program__institution=inst).count()} fee structures."))
        if opts["demo"]:
            self.seed_demo(inst, staff)

    # ------------------------------------------------------------------------------------
    def seed_rules(self, inst, data):
        create_default_aicte_regulation(inst)
        rules = data.get("academic_rules") or {}
        uni = inst.affiliating_university or "Affiliating university"
        reg_u, _ = Regulation.objects.get_or_create(
            institution=inst, scope=Regulation.UNIVERSITY, name=f"{uni} ordinances",
            defaults={"reference": "Applies to batches admitted before autonomy"})
        Rule.objects.get_or_create(regulation=reg_u, key="min_attendance_percent",
                                   defaults={"value": Decimal("75"), "note": "University attendance requirement"})
        reg_i, _ = Regulation.objects.get_or_create(
            institution=inst, scope=Regulation.INSTITUTE, name=(rules.get("scheme") or "Autonomous academic rules")[:200],
            defaults={"effective_from": year_of(inst_data_autonomy(data)), "reference": inst.website})
        att = rules.get("attendance_requirement_percent")
        if att:
            Rule.objects.get_or_create(regulation=reg_i, key="min_attendance_percent",
                                       defaults={"value": Decimal(str(att)), "note": (rules.get("attendance_rules") or "")[:300]})
        passing = rules.get("passing_criteria") or ""
        m = re.search(r"(\d{2})%\s*\(?UG", passing)
        if m:
            Rule.objects.get_or_create(regulation=reg_i, key="pass_percent_component", program=None,
                                       defaults={"value": Decimal(m.group(1)), "note": passing[:300]})
        self.reg_institute = reg_i
        self.pg_pass = re.search(r"(\d{2})%\s*\(?PG", passing)

    def seed_departments(self, inst, data):
        depts = {}
        for d in data.get("departments", []):
            dept, _ = Department.objects.update_or_create(
                institution=inst, code=d["code"],
                defaults={"name": d["name"], "is_first_year": d["code"] in ("HS", "FE") or "humanities" in d["name"].lower()})
            depts[d["code"]] = (dept, d)
        return depts

    def seed_programs(self, inst, data, depts):
        by_name = {dept.name.lower(): dept for dept, _ in depts.values()}
        nba_lookup = {n.get("program", "").lower(): n for n in (data["institution"].get("nba_accredited_programs") or [])}
        for p in data.get("programs", []):
            level = {"UG": Program.UG, "PG": Program.PG}.get(p.get("level"), Program.PHD)
            degree = "B.Tech" if "b.tech" in p["name"].lower() else ("B.E." if "b.e." in p["name"].lower() else
                                                                    ("M.Tech" if level == Program.PG else ("Ph.D." if level == Program.PHD else "B.E.")))
            clean = re.sub(r"^(B\.Tech|B\.E\.|M\.Tech|M\.E\.)\s*", "", p["name"]).replace(" (legacy)", "")
            nba = None
            if level == Program.UG:  # NBA entries are for the UG programmes
                nba = nba_lookup.get(p["name"].lower()) or next((v for k, v in nba_lookup.items() if clean.lower() in k), None)
            accredited = bool(nba and nba.get("status") == "Accredited") or (
                level == Program.UG and str(p.get("nba") or "").lower().startswith("accredited"))
            program, _ = Program.objects.update_or_create(
                institution=inst, code=p["code"],
                defaults={"name": clean, "level": level, "degree": degree, "department": by_name.get((p.get("department") or "").lower()),
                          "duration_years": p.get("duration_years") or (4 if level == Program.UG else 2 if level == Program.PG else 3),
                          "intake": p.get("intake") or 0, "year_started": year_of(p.get("year_started")),
                          "nba_accredited": accredited, "nba_valid_till": year_of(nba.get("valid_upto")) if nba else None,
                          "active": p.get("status") not in ("unclear", "unconfirmed") and bool(p.get("intake"))})
            if level == Program.PG and self.pg_pass:
                Rule.objects.get_or_create(regulation=self.reg_institute, key="pass_percent_component", program=program,
                                           defaults={"value": Decimal(self.pg_pass.group(1)), "note": "PG passing criterion"})

    def seed_staff(self, inst, data, depts):
        staff = []
        counters = {}
        principal_name = ((data["institution"].get("leadership") or {}) if isinstance(data["institution"].get("leadership"), dict) else {}).get("principal")
        for code, (dept, d) in depts.items():
            people = [(f, "teaching") for f in d.get("faculty", [])] + [(s, s.get("staff_type") or "lab") for s in d.get("staff", [])]
            for person, kind in people:
                if not person.get("name"):
                    continue
                first, last = split_name(person["name"])
                username = slugify(f"{first}.{last}").replace("-", ".")[:140] or slugify(person["name"])
                user, made = User.objects.get_or_create(
                    username=username, defaults={"first_name": first[:150], "last_name": last[:150], "role": User.TEACHER,
                                                 "department": dept.name})
                if made:
                    user.set_unusable_password()
                    user.save()
                if hasattr(user, "faculty_profile"):
                    staff.append(user.faculty_profile)
                    continue
                counters[code] = counters.get(code, 0) + 1
                staff_type = {"teaching": FacultyProfile.TEACHING, "lab": FacultyProfile.LAB}.get(kind, FacultyProfile.NON_TEACHING)
                desig = designation_of(person.get("designation")) if staff_type == FacultyProfile.TEACHING else (
                    designation_of(person.get("designation")) if person.get("designation") else ("lab_assistant" if staff_type == FacultyProfile.LAB else "admin"))
                qual = person.get("qualification") or ""
                fp = FacultyProfile.objects.create(
                    user=user, employee_id=f"{inst.short_name.replace('.', '').replace(' ', '') or 'EMP'}-{code}-{counters[code]:03d}",
                    department=dept, staff_type=staff_type, designation=desig,
                    employment=FacultyProfile.VISITING if desig == "adjunct" else FacultyProfile.PERMANENT,
                    qualification=qual[:120], has_phd=("ph.d" in qual.lower() and "pursuing" not in qual.lower()),
                    specialization=person.get("specialization") or "",
                    experience_years=person.get("experience_years"), biometric_id=f"{code}{counters[code]:03d}")
                for comp in re.split(r",|;|/| and ", person.get("specialization") or ""):
                    comp = comp.strip(" .").capitalize()
                    if 2 < len(comp) < 120:
                        c, _ = Competency.objects.get_or_create(name=comp, defaults={"area": Competency.SUBJECT})
                        FacultyCompetency.objects.get_or_create(faculty=fp, competency=c, defaults={"level": 3, "evidence": "Listed specialisation"})
                staff.append(fp)
            # the department's published HOD (deputy / program-coordinator titles are not the HOD)
            hod_name = d.get("hod")
            hod = None
            if hod_name:
                first, last = split_name(hod_name)
                hod = User.objects.filter(first_name=first, last_name=last).first()
            if hod is None and hod_name:  # published HOD not in the scraped faculty list: add them
                first, last = split_name(hod_name)
                hod = User.objects.create(username=slugify(f"{first}.{last}").replace("-", "."), first_name=first, last_name=last,
                                          role=User.TEACHER, department=dept.name)
                hod.set_unusable_password()
                hod.save()
                counters[code] = counters.get(code, 0) + 1
                staff.append(FacultyProfile.objects.create(
                    user=hod, employee_id=f"{inst.short_name.replace('.', '').replace(' ', '') or 'EMP'}-{code}-{counters[code]:03d}",
                    department=dept, designation="professor", qualification="Ph.D." if hod_name.startswith("Dr") else "",
                    has_phd=hod_name.startswith("Dr"), biometric_id=f"{code}{counters[code]:03d}"))
            if hod is not None:
                dept.hod = hod
                dept.save(update_fields=["hod"])
        if principal_name:
            first, last = split_name(principal_name)
            u = User.objects.filter(first_name=first, last_name=last).first()
            if u and hasattr(u, "faculty_profile"):
                u.faculty_profile.designation = "principal"
                u.faculty_profile.save(update_fields=["designation"])
        return staff

    def seed_fees(self, inst, data):
        fees = data.get("fees") or {}
        tuition = FeeHead.objects.get_or_create(name="Tuition fee", defaults={"waivable_under_tfws": True, "order": 1})[0]
        dev = FeeHead.objects.get_or_create(name="Development fee", defaults={"order": 2})[0]
        total_head = FeeHead.objects.get_or_create(name="FRA sanctioned fee (break-up not published)", defaults={"order": 3})[0]
        approved = fees.get("fra_body") or ""
        ug = list(inst.programs.filter(level=Program.UG, active=True))
        for entry in fees.get("entries", []):
            ay = entry.get("academic_year")
            if not ay:
                continue
            years = [1] if "first year" in (entry.get("program") or "").lower() else [1, 2, 3, 4]
            for prog in ug:
                for y in years:
                    fs, created = FeeStructure.objects.get_or_create(program=prog, academic_year=ay, year_of_study=y, category="",
                                                                    defaults={"approved_by": approved[:120]})
                    if not created:
                        continue
                    if entry.get("tuition_fee"):
                        FeeLine.objects.create(structure=fs, head=tuition, amount=Decimal(str(entry["tuition_fee"])))
                        if entry.get("development_fee"):
                            FeeLine.objects.create(structure=fs, head=dev, amount=Decimal(str(entry["development_fee"])))
                    elif entry.get("total"):
                        FeeLine.objects.create(structure=fs, head=total_head, amount=Decimal(str(entry["total"])))

    # ------------------------------------------------------------------------------------
    def seed_demo(self, inst, staff):
        """Synthetic, clearly labelled data so every module has something to show."""
        rng = random.Random(42)
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.edu", "admin123", first_name="Super", last_name="Admin",
                                          role=User.TEACHER)
        reg = Regulation.objects.filter(institution=inst, scope=Regulation.INSTITUTE).first()
        if reg:
            Rule.objects.get_or_create(regulation=reg, key="max_backlogs_for_promotion",
                                       defaults={"value": Decimal("4"), "note": "DEMO value - full ATKT rule not published"})
        today = timezone.localdate()
        ay_start = today.year if today.month >= 6 else today.year - 1
        academic_year = f"{ay_start}-{str(ay_start + 1)[2:]}"
        made = 0
        for prog in inst.programs.filter(level=Program.UG, active=True):
            for year in (1, 2, 3, 4):
                for i in range(1, 7):
                    adm_year = ay_start - (year - 1)
                    adm_no = f"DEMO-{prog.code}-{adm_year}-{i:03d}"
                    if StudentProfile.objects.filter(admission_no=adm_no).exists():
                        continue
                    u = User.objects.create_user(
                        username=adm_no.lower(), password="student123", first_name="Demo", last_name=f"{prog.code} Y{year} #{i}",
                        role=User.STUDENT, roll_no=f"{prog.code[:2]}{adm_year % 100}{i:02d}", prn=f"DEMO{adm_year}{prog.pk:02d}{i:04d}",
                        year_of_study=year, department=prog.department.name if prog.department else "")
                    sem = 2 * year - 1
                    lateral = year >= 2 and i == 6
                    sp = StudentProfile.objects.create(
                        user=u, program=prog, admission_no=adm_no, admission_year=adm_year + (1 if lateral else 0),
                        admission_type=StudentProfile.LATERAL if lateral else rng.choice([StudentProfile.CAP] * 4 + [StudentProfile.TFWS, StudentProfile.INSTITUTE]),
                        category=rng.choice(["Open", "Open", "OBC", "SC", "EWS"]), entry_semester=3 if lateral else 1,
                        current_semester=sem, division="A", qualifying_exam="Diploma" if lateral else "MHT-CET",
                        remarks="DEMO student - synthetic record")
                    skill = rng.uniform(5.8, 9.4)
                    backlogs = 0
                    for s in range(sp.entry_semester, sem):
                        sgpa = Decimal(str(round(min(10, max(4, rng.gauss(skill, 0.6))), 2)))
                        fail = 1 if sgpa < 5.2 and rng.random() < 0.6 else 0
                        backlogs = max(0, backlogs + fail - (1 if backlogs and rng.random() < 0.7 else 0))
                        SemesterRecord.objects.create(
                            student=sp, semester=s, academic_year=f"{adm_year + (s - 1) // 2}-{str(adm_year + (s - 1) // 2 + 1)[2:]}",
                            sgpa=sgpa, credits_attempted=Decimal("20"), credits_earned=Decimal("20") - 4 * fail,
                            attendance_percent=Decimal(str(round(rng.uniform(76, 97), 1))), backlogs=backlogs,
                            result=SemesterRecord.ATKT if backlogs else SemesterRecord.PASS, source="demo", remarks="DEMO")
                    SemesterRecord.objects.create(student=sp, semester=sem, academic_year=academic_year,
                                                  result=SemesterRecord.IN_PROGRESS, source="demo", backlogs=backlogs)
                    inv = raise_invoice(sp, academic_year, due_date=date(ay_start, 8, 31))
                    if inv and inv.amount:
                        share = rng.choice([1, 1, 1, 0.5, 0])
                        if share:
                            record_payment(inv, (inv.amount * Decimal(str(share))).quantize(Decimal("1")), date(ay_start, 8, rng.randint(1, 28)),
                                           mode=rng.choice(["upi", "neft", "card"]), reference="DEMO")
                    made += 1
        # demo salary structures (illustrative 7th-pay style figures, not the college's)
        for fp in staff:
            if fp.salary_structures.exists():
                continue
            basic = {"principal": 144200, "professor": 144200, "associate": 131400, "assistant": 57700,
                     "lab_technician": 25500, "lab_assistant": 21700, "adjunct": 0}.get(fp.designation, 25500)
            SalaryStructure.objects.create(faculty=fp, effective_from=date(ay_start, 6, 1), basic=Decimal(basic),
                                           da_percent=Decimal("46"), hra_percent=Decimal("10"), professional_tax=Decimal("200"),
                                           hourly_rate=Decimal("1500") if fp.designation == "adjunct" else Decimal("0"))
        # demo workload, ratings and biometric punches for the current month
        device, _ = Device.objects.get_or_create(name="Main gate (DEMO)", defaults={"location": "Main gate", "vendor": "Demo"})
        for fp in staff:
            if fp.staff_type == FacultyProfile.TEACHING and not fp.allocations.exists():
                hours = {"professor": 12, "principal": 6, "associate": 15, "assistant": 17, "adjunct": 4}.get(fp.designation, 16)
                WorkloadAllocation.objects.create(faculty=fp, description="DEMO theory load", role=WorkloadAllocation.THEORY,
                                                  hours_per_week=Decimal(hours - 4 if hours > 4 else hours))
                if hours > 4:
                    WorkloadAllocation.objects.create(faculty=fp, description="DEMO lab load", role=WorkloadAllocation.LAB, hours_per_week=Decimal(4))
                FacultyRating.objects.create(faculty=fp, source="students", score=Decimal(str(round(rng.uniform(3.4, 4.8), 2))),
                                             responses=rng.randint(40, 120), comment="DEMO feedback average")
            elif fp.staff_type == FacultyProfile.LAB and not fp.allocations.exists():
                WorkloadAllocation.objects.create(faculty=fp, description="DEMO lab assistance", role=WorkloadAllocation.LAB_ASSIST,
                                                  hours_per_week=Decimal(30))
        tz = timezone.get_current_timezone()
        start = today.replace(day=1)
        people = [(fp.user, fp.biometric_id) for fp in staff] + [
            (sp.user, sp.admission_no) for sp in StudentProfile.objects.filter(admission_no__startswith="DEMO-")[:60]]
        day = start
        if Punch.objects.filter(source="demo", timestamp__date__gte=start).exists():
            day = today + timedelta(days=1)  # already seeded this month: keep re-runs idempotent
        while day <= today:
            if day.weekday() < 6:
                for user, bid in people:
                    if rng.random() < 0.9:
                        t_in = datetime.combine(day, time(8, rng.randint(0, 50)), tzinfo=tz)
                        t_out = datetime.combine(day, time(16, rng.randint(0, 59)), tzinfo=tz)
                        for ts, direction in ((t_in, "in"), (t_out, "out")):
                            Punch.objects.get_or_create(biometric_id=bid, timestamp=ts, device=device,
                                                        defaults={"person": user, "direction": direction, "source": "demo"})
            day += timedelta(days=1)
        # demo deadlines timetable for the notification centre
        def at(days, hour=17):
            return datetime.combine(today + timedelta(days=days), time(hour, 0), tzinfo=tz)
        admin_user = User.objects.filter(username="admin").first()
        for title, kind, comp, due, remind, audience, desc in [
            ("Submit MSE question papers (DEMO)", Deadline.PAPER, "MSE", at(4), 7, Deadline.TEACHERS,
             "Upload the paper and set the exam to Scheduled."),
            ("Attendance up to date for the week (DEMO)", Deadline.ATTENDANCE, "", at(-1, 18), 2, Deadline.TEACHERS, ""),
            ("Release ISE-1 marks (DEMO)", Deadline.MARKS, "ISE", at(10), 5, Deadline.TEACHERS, ""),
            ("Submit ESE question papers (DEMO)", Deadline.PAPER, "ESE", at(30), 10, Deadline.TEACHERS, ""),
            ("Submit course file to HOD (DEMO)", Deadline.CUSTOM, "", at(6), 7, Deadline.ALL_STAFF,
             "Lesson plan, CO-PO mapping, question papers and result analysis."),
            ("ESE exam form submission (DEMO)", Deadline.CUSTOM, "", at(12), 10, Deadline.STUDENTS,
             "Fill in the exam form on the university portal and submit the receipt at the exam cell."),
            ("Diwali vacation 2-7 November (DEMO)", Deadline.NOTICE, "", None, 0, Deadline.EVERYONE,
             "The college is closed; classes resume on Monday."),
        ]:
            Deadline.objects.get_or_create(title=title, defaults={
                "kind": kind, "component_kind": comp, "due_at": due, "remind_days": remind, "audience": audience,
                "description": desc, "created_by": admin_user})
        self.stdout.write(self.style.SUCCESS("DEMO super admin: admin / admin123"))
        self.stdout.write(self.style.SUCCESS(f"DEMO: {made} synthetic students with semester history, invoices and payments; "
                                             f"salary structures, workload, ratings and {Punch.objects.filter(source='demo').count()} punches."))


def inst_data_autonomy(data):
    return (data.get("institution") or {}).get("autonomous_since")
