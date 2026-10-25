# University of Iceland (Háskóli Íslands) Schedule Optimiser

## Overview

The University of Iceland (Háskóli Íslands, HÍ) currently produces its semester timetable partly by hand, resulting in scheduling conflicts where students enrolled in two required courses find both sessions at the same time. This tool uses Mixed-Integer Linear Programming (MILP) to propose a minimal set of session relocations — moving a limited number of existing lecture sessions to different time slots — in order to reduce the total number of affected students as much as possible.

The approach is **Option C (Hybrid)**: the existing schedule is kept as the baseline, and the optimizer is allowed to move at most MAX_MOVES sessions (default: 20) to alternative slots. This limits disruption while targeting the most harmful conflicts first.

---

## Objective

Minimise the total number of students affected by scheduling conflicts, measured as the sum across all conflicting course pairs of (number of students enrolled in both courses) × (1 if those courses have at least one overlapping session, 0 otherwise).

Secondary objective (implicit): minimise disruption by bounding the number of sessions moved via a hard move-budget constraint.

---

## Constraints

### C1: Assignment Completeness
Every moveable lecture session must be assigned to exactly one time slot. Sessions that are not moved keep their original slot.

### C2: Move Budget
The total number of sessions relocated must not exceed MAX_MOVES (default = 20). This limits disruption and keeps the solution practical for administrators.

### C3: Room Capacity
A session may only be moved to a time slot where at least one room with sufficient capacity (>= enrolled student count) is available. Room availability is determined from the Stofubókanir booking log covering the entire university.

### C4: Clash Propagation
For each pair of courses with shared enrolled students, a binary clash variable is activated (= 1) if and only if any lecture session of the first course overlaps in time with any lecture session of the second course. Two sessions overlap if they are on the same weekday and their slot ranges intersect.

### C5: Non-moveable Sessions
Sessions with event type "unknown" (exams, workshops, manual allocations) and Saturday sessions are excluded from the optimisation and kept at their current slots.

---

## Input

### Bókanir (Session Bookings)
File: `Stundatöflugögn - Bókanir (1).csv`

One row per booked session. Multi-room broadcasts for the same session are deduplicated (largest enrolment kept).

| Field | Type | Description |
|-------|------|-------------|
| course_id | string | Course code (e.g. TÖL105G) |
| course_name | string | Full course name in Icelandic |
| activity_code | int | Unique session identifier |
| event_type | string | lecture / tutorial / lab / unknown |
| slot | string | Day-slot code e.g. "mán-0" (Monday slot 0) |
| n_timeslot | int | Duration in 50-minute slots |
| size | int | Actual enrolled students |
| plan_size | int | Planned capacity |
| use_plan_size | string | 'Y' = use plan_size as enrolment count |
| room | string | Room name |
| room_id | int | Room identifier |

Slot decoding: prefix = day (mán=Mon, þri=Tue, mið=Wed, fim=Thu, fös=Fri, lau=Sat), number = slot index (0 = 08:20, each step = 50 minutes).

### Árekstur (Conflict Matrix)
File: `Stundatöflugögn - Árekstur.csv`

A 205×205 upper-triangular matrix. Each cell contains "expected,actual" where:
- **expected** = number of students who had this conflict in the previous academic year
- **actual** = number of students currently enrolled in BOTH courses (will clash if the courses overlap)

| Field | Type | Description |
|-------|------|-------------|
| course_short_id (row) | string | Course code |
| course_short_id (column) | string | Other course code |
| cell value | string | "expected,actual" (e.g. "43,58") |

### Stofur (Rooms)
File: `Stundatöflugögn - Stofur.csv`

| Field | Type | Description |
|-------|------|-------------|
| room_id | int | Unique room identifier |
| rooms | string | Room name (e.g. HT-300) |
| buildings | string | Building name |
| capacity | int | Maximum seating capacity |
| morning/afternoon | string | Availability window |

### Stofubókanir (Room Bookings – University Wide)
File: `Stundatöflugögn - Stofubókanir.csv`

All confirmed room bookings across all faculties. Used to determine which rooms are free in which slots.

| Field | Type | Description |
|-------|------|-------------|
| room_id | int | Room identifier |
| slot | string | Day-slot code |
| n_timeslot | int | Duration |
| course_id | string | Course using the room |

### Stundatöflur (Study Programs) — Reference only
File: `Stundatöflugögn - Stundatöflur.csv`

Study program definitions (144 programs). Each row contains a JSON column listing which courses each year of each program takes together. Currently used as reference context; not consumed directly by the MILP.

### Kjörsvið (Study Tracks) — Reference only
File: `Stundatöflugögn - Kjörsvið.csv`

Membership of each course in study tracks, with role prefix (V=required, S=compulsory elective, B=free elective). Currently used as reference context; not consumed directly by the MILP.

---

## Output

### Solution Dictionary
`get_solution()` returns a dict:

| Key | Type | Description |
|-----|------|-------------|
| status | int | Gurobi status code |
| is_feasible | bool | True if any solution was found |
| is_optimal | bool | True if proven optimal |
| objective_value | float | Weighted student clash count |
| total_student_conflicts | int | Rounded integer clash count |
| n_moves | int | Number of sessions relocated |
| moves | list of dicts | One entry per relocated session |

Each move entry contains: course_id, course_name, event_type, from_day, from_slot, to_day, to_slot.

### Exported Schedule
`export_new_schedule('hi_new_schedule.csv')` writes a CSV with all moveable sessions including a `moved` flag column.

### Console Summary
`print_summary()` prints a formatted table of proposed moves and key statistics.

---

## Test Scenarios

### T1: No Conflicts Exist
**Description**: When there are no conflict pairs in the input, the objective must be 0 and no sessions are moved.

**Acceptance Criteria**:
- is_optimal = True
- total_student_conflicts = 0
- n_moves = 0

---

### T2: Single Conflict Resolved by One Move
**Description**: Two courses share the same Monday slot 0. A third course provides an alternative slot (Thursday slot 0). Budget = 20.

**Input Data**:

| course_id | day | slot | n_timeslot | eff_size |
|-----------|-----|------|-----------|---------|
| C1 | mán | 0 | 1 | 30 |
| C2 | mán | 0 | 1 | 20 |
| C3 | fim | 0 | 1 | 10 |

conflict_pairs = {(C1,C2): 25}

**Acceptance Criteria**:
- total_student_conflicts = 0
- n_moves = 1

---

### T3: Zero Move Budget — Clashes Cannot Be Resolved
**Description**: MAX_MOVES = 0. Two courses conflict but cannot be moved.

**Acceptance Criteria**:
- total_student_conflicts = 30 (unchanged)
- n_moves = 0

---

### T4: Room Capacity Prevents Move of Large Session
**Description**: A large session (100 students) has no available room in the alternative slot. A small session (10 students) can move instead.

**Acceptance Criteria**:
- total_student_conflicts = 0 (small session moved successfully)

---

### T5: Two Conflict Pairs — Best Single Move Chosen
**Description**: Two independent conflict pairs exist. With budget = 1, the optimizer must pick the move that resolves the larger clash (50 students) over the smaller one (10 students).

**Acceptance Criteria**:
- total_student_conflicts ≤ 10

---

### T6: Sessions on Different Days Never Conflict
**Description**: Two courses are already on different weekdays. The clash variable must be 0 regardless of exact slot assignment.

**Acceptance Criteria**:
- total_student_conflicts = 0

---

### T7: Multi-Slot Session Overlap Detection
**Description**: A 2-slot session at Monday slot 0 (occupies slots 0 and 1) overlaps with a 1-slot session at Monday slot 1. The model must detect this overlap.

**Acceptance Criteria**:
- Conflict detected before any move
- total_student_conflicts = 0 after one move

---

### T8: No Solve Called — get_solution Returns is_feasible=False
**Description**: Calling get_solution() immediately after build_model() without calling solve() must return is_feasible=False.

**Acceptance Criteria**:
- is_feasible = False

---

### T9: Move Budget Strictly Enforced
**Description**: Two independent conflicts exist, each fixable by one move, but MAX_MOVES = 1. Only one conflict may be resolved.

**Acceptance Criteria**:
- n_moves ≤ 1

---

### T10: Hub Course — One Move Resolves Multiple Clashes
**Description**: A single "hub" course (H) clashes with three other courses (A, B, C), all on Monday slot 0. Moving H to Thursday slot 0 resolves all three clashes with a single move.

**Input Data**:

| course | day | slot | eff_size |
|--------|-----|------|---------|
| H | mán | 0 | 80 |
| A | mán | 0 | 30 |
| B | mán | 0 | 20 |
| C | mán | 0 | 15 |
| X | fim | 0 | 5 |

conflict_pairs = {(A,H):25, (B,H):18, (C,H):12}

**Acceptance Criteria**:
- total_student_conflicts = 0
- n_moves = 1

---

## Out of Scope

- Tutorial and lab sessions are not moved in the current model (only lectures).
- Room assignment for relocated sessions is validated at the slot level (any suitable free room) but the specific room assignment is not made by the MILP.
- Exam schedule conflicts are not modelled (unknown sessions are fixed).
- Teacher/lecturer availability is not considered.
- Student group restrictions and prerequisite chains are not modelled.
- The Kjörsvið (study track) and Stundatöflur (program) sheets are loaded for reference but not used as direct constraints.
