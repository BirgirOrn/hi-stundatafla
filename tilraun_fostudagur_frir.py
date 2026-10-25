"""
tilraun_fostudagur_frir.py
--------------------------------------------------------------
TILRAUN, ekki endanleg lausn.

Hugmyndin (fra ykkur i timanum): allir daematimar og verklegt eru
sett a EINN dag - fostudag - og ENGINN fyrirlestur ma vera a
fostudegi. Afleiding: hver sa nemandi sem er ekki med daematima
eda verklegt fyrirlestur fer sjalfkrafa fri a fostudegi, af thvi
ekkert er kennt tha nema daematimar/verklegt.

Thetta er STRUKTURBREYTING a leitarrymi likansins, ekki bara ny
skorda. Thess vegna er thetta serstakt skript sem ERFIR fra
HiScheduleOptimizer og skrifar bara yfir EINA adferd
(_compute_candidates), i stad thess ad breyta hi_schedule_optimizer.py.

BREYTIR ENGU i hi_schedule_optimizer.py.

Notkun:
    python3 tilraun_fostudagur_frir.py

Skraadar ut:
    tilraun_fostudagur_tafla.csv
"""

import os
import time

import gurobipy as gp
from gurobipy import GRB

import hi_schedule_optimizer as mod
from hi_schedule_optimizer import HiScheduleOptimizer

# ============================================================
# STILLINGAR
# ============================================================

FRIDAY = 'mið'  # NYTT: breytt ur 'fös' i 'mið' - laegsta heildar-throskun
                # (67 fyrirlestrar ut + 90 daematimar inn = 157, laegst allra daga)

DAY_NAMES = {'mán': 'mánudagur', 'þri': 'þriðjudagur', 'mið': 'miðvikudagur',
             'fim': 'fimmtudagur', 'fös': 'föstudagur'}
DAGUR = DAY_NAMES[FRIDAY]  # notad i utskrift svo textinn se rettur

# Heildar faerslukvoti fyrir thessa tilraun. Tharf ad rumma BAEDI
# thvingudu faerslurnar (daematimar sem thurfa a daginn + fyrirlestrar
# sem thurfa af honum) OG svigrum fyrir venjulega arekstra-hagraedingu.
# Vid stillum hann rausnarlega - markmidid er ad sja hvort hugmyndin
# GENGUR UPP, ekki ad spara faerslur.
MAX_MOVES_TILRAUN = 180
# NYTT: laekkad ur 250. 112 faerslur eru thvingadar (67 fyrirlestrar +
# 45 daematimar), svo 180 skilur eftir 68 valfrjalsar - nog svigrum,
# en minna leitarrymi en 250 gaf. Straejra leitarrymi = hraedari leit.

TIME_LIMIT = None  # NYTT: engin tímamörk - keyrir thar til sannad


class FridayTutorialOptimizer(HiScheduleOptimizer):
    """
    Sama likan og HiScheduleOptimizer, en med thremur ólíkum reglum
    eftir lotugerd:

      - daematimar (tutorial):  AÐEINS a DAGURINN (t.d. midvikudagur)
      - fyrirlestrar (lecture): ALLIR DAGAR NEMA DAGURINN
      - verklegt (lab):         ALGJÖRLEGA FRJALST - hvada dagur sem er,
                                 lika DAGURINN sjalfan, engin bond
      - allt annad:             situr kyrrt eins og fyrr
    """

    def _compute_candidates(self):
        self.candidates = {}
        for idx, row in self.sessions_df.iterrows():
            current = (row['day_code'], row['slot_num'])
            n       = int(row['n_timeslot'])
            size    = int(row['eff_size'])
            etype   = row['event_type']

            if etype not in ('lecture', 'tutorial', 'lab'):
                self.candidates[idx] = [current]
                continue

            if etype == 'lab':
                # ALGJÖRLEGA FRJALST - eins og fyrirlestur en EKKI
                # bannad a DAGURINN heldur.
                cands = [current]
                for (day, start) in self.all_slots:
                    if (day, start) == current:
                        continue
                    if self._room_available(day, start, n, size):
                        cands.append((day, start))

            elif etype == 'tutorial':
                # ADEINS DAGURINN kemur til greina.
                cands = [current] if current[0] == FRIDAY else []
                for (day, start) in self.all_slots:
                    if day != FRIDAY or (day, start) == current:
                        continue
                    if self._room_available(day, start, n, size):
                        cands.append((day, start))

            else:
                # FYRIRLESTUR: allir dagar NEMA DAGURINN.
                cands = [] if current[0] == FRIDAY else [current]
                for (day, start) in self.all_slots:
                    if day == FRIDAY or (day, start) == current:
                        continue
                    if self._room_available(day, start, n, size):
                        cands.append((day, start))

            if not cands:
                cands = [current]

            self.candidates[idx] = cands


def finna_skrar():
    base = os.path.dirname(os.path.abspath(__file__))
    return (
        base,
        mod._find_bokanir(base),
        mod._find_file(base, 'rekstur'),
        mod._find_file(base, 'Stofur', exclude='Stofub'),
        mod._find_file(base, 'Stofub'),
    )


def main():
    base, bokanir, arekstur, stofur, stofubokanir = finna_skrar()
    print("Nota skrar ur:", base)
    print()
    print(f"TILRAUN: allir daematimar/verklegt -> {DAGUR}")
    print(f"         engir fyrirlestrar a {DAGUR}")
    print(f"         faerslukvoti fyrir tilraunina: {MAX_MOVES_TILRAUN}")
    print(f"         hamarkstimi: ENGIN - keyrir thar til sannad")
    print()

    gamalt = mod.MAX_MOVES
    mod.MAX_MOVES = MAX_MOVES_TILRAUN

    try:
        with gp.Env(empty=True) as env:
            env.setParam('OutputFlag', 1)
            # ENGIN TimeLimit sett - Gurobi keyrir sjalfgefid an takmarka
            env.start()

            with FridayTutorialOptimizer(env) as opt:
                print("Hledur gogn ...")
                opt.load_data(bokanir, arekstur, stofur, stofubokanir)

                grunnlina, n_par = opt.compute_baseline()
                print(f"  GRUNNLINA (nuverandi tafla, ohreyfd): {grunnlina}"
                      f"  ({n_par} skarandi por)")

                # -- greina thvingudu faerslurnar FYRIRFRAM, ur gognunum --
                lec_fri = opt.sessions_df[
                    (opt.sessions_df.event_type == 'lecture')
                    & (opt.sessions_df.day_code == FRIDAY)
                ]
                tut_not_fri = opt.sessions_df[
                    (opt.sessions_df.event_type == 'tutorial')
                    & (opt.sessions_df.day_code != FRIDAY)
                ]
                n_lab = (opt.sessions_df.event_type == 'lab').sum()
                thvingad = len(lec_fri) + len(tut_not_fri)
                print()
                print(f"  Fyrirlestrar sem VERDA ad faerast af deginum ({DAGUR}): "
                      f"{len(lec_fri)}")
                print(f"  Daematimar sem VERDA ad faerast A "
                      f"{DAGUR}: {len(tut_not_fri)}")
                print(f"  Verklegt (lab): {n_lab} - ALGJÖRLEGA FRJALST, "
                      f"ekki thvingad")
                print(f"  Thvingadar faerslur samtals (loor fyrir "
                      f"svigrum): {thvingad}")
                print(f"  Svigrum fyrir valfrjalsa arekstra-baetingu: "
                      f"~{MAX_MOVES_TILRAUN - thvingad}")

                print()
                print("Byggir likan ...")
                opt.build_model()
                # LAGFAERT: build_model() i grunnskranni stillir alltaf
                # TimeLimit a SITT EIGID sjalfgefna gildi (300 sek), sem
                # yfirskrifar env-stillinguna sem var sett ad ofan. Verdur
                # ad setja thetta AFTUR hér, annars keyrir leitin bara 300
                # sek i stad TIME_LIMIT sem er stillt efst i skjalinu.
                # ENGIN TIMAMORK - keyrir thar til SANNAD eda thu stodvar
                # hana sjalf(ur) med Ctrl+C. MIPFocus haldid til ad flyta
                # fyrir godum lausnum a leidinni, en ENGIN MIPGap-stodvun -
                # hun heldur afram thar til gap = 0.00%.
                # ATH: build_model() i grunnskranni setur ALLTAF eitthvad
                # TimeLimit sjalft (hvad sem hi_schedule_optimizer.py er
                # stillt a thann stundina). Til ad tryggja RAUNVERULEGA
                # ENGIN tímamörk verdum vid ad yfirskrifa thad beint med
                # GRB.INFINITY - annars fengi keyrslan óvart einhver
                # gomul takmork.
                opt.model.Params.TimeLimit = GRB.INFINITY
                opt.model.Params.MIPFocus = 1
                print(f"  breytur {opt.model.NumVars} | "
                      f"skordur {opt.model.NumConstrs}")

                print("Leysir (ENGIN tímamörk - keyrir thar til sannad, "
                      "getur tekid marga klukkutima) ...")
                t0 = time.time()
                opt.model.optimize()
                dt = time.time() - t0

                status = opt.model.Status
                print()
                print("=" * 60)
                if status == GRB.INFEASIBLE:
                    print("  OLEYSANLEGT.")
                    print(f"  Thetta thydi ad reglan 'allt a {DAGUR}'")
                    print("  gengur EKKI upp med nuverandi herbergja- og")
                    print("  vikuframbodi. Thad er sjalft nidurstada -")
                    print("  hugmyndin tharf ad mildast (t.d. tveir dagar")
                    print("  i stad eins).")
                    print("=" * 60)
                    return

                if opt.model.SolCount == 0:
                    print("  Engin lausn fannst a thessum tima.")
                    print("  Reyndu ad haekka TIME_LIMIT og keyra aftur.")
                    print("=" * 60)
                    return

                sol = opt.get_solution()
                obj = sol['total_student_conflicts']

                print(f"  Stada                 : "
                      f"{'Sannad optimal' if status == GRB.OPTIMAL else 'Timamork (ekki sannad)'}")
                print(f"  Timi                   : {dt:.1f} sek")
                if status != GRB.OPTIMAL:
                    print(f"  Gap                     : "
                          f"{opt.model.MIPGap * 100:.2f}%")
                print()
                print(f"  Arekstrar ({DAGUR}-utgafa) : {obj}")
                print(f"  Grunnlina (ohreyfd tafla)    : {grunnlina}")
                print(f"  Faerslur notadar             : {sol['n_moves']}"
                      f"  (af {MAX_MOVES_TILRAUN} leyfdum)")

                # -- stadfesta ad reglan hafi i raun haldist --
                after = opt.sessions_df.copy()
                pos = {}
                for idx in after.index:
                    for t in opt.candidates[idx]:
                        if (idx, t) in opt.x and opt.x[idx, t].X > 0.5:
                            pos[idx] = t
                            break

                lec_on_fri_after = sum(
                    1 for idx, r in after.iterrows()
                    if r.event_type == 'lecture' and pos[idx][0] == FRIDAY
                )
                tut_on_fri_after = sum(
                    1 for idx, r in after.iterrows()
                    if r.event_type == 'tutorial' and pos[idx][0] == FRIDAY
                )
                tut_total = len(after[after.event_type == 'tutorial'])
                lab_on_fri_after = sum(
                    1 for idx, r in after.iterrows()
                    if r.event_type == 'lab' and pos[idx][0] == FRIDAY
                )
                lab_total = len(after[after.event_type == 'lab'])

                print()
                print("  STADFESTING A REGLUNNI:")
                print(f"    Fyrirlestrar a {DAGUR} eftir      : "
                      f"{lec_on_fri_after}   (a ad vera 0)")
                print(f"    Daematimar a {DAGUR}              : "
                      f"{tut_on_fri_after} / {tut_total}"
                      f"   (a ad vera allir)")
                print(f"    Verklegt a {DAGUR} (upplysing)    : "
                      f"{lab_on_fri_after} / {lab_total}"
                      f"   (algjorlega frjalst - engin krafa)")

                if lec_on_fri_after == 0 and tut_on_fri_after == tut_total:
                    print()
                    print(f"    ==> REGLAN HELDUR. {DAGUR.capitalize()} er nuna")
                    print("        institutionally daematima-dagur -")
                    print("        hver nemandi an daematima")
                    print(f"        er sjalfkrafa frjals a {DAGUR}.")
                    print("        (Verklegt getur enn verid a deginum")
                    print("        af thvi thad var alveg frjalst, en")
                    print("        thad er ekki krafa.)")
                else:
                    print()
                    print("    ==> VIDVORUN: reglan heldur EKKI fullkomlega.")
                    print("        Solver-timamork gaetu hafa komid i veg")
                    print("        fyrir fulla lausn - reyndu haerra")
                    print("        TIME_LIMIT.")

                print("=" * 60)
                print()
                print(f"  Til samanburdar (venjulegt likan, enginn")
                print(f"  dags-thvingun, kvoti 386): 330 arekstrar")
                print(f"  (ur fyrri keyrslu - efri mork, ekki sannad)")
                print()
                kostnadur = obj - 330
                if kostnadur > 0:
                    print(f"  ==> Ad thvinga daematima-dag KOSTAR um thad "
                          f"bil {kostnadur} auka arekstra")
                    print(f"      samanborid vid bestu mogulegu lausn an "
                          f"theirrar thvingunar.")
                else:
                    print(f"  ==> Fostudags-reglan kostar ekkert merkjanlegt "
                          f"i arekstrum!")

                opt.export_new_schedule(
                    os.path.join(base, 'tilraun_fostudagur_tafla.csv'))

    finally:
        mod.MAX_MOVES = gamalt


if __name__ == '__main__':
    main()
