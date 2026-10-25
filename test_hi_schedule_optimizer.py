"""
Unit tests for the HÍ Schedule Optimizer.

All tests use small synthetic datasets to stay well within the
100-variable / 100-constraint limit of the test environment.

Test scenarios map:
  T1  – No conflicts exist → objective = 0, no moves needed
  T2  – One conflict, one move resolves it within budget
  T3  – Move budget = 0 → no relocations allowed, clashes remain
  T4  – Room capacity prevents a move
  T5  – Multiple conflict pairs, optimizer picks best resolution
  T6  – Sessions on different days never conflict (zero clashes regardless of assignment)
  T7  – Multi-slot sessions (n_timeslot > 1) overlap detection
  T8  – get_solution returns is_feasible=False when no solve was run
  T9  – Move budget constraint is respected exactly
  T10 – Optimal solution found when single session resolves two clashes
"""

import time
import pytest
import pandas as pd
import gurobipy as gp
from gurobipy import GRB

from hi_schedule_optimizer import HiScheduleOptimizer, MAX_MOVES

# ============================================================
# HELPER: build a minimal Gurobi environment for each test
# ============================================================

def make_env():
    """Return a started, silent Gurobi environment."""
    env = gp.Env(empty=True)
    env.setParam('OutputFlag', 0)
    env.setParam('TimeLimit', 10)
    env.start()
    return env


def make_sessions(rows):
    """
    Build a minimal sessions DataFrame from a list of dicts.
    Required keys: course_id, day_code, slot_num, n_timeslot, eff_size
    Optional keys: event_type (default 'lecture'), course_name
    """
    for r in rows:
        r.setdefault('event_type', 'lecture')
        r.setdefault('course_name', r['course_id'])
    return pd.DataFrame(rows)


# ============================================================
# T1 – No conflicts: objective should be 0, no moves needed
# ============================================================

def test_T1_no_conflicts():
    """T1: When there are no conflict pairs, objective is 0."""
    t0 = time.time()

    sessions = make_sessions([
        {'course_id': 'C1', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
        {'course_id': 'C2', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
    ])
    conflict_pairs = {}
    room_capacity  = {1: 100}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    assert sol['is_optimal']
    assert sol['total_student_conflicts'] == 0
    assert sol['n_moves'] == 0
    assert time.time() - t0 < 15


# ============================================================
# T2 – One conflict, budget allows one move to resolve it
# ============================================================

def test_T2_single_conflict_resolved():
    """T2: One conflicting pair; the optimizer moves one session away."""
    t0 = time.time()

    # C1 and C2 are both on Monday slot 0 → conflict
    # C2 can move to Thursday slot 0 where no conflict exists
    sessions = make_sessions([
        {'course_id': 'C1', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 30},
        {'course_id': 'C2', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 20},
        {'course_id': 'C3', 'day_code': 'fim', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
    ])
    conflict_pairs = {('C1', 'C2'): 25}
    room_capacity  = {1: 100}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    assert sol['is_optimal']
    # Conflict should be fully resolved
    assert sol['total_student_conflicts'] == 0
    assert sol['n_moves'] == 1
    assert time.time() - t0 < 15


# ============================================================
# T3 – Move budget = 0: no relocations, existing clashes remain
# ============================================================

def test_T3_zero_budget_clashes_remain(monkeypatch):
    """T3: With MAX_MOVES=0 sessions cannot be moved, clash stays."""
    import hi_schedule_optimizer as mod
    monkeypatch.setattr(mod, 'MAX_MOVES', 0)

    t0 = time.time()
    sessions = make_sessions([
        {'course_id': 'A', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 50},
        {'course_id': 'B', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 40},
        {'course_id': 'C', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
    ])
    conflict_pairs = {('A', 'B'): 30}
    room_capacity  = {1: 100}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    # Clash must remain because no moves are allowed
    assert sol['total_student_conflicts'] == 30
    assert sol['n_moves'] == 0
    assert time.time() - t0 < 15


# ============================================================
# T4 – Room capacity blocks a candidate slot
# ============================================================

def test_T4_room_capacity_constraint():
    """T4: Session cannot move to a slot where no room is large enough."""
    t0 = time.time()

    # C1 (100 students) and C2 (10 students) conflict on Monday slot 0.
    # The only other slot is Tuesday slot 0.
    # Room 1 has capacity 15 (fits C2 but NOT C1).
    # Room 2 has capacity 200 but is occupied on Tuesday slot 0.
    # → C1 has no valid alternative slot; C2 can move to Tuesday.
    sessions = make_sessions([
        {'course_id': 'C1', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 100},
        {'course_id': 'C2', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
        {'course_id': 'C3', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
    ])
    conflict_pairs = {('C1', 'C2'): 8}
    room_capacity  = {1: 15, 2: 200}
    # Room 2 (200 seats) occupied on Tuesday slot 0 → C1 cannot move there
    room_occupied  = {(2, 'þri', 0)}

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    # C2 (small) can move to Tuesday, resolving the clash
    assert sol['total_student_conflicts'] == 0
    assert time.time() - t0 < 15


# ============================================================
# T5 – Two conflict pairs; optimizer picks the most impactful move
# ============================================================

def test_T5_two_pairs_best_move():
    """T5: Two conflict pairs; one move resolves the larger clash."""
    t0 = time.time()

    # C1@mán-0, C2@mán-0 → 50 student clash
    # C3@þri-0, C4@þri-0 → 10 student clash
    # Budget = 1: should resolve the 50-student clash
    sessions = make_sessions([
        {'course_id': 'C1', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 60},
        {'course_id': 'C2', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 55},
        {'course_id': 'C3', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 15},
        {'course_id': 'C4', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 12},
        # Extra slot so sessions can actually move somewhere
        {'course_id': 'C5', 'day_code': 'mið', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
    ])
    conflict_pairs = {('C1', 'C2'): 50, ('C3', 'C4'): 10}
    room_capacity  = {1: 200}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    # With budget=1, best is to eliminate the 50-student clash (leaving 10)
    assert sol['total_student_conflicts'] <= 10
    assert sol['n_moves'] <= MAX_MOVES
    assert time.time() - t0 < 15


# ============================================================
# T6 – Sessions on different days never conflict
# ============================================================

def test_T6_different_days_no_conflict():
    """T6: Sessions on different days produce zero student clashes."""
    t0 = time.time()

    sessions = make_sessions([
        {'course_id': 'X', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 2, 'eff_size': 30},
        {'course_id': 'Y', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 2, 'eff_size': 30},
    ])
    conflict_pairs = {('X', 'Y'): 20}
    room_capacity  = {1: 100}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    # Regardless of where they are placed, two sessions on different days
    # can never be simultaneously attended → zero conflicts
    assert sol['total_student_conflicts'] == 0
    assert time.time() - t0 < 15


# ============================================================
# T7 – Multi-slot session overlap detection
# ============================================================

def test_T7_multislot_overlap():
    """T7: A 2-slot session at slot 0 overlaps with a 1-slot session at slot 1."""
    t0 = time.time()

    # C1: Monday slot 0, duration 2  (occupies slots 0 and 1)
    # C2: Monday slot 1, duration 1  (occupies slot 1 only)
    # → they overlap on slot 1
    sessions = make_sessions([
        {'course_id': 'C1', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 2, 'eff_size': 40},
        {'course_id': 'C2', 'day_code': 'mán', 'slot_num': 1, 'n_timeslot': 1, 'eff_size': 20},
        # Extra slot for moving
        {'course_id': 'C3', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
    ])
    conflict_pairs = {('C1', 'C2'): 15}
    room_capacity  = {1: 100}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    # Optimizer should move C2 away from overlapping position
    assert sol['total_student_conflicts'] == 0
    assert time.time() - t0 < 15


# ============================================================
# T8 – get_solution before solve returns is_feasible=False
# ============================================================

def test_T8_no_solve_returns_infeasible():
    """T8: Calling get_solution before solve gives is_feasible=False."""
    sessions = make_sessions([
        {'course_id': 'A', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 10},
    ])

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, {}, {1: 100}, set())
        # Build model but do NOT call solve()
        opt.build_model()
        sol = opt.get_solution()

    assert not sol['is_feasible']


# ============================================================
# T9 – Move budget is strictly respected
# ============================================================

def test_T9_move_budget_respected(monkeypatch):
    """T9: Number of moves in solution never exceeds MAX_MOVES."""
    import hi_schedule_optimizer as mod
    monkeypatch.setattr(mod, 'MAX_MOVES', 1)

    t0 = time.time()

    # Three conflicts, each fixable by one move, but budget = 1
    sessions = make_sessions([
        {'course_id': 'A', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 50},
        {'course_id': 'B', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 40},
        {'course_id': 'D', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 30},
        {'course_id': 'E', 'day_code': 'þri', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 20},
        # Free slots for moves
        {'course_id': 'Z', 'day_code': 'mið', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
        {'course_id': 'W', 'day_code': 'fim', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
    ])
    conflict_pairs = {('A', 'B'): 35, ('D', 'E'): 25}
    room_capacity  = {1: 200}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    assert sol['n_moves'] <= 1
    assert time.time() - t0 < 15


# ============================================================
# T10 – Single move resolves multiple clash pairs (hub course)
# ============================================================

def test_T10_hub_course_one_move_resolves_many():
    """T10: Moving a 'hub' course shared by many conflict pairs clears them all."""
    t0 = time.time()

    # H is a hub course at Monday slot 0, clashing with A, B, C
    # Moving H to Thursday slot 0 resolves all three clashes at once
    sessions = make_sessions([
        {'course_id': 'H', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 80},
        {'course_id': 'A', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 30},
        {'course_id': 'B', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 20},
        {'course_id': 'C', 'day_code': 'mán', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 15},
        # Anchor slot so H has somewhere to go
        {'course_id': 'X', 'day_code': 'fim', 'slot_num': 0, 'n_timeslot': 1, 'eff_size': 5},
    ])
    conflict_pairs = {
        ('A', 'H'): 25,
        ('B', 'H'): 18,
        ('C', 'H'): 12,
    }
    room_capacity  = {1: 200}
    room_occupied  = set()

    env = make_env()
    with HiScheduleOptimizer(env) as opt:
        opt.set_data(sessions, conflict_pairs, room_capacity, room_occupied)
        opt.build_model()
        opt.solve()
        sol = opt.get_solution()

    assert sol['is_feasible']
    assert sol['total_student_conflicts'] == 0
    assert sol['n_moves'] == 1   # one move of H resolves all three clashes
    assert time.time() - t0 < 15
