"""Learning plan generation: turns units/topics/COs into dated-ready plan items.

Each plan item is one contact session with an objective, Bloom level, CO
mapping, a pedagogy suited to the students' year and a set of digital tools
matched to the topic.
"""

import math
import re

from django.db import transaction

from .models import BLOOM_SHORT, CourseOutcome, PlanItem

BLOOM_VERBS = {
    1: ["define", "list", "state", "recall", "name", "identify", "label", "remember"],
    2: ["explain", "describe", "discuss", "summarize", "summarise", "classify", "understand", "interpret", "illustrate", "outline"],
    3: ["apply", "implement", "solve", "use", "compute", "calculate", "demonstrate", "construct", "develop", "execute", "perform"],
    4: ["analyse", "analyze", "compare", "differentiate", "examine", "distinguish", "investigate", "derive", "test"],
    5: ["evaluate", "justify", "assess", "judge", "critique", "select", "recommend", "validate", "choose"],
    6: ["design", "create", "formulate", "propose", "build", "compose", "plan", "synthesize", "invent"],
}

STOPWORDS = set(
    "the and of to a an in for on with using by as its their is are be from at into such including "
    "students will able understand explain apply analyse analyze evaluate design implement concepts "
    "concept various different basic fundamentals introduction techniques methods given problems".split()
)

# keyword -> suggested digital tools
TOOL_CATALOG = [
    (r"python|program|coding|algorithm|data structure|stack|queue|linked list|sort|search|tree|graph|hash|recursion|oop|object",
     ["Google Colab / Jupyter notebooks", "Python Tutor (code visualiser)", "GitHub Classroom for code submissions", "VisuAlgo animations"]),
    (r"machine learning|neural|deep learning|regression|classification|data science|dataset|ai\b|artificial",
     ["Google Colab (GPU)", "Kaggle datasets & notebooks", "TensorFlow Playground", "scikit-learn"]),
    (r"circuit|diode|transistor|amplifier|op-?amp|electronic|resistor|kirchhoff|network theorem|rlc",
     ["Falstad circuit simulator", "LTspice", "Tinkercad Circuits", "Virtual Labs (vlab.co.in)"]),
    (r"digital|logic gate|flip.?flop|counter|combinational|sequential|verilog|vhdl|fpga",
     ["Logisim Evolution", "EDA Playground (Verilog/VHDL)", "Virtual Labs (vlab.co.in)"]),
    (r"microcontroller|arduino|embedded|sensor|iot|raspberry|8051|arm",
     ["Wokwi embedded simulator", "Arduino IDE", "Tinkercad Circuits", "Node-RED dashboards"]),
    (r"signal|fourier|laplace|z-transform|filter|control|transfer function|stability|pid|dsp",
     ["MATLAB / Simulink Online", "GNU Octave", "Python SciPy notebooks", "Desmos for signal plots"]),
    (r"matrix|calculus|differential|integral|vector|probability|statistics|equation|linear algebra|numerical",
     ["GeoGebra", "Desmos", "Wolfram|Alpha", "Python NumPy notebooks"]),
    (r"mechanic|force|stress|strain|beam|thermo|fluid|heat|kinematic|dynamics|vibration|machine",
     ["PhET simulations", "Ansys Student", "SimScale (cloud CAE)", "Virtual Labs (vlab.co.in)"]),
    (r"drawing|cad|projection|drafting|3d model|solid model|design of machine",
     ["Autodesk Fusion 360 (education)", "FreeCAD", "Onshape (browser CAD)"]),
    (r"network|tcp|ip\b|protocol|routing|osi|lan|wireless|socket",
     ["Cisco Packet Tracer", "Wireshark packet captures", "GNS3"]),
    (r"database|sql|normali[sz]ation|er model|transaction|query|dbms",
     ["DB Fiddle / SQLite online", "dbdiagram.io for ER models", "MongoDB Atlas (free tier)"]),
    (r"web|html|css|javascript|react|api|cloud|devops|docker",
     ["CodePen / StackBlitz", "GitHub Codespaces", "Postman", "AWS/Azure education credits"]),
    (r"operating system|process|thread|scheduling|memory management|deadlock|file system",
     ["OS scheduling simulators", "Linux terminal (WSL / Replit)", "Python Tutor for concurrency demos"]),
    (r"chemistry|material|polymer|corrosion|electrochem",
     ["PhET chemistry simulations", "Virtual Labs (vlab.co.in)", "ChemCollective virtual lab"]),
    (r"physics|optic|laser|quantum|semiconductor|wave",
     ["PhET simulations", "oPhysics interactive", "Virtual Labs (vlab.co.in)"]),
    (r"survey|concrete|structure|soil|geotech|transport|hydraulic|civil",
     ["STAAD.Pro / ETABS (education)", "QGIS", "Google Earth Pro", "Virtual Labs (vlab.co.in)"]),
    (r"power system|machine|motor|generator|transformer|power electronics|electrical",
     ["MATLAB Simscape Electrical", "PSIM / LTspice", "Virtual Labs (vlab.co.in)"]),
    (r"communication|english|report|presentation|ethic|management|economics",
     ["Padlet collaborative board", "Grammarly / Hemingway editor", "Canva for presentations"]),
]
ENGAGEMENT_TOOLS = ["Mentimeter / Slido live poll", "Kahoot or Quizizz quick quiz", "Padlet exit ticket"]

# Year-sensitive pedagogy.  Each list is cycled through within a unit.
PEDAGOGY = {
    1: ["Concept introduction with worked examples & live demo", "Guided practice + think-pair-share",
        "Peer instruction with concept-check poll", "Demonstration + guided problem solving"],
    2: ["Interactive lecture with worked examples", "Think-pair-share problem solving",
        "Flipped classroom: pre-read + in-class problems", "Peer instruction with concept tests"],
    3: ["Flipped classroom with case discussion", "Problem-based learning (PBL) mini-task",
        "Simulation-led inquiry", "Jigsaw collaborative learning"],
    4: ["Case study / industry problem discussion", "Project-based learning checkpoint",
        "Research paper discussion (journal club)", "Design studio critique"],
}
UNIT_START = "Motivation & context: real-world application, prerequisite recap quiz"
UNIT_END = "Unit wrap-up: concept map + low-stakes quiz"


def infer_bloom(text, default=2):
    words = re.findall(r"[a-z]+", (text or "").lower())
    for word in words[:4]:
        for level, verbs in BLOOM_VERBS.items():
            if word in verbs:
                return level
    for level in sorted(BLOOM_VERBS, reverse=True):
        if any(w in BLOOM_VERBS[level] for w in words):
            return level
    return default


def _keywords(text):
    return {w for w in re.findall(r"[a-z][a-z\-']{2,}", (text or "").lower()) if w not in STOPWORDS}


def match_outcomes(text, outcomes, unit_index=0, unit_count=1):
    """Pick the outcomes whose descriptions best overlap with the text."""
    outcomes = list(outcomes)
    if not outcomes:
        return []
    words = _keywords(text)
    scored = []
    for o in outcomes:
        overlap = len(words & _keywords(o.description))
        scored.append((overlap, o))
    best = max(s for s, _ in scored)
    if best > 0:
        return [o for s, o in scored if s == best][:2]
    # fall back to proportional mapping: unit i -> CO i (typical syllabus layout)
    idx = min(len(outcomes) - 1, int(unit_index * len(outcomes) / max(unit_count, 1)))
    return [outcomes[idx]]


def suggest_tools(text, limit=3):
    found = []
    lowered = (text or "").lower()
    for pattern, tools in TOOL_CATALOG:
        if re.search(pattern, lowered):
            for t in tools:
                if t not in found:
                    found.append(t)
    return found[:limit]


def _chunk(items, n):
    """Split a list into n roughly equal consecutive chunks (n may exceed len)."""
    n = max(1, n)
    if len(items) >= n:
        size = len(items) / n
        return [items[round(i * size): round((i + 1) * size)] for i in range(n)]
    # fewer topics than sessions: spread sessions across topics (topic spans several sessions)
    chunks = []
    per = n / len(items)
    for i, item in enumerate(items):
        count = round((i + 1) * per) - round(i * per)
        chunks += [[item]] * max(1, count)
    return chunks[:n]


def build_session_plan(course, unit, topics, index_in_unit, total_in_unit, outcomes, unit_idx, unit_count, part=None):
    names = [t.title for t in topics]
    text = f"{unit.title} " + " ".join(names)
    cos = match_outcomes(text, outcomes, unit_idx, unit_count)
    top_bloom = max([o.bloom_level for o in cos] or [2])
    # climb Bloom's ladder within the unit up to the CO's level
    progress = (index_in_unit + 1) / max(total_in_unit, 1)
    bloom = max(1, min(top_bloom, 2 + math.floor(progress * (top_bloom - 1))))
    if index_in_unit == 0:
        bloom = min(bloom, 2)

    title = "; ".join(names)
    if part:
        title = f"{title} (part {part[0]}/{part[1]})"
    if index_in_unit == 0:
        pedagogy = UNIT_START
    elif index_in_unit == total_in_unit - 1 and total_in_unit > 2:
        pedagogy = UNIT_END
    else:
        options = PEDAGOGY.get(course.year, PEDAGOGY[2])
        pedagogy = options[(index_in_unit - 1) % len(options)]

    tools = suggest_tools(text) + [ENGAGEMENT_TOOLS[index_in_unit % len(ENGAGEMENT_TOOLS)]]
    verb = BLOOM_SHORT[bloom].lower()
    objective = f"Students will be able to {verb} {names[0][0].lower() + names[0][1:]}"
    if len(names) > 1:
        objective += f" and related ideas ({', '.join(n.lower() for n in names[1:3])})"
    objective += "."

    first_text = course.references.filter(kind="textbook").first()
    pre = f"Read/watch: {first_text.text[:120]}" if first_text else "Short pre-read or NPTEL/YouTube video on the topic"
    if course.year >= 3:
        pre += "; bring one question or real-world example"
    in_class = f"{pedagogy}. Check understanding with a 2-minute poll mid-session."
    post = "3-5 practice problems; exit ticket: one thing learned, one doubt"
    if bloom >= 3:
        post = "Apply it: solve a numerical/coding task and upload the work; exit ticket"
    return {
        "title": title[:400],
        "objective": objective,
        "bloom_level": bloom,
        "outcomes": cos,
        "pedagogy": pedagogy[:200],
        "tools": "\n".join(tools),
        "pre_class": pre,
        "in_class": in_class,
        "post_class": post,
    }


@transaction.atomic
def generate_learning_plan(course, replace=True):
    """(Re)build the plan from the syllabus structure. Returns number of items."""
    if replace:
        course.plan_items.all().delete()
    outcomes = list(CourseOutcome.objects.filter(course=course))
    units = list(course.units.prefetch_related("topics"))
    seq = course.plan_items.count()
    created = 0

    for u_idx, unit in enumerate(units):
        topics = list(unit.topics.all())
        if not topics:
            continue
        sessions = unit.hours or len(topics)
        chunks = _chunk(topics, sessions)
        # label multi-session topics as parts
        spans = {}
        for chunk in chunks:
            spans[chunk[0].pk] = spans.get(chunk[0].pk, 0) + (1 if len(chunk) == 1 else 0)
        seen = {}
        for i, chunk in enumerate(chunks):
            part = None
            if len(chunk) == 1 and spans.get(chunk[0].pk, 0) > 1:
                seen[chunk[0].pk] = seen.get(chunk[0].pk, 0) + 1
                part = (seen[chunk[0].pk], spans[chunk[0].pk])
            data = build_session_plan(course, unit, chunk, i, len(chunks), outcomes, u_idx, len(units), part)
            seq += 1
            item = PlanItem.objects.create(
                course=course, sequence=seq, session_type="lecture", unit=unit,
                duration_minutes=60, **{k: v for k, v in data.items() if k != "outcomes"},
            )
            item.topics.set(chunk)
            item.outcomes.set(data["outcomes"])
            unit.outcomes.add(*data["outcomes"])
            created += 1

    # tutorials: one per week-equivalent, cycling through units
    if course.tutorial_hours and units:
        weeks = _semester_weeks(course)
        for w in range(weeks * course.tutorial_hours):
            unit = units[min(len(units) - 1, int(w * len(units) / max(weeks * course.tutorial_hours, 1)))]
            seq += 1
            item = PlanItem.objects.create(
                course=course, sequence=seq, session_type="tutorial", unit=unit,
                title=f"Tutorial {w + 1}: problem solving on {unit.title}",
                objective=f"Students will be able to solve problems on {unit.title.lower()}.",
                bloom_level=3, pedagogy="Small-group problem solving with peer review",
                tools="\n".join(suggest_tools(unit.title) + ["Padlet exit ticket"]),
                in_class="Worked example (10 min) then graded problem set in groups of 3-4.",
                post_class="Complete remaining problems; upload solutions.",
            )
            item.outcomes.set(unit.outcomes.all())
            created += 1

    # lab sessions from the list of experiments
    for exp in course.experiments.all():
        text = exp.title
        seq += 1
        cos = match_outcomes(text, outcomes, 0, 1)
        item = PlanItem.objects.create(
            course=course, sequence=seq, session_type="lab", experiment=exp,
            title=f"Experiment {exp.number}: {exp.title}"[:400],
            objective=f"Students will be able to {exp.title[0].lower() + exp.title[1:]}.",
            bloom_level=max(3, infer_bloom(text, 3)), duration_minutes=120,
            pedagogy="Hands-on lab with pre-lab quiz and viva",
            tools="\n".join(suggest_tools(text + " " + course.title) or ["Virtual Labs (vlab.co.in)"]),
            pre_class="Pre-lab: read the aim/theory, write the algorithm or circuit diagram in the journal",
            in_class="Perform the experiment, record observations, get the output verified",
            post_class="Complete the write-up (conclusion + viva questions) and submit",
        )
        item.outcomes.set(cos)
        created += 1
    return created


def _semester_weeks(course):
    if course.start_date and course.end_date:
        return max(1, (course.end_date - course.start_date).days // 7)
    return 14


def renumber(course):
    for i, item in enumerate(course.plan_items.order_by("sequence", "pk"), start=1):
        if item.sequence != i:
            PlanItem.objects.filter(pk=item.pk).update(sequence=i)
