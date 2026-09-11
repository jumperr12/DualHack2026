# Kotwica — monitoring infrastruktury podmorskiej Bałtyku (BDUH 2026, ścieżka Space)

> Nazwa robocza. Projekt na Baltic Dual Use Hackathon 2026 (Gdańsk, 11–13.09.2026).
> Ten plik to pełna specyfikacja. Czytaj go przed każdą większą zmianą.

## 1. Co budujemy (w jednym akapicie)

System, który na żywo nasłuchuje ruchu statków (AIS), zna trasy kabli i rurociągów
na Bałtyku i każdemu statkowi przy infrastrukturze liczy wynik podejrzliwości
(wzorzec wleczenia kotwicy, znikanie z AIS przy kablu). Drugi moduł to satelity, tylko
w wybranych obszarach: radar Sentinel-1 wykrywa statki i porównuje je z AIS (statek widoczny
na radarze bez AIS przy kablu = „ciemny”), a Sentinel-2 wykrywa plamy ropy i przypisuje je
do statków na podstawie trajektorii AIS. Operator nie pilnuje
tysięcy statków, tylko dostaje kilka alertów z uzasadnieniem i raportem.

Dual-use: cywilnie (operatorzy infrastruktury, ochrona środowiska, MSPiR/SAR),
obronnie (Marynarka Wojenna, Straż Graniczna, ochrona przed sabotażem kabli).

## 2. Twarde ograniczenia

- Czas: ok. 38 h pracy. Próby prezentacji w niedzielę 11:00, finał 14:00.
- Zespół mały, więc prostota wygrywa z elegancją.
- **Żadne obliczenia nie są wyzwalane przez użytkownika.** Wszystko liczą workery w tle.
  API tylko czyta z bazy. Jedyny wyjątek: generowanie raportu LLM (wynik cache'owany w bazie).
- Replay scenariusza demo MUSI przechodzić przez ten sam kod detektora co dane na żywo.
  **Zero zahardkodowanych alertów.**
- Wszystkie progi i wagi w jednym pliku konfiguracyjnym, nigdy w kodzie logiki.

## 3. Priorytety

- **P0 (musi działać):** ingest AIS → baza, strefy wokół kabli, detektor z regułami i testami,
  API, mapa z kablami i statkami, panel alertów z uzasadnieniem, replay scenariusza „Eagle S”, deploy.
- **P1 (mocno chcemy), w tej kolejności:** (1) moduł statków Sentinel-1: wykrycia radarowe
  dopasowane do AIS, „ciemne” statki przy infrastrukturze, na prawdziwej scenie z weekendu;
  (2) moduł plam Sentinel-2 (jedna dobrze przygotowana scena) i atrybucja plama → statek;
  (3) raport LLM, licznik statystyk do pitchu. Ścieżka Kosmos: satelita musi być w demo.
- **P2 (jeśli starczy czasu):** korekta dryfu wiatrowego (Open-Meteo), detekcje SAR z Global
  Fishing Watch, powiązanie luki AIS z wykryciem S1, drugi scenariusz z luką AIS, wersja EN raportów.

## 4. Architektura

```
HOST 1: VM (Oracle A1 / Railway) — docker compose           HOST 2: Vercel
┌──────────────────────────────────────────────────┐         ┌──────────────────┐
│ ais-worker  (ciągle)   MQTT Digitraffic ──┐      │         │ React + Vite +   │
│ detector    (co 60 s)  scoring, alerty ───┤      │         │ MapLibre GL      │
│ sat-worker  (co 3600s) S1 statki, S2 plamy┤      │         │                  │
│ replay      (na żądanie) wstrzykuje pingi ┘      │  HTTPS  │ polling 5 s +    │
│                     ▼                            │◄───────►│ SSE alertów      │
│              SQLite (WAL) na wolumenie /data     │         │                  │
│                     ▲                            │         └──────────────────┘
│ api (FastAPI) — tylko ODCZYT + raport LLM        │
│ caddy — HTTPS (Let's Encrypt) → api:8000         │
└──────────────────────────────────────────────────┘
```

Wszystkie workery i API na jednym VM, bo SQLite nie działa po sieci. Jeden proces
pisze do danej tabeli, reszta czyta. Tryb WAL włączony przy każdym połączeniu.

## 5. Struktura repo

```
kotwica/
├── CLAUDE.md
├── docker-compose.yml
├── Caddyfile
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── kotwica/
│   │   ├── config.py            # pydantic-settings + wszystkie progi (sekcja 8)
│   │   ├── db.py                # połączenie SQLite, WAL, migracje (CREATE IF NOT EXISTS)
│   │   ├── geo.py               # projekcje (EPSG:4326 <-> EPSG:3035), strefy, STRtree
│   │   ├── models.py            # dataclasses / pydantic: Ping, VesselMeta, Alert, Slick
│   │   ├── detector/
│   │   │   ├── rules.py         # czyste funkcje reguł, każda zwraca (punkty, opis) albo None
│   │   │   ├── state.py         # stan per statek: historia SOG, czas w strefie, przecięcia
│   │   │   └── engine.py        # Detector.process(ping) -> list[AlertUpdate]
│   │   ├── sat/
│   │   │   ├── aoi.py           # obszar analizy: statyczny + dynamiczny, cięcie na kafelki
│   │   │   ├── fetch.py         # STAC Planetary Computer, odc.stac.load z bbox
│   │   │   ├── ships_s1.py      # S1: CFAR na VV, maska lądu i obiektów stałych
│   │   │   ├── ais_match.py     # wykrycia S1 <-> pozycje AIS w chwili zdjęcia
│   │   │   ├── slicks.py        # maska SCL, anomalie, segmentacja, filtry
│   │   │   └── attribution.py   # plama -> podejrzane statki
│   │   ├── llm/report.py        # klient OpenAI-compatible, prompt, cache
│   │   └── api/main.py          # FastAPI
│   ├── workers/
│   │   ├── ais_ingest.py
│   │   ├── detector.py
│   │   ├── satellite.py
│   │   └── replay.py
│   ├── scripts/
│   │   ├── load_static.py       # EMODnet -> data/static/*.geojson (uproszczone, WGS84)
│   │   ├── make_scenario.py     # generuje data/scenarios/eagle_s.json
│   │   └── load_dma_csv.py      # (P1/P2) historyczne AIS z duńskiego archiwum
│   └── tests/
│       ├── test_rules.py
│       ├── test_engine_scenarios.py
│       ├── test_ais_match.py
│       └── test_attribution.py
├── frontend/
│   ├── package.json
│   └── src/ (App.tsx, api.ts, components/…)
└── data/                        # NIE commitować (poza data/static i data/scenarios)
    ├── static/                  # kable, rurociągi, platformy, farmy wiatrowe
    └── scenarios/
```

## 6. Źródła danych

### AIS na żywo — Digitraffic (Fintraffic), P0
- MQTT over WebSocket, TLS: host `meri.digitraffic.fi`, port `443`, path `/mqtt`.
- Subskrybujemy `vessels-v2/#` i rozpoznajemy typ po ostatnim członie tematu
  (dokumentacja podaje raz `location`, raz `locations`; `vessels-v2/status` ignorujemy).
- Pola lokalizacji (v2): `time` (**sekundy**), `sog` (kn), `cog`, `navStat`, `rot`, `posAcc`, `raim`,
  `heading`, `lon`, `lat`. „Niedostępne” → NULL: `sog` 102.3, `cog` 360, `heading` 511; lat 91 / lon 181 → ping odrzucony.
- Pola metadanych (zweryfikowane): `timestamp` (**ms**), `name`, **`type`** (nie `shipType`), `imo`, `callSign`,
  `destination`, `draught` (**dziesiąte części metra**, 68 = 6.8 m), `eta`. Zera (`imo`, `type`, `draught`) → NULL.
- MMSI jest w temacie (`vessels-v2/<mmsi>/location`).
- Client id: `"kotwica; <uuid4>"` + nagłówek WS `Digitraffic-User: kotwica`.
- paho-mqtt v2: `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")`,
  `tls_set()`, `ws_set_options(path="/mqtt")`, reconnect z backoffem.
- Downsampling: max 1 ping na MMSI na 30 s. Zapis wsadowy co ~2 s. Retencja pozycji: 7 dni.
- Usuwanie czystych rejsów: statek zacumowany (`navStat` 5 i `sog < 0.5`) przez ≥ 30 min kończy rejs.
  Jeśli nie ma żadnego wiersza w `alerts`, jego pozycje do początku postoju są kasowane po `CLEAN_GRACE_H` (24 h).
  Karencja chroni atrybucję plam (okno 6 h + opóźnienie sceny) i analizę wsteczną po awarii kabla.
  Kotwiczenie (`navStat` 1) NIE kończy rejsu. Kasuje tylko `ais-worker` (jedyny pisarz `positions`).
  Realnie ok. 700 pingów/min po downsamplingu (~1 mln/dobę).

### AIS alternatywny — AISstream.io, P2
- `wss://stream.aisstream.io/v0/stream`, wiadomość subskrypcji z `APIKey` i `BoundingBoxes`.
- Tylko jeśli potrzebujemy pokrycia polskich wód. Klucz w `.env` (`AISSTREAM_KEY`).

### Infrastruktura — EMODnet Human Activities, P0
- Warstwy: kable telekomunikacyjne, kable energetyczne, rurociągi, farmy wiatrowe.
- Pobierane RĘCZNIE (GeoJSON/shapefile) do `data/static/raw/`, potem `scripts/load_static.py`:
  przycięcie do bbox Bałtyku (ok. lon 9–31, lat 53–66), uproszczenie geometrii,
  zapis `data/static/{cables,pipelines,windfarms}.geojson` z polem `name`.
- Platformy wiertnicze: ręcznie w `data/static/platforms.geojson` (garstka punktów, m.in. złoża B3 i B8
  Orlen Petrobaltic, rosyjska D-6). Współrzędne uzupełnia człowiek, nie zgaduj ich.
- Fallback, gdyby EMODnet nie działał: człowiek dostarczy ręcznie narysowany GeoJSON kluczowych kabli.

### Sentinel-2 — Microsoft Planetary Computer, P1
- STAC: `https://planetarycomputer.microsoft.com/api/stac/v1`, kolekcja `sentinel-2-l2a`,
  podpisywanie przez `planetary_computer.sign_inplace`. Konto niepotrzebne.
- Czytamy tylko fragmenty (COG) przez `odc.stac.load(..., bbox=tile_bbox, resolution=20)`.
- Pasma: `B03, B04, B05, B08, B11, SCL`.
- Filtr zachmurzenia: liczony z `SCL` WEWNĄTRZ kafelka, nie z `eo:cloud_cover` sceny.

### Sentinel-1 — Microsoft Planetary Computer, P1
- Kolekcja `sentinel-1-rtc` (zgeokodowane COG, 10 m, działa wprost z `odc.stac.load`), pasma `vv`, `vh`.
  **Zweryfikuj dostęp bez konta.** Fallback: `sentinel-1-grd` (geometria radarowa, trzeba geokodować)
  albo wycinek z Copernicus Data Space (konto, dane logowania w `.env`).
- Przeloty nad Zatoką Fińską co ok. 1–2 dni (S1A + S1C), dane 1–3 h po zdjęciu.
  **Zawczasu sprawdź plan akwizycji na 12–13.09**, żeby wiedzieć, kiedy przyjdzie scena pokrywająca się z nagranym AIS.

### Pozostałe (P2)
- Global Fishing Watch API: detekcje statków z SAR (token w `.env`, `GFW_TOKEN`).
- Open-Meteo: wiatr nad morzem do korekty dryfu plamy.

## 7. Model danych (SQLite, WAL)

```sql
vessels(mmsi INTEGER PRIMARY KEY, name TEXT, ship_type INTEGER, imo INTEGER,
        call_sign TEXT, destination TEXT, draught REAL, updated_at INTEGER)

positions(mmsi INTEGER, ts INTEGER, lat REAL, lon REAL, x REAL, y REAL,
          sog REAL, cog REAL, heading REAL, nav_stat INTEGER, is_replay INTEGER DEFAULT 0)
-- indeksy: (mmsi, ts), (ts)

vessel_state(mmsi INTEGER PRIMARY KEY, last_ts INTEGER, lat REAL, lon REAL,
             sog REAL, cog REAL, score INTEGER, level TEXT, zone TEXT, is_replay INTEGER)
-- zapisuje TYLKO detector; api czyta to dla /vessels

alerts(id INTEGER PRIMARY KEY, mmsi INTEGER, asset TEXT, category TEXT,
       level TEXT, score INTEGER, reasons TEXT /*JSON*/, ts_start INTEGER,
       ts_last INTEGER, status TEXT /*open|closed*/, is_replay INTEGER)

scenes(scene_id TEXT, tile_id TEXT, collection TEXT, acquired_at INTEGER,
       processed_at INTEGER, status TEXT, cloud_frac REAL,
       PRIMARY KEY (scene_id, tile_id))

slicks(id INTEGER PRIMARY KEY, scene_id TEXT, acquired_at INTEGER, geojson TEXT,
       area_km2 REAL, length_km REAL, elongation REAL, confidence REAL, near_asset TEXT)

slick_suspects(slick_id INTEGER, mmsi INTEGER, overlap REAL, angle_diff REAL,
               minutes_before INTEGER, score REAL)

sar_detections(id INTEGER PRIMARY KEY, scene_id TEXT, acquired_at INTEGER, lat REAL, lon REAL,
               x REAL, y REAL, length_m REAL, intensity_db REAL, mmsi INTEGER /*NULL = brak AIS*/,
               match_dist_m REAL, near_asset TEXT, is_dark INTEGER)
-- zapisuje TYLKO sat-worker

reports(incident_type TEXT, incident_id INTEGER, lang TEXT, text TEXT,
        created_at INTEGER, PRIMARY KEY (incident_type, incident_id, lang))

meta(key TEXT PRIMARY KEY, value TEXT)   -- np. started_at, licznik pingów
```

Współrzędne metryczne `x, y` w EPSG:3035 (LAEA Europe) liczone przy zapisie (pyproj `Transformer`,
`always_xy=True`). Wszystkie operacje odległości i buforów w EPSG:3035.

## 8. Detektor: reguły i progi

Detektor to czysta logika: `Detector.process(ping) -> list[AlertUpdate]`. Stan per statek w pamięci
(odtwarzany z bazy przy starcie z ostatnich 3 h). Worker `detector` co 60 s pobiera nowe pingi
(`ts > last_processed_ts`) i przepuszcza je przez silnik. Replay używa dokładnie tego samego silnika.

Strefy: bufor `ZONE_BUFFER_M` wokół każdej linii kabla/rurociągu. Trzymaj bufory osobno w `STRtree`
(żeby wiedzieć, KTÓRY kabel) oraz jako `prepared` union do szybkiego `contains`.

| Reguła | Warunek | Punkty |
|---|---|---|
| `slow_in_zone` | w strefie, `SLOW_MIN < sog < SLOW_MAX`, `nav_stat` NIE w {1 na kotwicy, 5 zacumowany} | 40 |
| `speed_drop` | średni SOG z poprzednich `SPEED_WINDOW_H` h ≥ `SPEED_DROP_RATIO` × obecny SOG | 25 |
| `dwell` | ciągły czas w strefie > `DWELL_MIN` min | 20 |
| `repeat_crossing` | ≥ 2 przecięcia tego samego kabla w ciągu 3 h | 15 |
| `ais_gap` | luka > `GAP_MIN` min, ostatnia pozycja w strefie, **i** potwierdzone pokrycie: w trakcie luki ≥ 3 inne statki nadawały w promieniu 20 km | 30 |
| `tanker_bonus` | `ship_type` 80–89 | +10 (tylko gdy inne reguły już dały punkty) |

Kategorie i modyfikatory:
- Biała lista (pomijamy): `ship_type` 31, 32, 33 (holowanie, prace podwodne), 50–55 (pilot, SAR, holownik, służby).
- Rybackie (`ship_type` 30 lub `nav_stat` 7): kategoria `accidental_risk`, wynik × 0.5.
- Pozostałe: kategoria `suspicious`.

Poziomy: `score ≥ 50` → `watch` (żółty), `score ≥ 80` → `alarm` (czerwony).
Cykl życia alertu: jeden alert na (mmsi, asset) na przejście. Otwierany przy przekroczeniu 50,
aktualizowany (score = max), zamykany po 60 min poza strefą.

Alerty z radaru: detektor (jedyny pisarz `alerts`) czyta nowe wiersze `sar_detections` z `is_dark = 1`
i `near_asset IS NOT NULL` i otwiera alert kategorii `dark_vessel` z `mmsi = NULL`, regułą `sar_dark`
i wynikiem `SAR_DARK_POINTS` (domyślnie 60 → `watch`). To dowód z opóźnieniem (czas zdjęcia), nie alert na żywo.

`reasons` to lista JSON, np.:
`[{"rule":"slow_in_zone","points":40,"detail":"SOG 5.8 kn w strefie Estlink 2, status: under way"}]`
Frontend i LLM korzystają wprost z tego pola. To jest nasza wyjaśnialność.

Domyślne wartości w `config.py`:
```python
ZONE_BUFFER_M = 2000
SLOW_MIN, SLOW_MAX = 1.0, 7.0          # węzły
SPEED_WINDOW_H, SPEED_DROP_RATIO = 2, 1.5
DWELL_MIN = 30
GAP_MIN = 30
LEVEL_WATCH, LEVEL_ALARM = 50, 80
```

## 9. Scenariusz demo „Eagle S” (P0)

`scripts/make_scenario.py` generuje syntetyczną trajektorię na PRAWDZIWEJ geometrii kabla
Estlink 2 z `data/static/cables.geojson` (znajdź po nazwie; jeśli brak, weź inny kabel w Zatoce Fińskiej
i daj znać). Nie wymyślaj współrzędnych kabla.

Przebieg: tankowiec (`ship_type` 80, MMSI z prefiksem `999`), kurs na zachód 11 kn; zbliża się do kabla,
zwalnia do ok. 6 kn na ok. 2 h, trajektoria przecina / biegnie wzdłuż kabla, `nav_stat` cały czas 0,
potem przyspiesza. Pingi co 30 s. Wariant P2: dodatkowo 45-minutowa luka AIS nad kablem
(plus kilka syntetycznych statków w pobliżu, żeby spełnić warunek pokrycia).

`workers/replay.py`: na żądanie (`POST /replay/start`) mapuje czas scenariusza na „teraz”,
przyspieszenie `REPLAY_SPEED` (domyślnie ×60), wstawia pingi z `is_replay=1` do `positions`.
Statystyki pitchowe liczą tylko `is_replay=0`.

Testy (`test_engine_scenarios.py`): scenariusz Eagle S musi dać alert `alarm` z regułami
`slow_in_zone` + `speed_drop` + `dwell`; normalny tankowiec 11 kn przecinający kabel NIE może dać alertu;
kuter trałujący nad kablem daje najwyżej `accidental_risk`.

## 10. Moduł satelitarny (P1): statki z Sentinel-1, plamy z Sentinel-2

### Obszar analizy (`sat/aoi.py`), wspólny dla S1 i S2
- Statyczny: bufor 2 km wokół rurociągów i kabli, 10 km wokół platform.
- Dynamiczny (tip & cue): bufor 3 km wokół trajektorii z ostatnich 12 h statków z otwartym alertem
  oraz tankowców (`ship_type` 80–89).
- Union → siatka kafelków 10 × 10 km w EPSG:3035 → lista bbox w WGS84 z `tile_id`.
- Nigdy jeden bbox dla całego korytarza.

### Statki S1: detekcja (`sat/ships_s1.py`), na kafelek
1. `vv` (i `vh` pomocniczo) w skali liniowej, 10 m. Maska lądu: poligony lądu z bufora `LAND_BUFFER_M`
   (statyczny plik `data/static/land.geojson`). Maska obiektów stałych: farmy wiatrowe i platformy
   z bufora `FIXED_OBJECT_BUFFER_M`, bo turbiny i platformy świecą na radarze jak statki.
2. CFAR: piksel jaśniejszy niż tło w pierścieniu wokół niego (średnia + `CFAR_K` × odchylenie,
   okno ok. 500 m, bez strefy ochronnej wokół celu). Łączenie w obiekty (`skimage.measure.label`).
3. Filtry rozmiaru: `SAR_MIN_PX` ≤ piksele ≤ `SAR_MAX_PX`. Długość z osi obiektu → `length_m`.
4. Wynik: punkt (centroid) → WGS84 + EPSG:3035 → `sar_detections`.

### Statki S1: dopasowanie do AIS (`sat/ais_match.py`)
- Dla każdego statku z pingiem w `[t_img − AIS_TIME_TOL_MIN, t_img + AIS_TIME_TOL_MIN]` interpoluj pozycję na `t_img`.
- Dopasowanie zachłanne po odległości, max `MATCH_RADIUS_M`. Promień jest duży, bo radar przesuwa
  poruszające się statki w azymucie (Doppler), nawet o setki metrów.
- Wykrycie bez pary → `is_dark = 1`. Jeśli leży w strefie kabla/rurociągu, `near_asset` = nazwa obiektu.
- Statek AIS w zasięgu sceny bez wykrycia nie jest alarmem (mały statek, fala, szum), tylko statystyką.
- Test (`test_ais_match.py`): syntetyczne wykrycia + trajektorie. Statek z AIS w pobliżu → dopasowany.
  Wykrycie bez AIS przy kablu → `is_dark` z `near_asset`. Wykrycie na farmie wiatrowej → zamaskowane.
- P2: wykrycie „ciemne” w miejscu, gdzie statek z luką AIS mógł być w chwili zdjęcia → podejrzany MMSI.

Progi `CFAR_K`, `SAR_MIN_PX`, `SAR_MAX_PX`, `MATCH_RADIUS_M`, `AIS_TIME_TOL_MIN`, `LAND_BUFFER_M`,
`FIXED_OBJECT_BUFFER_M`, `SAR_DARK_POINTS` w config, oznaczone `# TODO: kalibracja na scenie testowej`.

### Plamy S2: detekcja (`sat/slicks.py`), na kafelek
1. Maska: tylko `SCL == 6` (woda). Chmury i cienie (`SCL` 3, 8, 9, 10) wyrzucić i poszerzyć o 500 m.
   Jeśli woda < 30% kafelka → pomiń kafelek.
2. Anomalia: dla B08 i B04 odchylenie od lokalnego tła (mediana + MAD w oknie ok. 1–2 km),
   `|z| > Z_THR`. Otwarcie morfologiczne, `skimage.measure.label` + `regionprops`.
3. Filtry: `area ≥ 0.05 km²`; wydłużenie (major/minor) ≥ 3 → typ `discharge` (zrzut ze statku);
   zwarte obiekty zostają tylko przy infrastrukturze → typ `source_leak`.
4. Odrzucenie sinic: indeks red-edge `(B05 - B04) / (B05 + B04) > ALGAE_THR` → to glony, nie ropa.
5. Rozrost adaptacyjny: jeśli kandydat dotyka krawędzi kafelka, dociągnij sąsiedni kafelek
   (max 5 rozszerzeń) i scal.
6. Poligony przez `rasterio.features.shapes` → WGS84 → tabela `slicks`.

Wszystkie progi (`Z_THR`, `ALGAE_THR` itd.) oznacz w config jako `# TODO: kalibracja na scenie testowej`.

### Plamy S2: atrybucja (`sat/attribution.py`)
- Oś plamy = linia środkowa dłuższego boku `minimum_rotated_rectangle`.
- Trajektorie AIS z okna `[t_img − 6 h, t_img]`.
- Dla każdego statku: `overlap` = długość trajektorii w buforze 300 m wokół osi / długość osi;
  różnica kierunków osi i trajektorii (mod 180°).
- Podejrzany, jeśli `overlap > 0.6` i różnica kąta < 15°. Ranking po `overlap`.
- P2: przesunięcie plamy o dryf wiatrowy (≈ 3% prędkości wiatru × czas) przed dopasowaniem.

### Worker `satellite.py`
Co godzinę: zbuduj AOI, dla każdego kafelka szukaj scen S1 (`sentinel-1-rtc`) i S2 (`sentinel-2-l2a`)
z ostatnich 5 dni, pomiń pary (scene_id, tile_id) już w `scenes` (kolumna `collection` mówi, który satelita),
przetwórz, zapisz. Najpierw S1, potem S2. Limit pamięci kontenera 3 GB.
Tryb demo: `python -m workers.satellite --scene <ID> --bbox ...` przetwarza jedną wskazaną scenę.

## 11. API (FastAPI)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/health` | status workerów (ostatni zapis każdego) |
| GET | `/infrastructure` | GeoJSON kabli, rurociągów, platform, farm (statyczny, cache) |
| GET | `/vessels?bbox=minLon,minLat,maxLon,maxLat` | z `vessel_state`, tylko w bbox, z `score` i `level` |
| GET | `/vessels/{mmsi}` | metadane + ostatni alert |
| GET | `/vessels/{mmsi}/track?hours=6` | trajektoria jako GeoJSON LineString |
| GET | `/alerts?status=open&since=` | lista alertów z `reasons` |
| GET | `/alerts/stream` | SSE (`sse-starlette`): nowe i zaktualizowane alerty |
| GET | `/slicks?since=` | plamy jako FeatureCollection + podejrzani |
| GET | `/sar-detections?since=` | wykrycia S1 jako FeatureCollection (dopasowane / ciemne) |
| POST | `/incidents/{type}/{id}/report?lang=pl` | raport LLM, cache w `reports` |
| GET | `/stats` | pingi, unikalne statki, alerty, uptime (bez replay) |
| POST | `/replay/start?scenario=eagle_s` | wymaga nagłówka `X-Admin-Token` |

CORS: domena z Vercel (`FRONTEND_ORIGIN` w `.env`), plus domeny preview, jeśli potrzebne.

## 12. Raport LLM (P1)

- Klient OpenAI-compatible (httpx): `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` z `.env`.
  Preferowany Bielik (partner eventu), jeśli mamy dostęp; w przeciwnym razie dowolny zgodny endpoint.
- Wejście: JSON alertu z `reasons`, metadane statku, podsumowanie trajektorii, najbliższy obiekt infrastruktury.
- Sekcje raportu: podsumowanie, chronologia, dane jednostki, przesłanki (z `reasons`),
  rekomendowane działania weryfikacyjne, poziom pewności.
- Zasady promptu: nie przypisuj winy, pisz o „przesłankach wymagających weryfikacji”,
  nie wymyślaj faktów spoza danych wejściowych.
- Raport generowany raz na incydent i język, potem z cache.

## 13. Frontend

- React + Vite + TypeScript, MapLibre GL JS. Basemap bez klucza (np. OpenFreeMap; fallback CARTO).
- `VITE_API_URL` w env Vercela.
- Warstwy z przełącznikami: kable, rurociągi, strefy (półprzezroczyste), statki (szare / żółte / czerwone
  wg `level`), wykrycia radarowe S1 (dopasowane do AIS / ciemne), plamy (poligony), trajektoria wybranego statku.
- Panel alertów po prawej: lista od najwyższego wyniku, kliknięcie → centrowanie mapy i szuflada statku.
- Szuflada statku: metadane, rozbicie wyniku reguła po regule (z `reasons`), trajektoria 6 h,
  przycisk „Generuj raport”.
- Karta plamy: powierzchnia, długość, czas zdjęcia, lista podejrzanych z `overlap`.
- Pasek statystyk u góry: pingi / statki / alerty / czas działania.
- Sterowanie replay (ukryte za tokenem admina): start scenariusza.
- Odświeżanie: `/vessels?bbox=` co 5 s dla widocznego obszaru; alerty przez `EventSource` na `/alerts/stream`.
- UI po angielsku (event dwujęzyczny), raporty PL z opcją EN.

## 14. Deploy

- `docker-compose.yml`: serwisy `ais-worker`, `detector`, `sat-worker` (`mem_limit: 3g`), `api`, `caddy`;
  wspólny wolumen `data:/data`; `restart: always`.
- Obraz bazowy `python:3.12-slim`. Na Oracle A1 (ARM) sprawdź, czy wheels shapely / pyproj / rasterio
  instalują się na arm64; jeśli nie, doinstaluj GDAL z apt.
- `Caddyfile`:
  ```
  api.{$DOMAIN} {
      reverse_proxy api:8000 {
          flush_interval -1
      }
  }
  ```
  (`flush_interval -1` żeby SSE działało bez buforowania.)
- Frontend na Vercel, `VITE_API_URL=https://api.<domena>`. Bez HTTPS na backendzie przeglądarka
  zablokuje zapytania (mixed content).

`.env.example`:
```
DOMAIN=
FRONTEND_ORIGIN=
DB_PATH=/data/kotwica.db
ADMIN_TOKEN=
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
AISSTREAM_KEY=
GFW_TOKEN=
REPLAY_SPEED=60
```

## 15. Kamienie milowe

- **M0 (pt do ~23:00):** szkielet repo, compose, `ais-worker` zapisuje do SQLite, `load_static.py`,
  deploy na VM. **Zbieranie danych ma ruszyć jak najwcześniej.**
- **M1 (sob do 12:00):** `geo.py`, detektor + reguły + testy, worker `detector`, API bez raportów.
- **M2 (sob do 18:00):** frontend (mapa, alerty, szuflada), scenariusz Eagle S + replay, SSE, deploy frontu.
- **M3 (sob do ~02:00):** najpierw moduł statków S1 na prawdziwej scenie z weekendu (dopasowanie do
  nagranego AIS, alerty `dark_vessel`), potem plamy S2 na jednej scenie + atrybucja, raport LLM, `/stats`.
  Jeśli czasu brak, S1 ma pierwszeństwo przed S2.
- **M4 (nd do 10:00):** poprawki, nagranie wideo z demo jako backup.

Nie zaczynaj kolejnego kamienia, dopóki poprzedni nie działa end-to-end na serwerze.

## 16. Zasady pracy dla Claude Code

- Realizuj kamienie po kolei. Po każdym uruchom testy i krótko podsumuj, co działa, a co nie.
- Każda reguła detektora ma test na syntetycznej trajektorii.
- Workery idempotentne i odporne na restart (reconnect z backoffem, śledzenie `last_processed_ts`).
- Logowanie `logging` na poziomie INFO, jedna linia na zdarzenie. Żadnych printów.
- Nie commituj `.env` ani `data/` (poza `data/static/` i `data/scenarios/`).
- Przed zmianą architektury, dodaniem ciężkiej zależności lub zmianą schematu bazy — zapytaj.
- Jeśli coś w tej specyfikacji jest sprzeczne albo niemożliwe, powiedz o tym, zamiast po cichu obchodzić.

## 17. Uczciwe ograniczenia (nie obiecuj więcej w UI ani w raportach)

- System wskazuje przesłanki i priorytety, nie dowodzi winy.
- Pokrycie AIS zależy od sieci odbiorników; luka nie zawsze oznacza wyłączenie transpondera
  (dlatego reguła `ais_gap` wymaga potwierdzenia pokrycia).
- Sentinel-1 widzi w nocy i przez chmury, ale to migawka co 1–2 dni, nie monitoring ciągły. Małe łodzie
  (poniżej ok. 15–20 m) i statki przy silnej fali mogą nie być widoczne. Wykrycie „ciemne” to przesłanka, nie dowód.
- Sentinel-2 działa tylko w dzień i przy małym zachmurzeniu; przeloty co kilka dni.
- Plamy dryfują; atrybucja bez korekty wiatru jest przybliżeniem.
- Progi detekcji plam wymagają kalibracji na prawdziwych scenach.
