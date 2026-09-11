# Decyzje techniczne: Baltic Dual Use Hackathon 2026

**Stan na:** piątek, 11 września 2026, wieczór
**Uzupełnia:** `podsumowanie_BDUH.md`. Koncepcja, E1–E4, role i harmonogram z podsumowania obowiązują bez zmian. Ten plik opisuje, jak to budujemy.

**Ścieżka:** Space, Track 01: *"Work on the systems that keep an eye on the ground from orbit and connect people when nothing else does — from satellite communication to early warning and Earth observation."* To jedyna oficjalna wytyczna. Pozostałe założenia są nasze. Pasujemy do „early warning” i obserwacji Ziemi, a do tego jednym systemem kosmicznym (Sentinel-1) chronimy inny (GNSS).

**Decyzja z 11.09 wieczorem:** rozważaliśmy zmianę tematu (zniszczenia infrastruktury, analiza lądu, łączność w czasie powodzi) i zostajemy przy tym pomyśle. Rdzeniem są strefy zakłóceń GNSS i rozdzielenie sprawcy od ofiary, a nie ciemne statki przy kablach.

---

## 1. W skrócie

System wykrywa zakłócenia GNSS na morzu i rozdziela statki, które kłamią o swojej pozycji, od statków, którym ktoś fałszuje GPS. Działa w całości na serwerze i ma dwie warstwy:
1. **Ruch statków na żywo z AIS**, z ciągłą kontrolą wiarygodności każdej pozycji (E2).
2. **Weryfikacja przez Sentinel-1** przy każdym przelocie satelity.

**Rdzeń projektu:**
- mapa stref na siatce 5 km: czysto, zagłuszanie, spoofing,
- klasa każdego statku: zgodny, oszukany, zagłuszony, ciemny podejrzany, pojedynczy kłamca (E3),
- projekt ostrzeżenia nawigacyjnego dla strefy.

Zakłócenia GNSS nad Bałtykiem trwają tygodniami, więc satelita przelatujący co 1–2 dni ma co mierzyć.

**Dodatek, nie główny alert:** obiekt widoczny na radarze bez AIS przy infrastrukturze krytycznej. Pokazujemy go, jeśli przelot trafi w odpowiedni moment. Incydent przy kablu trwa jednak godziny, więc satelita rzadko go złapie.

Przeglądarka użytkownika tylko wyświetla wyniki. Wszystkie obliczenia są na serwerze.

---

## 2. Dwie warstwy

| Warstwa | Źródło | Jak często | Co mówi |
|---|---|---|---|
| **Na żywo** | AIS z Digitraffic + E2 | Cały czas, aktualizacja co ok. 1 s | Gdzie statki twierdzą, że są, i czy te pozycje są fizycznie wiarygodne |
| **Weryfikacja** | Sentinel-1 | Przy przelocie, dany punkt co ok. 1–2 dni, dane 1–3 h po zdjęciu | Kto jest tam, gdzie mówi, kto jest oszukiwany, kogo radar widzi bez AIS |

Satelita to migawka, nie transmisja. Statek bez AIS da się wykryć tylko na zdjęciu, czyli w chwili przelotu. Między przelotami działa wyłącznie E2.

**Zasada częstotliwości.** Satelita wystarcza, jeśli zjawisko trwa dłużej niż przerwa między przelotami albo jeśli przerwy wypełnia ciągłe źródło danych.

| Zjawisko | Jak długo trwa | Czy satelita zdąży |
|---|---|---|
| Strefa zakłóceń GNSS | Tygodnie i miesiące | **Tak.** Każdy przelot to nowy pomiar, a między przelotami działa E2 |
| Pojedynczy epizod spoofingu grupy statków | Godziny | Czasem, jako dodatek |
| Ciemny statek przy kablu | Godziny | **Rzadko.** Nie nadaje się na alarm |

Odpowiedź dla jury: „AIS mierzy cały czas, a radar raz na dobę sprawdza, czy AIS kłamie”.

**Status zaufania każdego statku na mapie:**
- zgodny z radarem, z czasem od weryfikacji; pewność maleje z upływem czasu,
- niezweryfikowany,
- podejrzany według E2,
- sprzeczny z radarem.

---

## 3. Infrastruktura

- **Zwykły serwer (VPS) działający bez przerwy, nie Vercel.** Funkcje serverless nie utrzymają stałego połączenia z AIS i nie przeliczą sceny SAR.
- **Rozmiar:** 4 rdzenie, 8 GB RAM. Wystarczy, bo pobieramy wycinki obszarów, a nie całe sceny.
- **Wdrożenie:** `docker compose` z backendem i Caddy, który sam ustawia HTTPS. Frontend serwujemy z tego samego serwera, bo strona na https nie połączy się z backendem na zwykłym http.
- **Aktualizacja:** `git pull && docker compose up -d --build`.
- **Hasło do Copernicusa** trzymamy w zmiennych środowiskowych, nigdy w repo.
- **Finał bez internetu:** ten sam `docker compose` uruchamiamy na laptopie w trybie powtórki nagranego AIS. Dodatkowo wideo zapasowe, jak w planie.

---

## 4. Backend (Python, FastAPI, jeden proces)

**Robi:**
- odbiór AIS z Digitraffic przez MQTT (pozycje i osobny temat z metadanymi),
- trzymanie w pamięci ostatniej pozycji i kilkudziesięciu ostatnich punktów każdego statku,
- sprawdzanie E2 przy każdej nowej wiadomości, względem poprzedniego punktu tego samego statku,
- zapis wiadomości do Parquet co minutę,
- wysyłanie przez WebSocket paczki zmian do przeglądarek co ok. 1 s,
- udostępnianie przez API śladu wybranego statku na żądanie,
- pipeline satelitarny: wycinek obszaru z Copernicus Data Space → CFAR → dopasowanie do nagranego AIS → E1/E3 → zdarzenie weryfikacji dla frontendu.

**Tryby pracy:**
- **na żywo:** źródłem jest Digitraffic,
- **powtórka:** źródłem są nagrane pliki Parquet.

Oba tryby przechodzą przez tę samą ścieżkę kodu. Tryb powtórki budujemy od początku, a nie na koniec.

---

## 5. Dane

**Trzy tabele połączone numerem MMSI:**

| Tabela | Zawartość |
|---|---|
| `positions` | `mmsi, ts, lat, lon, sog, cog, heading, nav_status, source`. Tylko dopisywanie. |
| `vessels` | Nazwa, IMO, typ, długość, cel podróży |
| `anomalies` | `mmsi, ts, typ, wartość, lat, lon`, czyli wyniki E2 |

**Przechowywanie:**
- Pliki Parquet w folderach po dniu i godzinie. Zapytania robimy w DuckDB.
- Plik bazy DuckDB może otwierać do zapisu tylko jeden proces naraz. Dlatego backend dopisuje nowe pliki Parquet, a pipeline i API tylko je czytają.
- Pamięć DuckDB ograniczamy, np. `SET memory_limit = '3GB'`, żeby duże zapytanie nie zagłodziło odbioru AIS i przetwarzania SAR.
- Wszystkie znaczniki czasu zapisujemy w UTC, tak jak w Sentinel-1.
- MMSI nie jest wiarygodnym unikalnym kluczem, bo bywa błędny, współdzielony albo podrobiony. Traktujemy go jak etykietę, którą można podważyć.

**Skala (szacunek):**

| | Na dobę | Na weekend |
|---|---|---|
| Liczba pozycji | ok. 5–15 mln | ok. 30 mln |
| Rozmiar w Parquet | ok. 100–200 MB | poniżej 1 GB |

To lekkie zadanie dla 4 rdzeni. Najwięcej mocy zajmuje SAR, nie AIS.

---

## 6. Reguły E2 (zaktualizowana lista)

Każdą regułę sprawdzamy przy każdej wiadomości, co kosztuje stały, bardzo mały czas.

**Z podsumowania:**
- skok pozycji wymagający ponad 50 węzłów,
- pozycja na lądzie,
- wiele statków w identycznym punkcie,
- pozycja stojąca w miejscu przy deklarowanej prędkości,
- pozycja oznaczona jako niedostępna.

**Nowe:**
- prędkość podana w AIS niezgodna z faktycznym przesunięciem,
- kurs podany w AIS niezgodny z kierunkiem ruchu,
- **ten sam MMSI w dwóch odległych miejscach naraz**, co może oznaczać podszywanie się pod inny statek. Pasuje do klasy „pojedynczy kłamca”.
- **bardzo wolny ruch albo krążenie nad kablem lub rurociągiem.** Eagle S przy Estlink 2 miał włączony AIS, więc sam radar by go nie wyłapał.

---

## 7. Obszar i infrastruktura

- **Główny obszar: Zatoka Fińska.** Są tam częste zakłócenia GNSS i darmowy AIS na żywo z Digitraffic. Strefy zakłóceń sprawdzamy niezależnie na gpsjam.org.
- **Nie pobieramy całych scen Sentinel-1.** Przez API Copernicus Data Space zamawiamy tylko wycinek obszaru obserwacji (AOI) jako GeoTIFF (sigma0 VV). To kilka MB zamiast około 1 GB. AOI to w pierwszej kolejności rejony zakłóceń z dużym ruchem statków.
- **Infrastruktura krytyczna jako warstwa dodatkowa.** Kable i rurociągi Zatoki Fińskiej (Estlink 1/2, Balticconnector, C-Lion1) bierzemy z EMODnet Human Activities. Służą do reguły E2 o wolnym ruchu nad kablem i do dodatkowego alertu o obiekcie bez AIS, jeśli przelot trafi.
- **Dane historyczne z DMA (wody duńskie)** zostają jako baseline i plan B, jak w podsumowaniu.

---

## 8. Frontend

**Technologia:** statyczny HTML, zwykły JS i MapLibre GL JS, bez frameworka i bez kroku budowania. Bibliotekę trzymamy lokalnie w `vendor/`, a nie z CDN, żeby działała offline. Każde z nas musi umieć wytłumaczyć ten kod jury.

**Wygląd:** konsola operatora, a nie sama mapa.
- lista alertów posortowana według wagi,
- panel statku z jednym zdaniem wyjaśnienia, dlaczego ma daną klasę,
- przełączniki warstw: radar, AIS, wektory przesunięć, strefy,
- wykres głosowania z E1,
- projekt ostrzeżenia nawigacyjnego z przyciskiem „Zatwierdź”.

**Renderowanie, żeby nie obciążać urządzenia:**
- statki jako warstwa MapLibre (`symbol`/`circle`), nigdy jako markery DOM,
- do zoomu 7 klastry, powyżej pojedyncze statki,
- ikona obrócona według kursu z AIS, kolor według klasy z E3,
- nazwy statków od zoomu 10,
- ślad tylko po kliknięciu statku, uproszczony i z wybranego zakresu czasu,
- odświeżanie warstwy (`setData`) mniej więcej raz na sekundę, nie w każdej klatce.

**Offline:** żadnego podkładu mapowego z internetu. Tłem są poligony lądu z GSHHG/OSM, te same co w masce lądu.

---

## 9. Przechowywanie i usuwanie danych

- **Na hackathonie niczego nie kasujemy.** Historia jest potrzebna do dopasowania z satelitą (scena przychodzi kilka godzin po przelocie), do powtórki na finale i do uzasadnienia alertów.
- **Z mapy na żywo i z pamięci usuwamy statek, gdy:**
  - stoi w porcie: prędkość poniżej 0,5 węzła przez ponad 15 min w obrysie portu,
  - albo nie nadaje przez ponad 30 min.
  - Wyjątek: jeśli zamilkł przy kablu, zostaje na mapie jako alert „zamilkł”.
- **Historię dzielimy na rejsy.** Wejście do portu zamyka rejs, a mapa pokazuje ślad bieżącego rejsu.
- **W produkcie usuwamy dane według wieku, nie zdarzeń:**
  - pełna szczegółowość przez 7–30 dni,
  - potem uproszczone ślady,
  - potem usunięcie folderu z danego dnia.
  - To też argument prawny na pitch: dane AIS małych jachtów (klasa B) mogą być danymi osobowymi.

---

## 10. Sentinel-2

- **Uzupełnienie, nie rdzeń.** Nie widzi w nocy ani przez chmury. Dodaje za to przeloty przed południem (ok. 10:30 czasu lokalnego) między porannym a wieczornym przelotem S1 i daje zdjęcia, które każdy rozpozna.
- **Wspólny format wykryć dla obu satelitów:** `lat, lon, czas, rozmiar, sensor, pewność`. Dopasowanie, E1, E3 i mapa działają dla obu. Korektę przesunięcia azymutalnego stosujemy tylko dla SAR.
- **Detektor:** jasne obiekty w paśmie B08 na wodzie. Chmury i cienie odrzucamy warstwą klasyfikacji sceny (SCL) z produktu L2A.
- **Kiedy:** dopiero po sobotnim punkcie kontrolnym o 14:00 i tylko, jeśli w weekend jest pogodny przelot nad naszym obszarem.

---

## 11. Priorytety

1. **Nagrywanie AIS: natychmiast.** Na laptopie, dopóki nie ma serwera. Każda godzina bez nagrania to mniej danych do dopasowania z przelotem.
2. **Baseline do soboty 14:00.** Twardy punkt kontrolny bez zmian: bez działającego baseline'u nie ruszamy rozszerzenia.
3. **Równolegle:** backend na żywo, E2, mapa na żywo, tryb powtórki.
4. **Weryfikacja S1:** do demo wystarczy ręcznie pobrany wycinek i pipeline uruchamiany jednym poleceniem.
5. **Po 14:00, czyli rdzeń rozszerzenia:** E1 (głosowanie przesunięć), E3 (klasy statków), mapa stref, ostrzeżenie nawigacyjne.
6. **Tylko jeśli zostanie czas:** alert o obiekcie bez AIS przy infrastrukturze, lokalizacja źródła zakłóceń (E4.2), automatyczne sprawdzanie nowych scen w Copernicus Data Space, Sentinel-2, publiczny link z kodem QR.

**Zasada cięć:** jeśli czasu brakuje, warstwa na żywo może się uprościć do powtórki nagranego AIS. Rdzenia, czyli E1, E3, mapy stref i ostrzeżenia, nie ruszamy.

**Pomysł na demo:** powtórka nagranego ruchu. Pojawia się przelot Sentinel-1 i statusy statków się zmieniają:
- grupa statków w jednej strefie ze wspólnym przesunięciem zostaje oznaczona jako „oszukane”, a nie „ciemne”,
- na mapie pojawia się strefa spoofingu,
- system tworzy gotowy projekt ostrzeżenia nawigacyjnego do zatwierdzenia.

---

## 12. Otwarte sprawy

1. **Dostawca serwera.** Najpierw zapytać organizatorów, czy partnerzy dają kredyty w chmurze. W przeciwnym razie Hetzner (serwery w UE, rozliczenie godzinowe).
2. **Kto stawia serwer i frontend.** W podsumowaniu dashboard ma osoba „Statki”, która ma już dużo innych zadań.
3. **Przelot Sentinel-1 nad Zatoką Fińską w weekend.** To największe ryzyko: potrzebna jest przynajmniej jedna scena, która pokrywa się w czasie z nagranym AIS. Plan B: dane z DMA z dnia, dla którego jest scena.
4. **Pogodny przelot Sentinel-2.** Sprawdzić, zanim poświęcimy na niego czas.
5. **Limity darmowego konta Copernicus Data Space** przy pobieraniu wycinków przez API.
6. **Nazwa projektu.**
