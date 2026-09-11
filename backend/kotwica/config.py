"""Jedyne miejsce na progi, wagi i ustawienia. Kod logiki nie trzyma własnych stałych."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- środowisko ---
    DB_PATH: str = "../data/kotwica.db"
    STATIC_DIR: str = "../data/static"
    FRONTEND_ORIGIN: str = ""
    ADMIN_TOKEN: str = ""
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    AISSTREAM_KEY: str = ""
    GFW_TOKEN: str = ""
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
    PORT_SOG_MAX: float = 0.5       # węzły
    PORT_NAV_STATS: tuple[int, ...] = (5,)  # 5 = zacumowany; 1 (na kotwicy) celowo NIE
    PORT_STATIONARY_MIN: int = 30
    CLEAN_GRACE_H: int = 24         # okno na atrybucję satelitarną i analizę wsteczną

    # --- detektor (sekcja 8) ---
    ZONE_BUFFER_M: float = 2000
    SLOW_MIN: float = 1.0
    SLOW_MAX: float = 7.0
    SPEED_WINDOW_H: float = 2
    SPEED_DROP_RATIO: float = 1.5
    DWELL_MIN: int = 30
    GAP_MIN: int = 30
    LEVEL_WATCH: int = 50
    LEVEL_ALARM: int = 80


settings = Settings()
