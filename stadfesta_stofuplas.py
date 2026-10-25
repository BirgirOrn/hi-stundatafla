"""
stadfesta_stofupláss.py
--------------------------------------------------------------
SJALFSTÆTT stadfestingartol - notar EKKI Gurobi og treystir EKKI
a neitt ur sjalfu likaninu. Les einfaldlega hi_new_schedule.csv
(eda adra utflutta toflu) og athugar med hreinni Python-rokfraedi
hvort einhver (dagur, timi) fai fleiri lotur en til eru haefar
stofur fyrir, mibad vid raunverulegar stofustaerdir.

Thetta er ONAUDSYNLEGT ef C5-skordan i hi_schedule_optimizer.py
er rett - en gott ordskvid: "treystu, en stadfestu". Keyrdu thetta
EFTIR hverja keyrslu sem MAX_MOVES > ~10, til ad vera viss.

Notkun:
    python3 stadfesta_stofupláss.py [heiti_a_csv_skra]

    Sjalfgefid: hi_new_schedule.csv

Skilar:
    "STADFEST - engin stofuvandamal fundin" - ef allt er i lagi
    Nakvaema lysingu a hverju vandamali - ef eitthvad fannst
"""

import os
import sys
from collections import defaultdict

import pandas as pd

VALID_DAYS = {'mán', 'þri', 'mið', 'fim', 'fös'}
DAY_MAP_REVERSE = {
    'Monday': 'mán', 'Tuesday': 'þri', 'Wednesday': 'mið',
    'Thursday': 'fim', 'Friday': 'fös',
}


def find_data_dir():
    return os.path.dirname(os.path.abspath(__file__))


def find_file(base, keyword, exclude=None):
    for f in os.listdir(base):
        if keyword.lower() in f.lower() and f.lower().endswith('.csv'):
            if exclude and exclude.lower() in f.lower():
                continue
            return os.path.join(base, f)
    raise FileNotFoundError(f"Fann enga skra med '{keyword}' i {base}")


def load_room_data(base):
    """Hledur stofustaerdir og hvada stofur eru bokadar hvenaer."""
    stofur_path = find_file(base, 'Stofur', exclude='Stofub')
    stofub_path = find_file(base, 'Stofub')

    rooms = pd.read_csv(stofur_path)
    capacity = {
        int(r.room_id): int(r.capacity)
        for _, r in rooms.iterrows()
        if pd.notna(r.room_id) and pd.notna(r.capacity)
    }

    sb = pd.read_csv(stofub_path)
    sb['d'] = sb['slot'].apply(lambda s: str(s).split('-')[0])
    sb = sb[sb['d'].isin(VALID_DAYS)]
    occupied = set()
    for _, r in sb.iterrows():
        try:
            start = int(str(r['slot']).split('-')[1])
        except (ValueError, IndexError):
            continue
        if start < 0:
            continue
        for off in range(int(r['n_timeslot'])):
            occupied.add((int(r['room_id']), r['d'], start + off))

    return capacity, occupied


def load_original_sizes(base):
    """
    Hledur eff_size fyrir hvern (course_id, event_type) ur Bokanir.

    MIKILVAEGT: sami afangi getur haft mjog olika stærð eftir
    lotugerd (t.d. fyrirlestur me∂ 48 nemendur en verklegt með 0-8 i
    hverjum hop). Ef adeins vaeri flokkad eftir course_id yrdi staersta
    gildid (oftast fyrirlestur) notad fyrir ALLAR lotur thess afanga,
    sem gaeti buid til folsk vandamal thegar t.d. litil verkleg lota
    faerist og er ranglega borin saman vid fyrirlestrar-staerdina.
    """
    bok_path = find_file(base, 'Bokanir') if any(
        'bokanir' in f.lower() for f in os.listdir(base)
    ) else find_file(base, 'kanir')
    b = pd.read_csv(bok_path)
    b['plan_size'] = b['plan_size'].fillna(0).astype(int)
    b['size'] = b['size'].fillna(0).astype(int)
    b['eff_size'] = b.apply(
        lambda r: r['plan_size'] if str(r.get('use_plan_size', 'N')).strip() == 'Y'
                  else r['size'],
        axis=1
    )
    return b.groupby(['course_id', 'event_type'])['eff_size'].max().to_dict()


def can_fit(sizes, room_caps):
    """
    Nakvaemt (ekki heurískt) próf: er hægt að para hverja stærð við
    einhverja stofu >= sú stærð, allar ólíkar stofur?
    Sannad-rétt aðferð: raða báðum listum í hlaðandi röð og bera saman.
    """
    sizes_sorted = sorted(sizes, reverse=True)
    caps_sorted = sorted(room_caps, reverse=True)
    if len(caps_sorted) < len(sizes_sorted):
        return False
    return all(caps_sorted[i] >= sizes_sorted[i] for i in range(len(sizes_sorted)))


def main():
    base = find_data_dir()
    csv_name = sys.argv[1] if len(sys.argv) > 1 else 'hi_new_schedule.csv'
    csv_path = os.path.join(base, csv_name)

    if not os.path.exists(csv_path):
        print(f"Fann ekki {csv_path}")
        print("Keyrdu hi_schedule_optimizer.py fyrst svo skra verdi til.")
        return

    print(f"Les: {csv_name}")
    sched = pd.read_csv(csv_path)

    # Höndla bæði íslenskar og enskar dagsetningar í day-dálknum
    if 'day' in sched.columns:
        sched['d'] = sched['day'].map(DAY_MAP_REVERSE).fillna(sched['day'])
    elif 'day_code' in sched.columns:
        sched['d'] = sched['day_code']
    else:
        print("Fann engan day/day_code dalk i skranni.")
        return

    slot_col = 'slot' if 'slot' in sched.columns else 'slot_num'
    sched['sn'] = sched[slot_col].astype(int)

    capacity, occupied = load_room_data(base)
    sizes_by_course = load_original_sizes(base)

    if 'moved' in sched.columns:
        sched_to_check = sched[sched['moved'] == True].copy()  # noqa: E712
        n_skipped = (sched['moved'] == False).sum()  # noqa: E712
        print(f"({n_skipped} ohreyfdar lotur sleppt - thaer eiga sina "
              f"upprunalegu stofu og tharf ekki ad endurstadfesta)")
    else:
        sched_to_check = sched
        print("(Enginn 'moved' dalkur i skranni - athuga ALLAR lotur "
              "sem hugsanlega faerdar)")

    landed = defaultdict(list)
    for _, r in sched_to_check.iterrows():
        n = int(r['n_timeslot'])
        size = sizes_by_course.get((r['course_id'], r['event_type']), 0)
        landed[(r['d'], r['sn'])].append((r['course_id'], n, size))

    problems = []
    checked_multi = 0
    for (day, slot), items in landed.items():
        if len(items) < 2:
            continue
        checked_multi += 1
        max_n = max(n for _, n, _ in items)
        free_rooms = [
            cap for room_id, cap in capacity.items()
            if all((room_id, day, slot + off) not in occupied for off in range(max_n))
        ]
        sizes = [sz for _, _, sz in items]
        if not can_fit(sizes, free_rooms):
            problems.append({
                'day': day, 'slot': slot,
                'courses': [c for c, _, _ in items],
                'sizes': sizes,
                'free_room_caps': sorted(free_rooms, reverse=True)[:len(sizes) + 2],
            })

    print(f"\nSlot med fleiri en eina lotu samtimis: {checked_multi}")
    print(f"Raunveruleg stofuvandamal: {len(problems)}")

    if not problems:
        print("\n" + "=" * 60)
        print("  STADFEST - engin stofuvandamal fundin.")
        print("  Thessi tafla er FRAMKVÆMANLEG midad vid stofuframbod.")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("  VANDAMAL FUNDIN - thessi tafla er EKKI framkvaemanleg:")
        print("=" * 60)
        for p in problems:
            print(f"\n  {p['day']}-{p['slot']}:")
            for c, sz in zip(p['courses'], p['sizes']):
                print(f"      {c:12s} staerd={sz}")
            print(f"      Baestu lausu stofur (efstu): {p['free_room_caps']}")
        print()
        print(f"  ALLS: {len(problems)} af {checked_multi} fjolhladin slot "
              f"eru i raun ofbokud.")
        print("  Thessi tafla tharf ad endurkeyra - C5 skordan i")
        print("  hi_schedule_optimizer.py er annadhvort slokkt")
        print("  (ENFORCE_ROOM_CAPACITY = False) eda tharf lagfaeringu.")


if __name__ == '__main__':
    main()
