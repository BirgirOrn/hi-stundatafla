"""
sopun_faerslukvota.py
--------------------------------------------------------------
Keyrir likanid einu sinni fyrir hvert gildi a faerslukvotanum og
byr til feril: hversu marga arekstra faum vid burt fyrir hverja
faerslu sem vid leyfum?

Thetta svarar spurningunni sem MAX_MOVES = 20 getur ekki svarad:
er 20 nog, eda vaeri 50 miklu betra?

BREYTIR ENGU i hi_schedule_optimizer.py -- flytur hann bara inn.

Notkun:
    python3 sopun_faerslukvota.py

Skraadar ut:
    sopun_faerslukvoti.csv        toflan
    sopun_tafla_<k>.csv           stundatafla fyrir hvert gildi
    sopun_ferill.png              graf (ef matplotlib er uppsett)
"""

import os
import time

import pandas as pd
import gurobipy as gp
from gurobipy import GRB

import hi_schedule_optimizer as mod
from hi_schedule_optimizer import HiScheduleOptimizer

# ============================================================
# STILLINGAR
# ============================================================

# Kvotagildin sem a ad prufa.
#
# THETTA ER KEYRT I TVEIMUR UMFERDUM svo thu sitjir ekki
# klukkutimum saman an thess ad vita hvort nokkud gerist:
#
#   UMFERD A - throngur ferill a laga svaedinu, leysist hratt (~15-20 min)
#   UMFERD B - finnur "thakid" langt uti, faar rikulegan tima hver (~1-1.5 klst)
#
# Breyttu BUDGETS ad nedan eftir thvi hvora umferd thu ert ad keyra,
# eda keyrdu skriptid tvisvar med sitt hvoru gildinu.

# --- UMFERD A (keyr thessa fyrst) ---
# BUDGETS = [0, 5, 10, 15, 20, 25, 30, 40, 50]

# --- UMFERD B (settu thessa i stadinn thegar A er buin) ---
BUDGETS = [0, 20, 30, 50, 80, 120]

# Hamarkstimi i sekundum EFTIR STAERD KVOTANS. Lagir kvotar leysast
# samstundis og fa lítinn tima; haerri kvotar fa meiri tima svo Gurobi
# nai ad SANNA bestu lausn (gap = 0.00%) i stad thess ad giska.
TIME_BY_BUDGET = {
    0: 60, 20: 900, 30: 1500, 50: 2700, 80: 3600, 120: 3600,
}
DEFAULT_TIME = 900  # notad ef kvoti er ekki i listanum ad ofan

# Skrifa stundatoflu fyrir hvert gildi?
SKRIFA_TOFLUR = True

# Synilegur Gurobi-log? 0 = thogull (maelt med fyrir sopun)
GUROBI_LOG = 0


# ============================================================

def finna_skrar():
    base = os.path.dirname(os.path.abspath(__file__))
    return (
        base,
        mod._find_bokanir(base),
        mod._find_file(base, 'rekstur'),
        mod._find_file(base, 'Stofur', exclude='Stofub'),
        mod._find_file(base, 'Stofub'),
    )


def stodutexti(status):
    return {
        GRB.OPTIMAL: 'Optimal',
        GRB.TIME_LIMIT: 'Timamork',
        GRB.INFEASIBLE: 'Olausanlegt',
        GRB.SOLUTION_LIMIT: 'Lausnamork',
    }.get(status, f'stada {status}')


def ascii_ferill(nidur, breidd=48):
    """Einfalt sulurit i terminalinn."""
    gildi = [r['arekstrar'] for r in nidur if r['arekstrar'] is not None]
    if not gildi:
        return
    hamark = max(gildi)
    print()
    print("  Ferill: arekstrar eftir faerslukvota")
    print("  " + "-" * (breidd + 22))
    for r in nidur:
        if r['arekstrar'] is None:
            continue
        n = int(round(breidd * r['arekstrar'] / hamark)) if hamark else 0
        print(f"  kvoti {r['kvoti']:>3}  {'#' * n:<{breidd}} {r['arekstrar']:>5}")
    print("  " + "-" * (breidd + 22))


def main():
    skrar = finna_skrar()
    base, bokanir, arekstur, stofur, stofubokanir = skrar

    print("Nota skrar ur:", base)
    print(f"Kvotagildi sem verda profud: {BUDGETS}")
    print(f"Vikunumer notud        : {mod.USE_WEEK_NUMBERS}")
    print(f"Sjalfsskorun bonnud    : {mod.ENFORCE_NO_SELF_OVERLAP}")
    print()

    nidur = []

    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', GUROBI_LOG)
        env.setParam('TimeLimit', DEFAULT_TIME)
        env.start()

        # Likanid er byggt EINU SINNI. Fyrir hvert kvotagildi
        # breytum vid bara haegri hlid move_budget-skordunnar,
        # sem er margfalt fljotlegra en ad byggja upp a nytt.
        gamalt = mod.MAX_MOVES
        mod.MAX_MOVES = max(BUDGETS)

        try:
            with HiScheduleOptimizer(env) as opt:
                print("Hledur gogn ...")
                opt.load_data(bokanir, arekstur, stofur, stofubokanir)

                grunnlina, n_par = opt.compute_baseline()
                grunn_sjalfsskaranir = opt.count_self_overlaps(use_solution=False)
                print(f"  GRUNNLINA (nuverandi tafla): {grunnlina}"
                      f"  ({n_par} skarandi por)")
                print(f"  Sjalfsskaranir i grunnlinu  : {grunn_sjalfsskaranir}"
                      f"  (thessar eru fastir samhlida hopar - i lagi)")

                print("Byggir likan (einu sinni) ...")
                opt.build_model()
                opt.model.Params.OutputFlag = GUROBI_LOG
                opt.model.Params.TimeLimit  = DEFAULT_TIME
                print(f"  breytur {opt.model.NumVars} | "
                      f"skordur {opt.model.NumConstrs}")
                print()

                budget_c = opt.model.getConstrByName('move_budget')
                if budget_c is None:
                    raise RuntimeError(
                        "Fann ekki skorduna 'move_budget'. "
                        "Er hi_schedule_optimizer.py rett utgafa?"
                    )

                haus = (f"  {'kvoti':>5}  {'arekstrar':>10}  {'faerslur':>9}"
                        f"  {'baeting':>8}  {'gap':>7}  {'sek':>6}  stada")
                print(haus)
                print("  " + "-" * (len(haus) - 2))

                for k in BUDGETS:
                    budget_c.RHS = k
                    opt.model.Params.TimeLimit = TIME_BY_BUDGET.get(
                        k, DEFAULT_TIME)
                    t0 = time.time()
                    opt.model.optimize()
                    dt = time.time() - t0

                    if opt.model.SolCount == 0:
                        print(f"  {k:>5}  {'engin lausn':>10}")
                        nidur.append({'kvoti': k, 'arekstrar': None,
                                      'faerslur': None, 'baeting': None,
                                      'gap_pct': None, 'sekundur': round(dt, 1),
                                      'stada': stodutexti(opt.model.Status)})
                        continue

                    sol = opt.get_solution()
                    obj = sol['total_student_conflicts']
                    baeting = grunnlina - obj
                    gap = opt.model.MIPGap * 100.0
                    sjalf = opt.count_self_overlaps(use_solution=True)
                    nyjar_sjalfskaranir = sjalf - grunn_sjalfsskaranir

                    merki = ('' if nyjar_sjalfskaranir <= 0
                              else f'  <-- {nyjar_sjalfskaranir} NYJAR SJALFSSKORUN!')
                    print(f"  {k:>5}  {obj:>10}  {sol['n_moves']:>9}"
                          f"  {baeting:>8}  {gap:>6.2f}%  {dt:>6.1f}"
                          f"  {stodutexti(opt.model.Status)}{merki}")

                    nidur.append({
                        'kvoti': k,
                        'arekstrar': obj,
                        'faerslur': sol['n_moves'],
                        'baeting': baeting,
                        'baeting_pct': round(100.0 * baeting / grunnlina, 1)
                                       if grunnlina else 0,
                        'gap_pct': round(gap, 2),
                        'sjalfsskaranir': sjalf,
                        'sekundur': round(dt, 1),
                        'stada': stodutexti(opt.model.Status),
                    })

                    if SKRIFA_TOFLUR:
                        opt.export_new_schedule(
                            os.path.join(base, f'sopun_tafla_{k}.csv')
                        )

        finally:
            mod.MAX_MOVES = gamalt

    # ---- samantekt ----
    df = pd.DataFrame(nidur)
    ut = os.path.join(base, 'sopun_faerslukvoti.csv')
    df.to_csv(ut, index=False, encoding='utf-8')

    ascii_ferill(nidur)

    # jadaravinningur: hvad skilar hver vidbotarfaersla?
    print()
    print("  Jadaravinningur (arekstrar sem hverfa a hverja vidbotarfaerslu)")
    fyrri = None
    for r in nidur:
        if r['arekstrar'] is None:
            continue
        if fyrri is not None:
            d_kv = r['kvoti'] - fyrri['kvoti']
            d_ar = fyrri['arekstrar'] - r['arekstrar']
            per = d_ar / d_kv if d_kv else 0
            print(f"    {fyrri['kvoti']:>3} -> {r['kvoti']:>3} faerslur:"
                  f" {d_ar:>4} arekstrar burt  ({per:.1f} a faerslu)")
        fyrri = r

    print()
    print(f"  Tafla skrifud i: {ut}")

    # ---- graf ef haegt er ----
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        gild = [r for r in nidur if r['arekstrar'] is not None]
        x = [r['kvoti'] for r in gild]
        y = [r['arekstrar'] for r in gild]

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(x, y, marker='o')
        ax.set_xlabel('Leyfdar faerslur (MAX_MOVES)')
        ax.set_ylabel('Vigtadir arekstrar')
        ax.set_title('Arekstrar a moti roskun')
        ax.grid(True, alpha=0.3)
        for xi, yi in zip(x, y):
            ax.annotate(str(yi), (xi, yi),
                        textcoords='offset points', xytext=(0, 8),
                        ha='center', fontsize=8)
        fig.tight_layout()
        png = os.path.join(base, 'sopun_ferill.png')
        fig.savefig(png, dpi=150)
        print(f"  Graf skrifad i : {png}")
    except ImportError:
        print("  (matplotlib ekki uppsett - sleppi grafi.")
        print("   'pip3 install matplotlib' ef thu vilt PNG-mynd)")


if __name__ == '__main__':
    main()
