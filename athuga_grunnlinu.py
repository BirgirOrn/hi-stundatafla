"""
athuga_grunnlinu.py
--------------------------------------------------------------
Greiningarskript fyrir HI stundatoflu-hagraedinguna.

Thetta skript BREYTIR ENGU i hi_schedule_optimizer.py.
Thad flytur hann inn og keyrir tvaer maelingar:

  KEYRSLA A  - MAX_MOVES = 0
               Hver lota er neydd i sitt nuverandi slot.
               Markfallid maelir tha nuverandi stundatoflu HI beint.
               -> thetta er RETTA grunnlinan.

  KEYRSLA B  - SolutionLimit = 1
               Gurobi stoppar vid fyrstu lausn sem thad finnur.
               -> thetta er talan sem birtist sem
                  "Found heuristic solution" i logginu.

Notkun:
    python3 athuga_grunnlinu.py

Skraadar ut:
    grunnlina_max_moves_0.csv   (nuverandi tafla)
    fyrsta_lausn_gurobi.csv     (heuristic-lausn Gurobi)
"""

import os
import sys

import gurobipy as gp

import hi_schedule_optimizer as mod
from hi_schedule_optimizer import HiScheduleOptimizer


def finna_skrar():
    """Finnur CSV-skrarnar i sama mopu og thetta skript."""
    base = os.path.dirname(os.path.abspath(__file__))
    return (
        base,
        mod._find_bokanir(base),
        mod._find_file(base, 'rekstur'),
        mod._find_file(base, 'Stofur', exclude='Stofub'),
        mod._find_file(base, 'Stofub'),
    )


def keyra(env, skrar, titill, max_moves=None, solution_limit=None,
          ut_skra=None):
    """
    Byggir og leysir likanid einu sinni.
    max_moves      - ef gefid, yfirskrifar mod.MAX_MOVES fyrir thessa keyrslu
    solution_limit - ef gefid, laetur Gurobi stoppa eftir N lausnir
    """
    _, bokanir, arekstur, stofur, stofubokanir = skrar

    gamalt = mod.MAX_MOVES
    if max_moves is not None:
        mod.MAX_MOVES = max_moves

    try:
        print()
        print("=" * 62)
        print("  " + titill)
        print("=" * 62)

        with HiScheduleOptimizer(env) as opt:
            opt.load_data(bokanir, arekstur, stofur, stofubokanir)
            print(f"  {len(opt.sessions_df)} faeranlegar lotur | "
                  f"{len(opt.conflict_pairs)} arekstrapor")

            opt.build_model()

            # Fast fraekorn svo sama lausn faist i hvert skipti
            opt.model.Params.Seed = 0
            if solution_limit is not None:
                opt.model.Params.SolutionLimit = solution_limit

            opt.model.optimize()

            if opt.model.SolCount == 0:
                print("  ENGIN LAUSN FANNST.")
                return None

            sol = opt.get_solution()
            print()
            print(f"  >>> MARKFALL (vigtadir arekstrar) : "
                  f"{sol['total_student_conflicts']}")
            print(f"  >>> Fjoldi faerdra lota           : {sol['n_moves']}")

            if ut_skra:
                opt.export_new_schedule(ut_skra)

            return sol['total_student_conflicts']

    finally:
        mod.MAX_MOVES = gamalt


def main():
    skrar = finna_skrar()
    base = skrar[0]

    print("Nota skrar ur:", base)
    for p in skrar[1:]:
        print("   ", os.path.basename(p))

    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', 1)
        env.setParam('TimeLimit', 300)
        env.start()

        # ---- KEYRSLA A: retta grunnlinan ----
        grunnlina = keyra(
            env, skrar,
            "KEYRSLA A  -  MAX_MOVES = 0  (nuverandi stundatafla HI)",
            max_moves=0,
            ut_skra=os.path.join(base, 'grunnlina_max_moves_0.csv'),
        )

        # ---- KEYRSLA B: fyrsta lausn Gurobi ----
        fyrsta = keyra(
            env, skrar,
            "KEYRSLA B  -  SolutionLimit = 1  (fyrsta lausn Gurobi)",
            solution_limit=1,
            ut_skra=os.path.join(base, 'fyrsta_lausn_gurobi.csv'),
        )

    print()
    print("=" * 62)
    print("  SAMANTEKT")
    print("=" * 62)
    print(f"  Nuverandi stundatafla HI (grunnlina) : {grunnlina}")
    print(f"  Fyrsta lausn Gurobi (heuristic)      : {fyrsta}")
    print(f"  Hagraedd tafla (ur fyrri keyrslu)    : 261")
    print()
    if grunnlina is not None:
        print("  Rett baeting =")
        print(f"     {grunnlina} -> 261")
    print("=" * 62)


if __name__ == '__main__':
    main()
