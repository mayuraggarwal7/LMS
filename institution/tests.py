import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from biometric.models import Device, Punch
from biometric.services import course_correlation, ingest_csv, staff_attendance
from classroom import grading
from classroom.models import Assignment
from courses.models import Course, Enrollment
from delivery.models import AttendanceRecord, ClassSession
from exams.services import create_default_scheme
from finance.models import Concession, FeeHead, FeeLine, FeeStructure, SalaryStructure
from finance.services import raise_invoice, record_payment, run_payroll
from hr.models import Competency, FacultyCompetency, FacultyProfile, WorkloadAllocation
from hr.services import staffing_metrics, suggest_faculty, workload
from institution.models import (Department, Institution, Program, Regulation, Rule, compliance,
                                create_default_aicte_regulation, effective_rule, rule_value)
from students.models import SemesterRecord, StudentProfile
from students.services import compute_semester, promote, timeline


def make_institution():
    inst = Institution.objects.create(name="Test College", short_name="TC", affiliating_university="Test University")
    create_default_aicte_regulation(inst)
    dept = Department.objects.create(institution=inst, name="Computer Engineering", code="COMP")
    prog = Program.objects.create(institution=inst, department=dept, name="Computer Engineering", code="COMP", intake=60)
    return inst, dept, prog


def make_student(prog, n=1, sem=1, **kw):
    u = User.objects.create_user(f"stu{n}", password="pw123456", role=User.STUDENT, roll_no=f"R{n}", first_name=f"S{n}")
    return StudentProfile.objects.create(user=u, program=prog, admission_no=f"ADM{n}", admission_year=2024,
                                         current_semester=sem, entry_semester=kw.pop("entry", 1), **kw)


def make_faculty(dept, n, designation="assistant", staff_type=FacultyProfile.TEACHING, **kw):
    u = User.objects.create_user(f"fac{n}", password="pw123456", role=User.TEACHER, first_name=f"F{n}")
    return FacultyProfile.objects.create(user=u, employee_id=f"E{n}", department=dept, designation=designation,
                                         staff_type=staff_type, biometric_id=f"B{n}", **kw)


class RuleFrameworkTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()

    def test_precedence_institute_over_university_over_aicte(self):
        self.assertEqual(rule_value("min_attendance_percent", self.prog), Decimal("75"))
        uni = Regulation.objects.create(institution=self.inst, scope=Regulation.UNIVERSITY, name="MU")
        Rule.objects.create(regulation=uni, key="min_attendance_percent", value=Decimal("80"))
        inst_reg = Regulation.objects.create(institution=self.inst, scope=Regulation.INSTITUTE, name="R2024")
        Rule.objects.create(regulation=inst_reg, key="min_attendance_percent", value=Decimal("85"), program=self.prog)
        self.assertEqual(rule_value("min_attendance_percent", self.prog), Decimal("85"))
        other = Program.objects.create(institution=self.inst, name="Mech", code="MECH")
        self.assertEqual(rule_value("min_attendance_percent", other), Decimal("80"))
        self.assertEqual(effective_rule("min_attendance_percent", other).regulation.scope, Regulation.UNIVERSITY)

    def test_compliance_flags_weaker_than_aicte(self):
        reg = Regulation.objects.create(institution=self.inst, scope=Regulation.INSTITUTE, name="Lax rules")
        Rule.objects.create(regulation=reg, key="min_attendance_percent", value=Decimal("60"))  # weaker (min)
        Rule.objects.create(regulation=reg, key="faculty_student_ratio", value=Decimal("15"))  # stricter (max)
        issues = compliance(self.inst)
        self.assertEqual([i["rule"].key for i in issues], ["min_attendance_percent"])


class StudentLifecycleTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()
        self.teacher = User.objects.create_user("t", password="pw123456", role=User.TEACHER)
        self.course = Course.objects.create(teacher=self.teacher, code="CS201", title="Data Structures", semester=3,
                                            credits=Decimal("4"), program=self.prog)
        create_default_scheme(self.course)
        self.assignment = Assignment.objects.create(course=self.course, title="A1", max_points=Decimal("10"), status=Assignment.PUBLISHED,
                                                    component=self.course.components.get(name="ISE-1"))

    def test_compute_semester_from_lms_grades_and_promote_to_pass_out(self):
        sp = make_student(self.prog, sem=3, entry=3, admission_type=StudentProfile.LATERAL)
        Enrollment.objects.create(course=self.course, student=sp.user)
        grading.set_total_score(grading.get_or_create_submission(self.assignment, sp.user), Decimal("8.5"))  # 85% -> A+ (9)
        rec = compute_semester(sp, 3, "2025-26")
        self.assertEqual(rec.sgpa, Decimal("9.00"))
        self.assertEqual(rec.credits_earned, Decimal("4"))
        self.assertEqual(rec.result, SemesterRecord.PASS)
        self.assertEqual(rec.courses.get().grade, "A+")
        self.assertEqual([c["semester"] for c in timeline(sp)], [3, 4, 5, 6, 7, 8])  # lateral entry starts at sem 3
        self.assertIn("promoted to semester 4", promote(sp))
        for s in range(4, 9):  # remaining semesters passed
            SemesterRecord.objects.update_or_create(student=sp, semester=s, defaults={
                "sgpa": Decimal("8"), "credits_attempted": 20, "credits_earned": 20, "result": SemesterRecord.PASS, "academic_year": "2027-28"})
            promote(sp)
        sp.refresh_from_db()
        self.assertEqual(sp.status, StudentProfile.PASSED_OUT)
        self.assertEqual(sp.passed_out_year, 2028)
        self.assertEqual(sp.cgpa(), Decimal("8.04"))  # (9*4 + 8*100) / 104

    def test_detention_and_atkt_follow_rules(self):
        reg = Regulation.objects.create(institution=self.inst, scope=Regulation.INSTITUTE, name="R")
        Rule.objects.create(regulation=reg, key="max_backlogs_for_promotion", value=Decimal("0"))
        sp = make_student(self.prog, sem=3)
        Enrollment.objects.create(course=self.course, student=sp.user)
        grading.set_total_score(grading.get_or_create_submission(self.assignment, sp.user), Decimal("2"))  # F
        rec = compute_semester(sp, 3)
        self.assertEqual(rec.backlogs, 1)
        self.assertEqual(rec.result, SemesterRecord.FAIL)  # more backlogs than the rule allows
        self.assertIn("not promoted", promote(sp))
        rec.attendance_percent = Decimal("60")
        rec.save()
        from students.services import decide_result
        self.assertEqual(decide_result(sp, rec), SemesterRecord.DETAINED)


class HRTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()

    def test_workload_norm_from_aicte_rule_and_suggestion(self):
        prof = make_faculty(self.dept, 1, designation="professor")
        asst = make_faculty(self.dept, 2, designation="assistant")
        WorkloadAllocation.objects.create(faculty=prof, role=WorkloadAllocation.THEORY, hours_per_week=Decimal("15"))
        load = workload(prof)
        self.assertEqual(load["norm"], Decimal("14"))
        self.assertEqual(load["status"], "over")
        self.assertEqual(workload(asst)["norm"], Decimal("18"))
        ml = Competency.objects.create(name="Machine learning")
        FacultyCompetency.objects.create(faculty=asst, competency=ml, level=5)
        course = Course.objects.create(teacher=User.objects.create_user("t", password="x", role=User.TEACHER),
                                       code="ML1", title="Machine Learning", department="Computer Engineering")
        ranked = suggest_faculty(course)
        self.assertEqual(ranked[0]["faculty"], asst)  # professor is over load -> excluded, assistant matches ML
        self.assertNotIn(prof, [r["faculty"] for r in ranked])

    def test_staffing_metrics_cadre_and_ratio(self):
        make_faculty(self.dept, 1, designation="professor", has_phd=True)
        make_faculty(self.dept, 2, designation="associate")
        make_faculty(self.dept, 3, designation="assistant")
        make_faculty(self.dept, 4, staff_type=FacultyProfile.LAB, designation="lab_assistant")
        m = staffing_metrics(self.inst)
        self.assertEqual(m["teaching"], 3)
        self.assertEqual(m["lab_staff"], 1)
        self.assertEqual(m["cadre"], {"professor": 1, "associate": 1, "assistant": 1})
        self.assertEqual(m["sanctioned_strength"], 240)
        self.assertEqual(m["required_faculty"], 12)
        self.assertAlmostEqual(m["phd_percent"], 33.3)


class FinanceTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()
        self.tuition = FeeHead.objects.create(name="Tuition fee", waivable_under_tfws=True)
        self.dev = FeeHead.objects.create(name="Development fee")
        fs = FeeStructure.objects.create(program=self.prog, academic_year="2026-27", year_of_study=1)
        FeeLine.objects.create(structure=fs, head=self.tuition, amount=Decimal("173043"))
        FeeLine.objects.create(structure=fs, head=self.dev, amount=Decimal("25957"))

    def test_invoice_concessions_and_payments(self):
        tfws = make_student(self.prog, 1, admission_type=StudentProfile.TFWS)
        inv = raise_invoice(tfws, "2026-27")
        self.assertEqual(inv.gross, Decimal("199000"))
        self.assertEqual(inv.amount, Decimal("25957"))  # tuition waived under TFWS
        regular = make_student(self.prog, 2)
        Concession.objects.create(student=regular, academic_year="2026-27", head=self.dev, percent=Decimal("50"), reason="Merit")
        inv2 = raise_invoice(regular, "2026-27")
        self.assertEqual(inv2.amount, Decimal("199000") - Decimal("12978.50"))
        p1 = record_payment(inv2, Decimal("100000"), date(2026, 8, 1))
        p2 = record_payment(inv2, Decimal("1000"), date(2026, 8, 2))
        self.assertEqual(p1.receipt_no, "R202608-00001")
        self.assertEqual(p2.receipt_no, "R202608-00002")
        with self.assertRaises(ValueError):
            record_payment(inv2, inv2.balance + 1)

    def test_payroll_uses_biometric_days(self):
        fp = make_faculty(self.dept, 1)
        SalaryStructure.objects.create(faculty=fp, effective_from=date(2026, 1, 1), basic=Decimal("60000"), da_percent=Decimal("50"),
                                       hra_percent=Decimal("10"), pf_percent=Decimal("12"), professional_tax=Decimal("200"))
        tz = timezone.get_current_timezone()
        for d in range(1, 21):  # 20 days punched in September
            Punch.objects.create(person=fp.user, biometric_id="B1", timestamp=datetime(2026, 9, d, 9, 0, tzinfo=tz))
        run = run_payroll(date(2026, 9, 1), working_days=25, leaves={fp.pk: 2})
        slip = run.payslips.get(faculty=fp)
        self.assertEqual(slip.days_present, 20)
        self.assertEqual(slip.lop_days, 3)  # 25 - 20 - 2 leave
        full = Decimal("60000") * Decimal("1.6")
        self.assertEqual(slip.gross, (full - full / 25 * 3).quantize(Decimal("0.01")))
        self.assertEqual(slip.deductions, Decimal("10800.00") + Decimal("200"))  # 12% of basic+DA 90000 + PT

    def test_visiting_faculty_paid_per_hour(self):
        fp = make_faculty(self.dept, 2, designation="adjunct", employment=FacultyProfile.VISITING)
        SalaryStructure.objects.create(faculty=fp, effective_from=date(2026, 1, 1), hourly_rate=Decimal("1500"))
        course = Course.objects.create(teacher=fp.user, code="X1", title="Elective")
        for d in (3, 10, 17):
            ClassSession.objects.create(course=course, date=date(2026, 9, d), start_time=time(10), end_time=time(11),
                                        status=ClassSession.COMPLETED)
        slip = run_payroll(date(2026, 9, 1)).payslips.get(faculty=fp)
        self.assertEqual(slip.gross, Decimal("4500.00"))


class BiometricTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()
        self.fac = make_faculty(self.dept, 1)
        self.course = Course.objects.create(teacher=self.fac.user, code="C1", title="Course")
        self.s1 = make_student(self.prog, 1)
        self.s2 = make_student(self.prog, 2)
        for s in (self.s1, self.s2):
            Enrollment.objects.create(course=self.course, student=s.user)
        self.session = ClassSession.objects.create(course=self.course, date=date(2026, 9, 7), start_time=time(10), end_time=time(11),
                                                   status=ClassSession.COMPLETED)
        AttendanceRecord.objects.create(session=self.session, student=self.s1.user, status="P")
        AttendanceRecord.objects.create(session=self.session, student=self.s2.user, status="A")

    def test_csv_ingest_matches_people_and_correlates(self):
        device = Device.objects.create(name="Gate")
        csv_text = ("Enroll No,Date,Time,State\n"
                    "R2,07-09-2026,09:40:00,0\n"      # s2 on campus during class (but marked absent)
                    "R2,07-09-2026,15:00:00,1\n"
                    "B1,07-09-2026,09:30:00,0\n"      # teacher
                    "UNKNOWN9,07-09-2026,09:00:00,0\n"
                    "R2,07-09-2026,09:40:00,0\n")     # duplicate
        r = ingest_csv(csv_text, device)
        self.assertEqual((r["created"], r["duplicates"], r["unknown"]), (4, 1, 1))
        report = course_correlation(self.course)
        kinds = {(f["student"].username, f["kind"]) for f in report["flags"]}
        self.assertEqual(kinds, {("stu1", "present_no_punch"), ("stu2", "absent_on_campus")})
        staff = staff_attendance([self.fac.user], date(2026, 9, 1), date(2026, 9, 30))[0]
        self.assertEqual(staff["days_present"], 1)
        self.assertEqual(staff["classes_without_punch"], [])

    def test_api_requires_device_key(self):
        device = Device.objects.create(name="Lab block")
        url = reverse("api_punches")
        body = json.dumps({"punches": [{"biometric_id": "B1", "timestamp": "2026-09-08T09:05:00+05:30", "direction": "in"}]})
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 403)
        r = self.client.post(url, body, content_type="application/json", HTTP_X_DEVICE_KEY=device.api_key)
        self.assertEqual(r.json()["created"], 1)
        self.assertEqual(Punch.objects.get().person, self.fac.user)
        staff = staff_attendance([self.fac.user], date(2026, 9, 1), date(2026, 9, 30))[0]
        self.assertEqual(len(staff["classes_without_punch"]), 1)  # taught on 7 Sep without punching in


class SeedAndAccessTests(TestCase):
    def test_seed_published_data(self):
        out = io.StringIO()
        call_command("seed_institution", stdout=out)
        inst = Institution.objects.get()
        self.assertEqual(inst.affiliating_university, "University of Mumbai")
        comp = Program.objects.get(code="COMP")
        self.assertTrue(comp.nba_accredited)
        self.assertFalse(Program.objects.get(code="MTECH-COMP").nba_accredited)
        self.assertEqual(Department.objects.get(code="MECH").hod.last_name, "Shaikh")
        self.assertGreater(FacultyProfile.objects.count(), 50)
        self.assertEqual(rule_value("pass_percent_component", Program.objects.get(code="MTECH-COMP")), Decimal("45"))
        self.assertFalse(StudentProfile.objects.exists())  # no synthetic data without --demo

    def test_admin_only_pages_and_admission(self):
        inst, dept, prog = make_institution()
        teacher = User.objects.create_user("t", password="pw123456", role=User.TEACHER)
        self.client.force_login(teacher)
        for name in ("admin_console", "student_list", "faculty_list", "finance_dashboard", "biometric", "aicte_report"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        admin = User.objects.create_superuser("root", "r@x.edu", "pw123456")
        self.client.force_login(admin)
        for name in ("admin_console", "student_list", "faculty_list", "finance_dashboard", "biometric", "aicte_report", "rules", "payroll"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        r = self.client.post(reverse("admit_student"), {"first_name": "Asha", "program": prog.pk, "admission_no": "2026COMP001",
                                                        "admission_year": 2026, "admission_type": "lateral", "category": "Open"})
        sp = StudentProfile.objects.get(admission_no="2026COMP001")
        self.assertRedirects(r, reverse("student_detail", args=[sp.pk]))
        self.assertEqual((sp.entry_semester, sp.current_semester), (3, 3))
        # the student can see only their own progress
        self.client.force_login(sp.user)
        self.assertEqual(self.client.get(reverse("student_detail", args=[sp.pk])).status_code, 200)
        other = make_student(prog, 9)
        self.assertEqual(self.client.get(reverse("student_detail", args=[other.pk])).status_code, 403)
