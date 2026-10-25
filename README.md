# HÍ stundatafla – bestun með MILP

Verkefni 1 í Aðgerðagreiningu 2. Líkanið færir kennslustundir á Verkfræði- og náttúruvísindasviði HÍ til að fækka árekstrum í stundatöflu. Það er leyst með Python og Gurobi.

## Skrár

- `hi_schedule_optimizer.py` – aðallíkanið (MILP).
- `sopun_faerslukvota.py` – keyrir líkanið fyrir mismarga leyfða færslur.
- `tilraun_fostudagur_frir.py` – tilraun þar sem dæmatímar eru á miðvikudögum og fyrirlestrar ekki.
- `stadfesta_stofuplas.py` – óháð athugun á að stofur séu ekki tvíbókaðar.
- `athuga_grunnlinu.py` – reiknar fjölda árekstra í upprunalegu töflunni.
- `test_hi_schedule_optimizer.py` – próf.
- `specification.md` – lýsing á verkefninu.
- `results/` – niðurstöður keyrslna.

## Gögn

Hrágögnin frá HÍ eru ekki í repo-inu. Settu CSV-skrárnar `Stundatöflugögn - *.csv` (Bókanir, Árekstur, Stofur, Stofubókanir, Stundatöflur, Kjörsvið) í sömu möppu og kóðann.

## Keyrsla

```
pip install gurobipy pandas
python hi_schedule_optimizer.py
```

Gurobi þarf leyfi, til dæmis akademískt leyfi.
