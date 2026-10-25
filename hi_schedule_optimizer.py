"""
University of Iceland (Háskóli Íslands) - Schedule Optimizer
hi_schedule_optimizer.py

Uses Mixed-Integer Linear Programming (MILP) to find the best reassignment
of a limited number of course sessions to alternative time slots, minimising
the total number of students affected by scheduling conflicts.

Approach (Option C - Hybrid):
  - Start from the existing schedule as a baseline.
  - Allow up to MAX_MOVES lecture sessions to be moved to a different slot.
  - Hard rules: every moved session must fit in an available room with
    sufficient capacity.
  - Objective: minimise the weighted sum of student clashes across all
    conflicting course pairs.
"""

import os
import json
import pandas as pd
import gurobipy as gp
from gurobipy import GRB

# ============================================================
# CONFIGURATION PARAMETERS  (adjust here)
# ============================================================
MAX_MOVES = 40     # Maximum number of sessions the model may relocate
TIME_LIMIT     = 1800    # Gurobi time limit in seconds
OUTPUT_FLAG    = 1      # 1 = show Gurobi log, 0 = silent

# ---- NYTT ---------------------------------------------------
# Taka tillit til vikunumera. Tvaer lotur rekast adeins a ef thaer
# eru a sama tima OG eiga minnst eina sameiginlega kennsluviku.
USE_WEEK_NUMBERS = True

# Banna afanga ad rekast a sjalfan sig (tvaer lotur sama afanga
# mega ekki lenda a sama tima i somu viku).
ENFORCE_NO_SELF_OVERLAP = True

# ---- NYTT: alvoru stofu-audlindarskorda ----
# Kemur i veg fyrir ad lausnarinn setji fleiri lotur a sama (dag,tima)
# en til eru haefar stofur fyrir - sja C5 ny fyrir nedan fyrir details.
# Uppgotvad 2026: eldri utgafur af thessari skra gatu skilad "gildum"
# lausnum sem voru i raun ekki framkvaemanlegar (fleiri lotur en stofur
# a sama slot). Thetta VERDUR ad vera True fyrir allar alvoru keyrslur.
ENFORCE_ROOM_CAPACITY = True
# -------------------------------------------------------------

# ---- NYTT: refsingar fyrir snemma/seina tima ----
# Sami hugsunarhattur og upphaflega verkefnislysingin thin badst um
# (W_EARLY, W_LATE), en nuna a stofnanamaelikvarda.
#
# Refsingin er HLUTFALLSLEG vid hversu langt fyrir utan threpskuldinn
# lotan er, i minutum - ekki flatt gjald. Kveikt/slokkt med vigtunum:
# settu badar a 0.0 til ad fa gomlu hegdunina (bara arekstrar).
EARLY_THRESHOLD_MIN = 9 * 60    # 09:00 - lotur sem byrja fyrir thetta fa refsingu
LATE_THRESHOLD_MIN  = 16 * 60   # 16:00 - lotur sem enda eftir thetta fa refsingu

# ATH um vigtir: hrair heildar-arekstravigt (1191) og hraar heildar-
# tima-minutur (thusundir) eru a allt odrum skala. Ef W_EARLY/W_LATE
# eru 1.0/3.0 (eins og upphaflega verkefnislysingin bad um) yfirgnaefir
# tima-lidurinn arekstralidinn ~20x, og lausnarinn haettir ad hugsa um
# arekstra. Thess vegna eru sjalfgefnu gildin her mikid laegri, stillt
# svo badir lidir voru a svipudum skala vid grunnlinuna (~1191 hvor).
#
# W_EARLY = 0.0 -> "byrja fyrir 09:00" er EKKI refsad. Akvordun tekin
# eftir umraedu: fokusa a "enginn timi eftir 16" eitt og ser.
W_EARLY = 0.0    # slokkt - snemma-tima refsing ekki notud
W_LATE  = 0.0   # vigt a hverja minutu sem lota endar eftir 16:00

SLOT0_START_MIN = 8 * 60 + 20   # slot 0 byrjar 08:20
SLOT_LENGTH_MIN = 50            # hvert slot er 50 minutur
# --------------------------------------------------------


def slot_start_min(slot_num):
    """Klukkan hvad (i minutum fra midnaetti) byrjar thetta slot."""
    return SLOT0_START_MIN + slot_num * SLOT_LENGTH_MIN


def time_penalty(slot_num, n_timeslot):
    """
    Reiknar (snemma_refsing, seina_refsing) i MINUTUM fyrir lotu sem
    byrjar i slot_num og varir n_timeslot einingar.
    Hvor refsing er 0 ef lotan er innan thraepskuldanna.
    """
    start = slot_start_min(slot_num)
    end   = start + n_timeslot * SLOT_LENGTH_MIN
    early = max(0, EARLY_THRESHOLD_MIN - start)
    late  = max(0, end - LATE_THRESHOLD_MIN)
    return early, late

# Only lecture sessions are candidates for relocation
MOVEABLE_TYPES = {'lecture'}
# NYTT: daematimar og verklegt mega nuna lika hreyfast. Samhlida hopar
# (t.d. STÆ108G, EÐL108G) eru vardir gegn sjalfsskorunarskordunni med
# kyrrstaedri greiningu - sja _detect_parallel_groups().

# ---- NYTT ---------------------------------------------------
# Lotugerdir sem eru TALDAR MED i markfallinu. Their sem eru her
# en ekki i MOVEABLE_TYPES eru njorvadar nidur i sin nuverandi slot:
# likanid ser tha og fordast ad lenda a theim, en faerir tha ekki.
#
# {'lecture'}                     -> gamla hegdunin, grunnlina 449
# {'lecture','tutorial','lab'}    -> oll kennsla talin, grunnlina 1191
COUNTED_TYPES = {'lecture', 'tutorial', 'lab'}
# -------------------------------------------------------------

# Saturday sessions are never moved
VALID_DAYS = {'mán', 'þri', 'mið', 'fim', 'fös'}

# Human-readable day names for output
DAY_MAP = {
    'mán': 'Monday',   'þri': 'Tuesday',  'mið': 'Wednesday',
    'fim': 'Thursday', 'fös': 'Friday',   'lau': 'Saturday',
}


# ============================================================
# NYTT: vikunumer
# ============================================================

def parse_weeks(value):
    """
    Breytir week_numbers-dalknum i mengi af heiltolum.

    Daemi:  "34::35::36"  ->  {34, 35, 36}
            "33"          ->  {33}
            tomt / NaN    ->  None  (thydir "allar vikur")

    None er notad sem "veit ekki" og telst tha alltaf skarast,
    svo likanid verdi aldrei of bjartsynt.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == '' or s.lower() == 'nan':
        return None
    weeks = set()
    for part in s.split('::'):
        part = part.strip()
        if part.isdigit():
            weeks.add(int(part))
    return weeks if weeks else None


def weeks_overlap(w1, w2):
    """True ef loturnar eiga minnst eina sameiginlega viku."""
    if not USE_WEEK_NUMBERS:
        return True
    if w1 is None or w2 is None:
        return True          # oviss gogn -> gerum rad fyrir skorun
    return bool(w1 & w2)


# ============================================================
# MAIN OPTIMIZER CLASS
# ============================================================

class HiScheduleOptimizer:
    """
    University of Iceland Schedule Optimizer.

    Builds and solves a MILP that reassigns up to MAX_MOVES lecture sessions
    to new time slots in order to reduce student scheduling conflicts.
    """

    def __init__(self, env):
        """Initialise the optimizer with a Gurobi environment."""
        self.env = env
        self.model = None

        # --- raw data (set by load_data or set_data) ---
        self.sessions_df    = None   # DataFrame: one row per moveable session
        self.conflict_pairs = {}     # {(c1, c2): actual_student_count}
        self.room_capacity  = {}     # {room_id: capacity}
        self.room_occupied  = set()  # {(room_id, day_code, slot_num)}

        # --- derived data ---
        self.all_slots   = []   # sorted list of (day_code, slot_num)
        self.candidates  = {}   # {session_idx: [(day, slot), ...]}
        self.free_rooms  = {}   # {(day, slot, n_timeslot): set of room_ids}

        # --- model variables ---
        self.x     = {}   # {(sess_idx, (day, slot)): Binary var}
        self.clash = {}   # {(c1, c2): Binary var}

    # ----------------------------------------------------------
    # Context manager support
    # ----------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.model is not None:
            self.model.dispose()
        return False

    # ----------------------------------------------------------
    # Data loading from files
    # ----------------------------------------------------------

    def load_data(self, bokanir_path, arekstrar_path,
                  stofur_path, stofubokanir_path):
        """Load all input files and preprocess data."""
        self._load_sessions(bokanir_path)
        self._load_conflict_matrix(arekstrar_path)
        self._load_rooms(stofur_path)
        self._load_room_bookings(stofubokanir_path)
        self._precompute_room_availability()

    def _load_sessions(self, path):
        """
        Load session bookings from the Bókanir sheet.
        Keeps only moveable event types on valid weekdays.
        Deduplicates by activity_code (multi-room broadcasts become one row).
        """
        df = pd.read_csv(path)

        # Keep every session type that counts towards the objective.
        # Those not in MOVEABLE_TYPES are pinned later in _compute_candidates.
        df = df[df['event_type'].isin(COUNTED_TYPES)].copy()
        df['day_code'] = df['slot'].apply(lambda s: str(s).split('-')[0])
        df = df[df['day_code'].isin(VALID_DAYS)].copy()
        df['slot_num'] = df['slot'].apply(lambda s: int(str(s).split('-')[1]))
        df['n_timeslot'] = df['n_timeslot'].astype(int)

        # NYTT: lesa vikunumer inn sem mengi
        if 'week_numbers' in df.columns:
            df['weeks'] = df['week_numbers'].apply(parse_weeks)
        else:
            df['weeks'] = None

        # Determine enrolment size: use plan_size when flagged
        df['plan_size'] = df['plan_size'].fillna(0).astype(int)
        df['size']      = df['size'].fillna(0).astype(int)
        df['eff_size']  = df.apply(
            lambda r: r['plan_size'] if str(r.get('use_plan_size', 'N')).strip() == 'Y'
                      else r['size'],
            axis=1
        )

        # Deduplicate multi-room rows: keep the row with the largest eff_size
        df = df.sort_values('eff_size', ascending=False)
        df = df.drop_duplicates(subset=['activity_code'], keep='first')
        df = df.reset_index(drop=True)

        self.sessions_df = df
        self.all_slots = sorted({(r.day_code, r.slot_num)
                                  for _, r in df.iterrows()})

    def _load_conflict_matrix(self, path):
        """
        Load the Árekstur conflict matrix.

        CSV layout:
          Row 0: garbage header row (pandas artefact)
          Row 1: actual header  -> col 0 = 'course_short_id', cols 4+ = course IDs
          Row 2+: data          -> col 0 = course_id, cols 4+ = 'expected,actual'
        """
        raw = pd.read_csv(path, header=None)

        # Find the header row (contains 'course_short_id' in col 0)
        hdr_idx = None
        for i in range(min(5, len(raw))):
            if str(raw.iloc[i, 0]).strip() == 'course_short_id':
                hdr_idx = i
                break
        if hdr_idx is None:
            self.conflict_pairs = {}
            return

        # Column course IDs (cols 4 onwards of the header row)
        col_courses = [str(v).strip() for v in raw.iloc[hdr_idx, 4:].tolist()]

        conflict_pairs = {}
        for i in range(hdr_idx + 1, len(raw)):
            row = raw.iloc[i]
            c1 = str(row.iloc[0]).strip()
            if not c1 or c1 == 'nan':
                continue

            for j, c2 in enumerate(col_courses):
                if not c2 or c2 == 'nan':
                    continue
                if c1 == c2:
                    continue
                val = row.iloc[4 + j]
                if pd.isna(val):
                    continue

                val_str = str(val).strip()
                if ',' not in val_str:
                    continue
                try:
                    actual = int(val_str.split(',')[1].strip())
                    if actual > 0:
                        key = tuple(sorted([c1, c2]))
                        conflict_pairs[key] = max(
                            conflict_pairs.get(key, 0), actual
                        )
                except (ValueError, IndexError):
                    pass

        self.conflict_pairs = conflict_pairs

    def _load_rooms(self, path):
        """Load room capacities from the Stofur sheet."""
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            if pd.notna(row['room_id']) and pd.notna(row['capacity']):
                self.room_capacity[int(row['room_id'])] = int(row['capacity'])

    def _load_room_bookings(self, path):
        """
        Build a room-occupancy lookup from the Stofubókanir sheet.
        Records every (room_id, day_code, slot_num) triple that is booked.
        Negative slot numbers (data artefacts) are skipped.
        """
        df = pd.read_csv(path)
        df['day_code'] = df['slot'].apply(lambda s: str(s).split('-')[0])
        df = df[df['day_code'].isin(VALID_DAYS)]
        df['slot_num'] = df['slot'].apply(
            lambda s: int(str(s).split('-')[1]) if str(s).split('-')[1].lstrip('-').isdigit()
                      else -1
        )
        df = df[df['slot_num'] >= 0]

        for _, row in df.iterrows():
            start = int(row['slot_num'])
            n     = int(row['n_timeslot'])
            r     = int(row['room_id'])
            day   = row['day_code']
            for offset in range(n):
                self.room_occupied.add((r, day, start + offset))

    # ----------------------------------------------------------
    # Pre-computation
    # ----------------------------------------------------------

    def _precompute_room_availability(self):
        """
        For every (day, start_slot, duration) combination, compute which
        rooms are completely free across the required slot range.
        This avoids repeated inner loops during candidate generation.
        """
        if not self.all_slots:
            return

        max_n = (self.sessions_df['n_timeslot'].max()
                 if self.sessions_df is not None else 10)

        for (day, start) in self.all_slots:
            for n in range(1, int(max_n) + 1):
                free = set()
                for room_id, cap in self.room_capacity.items():
                    if all((room_id, day, start + off) not in self.room_occupied
                           for off in range(n)):
                        free.add(room_id)
                self.free_rooms[(day, start, n)] = free

    def _room_available(self, day, start, n_timeslot, min_cap):
        """Return True if at least one suitable room is free for the slot."""
        free = self.free_rooms.get((day, start, n_timeslot), set())
        return any(self.room_capacity.get(r, 0) >= min_cap for r in free)

    # ----------------------------------------------------------
    # Direct data injection (used by unit tests)
    # ----------------------------------------------------------

    def set_data(self, sessions_df, conflict_pairs,
                 room_capacity, room_occupied):
        """
        Inject data directly (bypassing file I/O).
        Used for unit testing with small synthetic datasets.

        sessions_df must have columns:
            course_id, event_type, day_code, slot_num, n_timeslot, eff_size
        """
        self.sessions_df    = sessions_df.reset_index(drop=True)
        self.conflict_pairs = conflict_pairs
        self.room_capacity  = room_capacity
        self.room_occupied  = room_occupied

        # NYTT: ef prufugogn hafa engan weeks-dalk tha telst
        # hver lota vera i "ollum vikum" og hegdunin verdur oborytt
        if 'weeks' not in self.sessions_df.columns:
            self.sessions_df['weeks'] = None

        self.all_slots = sorted({(r.day_code, r.slot_num)
                                  for _, r in sessions_df.iterrows()})
        self._precompute_room_availability()

    # ----------------------------------------------------------
    # Candidate slot generation
    # ----------------------------------------------------------

    def _compute_candidates(self):
        """
        For each session, build the list of valid target slots.
        Current slot is always included (the session may stay in place).
        """
        self.candidates = {}
        for idx, row in self.sessions_df.iterrows():
            current = (row['day_code'], row['slot_num'])
            n       = int(row['n_timeslot'])
            size    = int(row['eff_size'])
            cands   = [current]

            # NYTT: lotur sem eru taldar en ekki faeranlegar (daematimar,
            # verklegt) fa adeins sitt eigid slot -> thaer eru fastir tappar
            if row['event_type'] not in MOVEABLE_TYPES:
                self.candidates[idx] = cands
                continue

            for (day, start) in self.all_slots:
                if (day, start) == current:
                    continue
                if self._room_available(day, start, n, size):
                    cands.append((day, start))

            self.candidates[idx] = cands

    # ----------------------------------------------------------
    # Model construction
    # ----------------------------------------------------------

    def build_model(self):
        """Build the full MILP model."""
        self.model = gp.Model("HI_Schedule_Optimizer", env=self.env)
        self.model.Params.TimeLimit  = TIME_LIMIT
        self.model.Params.OutputFlag = OUTPUT_FLAG

        self._compute_candidates()
        self._detect_parallel_groups()
        self._create_variables()
        self._add_constraints()
        self._set_objective()
        self.model.update()

    def _detect_parallel_groups(self):
        """
        NYTT: finnur sjalfkrafa hvada tvaer lotur (sama afangi, sama
        lotugerd) eru i raun SAMHLIDA HOPAR i NUVERANDI toflu - the.
        sami dagur+timi+vika, en ADSKILDAR stofur. Thetta er visvitandi
        kyrrstaett: adeins pör sem eru THEGAR samhliða i dag fa
        undanthagu fra sjalfsskorunarskordunni. Ef lausnarinn sjalfur
        vildi seinna stinga tveimur OTENGDUM hopum saman a nyjan
        sameiginlegan tima, telst thad ENN sjalfsskorun og er bannad -
        annars vaeri thetta ny gataleid sambaerileg theirri sem C4 var
        buin til ad loka.
        """
        self.parallel_pairs = set()
        by_key = {}
        for idx, row in self.sessions_df.iterrows():
            by_key.setdefault((row['course_id'], row['event_type']), []).append(idx)

        for key, idxs in by_key.items():
            if len(idxs) < 2:
                continue
            for a in range(len(idxs)):
                i = idxs[a]
                ri = self.sessions_df.loc[i]
                for b in range(a + 1, len(idxs)):
                    j = idxs[b]
                    rj = self.sessions_df.loc[j]
                    if ri['room'] == rj['room']:
                        continue
                    if self._overlap(
                        ri['day_code'], ri['slot_num'], int(ri['n_timeslot']),
                        rj['day_code'], rj['slot_num'], int(rj['n_timeslot']),
                        ri['weeks'], rj['weeks'],
                    ):
                        self.parallel_pairs.add((min(i, j), max(i, j)))


    def _create_variables(self):
        """Create all binary decision variables."""
        # x[sess_idx, (day, slot)] = 1 iff session is placed in that slot
        for idx in self.sessions_df.index:
            for t in self.candidates[idx]:
                self.x[idx, t] = self.model.addVar(
                    vtype=GRB.BINARY,
                    name=f"x_{idx}_{t[0]}_{t[1]}"
                )

        # clash[(c1, c2)] = 1 iff the two courses have at least one pair of
        # sessions that overlap after the reassignment
        course_set = set(self.sessions_df['course_id'].unique())
        for (c1, c2) in self.conflict_pairs:
            if c1 in course_set and c2 in course_set:
                safe_c1 = c1.replace('-', '_')
                safe_c2 = c2.replace('-', '_')
                self.clash[c1, c2] = self.model.addVar(
                    vtype=GRB.BINARY,
                    name=f"clash_{safe_c1}_{safe_c2}"
                )

    @staticmethod
    def _overlap(day1, s1, n1, day2, s2, n2, w1=None, w2=None):
        """
        True ef loturnar tvaer skarast i raun.

        Thrju skilyrdi verda oll ad vera uppfyllt:
          1. sami vikudagur
          2. timabilin skarast
          3. minnst ein sameiginleg kennsluvika   <- NYTT

        w1/w2 eru mengi vikunumera (eda None = allar vikur).
        """
        if day1 != day2:
            return False
        if not (s1 < s2 + n2 and s2 < s1 + n1):
            return False
        return weeks_overlap(w1, w2)

    def _add_constraints(self):
        """Add all hard constraints to the model."""
        # --- C1: Each session assigned to exactly one slot ---
        for idx in self.sessions_df.index:
            self.model.addConstr(
                gp.quicksum(self.x[idx, t] for t in self.candidates[idx]) == 1,
                name=f"assign_{idx}"
            )

        # --- C2: Move budget ---
        current_slot = {
            idx: (row['day_code'], row['slot_num'])
            for idx, row in self.sessions_df.iterrows()
        }
        self.model.addConstr(
            gp.quicksum(
                self.x[idx, t]
                for idx in self.sessions_df.index
                for t in self.candidates[idx]
                if t != current_slot[idx]
            ) <= MAX_MOVES,
            name="move_budget"
        )

        # --- C3: Clash propagation constraints ---
        # clash[c1,c2] >= x[s1,t1] + x[s2,t2] - 1
        # for every (session pair, slot pair) that would overlap

        # Build course -> list of session indices
        c2sess = {}
        for idx, row in self.sessions_df.iterrows():
            c2sess.setdefault(row['course_id'], []).append(idx)

        for (c1, c2) in self.clash:
            for s1 in c2sess.get(c1, []):
                n1 = int(self.sessions_df.loc[s1, 'n_timeslot'])
                w1 = self.sessions_df.loc[s1, 'weeks']
                for s2 in c2sess.get(c2, []):
                    n2 = int(self.sessions_df.loc[s2, 'n_timeslot'])
                    w2 = self.sessions_df.loc[s2, 'weeks']
                    for t1 in self.candidates[s1]:
                        for t2 in self.candidates[s2]:
                            if self._overlap(t1[0], t1[1], n1,
                                             t2[0], t2[1], n2, w1, w2):
                                if (s1, t1) in self.x and (s2, t2) in self.x:
                                    sc1 = c1.replace('-', '_')
                                    sc2 = c2.replace('-', '_')
                                    self.model.addConstr(
                                        self.clash[c1, c2] >=
                                        self.x[s1, t1] + self.x[s2, t2] - 1,
                                        name=(f"cl_{sc1}_{sc2}"
                                              f"_{s1}_{t1[0]}{t1[1]}"
                                              f"_{s2}_{t2[0]}{t2[1]}")
                                    )

        # --- C4 (NYTT): afangi ma ekki rekast a sjalfan sig ---
        # Fyrir hverjar tvaer lotur SAMA afanga: thaer mega aldrei
        # baðar lenda a stodum sem skarast (sami dagur, sami timi,
        # sameiginleg vika). Thetta lokar glufunni sem let lausnarann
        # laekka markfallid med thvi ad stafla kennslu ofan a sjalfa sig.
        self.n_self_constraints = 0
        if ENFORCE_NO_SELF_OVERLAP:
            for course, idx_list in c2sess.items():
                for a in range(len(idx_list)):
                    s1 = idx_list[a]
                    n1 = int(self.sessions_df.loc[s1, 'n_timeslot'])
                    w1 = self.sessions_df.loc[s1, 'weeks']
                    for b in range(a + 1, len(idx_list)):
                        s2 = idx_list[b]

                        # Ef BAÐAR loturnar eru fastar getum vid ekkert
                        # gert i thvi hvort thaer skarast. Ad setja skordu
                        # a thaer myndi gera likanid olausanlegt.
                        if (len(self.candidates[s1]) == 1
                                and len(self.candidates[s2]) == 1):
                            continue

                        # NYTT: thekktir samhlida hopar (t.d. STÆ108G
                        # daematimar i tveimur stofum) mega vera a
                        # skarandi tima - thad er tilgangurinn med theim.
                        if (min(s1, s2), max(s1, s2)) in self.parallel_pairs:
                            continue

                        n2 = int(self.sessions_df.loc[s2, 'n_timeslot'])
                        w2 = self.sessions_df.loc[s2, 'weeks']
                        for t1 in self.candidates[s1]:
                            for t2 in self.candidates[s2]:
                                if self._overlap(t1[0], t1[1], n1,
                                                 t2[0], t2[1], n2, w1, w2):
                                    self.model.addConstr(
                                        self.x[s1, t1] + self.x[s2, t2] <= 1,
                                        name=f"self_{s1}_{s2}_"
                                             f"{t1[0]}{t1[1]}_{t2[0]}{t2[1]}"
                                    )
                                    self.n_self_constraints += 1

        # --- C5 (leidrett i dag): stofu-audlindarskorda ---
        #
        # VANDAMAL SEM THETTA LEYSIR: _room_available() athugar adeins
        # hvort EIN staka lota eigi einhverja lausa haefa stofu, midad vid
        # KYRRSTAED bokunargogn. Hun tekur ALDREI eftir thvi ef lausnarinn
        # sjalfur setur TVAER eda fleiri lotur a sama (dag, tima) - hver
        # athugun er blind a hinar.
        #
        # FYRRI UTGAFA (i dag) haf∂i villu: hun reiknadi lausar stofur
        # PUNKT-FYRIR-PUNKT i timanum, an tillits til LENGDAR lotunnar.
        # Stofa sem var laus a EINU tímabili en upptekin a thvi naesta
        # taldist "laus" fyrir thriggja-eininga lotu sem thurfti hana
        # samfellt yfir oll thrju - sem er rangt. Thetta faldist vid
        # lagan faerslukvota (fair langar lotur a hreyfingu) en birtist
        # skyrt vid haerri kvota.
        #
        # LEIDRETT UTGAFA: hopa lotum eftir NAKVAEMUM byrjunarpunkti
        # (day,start) - ekki staka tímaeiningu - og nota self.free_rooms
        # sem er THEGAR reiknad rett m.t.t. lengdar (fra
        # _precompute_room_availability). Fyrir hvern (day,start) er
        # notud staersta lengd sem einhver samkeppnisadili tharf, sem er
        # oruggt (íhaldssamt): stofa sem er laus lengur er lika laus
        # skemur, svo thetta bannar aldrei loglega samsetningu.
        self.n_room_constraints = 0
        if ENFORCE_ROOM_CAPACITY:
            thresholds = sorted(set(self.room_capacity.values()))

            by_start = {}
            for idx in self.sessions_df.index:
                size = int(self.sessions_df.loc[idx, 'eff_size'])
                n = int(self.sessions_df.loc[idx, 'n_timeslot'])
                current = (self.sessions_df.loc[idx, 'day_code'],
                           self.sessions_df.loc[idx, 'slot_num'])
                for t in self.candidates[idx]:
                    if t == current:
                        continue  # ad vera kyrr tharf enga nyja stofu
                    by_start.setdefault(t, []).append((idx, n, size))

            for t, items in by_start.items():
                day, start = t
                max_n = max(n for _, n, _ in items)
                free_rooms_here = self.free_rooms.get((day, start, max_n), set())

                for th in thresholds:
                    relevant = [(idx, n) for idx, n, size in items if size > th]
                    if not relevant:
                        continue
                    cap_here = sum(1 for r in free_rooms_here
                                   if self.room_capacity.get(r, 0) > th)
                    if len(relevant) <= cap_here:
                        continue
                    varlist = [self.x[idx, t] for idx, n in relevant]
                    self.model.addConstr(
                        gp.quicksum(varlist) <= cap_here,
                        name=f"roomcap_{day}_{start}_{th}"
                    )
                    self.n_room_constraints += 1

    def _set_objective(self):
        """
        Lagmarka vigtada arekstra PLUS refsingar fyrir snemma/seina tima.

        markfall = Σ (arekstravigt * clash)
                 + W_EARLY * Σ (minutur fyrir 09:00 * x)
                 + W_LATE  * Σ (minutur eftir 16:00 * x)

        Sidustu tveir lidirnir eru reiknadir fyrir HVERJA lotu i
        COUNTED_TYPES - lika thaer sem eru fastar (daematimar, verklegt).
        Fyrir fastar lotur er thetta fast framlag (breytist ekki vid
        hagraedingu) en er samt talid med svo grunnlinan og eftir-talan
        seu a sama maelikvarda.
        """
        arekstra_lidur = gp.quicksum(
            self.conflict_pairs[c1, c2] * self.clash[c1, c2]
            for (c1, c2) in self.clash
        )

        tima_lidur = gp.quicksum(
            (W_EARLY * time_penalty(t[1], int(self.sessions_df.loc[idx, 'n_timeslot']))[0]
             + W_LATE * time_penalty(t[1], int(self.sessions_df.loc[idx, 'n_timeslot']))[1])
            * self.x[idx, t]
            for idx in self.sessions_df.index
            for t in self.candidates[idx]
        )

        self.model.setObjective(arekstra_lidur + tima_lidur, GRB.MINIMIZE)

    # ----------------------------------------------------------
    # Solving
    # ----------------------------------------------------------

    def solve(self):
        """Run the Gurobi solver. Returns the Gurobi status code."""
        if self.model is None:
            raise RuntimeError("Call build_model() before solve().")
        self.model.optimize()
        return self.model.Status

    # ----------------------------------------------------------
    # Solution extraction
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # NYTT: grunnlina og sannreyning
    # ----------------------------------------------------------

    def compute_baseline(self):
        """
        Reiknar markfallid a NUVERANDI stundatoflu, an thess ad leysa neitt.
        Hver lota er latin vera kyrr i sinu slot og talid hvada arekstrapor
        skarast. Thetta er retta samanburdartalan vid nidurstodu likansins.

        Skilar (vigt, fjoldi_para).
        """
        by_course = {}
        for idx, row in self.sessions_df.iterrows():
            by_course.setdefault(row['course_id'], []).append(
                (row['day_code'], int(row['slot_num']),
                 int(row['n_timeslot']), row['weeks'])
            )

        weight = 0
        pairs  = 0
        for (c1, c2), w in self.conflict_pairs.items():
            if c1 not in by_course or c2 not in by_course:
                continue
            hit = any(
                self._overlap(a[0], a[1], a[2], b[0], b[1], b[2], a[3], b[3])
                for a in by_course[c1] for b in by_course[c2]
            )
            if hit:
                weight += w
                pairs  += 1
        return weight, pairs

    def count_self_overlaps(self, use_solution=True):
        """
        Telur hversu oft afangi rekst a sjalfan sig.
        use_solution=False -> telur i nuverandi toflu.
        Aetti alltaf ad vera 0 thegar ENFORCE_NO_SELF_OVERLAP er a.
        """
        pos = {}
        for idx, row in self.sessions_df.iterrows():
            pos[idx] = (row['day_code'], int(row['slot_num']))
        if use_solution and self.model is not None and self.model.SolCount > 0:
            for idx in self.sessions_df.index:
                for t in self.candidates[idx]:
                    if (idx, t) in self.x and self.x[idx, t].X > 0.5:
                        pos[idx] = t
                        break

        by_course = {}
        for idx, row in self.sessions_df.iterrows():
            by_course.setdefault(row['course_id'], []).append(idx)

        n = 0
        for course, idxs in by_course.items():
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    i, j = idxs[a], idxs[b]
                    if self._overlap(
                        pos[i][0], pos[i][1],
                        int(self.sessions_df.loc[i, 'n_timeslot']),
                        pos[j][0], pos[j][1],
                        int(self.sessions_df.loc[j, 'n_timeslot']),
                        self.sessions_df.loc[i, 'weeks'],
                        self.sessions_df.loc[j, 'weeks'],
                    ):
                        n += 1
        return n

    # ----------------------------------------------------------
    # NYTT: sundurlidun a snemma/seina refsingum
    # ----------------------------------------------------------

    def compute_time_penalties(self, use_solution=True):
        """
        Reiknar heildar snemma- og seina-refsingu (i vigtudum minutum)
        i nuverandi toflu (use_solution=False) eda i lausn likansins
        (use_solution=True).

        Skilar dict: {'early_min': .., 'late_min': ..,
                       'early_weighted': .., 'late_weighted': ..}
        """
        pos = {}
        for idx, row in self.sessions_df.iterrows():
            pos[idx] = (row['day_code'], int(row['slot_num']))
        if use_solution and self.model is not None and self.model.SolCount > 0:
            for idx in self.sessions_df.index:
                for t in self.candidates[idx]:
                    if (idx, t) in self.x and self.x[idx, t].X > 0.5:
                        pos[idx] = t
                        break

        early_min = 0
        late_min  = 0
        for idx, row in self.sessions_df.iterrows():
            n = int(row['n_timeslot'])
            e, l = time_penalty(pos[idx][1], n)
            early_min += e
            late_min  += l

        return {
            'early_min': early_min,
            'late_min': late_min,
            'early_weighted': round(W_EARLY * early_min),
            'late_weighted': round(W_LATE * late_min),
        }


    def get_solution(self):
        """
        Extract and return the optimisation result as a dictionary.

        Returns:
            dict with keys:
                status              - Gurobi status code
                is_feasible         - True if any solution was found
                is_optimal          - True if proven optimal
                objective_value     - total weighted student clashes (float)
                total_student_conflicts - rounded integer value
                n_moves             - number of sessions relocated
                moves               - list of dicts describing each move
        """
        if self.model is None or self.model.SolCount == 0:
            return {
                'status': self.model.Status if self.model else None,
                'is_feasible': False,
                'is_optimal': False,
                'objective_value': None,
                'total_student_conflicts': None,
                'n_moves': 0,
                'moves': [],
            }

        # Determine which sessions were moved
        moves = []
        current_slot = {
            idx: (row['day_code'], row['slot_num'])
            for idx, row in self.sessions_df.iterrows()
        }
        for idx, row in self.sessions_df.iterrows():
            cur = current_slot[idx]
            for t in self.candidates[idx]:
                if (idx, t) in self.x and self.x[idx, t].X > 0.5:
                    if t != cur:
                        moves.append({
                            'course_id':   row['course_id'],
                            'course_name': row.get('course_name', ''),
                            'event_type':  row['event_type'],
                            'from_day':    DAY_MAP.get(cur[0], cur[0]),
                            'from_slot':   cur[1],
                            'to_day':      DAY_MAP.get(t[0], t[0]),
                            'to_slot':     t[1],
                        })

        # NYTT: reikna arekstralidinn SERSTAKLEGA ur clash-breytunum,
        # svo 'total_student_conflicts' haldist a sama maelikvarda og
        # adur, jafnvel thott markfallid innihaldi nuna lika tima-refsingar.
        conflict_weighted = sum(
            self.conflict_pairs[c1, c2] * (1 if self.clash[c1, c2].X > 0.5 else 0)
            for (c1, c2) in self.clash
        )
        time_pen = self.compute_time_penalties(use_solution=True)

        obj = self.model.ObjVal
        return {
            'status':                  self.model.Status,
            'is_feasible':             True,
            'is_optimal':              self.model.Status == GRB.OPTIMAL,
            'objective_value':         obj,
            'total_student_conflicts': int(round(conflict_weighted)),
            'early_minutes':           time_pen['early_min'],
            'late_minutes':            time_pen['late_min'],
            'n_moves':                 len(moves),
            'moves':                   moves,
        }

    # ----------------------------------------------------------
    # Output
    # ----------------------------------------------------------

    def export_new_schedule(self, output_path='hi_new_schedule.csv'):
        """Write the optimised schedule to a CSV file."""
        if self.model is None or self.model.SolCount == 0:
            raise RuntimeError("No solution to export. Run solve() first.")

        records = []
        for idx, row in self.sessions_df.iterrows():
            current = (row['day_code'], row['slot_num'])
            assigned = current
            for t in self.candidates[idx]:
                if (idx, t) in self.x and self.x[idx, t].X > 0.5:
                    assigned = t
                    break

            records.append({
                'course_id':   row['course_id'],
                'course_name': row.get('course_name', ''),
                'event_type':  row['event_type'],
                'day':         DAY_MAP.get(assigned[0], assigned[0]),
                'slot':        assigned[1],
                'n_timeslot':  row['n_timeslot'],
                'room':        row.get('room', ''),
                'moved':       assigned != current,
            })

        df = pd.DataFrame(records)
        df.to_csv(output_path, index=False, encoding='utf-8')
        print(f"New schedule written to: {output_path}")
        return df

    def print_summary(self):
        """Print a human-readable optimisation summary to the console."""
        sol = self.get_solution()
        print("=" * 65)
        print("  HASKOLI ISLANDS - SCHEDULE OPTIMISER RESULTS")
        print("=" * 65)

        if not sol['is_feasible']:
            print("  No feasible solution found.")
            print("=" * 65)
            return

        status = "Optimal" if sol['is_optimal'] else "Best found (time limit reached)"
        print(f"  Status                : {status}")
        print(f"  Total student clashes : {sol['total_student_conflicts']}")
        print(f"  Sessions relocated    : {sol['n_moves']}  (budget = {MAX_MOVES})")
        print()

        if sol['moves']:
            print(f"  {'Course':<12}  {'Type':<10}  {'From':<22}  {'To'}")
            print("  " + "-" * 60)
            for m in sol['moves']:
                frm = f"{m['from_day']} slot {m['from_slot']}"
                to  = f"{m['to_day']} slot {m['to_slot']}"
                print(f"  {m['course_id']:<12}  {m['event_type']:<10}  {frm:<22}  {to}")
        else:
            print("  No moves required - current schedule is already optimal.")

        print("=" * 65)


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def _find_bokanir(directory):
    """
    Find the Bokanir CSV file in the given directory.
    Accepts any version: Bokanir.csv, Bokanir (1).csv, Bokanir (4).csv, etc.
    Excludes Stofubokanir files.
    """
    all_files = os.listdir(directory)
    candidates = [
        f for f in all_files
        if 'kanir' in f.lower()
        and 'stofub' not in f.lower()
        and f.endswith('.csv')
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No Bokanir CSV file found in {directory}. "
            "Expected a file with 'kanir' in the name."
        )
    candidates.sort(reverse=True)
    return os.path.join(directory, candidates[0])


def _find_file(directory, keyword, exclude=None):
    """Locate a CSV file in directory whose name contains keyword."""
    for f in sorted(os.listdir(directory)):
        if keyword in f and (exclude is None or exclude not in f) and f.endswith('.csv'):
            return os.path.join(directory, f)
    raise FileNotFoundError(
        f"No file containing '{keyword}' found in {directory}"
    )


def main():
    """Run the full optimisation pipeline on the HI dataset."""
    base = os.path.dirname(os.path.abspath(__file__))

    bokanir_path      = _find_bokanir(base)
    arekstrar_path    = _find_file(base, 'rekstur')
    stofur_path       = _find_file(base, 'Stofur', exclude='Stofub')
    stofubokanir_path = _find_file(base, 'Stofub')

    print(f"  Using Bokanir file : {os.path.basename(bokanir_path)}")
    print(f"  Using Arekstrar    : {os.path.basename(arekstrar_path)}")
    print(f"  Using Stofur       : {os.path.basename(stofur_path)}")
    print(f"  Using Stofubokanir : {os.path.basename(stofubokanir_path)}")
    print()

    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', OUTPUT_FLAG)
        env.setParam('TimeLimit',  TIME_LIMIT)
        env.start()

        with HiScheduleOptimizer(env) as opt:

            print("Loading data ...")
            opt.load_data(bokanir_path, arekstrar_path,
                          stofur_path, stofubokanir_path)

            n_sess   = len(opt.sessions_df)
            n_conf   = len(opt.conflict_pairs)
            n_rooms  = len(opt.room_capacity)
            print(f"  {n_sess} moveable sessions | "
                  f"{n_conf} conflict pairs | "
                  f"{n_rooms} rooms loaded")

            n_move = int(opt.sessions_df['event_type']
                         .isin(MOVEABLE_TYPES).sum())
            print(f"  ...thar af faeranlegar: {n_move} | "
                  f"fastar: {n_sess - n_move}")

            print()
            print(f"  Taldar lotugerdir          : "
                  f"{sorted(COUNTED_TYPES)}")
            print(f"  Faeranlegar lotugerdir     : "
                  f"{sorted(MOVEABLE_TYPES)}")
            print(f"  Vikunumer notud            : {USE_WEEK_NUMBERS}")
            print(f"  Sjalfsskorun bonnud        : {ENFORCE_NO_SELF_OVERLAP}")

            # --- NYTT: grunnlina reiknud beint ur nuverandi toflu ---
            base_w, base_p = opt.compute_baseline()
            base_self      = opt.count_self_overlaps(use_solution=False)
            base_time      = opt.compute_time_penalties(use_solution=False)
            print(f"  GRUNNLINA (nuverandi tafla): {base_w}"
                  f"  ({base_p} skarandi por)")
            print(f"  Sjalfsskaranir i dag       : {base_self}")
            print(f"  Snemma-minutur i dag (<09) : {base_time['early_min']}"
                  f"  (vigtad: {base_time['early_weighted']})")
            print(f"  Seina-minutur i dag (>16)  : {base_time['late_min']}"
                  f"  (vigtad: {base_time['late_weighted']})")
            print(f"  W_EARLY={W_EARLY}  W_LATE={W_LATE}  "
                  f"threpskuldir: 09:00 / 16:00")
            print()

            print("Building model ...")
            opt.build_model()
            print(f"  Variables   : {opt.model.NumVars}")
            print(f"  Constraints : {opt.model.NumConstrs}")
            print(f"  ...thar af sjalfsskordur : {opt.n_self_constraints}")
            print(f"  ...thar af stofuskordur  : {opt.n_room_constraints}")
            print(f"  Samhlida hopar fundnir (undanskildir C4): "
                  f"{len(opt.parallel_pairs)}")
            for (i, j) in opt.parallel_pairs:
                ci = opt.sessions_df.loc[i, 'course_id']
                ei = opt.sessions_df.loc[i, 'event_type']
                print(f"      {ci} ({ei})")

            print("Solving ...")
            opt.solve()

            opt.print_summary()

            # --- NYTT: sannreyning og samanburdur ---
            sol = opt.get_solution()
            if sol['is_feasible']:
                after_self = opt.count_self_overlaps(use_solution=True)
                print()
                print("  SAMANBURDUR")
                print(f"    Grunnlina (nuverandi tafla) : {base_w}")
                print(f"    Eftir hagraedingu           : "
                      f"{sol['total_student_conflicts']}")
                if base_w > 0:
                    pct = 100.0 * (base_w - sol['total_student_conflicts']) / base_w
                    print(f"    Baeting                     : "
                          f"{base_w - sol['total_student_conflicts']} "
                          f"({pct:.1f}%)")
                nyjar = after_self - base_self
                print(f"    Sjalfsskaranir fyrir/eftir  : "
                      f"{base_self} / {after_self}")
                if nyjar > 0:
                    print(f"    VIDVORUN: likanid bjo til {nyjar} nyjar "
                          f"sjalfsskaranir!")
                else:
                    print(f"    (engar nyjar - thaer sem eftir standa eru "
                          f"fastir samhlida hopar)")
                print()
                print(f"    Snemma-minutur fyrir/eftir  : "
                      f"{base_time['early_min']} / {sol['early_minutes']}")
                print(f"    Seina-minutur fyrir/eftir   : "
                      f"{base_time['late_min']} / {sol['late_minutes']}")
                print(f"    Samanlagt markfall (arekstr."
                      f"+tima)        : {round(sol['objective_value'])}")
                print("=" * 65)

            opt.export_new_schedule('hi_new_schedule.csv')


if __name__ == '__main__':
    main()
