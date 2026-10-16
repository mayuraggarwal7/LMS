"""The college "account": institution, departments, branches/programs and the rule framework.

Rules come in three layers, most general first:

* AICTE      - the nodal regulator's norms (attendance, faculty:student ratio, workload, intake...)
* UNIVERSITY - the affiliating university's ordinances (e.g. University of Mumbai)
* INSTITUTE  - the autonomous institute's own academic rules

`effective_rule(key, program)` returns the most specific applicable rule (institute over
university over AICTE, program-specific over college-wide). `compliance()` flags any
institute/university rule that is less strict than the AICTE norm it refines.
"""

from decimal import Decimal

from django.db import models


class Institution(models.Model):
    name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    established = models.PositiveSmallIntegerField(null=True, blank=True)
    management = models.CharField("Trust / management", max_length=200, blank=True)
    affiliating_university = models.CharField(max_length=200, blank=True)
    autonomous_since = models.PositiveSmallIntegerField(null=True, blank=True)
    aicte_id = models.CharField("AICTE permanent / application id", max_length=60, blank=True)
    naac_grade = models.CharField(max_length=20, blank=True)
    website = models.URLField(blank=True)
    data_source_note = models.TextField(blank=True, help_text="Where the seeded data came from")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @classmethod
    def current(cls):
        return cls.objects.order_by("pk").first()


class Department(models.Model):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=20)
    hod = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="heads")
    is_first_year = models.BooleanField(default=False, help_text="Humanities & applied sciences / first-year department")

    class Meta:
        ordering = ["name"]
        unique_together = [("institution", "code")]

    def __str__(self):
        return self.name


class Program(models.Model):
    """A degree programme / engineering branch with its AICTE-sanctioned intake."""

    UG, PG, PHD = "UG", "PG", "PHD"
    LEVELS = [(UG, "Undergraduate (B.E./B.Tech)"), (PG, "Postgraduate (M.E./M.Tech)"), (PHD, "Ph.D.")]
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="programs")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="programs")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    level = models.CharField(max_length=3, choices=LEVELS, default=UG)
    degree = models.CharField(max_length=40, default="B.E.")
    duration_years = models.PositiveSmallIntegerField(default=4)
    intake = models.PositiveIntegerField("Sanctioned intake per year", default=60)
    year_started = models.PositiveSmallIntegerField(null=True, blank=True)
    nba_accredited = models.BooleanField(default=False)
    nba_valid_till = models.PositiveSmallIntegerField(null=True, blank=True)
    total_credits = models.PositiveSmallIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["level", "name"]
        unique_together = [("institution", "code")]

    def __str__(self):
        return f"{self.degree} {self.name}"

    @property
    def semesters(self):
        return self.duration_years * 2


class Regulation(models.Model):
    AICTE, UNIVERSITY, INSTITUTE = "aicte", "university", "institute"
    SCOPES = [(AICTE, "AICTE (nodal body)"), (UNIVERSITY, "Affiliating university"), (INSTITUTE, "Institute (autonomous)")]
    PRECEDENCE = {AICTE: 0, UNIVERSITY: 1, INSTITUTE: 2}
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="regulations")
    scope = models.CharField(max_length=12, choices=SCOPES)
    name = models.CharField(max_length=200, help_text="e.g. AICTE APH 2024-27, University ordinance, Autonomous R-2023")
    effective_from = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Admission batch year it applies from")
    reference = models.CharField(max_length=300, blank=True, help_text="Circular / handbook / URL")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["scope", "-effective_from"]

    def __str__(self):
        return f"{self.get_scope_display()}: {self.name}"


# Known rule keys: (label, unit, direction) - direction says which way is "stricter".
RULE_KEYS = {
    "min_attendance_percent": ("Minimum attendance to appear for exams", "%", "min"),
    "faculty_student_ratio": ("Students per faculty (at most)", "students", "max"),
    "cadre_ratio": ("Faculty cadre ratio Prof : Assoc : Asst", "", "text"),
    "workload_professor": ("Teaching load, Professor", "hrs/week", "value"),
    "workload_associate": ("Teaching load, Associate Professor", "hrs/week", "value"),
    "workload_assistant": ("Teaching load, Assistant Professor", "hrs/week", "value"),
    "workload_lab_staff": ("Lab duty load, lab / technical staff", "hrs/week", "value"),
    "lateral_entry_percent": ("Lateral entry (Direct Second Year) seats", "% of intake", "max"),
    "tfws_percent": ("Tuition Fee Waiver Scheme seats", "% of intake", "max"),
    "min_credits_program": ("Credits required for the degree", "credits", "min"),
    "pass_percent_component": ("Passing marks per head (ISE / ESE)", "%", "min"),
    "max_backlogs_for_promotion": ("Backlogs allowed to keep terms (ATKT)", "courses", "max"),
    "max_years_to_complete": ("Maximum years to complete the degree", "years", "max"),
    "min_phd_faculty_percent": ("Faculty with Ph.D.", "%", "min"),
    "class_size_max": ("Students per division", "students", "max"),
    "lab_batch_max": ("Students per lab batch", "students", "max"),
}


class Rule(models.Model):
    regulation = models.ForeignKey(Regulation, on_delete=models.CASCADE, related_name="rules")
    key = models.CharField(max_length=40, choices=[(k, v[0]) for k, v in RULE_KEYS.items()])
    program = models.ForeignKey(Program, on_delete=models.CASCADE, null=True, blank=True, related_name="rules",
                                help_text="Leave empty to apply to every programme")
    value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    text = models.CharField(max_length=120, blank=True, help_text="For text rules such as a ratio")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.display_value}"

    @property
    def label(self):
        return RULE_KEYS.get(self.key, (self.key, "", "value"))[0]

    @property
    def unit(self):
        return RULE_KEYS.get(self.key, ("", "", ""))[1]

    @property
    def direction(self):
        return RULE_KEYS.get(self.key, ("", "", "value"))[2]

    @property
    def display_value(self):
        if self.text:
            return self.text
        if self.value is None:
            return "-"
        return f"{self.value.normalize():f}"


def applicable_rules(key, program=None, institution=None):
    """All active rules for a key that apply to `program`, most authoritative first."""
    qs = Rule.objects.filter(key=key, regulation__active=True).select_related("regulation", "program")
    if institution is not None:
        qs = qs.filter(regulation__institution=institution)
    rules = [r for r in qs if r.program_id is None or (program is not None and r.program_id == program.pk)]
    rules.sort(key=lambda r: (Regulation.PRECEDENCE[r.regulation.scope], r.program_id is not None,
                              r.regulation.effective_from or 0), reverse=True)
    return rules


def effective_rule(key, program=None, institution=None):
    rules = applicable_rules(key, program, institution)
    return rules[0] if rules else None


def rule_value(key, program=None, default=None, institution=None):
    rule = effective_rule(key, program, institution)
    if rule is None or rule.value is None:
        return default
    return rule.value


def compliance(institution):
    """Rules that refine an AICTE norm in the less-strict direction (a regulatory risk)."""
    issues = []
    rules = list(Rule.objects.filter(regulation__institution=institution, regulation__active=True)
                 .select_related("regulation", "program"))
    aicte = {(r.key, r.program_id): r for r in rules if r.regulation.scope == Regulation.AICTE}
    for r in rules:
        if r.regulation.scope == Regulation.AICTE or r.value is None or r.direction not in ("min", "max"):
            continue
        base = aicte.get((r.key, r.program_id)) or aicte.get((r.key, None))
        if base is None or base.value is None:
            continue
        weaker = r.value < base.value if r.direction == "min" else r.value > base.value
        if weaker:
            unit = f" {r.unit}" if r.unit else ""
            issues.append({"rule": r, "aicte": base,
                           "message": f"{r.regulation.get_scope_display()} sets '{r.label}' to {r.display_value}{unit}, "
                                      f"less strict than AICTE's {base.display_value}{unit}."})
    return issues


# AICTE norms seeded for a new account. Values follow the AICTE Approval Process Handbook and
# pay regulations as commonly applied; administrators should verify them against the current APH.
AICTE_DEFAULTS = [
    ("min_attendance_percent", Decimal("75"), "", "Minimum attendance commonly required by AICTE-approved institutions"),
    ("faculty_student_ratio", Decimal("20"), "", "AICTE APH: faculty:student ratio 1:20 (UG engineering)"),
    ("cadre_ratio", None, "1:2:6", "AICTE APH cadre ratio Professor : Associate : Assistant"),
    ("workload_professor", Decimal("14"), "", "AICTE pay regulations: direct teaching hrs/week"),
    ("workload_associate", Decimal("16"), "", "AICTE pay regulations: direct teaching hrs/week"),
    ("workload_assistant", Decimal("18"), "", "AICTE pay regulations: direct teaching hrs/week"),
    ("lateral_entry_percent", Decimal("10"), "", "Supernumerary lateral entry into second year"),
    ("tfws_percent", Decimal("5"), "", "Supernumerary Tuition Fee Waiver Scheme seats"),
    ("max_years_to_complete", Decimal("8"), "", "Twice the programme duration"),
]


def create_default_aicte_regulation(institution):
    reg, created = Regulation.objects.get_or_create(
        institution=institution, scope=Regulation.AICTE, name="AICTE norms (Approval Process Handbook)",
        defaults={"reference": "https://www.aicte-india.org/bureaus/approval"},
    )
    if created:
        for key, value, text, note in AICTE_DEFAULTS:
            Rule.objects.create(regulation=reg, key=key, value=value, text=text, note=note)
    return reg
