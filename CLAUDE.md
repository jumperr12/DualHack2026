# Kotwica — dowód i atrybucja uszkodzeń kabli podmorskich (BDUH 2026, ścieżka Space)

> Specyfikacja robocza. Czytaj przed każdą większą zmianą.
> Hasło projektu: **Every cable has a witness.**

## 1. Co budujemy (w jednym akapicie)

System pracuje w dwie strony. **Do przodu:** nasłuchuje AIS na żywo, zna trasy kabli i rurociągów
na Bałtyku i punktuje statki zachowujące się nietypowo przy infrastrukturze. **Do tyłu (to jest
sedno):** operator podaje czas i miejsce awarii kabla, a system rekonstruuje ruch w tym oknie,
zwraca ranking jednostek, które mogły ją spowodować, ocenia, czy to wyglądało na wypadek, i
generuje pakiet dowodowy z pochodzeniem danych oraz szkicem zgłoszenia wymaganego w 24 godziny.

Dual-use: cywilnie (właściciele kabli, deweloperzy farm wiatrowych, ubezpieczyciele, zgodność z CER),
obronnie (Marynarka Wojenna, Straż Graniczna, obraz działań hybrydowych na Bałtyku).

## 2. Twarde ograniczenia

- Czas: ok. 38 h. Próby prezentacji w niedzielę 11:00, finał 14:00.
- Zespół dwuosobowy, więc prostota wygrywa z elegancją.
- **Workery liczą w tle, API co do zasady tylko czyta.** Dwa świadome wyjątki: forensyka
  (`POST /forensics`) i generowanie pakietu — oba z twardymi limitami (sekcja 9).
- Replay i forensyka MUSZĄ przechodzić przez ten sam kod co dane na żywo. **Zero zahardkodowanych
  wyników.**
- Wszystkie progi i wagi w `config.py`, nigdy w kodzie logiki.
- **Nic w interfejsie ani w pakiecie nie orzeka o winie.** Zawsze „przesłanki wymagające weryfikacji".

## 3. Priorytety

- **P0:** ingest AIS → baza; strefy wokół kabli **plus warstwy wyłączeń** (porty, kotwicowiska,
  tory podejściowe); `signature.py`; detektor z regułami i testami; **`forensics.py`**; API; mapa
  z kablami i statkami; panel alertów z uzasadnieniem; formularz „kabel pękł tutaj i wtedy";
  pakiet dowodowy w HTML; replay scenariusza Eagle S; **pomiar fałszywych alarmów na prawdziwych danych**.
- **P1:** `intent.py` (wypadek czy działanie celowe); `gnss_trust.py`; eksport pakietu do PDF;
  szkic zgłoszenia z art. 15 CER; `/stats`.
- **P2:** warstwa satelitarna (czy istnieje zobrazowanie blisko czasu awarii, kiedy następny przelot);
  streszczenie potoczne z LLM; drugi scenariusz z luką AIS; wersja EN pakietu; import archiwum DMA.

**Zasada cięcia:** jeśli w niedzielę ma działać jedna rzecz, to ma być formularz awarii i ranking
kandydatów w kilka sekund.

## 4. Architektura

```
Wszystko na jednym hoście (laptop lub VM) — docker compose
┌──────────────────────────────────────────────────────────┐
│ ais-worker  (ciągle)    MQTT Digitraffic ──┐             │
│ detector    (co 60 s)   reguły, alerty ────┤             │
│ replay      (na żądanie) wstrzykuje pingi ─┘             │
│                     ▼                                    │
│              SQLite (WAL, busy_timeout) na /data         │
│                     ▲                                    │
│ api (FastAPI) — odczyt + forensyka + pakiet              │
└──────────────────────────────────────────────────────────┘
                        ▲
                frontend React + Vite + MapLibre
```

**Wdrożenie na hackathon: lokalnie.** `docker compose up`, frontend na `vite dev` lub build
serwowany przez API. Publiczny adres tylko jeśli potrzebny — szybkim tunelem, **bez własnej domeny
i bez ręcznej konfiguracji HTTPS**. Deploy na VM dopiero po M3, jeśli zostanie czas.
Prezentacja z laptopa, offline (poza żywym AIS).

SQLite: tryb WAL i `busy_timeout` (np. 5000 ms) ustawiane przy **każdym** połączeniu. Blokada
zapisu dotyczy całego pliku, nie pojedynczej tabeli — nie zakładaj inaczej.

## 5. Struktura repo

```
kotwica/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── kotwica/
│   │   ├── config.py            # pydantic-settings + wszystkie progi (sekcja 10)
│   │   ├── db.py                # SQLite, WAL, busy_timeout, migracje (CREATE IF NOT EXISTS)
│   │   ├── geo.py               # EPSG:4326 <-> EPSG:3035, strefy, wyłączenia, STRtree
│   │   ├── models.py            # Ping, VesselMeta, Alert, Candidate, ForensicCase
│   │   ├── detector/
│   │   │   ├── rules.py         # czyste funkcje: (punkty, opis) albo None
│   │   │   ├── signature.py     # NOWE: rozjazd HDG-COG, mediana floty, prostota trasy
│   │   │   ├── state.py         # stan per statek
│   │   │   └── engine.py        # Detector.process(ping) -> list[AlertUpdate]
│   │   ├── forensics.py         # NOWE, kluczowe: czas+miejsce awarii -> ranking kandydatów
│   │   ├── intent.py            # NOWE: wypadek / działanie celowe (P1)
│   │   ├── gnss_trust.py        # NOWE: wiarygodność pozycji w oknie (P1)
│   │   ├── evidence.py          # NOWE: pakiet dowodowy HTML, deterministyczny
│   │   └── api/main.py
│   ├── workers/
│   │   ├── ais_ingest.py
│   │   ├── detector.py
│   │   └── replay.py
│   ├── scripts/
│   │   ├── load_static.py       # EMODnet + warstwy wyłączeń -> data/static/*.geojson
│   │   ├── make_scenario.py     # data/scenarios/eagle_s.json
│   │   ├── measure_alerts.py    # NOWE: alerty na 100 km na dobę, przed/po wyłączeniach
│   │   └── load_dma_csv.py      # (P2)
│   └── tests/
│       ├── test_rules.py
│       ├── test_signature.py    # NOWE
│       ├── test_forensics.py    # NOWE
│       └── test_engine_scenarios.py
├── frontend/  (App.tsx, api.ts, components/…)
└── data/
    ├── static/                  # kable, rurociągi, platformy, farmy, WYŁĄCZENIA
    └── scenarios/
```

## 6. Źródła danych

### AIS na żywo — Digitraffic (Fintraffic), P0
- MQTT over WebSocket, TLS: host `meri.digitraffic.fi`, port `443`, path `/mqtt`.
- Tematy: `vessels-v2/+/location`, `vessels-v2/+/metadata`.
  **Zweryfikuj nazwy tematów i pól w dokumentacji: https://digitraffic.fi/en/marine-traffic**
- Pola lokalizacji: `time, sog, cog, navStat, rot, posAcc, raim, heading, lon, lat`.
- Pola metadanych: `name, shipType, imo, callSign, destination, draught, eta`.
- MMSI w temacie. Ustaw nagłówek `Digitraffic-User`.
- paho-mqtt v2: `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")`,
  `tls_set()`, `ws_set_options(path="/mqtt")`, reconnect z backoffem.
- **Wartości „niedostępne", które trzeba obsłużyć jawnie, nie zamieniać po cichu na 0:**
  `heading = 511`, `rot = 128`, `sog = 1023`, `cog = 3600`, `lat = 91`, `lon = 181`.
  Zapisuj je jako NULL i licz udział braków — to wchodzi do pakietu dowodowego.
- Downsampling: max 1 ping na MMSI na 30 s. Zapis wsadowy co ~2 s. Retencja: 7 dni.

### Infrastruktura i wyłączenia — EMODnet Human Activities + ręcznie, P0
- Z EMODnet: kable telekomunikacyjne, kable energetyczne, rurociągi, farmy wiatrowe.
- **Warstwy wyłączeń (NOWE, konieczne):** granice portów, kotwicowiska, tory podejściowe.
  Źródła: EMODnet, OpenStreetMap (`seamark:type=anchorage`, `harbour`), ręczne poligony.
  Bez tego detektor zaleje się alarmami, bo kable wychodzą na ląd właśnie przy portach.
- `scripts/load_static.py`: przycięcie do bbox Bałtyku (ok. lon 9–31, lat 53–66), uproszczenie
  geometrii, zapis do `data/static/{cables,pipelines,windfarms,exclusions}.geojson` z polem `name`.
- Platformy: ręcznie w `data/static/platforms.geojson`. **Współrzędne uzupełnia człowiek, nie zgaduj ich.**
- Fallback, gdyby EMODnet nie działał: człowiek dostarczy ręcznie narysowany GeoJSON kluczowych kabli.

### Pozostałe (P2)
- Archiwum AIS duńskiego urzędu (DMA) — historyczne odtworzenia.
- Sentinel-1/2 przez Microsoft Planetary Computer — tylko odpowiedź „czy istnieje zobrazowanie
  blisko czasu awarii i kiedy następny przelot".

## 7. Model danych (SQLite, WAL)

Bez zmian względem poprzedniej wersji:

```sql
vessels(mmsi INTEGER PRIMARY KEY, name TEXT, ship_type INTEGER, imo INTEGER,
        call_sign TEXT, destination TEXT, draught REAL, updated_at INTEGER)

positions(mmsi INTEGER, ts INTEGER, lat REAL, lon REAL, x REAL, y REAL,
          sog REAL, cog REAL, heading REAL, rot REAL, nav_stat INTEGER,
          is_replay INTEGER DEFAULT 0)
-- indeksy: (mmsi, ts), (ts), (x, y)  <- indeks przestrzenny ważny dla forensyki

vessel_state(mmsi INTEGER PRIMARY KEY, last_ts INTEGER, lat REAL, lon REAL,
             sog REAL, cog REAL, score INTEGER, level TEXT, zone TEXT, is_replay INTEGER)

alerts(id INTEGER PRIMARY KEY, mmsi INTEGER, asset TEXT, category TEXT, level TEXT,
       score INTEGER, reasons TEXT /*JSON*/, ts_start INTEGER, ts_last INTEGER,
       status TEXT, is_replay INTEGER)

meta(key TEXT PRIMARY KEY, value TEXT)
```

Nowe tabele:

```sql
forensic_cases(id INTEGER PRIMARY KEY, asset TEXT, fault_lat REAL, fault_lon REAL,
               fault_ts INTEGER, radius_m INTEGER, win_back_s INTEGER, win_fwd_s INTEGER,
               gnss_trust TEXT /*ok|degraded|unreliable*/, gnss_reasons TEXT /*JSON*/,
               n_vessels INTEGER, created_at INTEGER, runtime_ms INTEGER)

forensic_candidates(case_id INTEGER, mmsi INTEGER, rank INTEGER, score INTEGER,
                    min_dist_m REAL, tca_ts INTEGER, crossed INTEGER,
                    sig_z_med REAL, sig_persistence REAL, hdg_coverage REAL,
                    reasons TEXT /*JSON*/, intent_score REAL, intent_reasons TEXT /*JSON*/)

evidence_packs(case_id INTEGER, lang TEXT, html TEXT, inputs_sha TEXT /*JSON*/,
               created_at INTEGER, PRIMARY KEY (case_id, lang))
```

Współrzędne metryczne `x, y` w **EPSG:3035** liczone przy zapisie (`pyproj.Transformer`,
`always_xy=True`). Wszystkie odległości i bufory w EPSG:3035.

## 8. Detektor: reguły, sygnatura, progi

`Detector.process(ping) -> list[AlertUpdate]`. Stan per statek w pamięci, odtwarzany z bazy przy
starcie z ostatnich 3 h. Worker `detector` co 60 s bierze pingi `ts > last_processed_ts`.

Strefy: bufor `ZONE_BUFFER_M` wokół linii kabla/rurociągu, w `STRtree` (żeby wiedzieć KTÓRY obiekt)
oraz jako `prepared` union do szybkiego `contains`. **Wyłączenia stosuje się przed regułami:**
ping wewnątrz wyłączenia nie generuje punktów za `slow_in_zone`, `speed_drop` ani `dwell`.

### 8.1 Reguły bazowe

| Reguła | Warunek | Punkty |
|---|---|---|
| `slow_in_zone` | w strefie, `SLOW_MIN < sog < SLOW_MAX`, `nav_stat` NIE w {1, 5}, poza wyłączeniami | 40 |
| `speed_drop` | średni SOG z `SPEED_WINDOW_H` h ≥ `SPEED_DROP_RATIO` × obecny SOG | 25 |
| `dwell` | ciągły czas w strefie > `DWELL_MIN` min | 20 |
| `repeat_crossing` | ≥ 2 przecięcia tego samego obiektu w ciągu 3 h | 15 |
| `ais_gap` | luka > `GAP_MIN` min, ostatnia pozycja w strefie, **i** w trakcie luki ≥ 3 inne statki nadawały w promieniu 20 km | 30 |
| `tanker_bonus` | `ship_type` 80–89 | +10 (tylko gdy inne reguły dały punkty) |

### 8.2 `signature.py` — sygnatura wleczenia (NOWE, to jest nasz wyróżnik)

**Fizyka:** wleczona kotwica przykłada dużą siłę oporu przy dziobie. Żeby utrzymać kurs, statek
ustawia się skośnie do własnego śladu — dziób wskazuje gdzie indziej niż kierunek ruchu.

1. `delta = angular_diff(HDG, COG)` w zakresie 0–180°. Jeśli `HDG` jest NULL, ping nie wchodzi
   do statystyki, ale liczy się do `hdg_coverage`.
2. **Kontrola środowiskowa — flota jako grupa odniesienia.** Wiatr i prąd też powodują rozjazd,
   więc nie porównujemy z zerem. Dla tego samego kubełka czasu (`FLEET_BUCKET_MIN`) i promienia
   (`FLEET_RADIUS_M`) bierzemy statki z `SOG > 2 kn` i liczymy `median(delta)` oraz `MAD(delta)`.
   Wymagamy `FLEET_MIN_N` jednostek, inaczej sygnatura jest niedostępna (i tak to raportujemy).
3. `z = (delta - median_fleet) / (1.4826 * MAD + EPS)`.
4. `sig_persistence` = udział pingów z `z > Z_SIG` w oknie `SIG_WINDOW_MIN`.
   **Liczy się trwałość, nie pojedynczy skok.**
5. Cechy wspierające: `straightness` (odległość w linii prostej / długość trasy) w oknie — wleczona
   kotwica działa jak stępka i prostuje tor; oraz `rot` bliskie zeru mimo małej prędkości.

**Reguła:** `drag_signature` — `sig_persistence > SIG_PERSIST_MIN` i `straightness > STRAIGHT_MIN`
w strefie → **35 punktów**.

**Uczciwość, wymagana w kodzie i w interfejsie:**
- zawsze podajemy `hdg_coverage` (dla ilu pingów było dostępne `HDG`);
- rozjazd występuje też przy holowaniu, trałowaniu i awarii steru — dlatego to jedna z przesłanek,
  nigdy dowód;
- jeśli na naszych danych sygnatura nie separuje, **mówimy to wprost i pokazujemy wykres**.
  Nie stroimy progów tak, żeby wyszło.

### 8.3 Kategorie i modyfikatory
- Biała lista (pomijamy): `ship_type` 31, 32, 33 (holowanie, prace podwodne), 50–55 (służby).
- Rybackie (`ship_type` 30 lub `nav_stat` 7): kategoria `accidental_risk`, wynik × 0.5.
- Pozostałe: `suspicious`.
- Poziomy: `score ≥ 50` → `watch`, `score ≥ 80` → `alarm`.
- Cykl życia: jeden alert na (mmsi, asset) na przejście; otwierany przy 50, `score = max`,
  zamykany po 60 min poza strefą.

`reasons` to lista JSON, np.
`[{"rule":"drag_signature","points":35,"detail":"rozjazd dziób/kurs 18° powyżej mediany floty przez 74% pingów w 40 min; HDG dostępne w 92% pingów"}]`
**Frontend, forensyka i pakiet dowodowy korzystają wprost z tego pola. To jest nasza wyjaśnialność.**

## 9. `forensics.py` — tryb do tyłu (SERCE PROJEKTU)

**Wejście:** `asset` lub `(lat, lon)` punktu uszkodzenia, `fault_ts`, opcjonalnie `radius_m`
i długość okna. Operator zna te dane od razu: kabel energetyczny zgłasza się przez zabezpieczenia
w sekundach, światłowód przez reflektometr, z lokalizacją uszkodzenia z dokładnością do metrów.

**Algorytm:**
1. Okno `[fault_ts − WIN_BACK_S, fault_ts + WIN_FWD_S]` (domyślnie 6 h wstecz, 1 h w przód).
2. Zbiór kandydatów: wszystkie MMSI z pozycją w promieniu `FORENSIC_RADIUS_M` (domyślnie 10 km)
   od punktu uszkodzenia **lub** z interpolowanym odcinkiem trasy przecinającym ten promień.
3. Dla każdego kandydata liczymy:
   - `min_dist_m` i `tca_ts` (moment najmniejszego zbliżenia),
   - `crossed` — czy trasa przecina linię obiektu w tolerancji `radius_m`,
   - cechy sygnatury z 8.2 w oknie wokół `tca_ts`,
   - reguły z 8.1 puszczone **tym samym silnikiem** po oknie historycznym,
   - `ais_gap` w oknie, z potwierdzeniem pokrycia,
   - **zachowanie po zdarzeniu:** zmiana SOG i kursu w `POST_EVENT_MIN` minut po `fault_ts`.
4. Wynik = suma punktów reguł + bonus za bliskość (`max(0, 30 × (1 − min_dist/radius))`)
   + bonus za przecięcie (25). Ranking malejąco.
5. **Test kontrolny wbudowany:** jeśli żaden kandydat nie przekracza `CANDIDATE_MIN_SCORE`,
   zwracamy pustą listę z wyjaśnieniem. System ma prawo powiedzieć „nie wiem".
6. **Cisza jako przesłanka:** jeśli w oknie liczba nadających jednostek w promieniu jest istotnie
   niższa niż mediana z ostatnich 7 dni o tej porze, dopisujemy przesłankę
   „nietypowo mała liczba nadających jednostek w oknie".

**Limity (świadomy wyjątek od zasady „API tylko czyta"):** maksymalnie `FORENSIC_MAX_ROWS`
(domyślnie 200 000) wierszy pozycji i `FORENSIC_TIMEOUT_S` (domyślnie 8 s). Po przekroczeniu
zwracamy błąd z sugestią zawężenia okna. W produkcji docelowo kolejka zadań — na hackathon
synchronicznie, bo demo wymaga odpowiedzi w kilka sekund.

## 10. `intent.py`, `gnss_trust.py` (P1)

### 10.1 Wypadek czy działanie celowe
Wynik `intent_score` w zakresie −1 (raczej wypadek) … +1 (raczej celowe), z listą przesłanek.
**Nigdy etykieta „sabotaż".** W interfejsie suwak z dwoma końcami i lista.

| Przesłanka | Za wypadkiem | Za celowym |
|---|---|---|
| Miejsce rzucenia kotwicy | w kotwicowisku | poza kotwicowiskiem, nad korytarzem |
| Długość wleczenia | do kilku km | dziesiątki km |
| Reakcja po zdarzeniu | zwolnił, zatrzymał się, zawrócił | kontynuował bez zmiany |
| Liczba trafionych obiektów | jeden | kilka |
| Warunki | sztorm, ruch unikowy | pogoda spokojna |
| AIS | nadawał normalnie | luka tuż przed lub po, przy potwierdzonym pokryciu |
| Powtarzalność | pierwszy raz w rejonie | wcześniejsze przejścia tą trasą |

### 10.2 Wiarygodność pozycji
AIS podaje pozycję z odbiornika GNSS na statku. Nad Bałtykiem trwają zakłócenia i fałszowanie
sygnału, więc **pakiet dowodowy oparty na sfałszowanych pozycjach jest bezwartościowy.**

Liczymy wskaźniki dla **całej floty** w oknie i promieniu:
skok pozycji wymagający nierealnej prędkości; pozycja na lądzie; ≥ 3 różne MMSI w promieniu 100 m
poza portem; pozycja niedostępna (`lat = 91` / `lon = 181`); pozycja zamrożona przy `SOG > 3 kn`.

Wynik: `ok` / `degraded` / `unreliable` + powody. Trafia na górę pakietu dowodowego.

## 11. `evidence.py` — pakiet dowodowy (P0 w HTML, P1 w PDF)

**Generowany deterministycznie z danych. Żadnego LLM w treści dowodowej** — dokument idzie do
urzędu i do ubezpieczyciela, więc nie może zawierać zdań wymyślonych przez model. LLM (P2) pisze
wyłącznie streszczenie potoczne, w osobnej, wyraźnie oznaczonej sekcji.

Sekcje:
1. Nagłówek incydentu: obiekt, `fault_ts` (UTC), pozycja, promień niepewności.
2. **Ocena wiarygodności pozycji** (10.2) — na samej górze.
3. Okno analizy, liczba jednostek, liczba pingów.
4. Ranking kandydatów: MMSI, nazwa, IMO, typ, bandera (z MID), `min_dist_m`, `tca_ts`,
   przesłanki z punktami, `hdg_coverage`.
5. Sygnatura ruchu czołowego kandydata: wykres SOG oraz `delta` z medianą floty w tle.
6. Ocena wypadek / działanie celowe z przesłankami (P1).
7. **Pochodzenie danych:** źródło, licencja, czas pobrania, `sha256` plików surowych.
8. Czego brakuje i co zrobić dalej: zapis z rejestratora podróży, dziennik kotwiczny,
   zlecenie zobrazowania satelitarnego.
9. **Szkic zgłoszenia z art. 15 dyrektywy CER** (P1): charakter, przyczyna, możliwe skutki,
   wpływ transgraniczny — do uzupełnienia i podpisu przez człowieka.
10. Klauzula: dokument zawiera przesłanki wymagające weryfikacji, nie rozstrzyga o odpowiedzialności.

## 12. `config.py` — wartości startowe

```python
# strefy
ZONE_BUFFER_M = 2000
# reguły bazowe
SLOW_MIN, SLOW_MAX = 1.0, 7.0            # węzły
SPEED_WINDOW_H, SPEED_DROP_RATIO = 2, 1.5
DWELL_MIN = 30
GAP_MIN = 30
LEVEL_WATCH, LEVEL_ALARM = 50, 80
# sygnatura (8.2)  # TODO: kalibracja na prawdziwych danych
FLEET_RADIUS_M = 25_000
FLEET_BUCKET_MIN = 15
FLEET_MIN_N = 5
Z_SIG = 3.0
SIG_WINDOW_MIN = 40
SIG_PERSIST_MIN = 0.5
STRAIGHT_MIN = 0.9
# forensyka (9)
FORENSIC_RADIUS_M = 10_000
WIN_BACK_S, WIN_FWD_S = 6*3600, 3600
POST_EVENT_MIN = 60
CANDIDATE_MIN_SCORE = 30
FORENSIC_MAX_ROWS = 200_000
FORENSIC_TIMEOUT_S = 8
# replay
REPLAY_SPEED = 60
```

## 13. Scenariusz demo „Eagle S" i walidacja

### 13.1 Scenariusz (P0)
`scripts/make_scenario.py` generuje trajektorię na **PRAWDZIWEJ geometrii kabla Estlink 2**
z `data/static/cables.geojson` (znajdź po nazwie; jeśli brak, weź inny kabel w Zatoce Fińskiej
i daj znać). **Nie wymyślaj współrzędnych kabla.**

Przebieg: tankowiec (`ship_type` 80, MMSI z prefiksem `999`), kurs na zachód 11 kn; zwalnia do ok.
6 kn na ok. 2 h; tor przecina kabel i biegnie wzdłuż niego; `nav_stat` = 0 przez cały czas;
**`heading` przesunięty o 15–25° względem `cog` w fazie wleczenia**; po zdarzeniu przyspiesza
bez zatrzymania. Pingi co 30 s. Dodatkowo kilka syntetycznych statków w tle jako grupa kontrolna
dla floty (bez rozjazdu).

**W interfejsie i na slajdzie oznaczony jako „rekonstrukcja na podstawie opisów incydentu",
nie jako dane historyczne.**

### 13.2 Testy (obowiązkowe)
- `test_signature.py`: statek z zadanym rozjazdem przy medianie floty 0° daje wysokie `z`;
  cała flota z rozjazdem 20° (silny wiatr) daje `z` bliskie zeru — **to jest test kontroli
  środowiskowej i najważniejszy test w projekcie**.
- `test_engine_scenarios.py`: Eagle S → `alarm` z regułami `slow_in_zone` + `speed_drop` +
  `dwell` + `drag_signature`; tankowiec 11 kn przecinający kabel → brak alertu; kuter trałujący →
  najwyżej `accidental_risk`; statek zwalniający w kotwicowisku → brak alertu (test wyłączeń).
- `test_forensics.py`: przy zadanym punkcie i czasie właściwy MMSI jest na 1. miejscu;
  w rejonie bez ruchu ranking jest pusty.

### 13.3 Liczby na slajd (P0, `scripts/measure_alerts.py`)
1. **Alerty `watch` i `alarm` na dobę na 100 km kabla, przed i po włączeniu wyłączeń.**
   Plus ręczny przegląd 10 najwyżej punktowanych.
2. **Forensyka na prawdziwych danych:** bierzemy prawdziwy punkt na prawdziwym kablu i prawdziwy
   czas z własnych zebranych danych, pytamy „gdyby kabel pękł tutaj i wtedy". Mierzymy liczbę
   kandydatów, liczbę powyżej progu i czas odpowiedzi.
3. Czas wygenerowania pakietu dowodowego.

## 14. API

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/health` | status workerów |
| GET | `/infrastructure` | GeoJSON kabli, rurociągów, platform, farm, **wyłączeń** |
| GET | `/vessels?bbox=` | z `vessel_state`, ze `score` i `level` |
| GET | `/vessels/{mmsi}` | metadane + ostatni alert |
| GET | `/vessels/{mmsi}/track?hours=6` | GeoJSON LineString |
| GET | `/vessels/{mmsi}/signature?ts=&window=` | szereg `sog`, `delta`, mediana floty |
| GET | `/alerts?status=open&since=` | alerty z `reasons` |
| GET | `/alerts/stream` | SSE (`sse-starlette`) |
| **POST** | **`/forensics`** | `{asset?, lat?, lon?, fault_ts, radius_m?, win_back_s?}` → `case_id` + kandydaci |
| GET | `/forensics/{case_id}` | wynik sprawy |
| GET | `/forensics/{case_id}/evidence?lang=pl` | pakiet dowodowy (HTML; PDF w P1) |
| GET | `/stats` | pingi, statki, alerty, uptime (bez replay) |
| POST | `/replay/start?scenario=eagle_s` | wymaga `X-Admin-Token` |

## 15. Frontend

React + Vite + TypeScript, MapLibre GL JS, basemap bez klucza (OpenFreeMap; fallback CARTO).

- Warstwy z przełącznikami: kable, rurociągi, strefy, **wyłączenia**, statki (szare/żółte/czerwone),
  trajektoria wybranego statku.
- **Panel „Zgłoś awarię"** — to jest główny element interfejsu, nie dodatek: klik na mapie lub
  wybór kabla, pole czasu, przycisk. Wynik: lista kandydatów po prawej.
- Szuflada statku: metadane, rozbicie wyniku reguła po regule z `reasons`, trajektoria,
  **wykres sygnatury** (`sog` i `delta` z medianą floty w tle).
- Karta sprawy: ocena wiarygodności pozycji, ranking, suwak wypadek/celowe, przycisk „Pakiet dowodowy".
- Pasek statystyk u góry. Sterowanie replay za tokenem admina.
- Odświeżanie: `/vessels?bbox=` co 5 s; alerty przez `EventSource`.
- UI po angielsku (event dwujęzyczny), pakiet PL z opcją EN.

## 16. Kamienie milowe

- **M0 (pt do 23:00):** repo, compose, `ais-worker` **zapisuje do bazy** (pierwsze zadanie po
  starcie), `load_static.py` z kablami i wyłączeniami, mapa pokazuje kable i surowe statki.
- **M1 (sob do 12:00):** `geo.py`, `signature.py`, reguły + testy, worker `detector`, API,
  panel alertów, pierwszy pomiar alertów.
- **M2 (sob do 18:00):** **`forensics.py` + panel „Zgłoś awarię"**, wyłączenia włączone,
  pomiar przed i po.
- **M3 (sob do 23:00):** `evidence.py` (HTML), `intent.py`, `gnss_trust.py`, scenariusz Eagle S
  przez replay, wykres sygnatury.
- **M4 (nd do 10:00):** PDF, szkic CER, poprawki, nagranie wideo, slajdy.

**Nie zaczynaj kolejnego kamienia, dopóki poprzedni nie działa end-to-end.**
Punkty cięcia: jeśli o 18:00 forensyka nie działa — tniemy `intent.py` i `gnss_trust.py`.
Jeśli o 23:00 nie ma pakietu — pokazujemy ranking i mówimy, że pakiet to następny krok.

## 17. Zasady pracy dla Claude Code

- Realizuj kamienie po kolei. Po każdym uruchom testy i krótko podsumuj, co działa, a co nie.
- Każda reguła ma test na syntetycznej trajektorii.
- Workery idempotentne i odporne na restart (reconnect z backoffem, `last_processed_ts`).
- Logowanie przez `logging` na poziomie INFO, jedna linia na zdarzenie. Żadnych printów.
- Nie commituj `.env` ani `data/` (poza `data/static/` i `data/scenarios/`).
- Przed zmianą architektury, dodaniem ciężkiej zależności lub zmianą schematu bazy — zapytaj.
- **Jeśli coś w tej specyfikacji jest sprzeczne, niemożliwe albo jeśli dane nie potwierdzają
  hipotezy (np. sygnatura nie separuje) — powiedz o tym, zamiast po cichu obchodzić lub stroić
  progi pod wynik.**

## 18. Uczciwe ograniczenia (nie obiecuj więcej w UI ani w pakiecie)

- System wskazuje przesłanki i priorytety, **nie dowodzi winy**.
- Pokrycie AIS zależy od sieci odbiorników; luka nie zawsze oznacza wyłączenie transpondera
  (dlatego `ais_gap` wymaga potwierdzenia pokrycia).
- `heading` bywa niedostępne — zawsze raportujemy `hdg_coverage`.
- Rozjazd dziób/kurs występuje też przy holowaniu, trałowaniu i awarii steru.
- Statek z wyłączonym AIS jest dla trybu do przodu niewidoczny; w trybie do tyłu cisza w rejonie
  jest przesłanką, ale nie zastępuje obserwacji.
- Pozycje mogą być niewiarygodne przy zakłóceniach GNSS — dlatego sekcja 10.2 jest w każdym pakiecie.
- Progi sygnatury i forensyki wymagają kalibracji na większym zbiorze niż jeden weekend.
