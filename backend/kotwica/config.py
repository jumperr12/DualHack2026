"""Jedyne miejsce na progi, wagi i ustawienia. Kod logiki nie trzyma własnych stałych."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- środowisko ---
    DB_PATH: str = "../data/kotwica.db"
    STATIC_DIR: str = "../data/static"
    FRONTEND_ORIGIN: str = "http://localhost:5173"   # vite dev; po vite build front serwuje API
    ADMIN_TOKEN: str = ""
    REPLAY_SPEED: float = 60

    # --- obszar: bbox Bałtyku (min_lon, min_lat, max_lon, max_lat) ---
    BALTIC_BBOX: tuple[float, float, float, float] = (9.0, 53.0, 31.0, 66.0)

    # --- ingest AIS (Digitraffic) ---
    MQTT_HOST: str = "meri.digitraffic.fi"
    MQTT_PORT: int = 443
    MQTT_PATH: str = "/mqtt"
    MQTT_TOPIC: str = "vessels-v2/#"
    APP_NAME: str = "kotwica"
    DOWNSAMPLE_S: int = 30          # max 1 ping na MMSI na tyle sekund
    FLUSH_S: float = 2.0            # zapis wsadowy
    RETENTION_DAYS: int = 7         # twarda retencja pozycji
    RETENTION_CHECK_S: int = 3600   # jak często sprzątać

    # --- usuwanie czystych rejsów (statek zacumowany, bez alertu) ---
    # Domyślnie WYŁĄCZONE: tryb do tyłu (forensyka) potrzebuje śladów wszystkich jednostek,
    # także tych, które już weszły do portu. Zostaje tylko twarda retencja RETENTION_DAYS.
    CLEAN_VOYAGES: bool = False
    PORT_SOG_MAX: float = 0.5       # węzły
    PORT_NAV_STATS: tuple[int, ...] = (5,)  # 5 = zacumowany; 1 (na kotwicy) celowo NIE
    PORT_STATIONARY_MIN: int = 30
    CLEAN_GRACE_H: int = 24         # okno na atrybucję satelitarną i analizę wsteczną

    # --- detektor: strefy i reguły bazowe (sekcja 8.1) ---
    ZONE_BUFFER_M: float = 2000
    SLOW_MIN: float = 1.0
    SLOW_MAX: float = 7.0
    SPEED_WINDOW_H: float = 2
    SPEED_DROP_RATIO: float = 1.5
    # Sam stosunek prędkości nie wystarcza: „zwolnił z 0,2 do 0,1 kn" to szum pomiarowy stojącego
    # statku, a tak wyglądała połowa fałszywych alarmów w pierwszym pomiarze na prawdziwych danych.
    SPEED_DROP_MIN_KN: float = 5.0
    DWELL_MIN: int = 30
    GAP_MIN: int = 30
    REPEAT_CROSSING_H: float = 3
    GAP_COVERAGE_RADIUS_M: float = 20_000
    GAP_COVERAGE_MIN_VESSELS: int = 3
    LEVEL_WATCH: int = 50
    LEVEL_ALARM: int = 80
    ALERT_CLOSE_MIN: int = 60          # po tylu minutach poza strefą alert się zamyka

    # punkty reguł (tabela z sekcji 8.1 i 8.2)
    PTS_SLOW_IN_ZONE: int = 40
    PTS_SPEED_DROP: int = 25
    PTS_DWELL: int = 20
    PTS_REPEAT_CROSSING: int = 15
    PTS_AIS_GAP: int = 30
    PTS_TANKER_BONUS: int = 10
    PTS_DRAG_SIGNATURE: int = 35

    # --- sygnatura wleczenia (sekcja 8.2) ---  # TODO: kalibracja na prawdziwych danych
    FLEET_RADIUS_M: float = 25_000
    FLEET_BUCKET_MIN: int = 15
    FLEET_MIN_N: int = 5
    Z_SIG: float = 3.0
    SIG_WINDOW_MIN: int = 40
    SIG_PERSIST_MIN: float = 0.5
    STRAIGHT_MIN: float = 0.9
    # Poniżej tej prędkości COG z GNSS jest przypadkowy, więc rozjazd dziób/kurs nic nie znaczy.
    # Dotyczy tak samo kandydata, jak i floty odniesienia.
    SIG_SOG_MIN: float = 2.0
    # Dolne ograniczenie skali przy liczeniu z. Bez tego flota o zerowym rozrzucie (albo trzy
    # statki podające identyczny kurs) robi z 1° różnicy wynik rzędu setek.
    SIG_MIN_SCALE_DEG: float = 2.0

    # --- forensyka: tryb do tyłu (sekcja 9) ---
    FORENSIC_RADIUS_M: float = 10_000
    WIN_BACK_S: int = 6 * 3600
    WIN_FWD_S: int = 3600
    POST_EVENT_MIN: int = 60
    CANDIDATE_MIN_SCORE: int = 30
    FORENSIC_MAX_ROWS: int = 200_000
    FORENSIC_TIMEOUT_S: float = 8

    # --- kategorie (sekcja 8.3) ---
    WHITELIST_SHIP_TYPES: tuple[int, ...] = (31, 32, 33, 50, 51, 52, 53, 54, 55)
    FISHING_SHIP_TYPE: int = 30
    FISHING_NAV_STAT: int = 7
    FISHING_SCORE_FACTOR: float = 0.5
    TANKER_TYPES: tuple[int, int] = (80, 89)
    ANCHORED_NAV_STATS: tuple[int, ...] = (1, 5)


settings = Settings()
