# HamQ: MVP plan

Radni naziv: **HamQ**. QGIS plugin za radio amatere: QTH lokator, log veza na mapi, DXCC statistika i živi prijem veza iz WSJT-X.

## Cilj MVP-a

Radio amater uveze svoj ADIF log i za par minuta vidi sve svoje veze na mapi, sa rastojanjem, azimutom, zemljom i statistikom. Nove FT8/FT4 veze iz WSJT-X se same pojavljuju na mapi.

## Obim

**U MVP-u:**
- Maidenhead konverzija (lokator <-> koordinate, 4, 6 i 8 znakova)
- Generisanje Maidenhead mreže kao vektorskog sloja
- Uvoz ADIF (.adi) fajla u GeoPackage
- Sloj veza (tačke) i sloj putanja (geodetske linije od mog QTH-a)
- Rastojanje (km) i azimut za svaku vezu
- DXCC entitet, kontinent, CQ i ITU zona iz pozivnog znaka (cty.dat)
- Dock panel sa statistikom (broj veza, DXCC, kontinenti, po bandu i modu)
- WSJT-X UDP listener: nova veza se automatski upisuje i prikazuje
- Podešavanja: moj pozivni znak, moj lokator, putanja do GeoPackage-a, UDP port

**Van MVP-a (posle v0.1):**
- Hamlib `rigctld` (čitanje i podešavanje frekvencije i moda)
- Hamlib `rotctld` (klik na mapu okreće antenu)
- Profil trase i Fresnelova zona preko DEM-a
- Pokrivanje repetitora (viewshed)
- Grey line sloj
- QRZ.com / HamQTH lookup
- ADX (XML) format, izvoz u ADIF

## Tehničke odluke

| Tema | Odluka |
|---|---|
| QGIS verzije | Min 3.34, testira se na 3.40 LTR i 4.x (Qt6). `supportsQt6=True` |
| Qt importi | Isključivo `from qgis.PyQt...`, nikad direktno PyQt5/PyQt6 |
| Zavisnosti | Nula spoljnih paketa. Samo stdlib + ono što dolazi uz QGIS |
| Arhitektura | `core/` je čist Python bez `qgis` importa, testira se sa pytest |
| Skladište | Jedan GeoPackage po korisniku, EPSG:4326 |
| Mreža | `QgsNetworkAccessManager` za preuzimanje cty.dat, keš lokalno |
| Dugi poslovi | `QgsTask` za uvoz, `QUdpSocket` (asinhrono) za WSJT-X |
| Algoritmi | Processing provider `hamq`, GUI je tanak sloj iznad |
| Licenca | GPL-3.0 |

## Model podataka (GeoPackage)

**`qso`** (Point, EPSG:4326)

| Polje | Tip | Napomena |
|---|---|---|
| fid | int | PK |
| call | text | velika slova |
| qso_datetime | datetime | UTC, iz QSO_DATE + TIME_ON |
| band | text | npr. `20m` |
| mode | text | |
| submode | text | npr. `FT4` |
| freq_mhz | real | |
| rst_sent, rst_rcvd | text | |
| gridsquare | text | lokator druge strane |
| my_gridsquare | text | |
| dxcc | int | ADIF DXCC broj ako postoji |
| country | text | |
| cont | text | EU, AS, AF, NA, SA, OC, AN |
| cq_zone, itu_zone | int | |
| distance_km | real | geodetsko, WGS84 |
| bearing_deg | real | početni azimut od mog QTH-a |
| loc_source | text | `latlon`, `grid`, `cty` (preciznost pozicije) |
| source | text | `adif:<fajl>` ili `wsjtx` |
| dedup_key | text | UNIQUE: call + minut + band + mode |
| adif_extra | text | JSON sa ostalim ADIF poljima |

**`qso_path`** (MultiLineString, EPSG:4326): `qso_fid`, `distance_km`, `bearing_deg`, `band`, `mode`. Geodetska linija, presečena na antimeridijanu.

## Faze

Svaka faza ima svoj task fajl u `tasks/`. Faza je gotova kad su ispunjeni kriterijumi prihvatanja.

### M0. Skelet (0,5 dana)
- Struktura repozitorijuma, `metadata.txt`, `classFactory`, prazan meni i toolbar
- Prazan Processing provider `hamq`
- pytest radi za `core/`, CI na GitHub Actions
- **Gotovo kad:** plugin se učitava bez greške u 3.40 i 4.x, `pytest` prolazi

### M1. Maidenhead (1 dan)
- `core/maidenhead.py`: `to_locator`, `to_latlon` (centar), `to_bounds`, `is_valid`
- Processing: *Locator to point*, *Generate Maidenhead grid* (nivo: polje / kvadrat / podkvadrat, za zadati obuhvat)
- Pretraga lokatora iz toolbara (unese `KN04ft`, mapa se centrira)
- **Gotovo kad:** test vektori iz skill-a prolaze, mreža na nivou kvadrata za Evropu se generiše za manje od 2 s

### M2. ADIF uvoz (2 dana)
- `core/adif.py`: parser .adi (header, zapisi, dužine polja, nepoznata polja)
- Processing: *Import ADIF*, upis u `qso` sa deduplikacijom
- Pozicija: LAT/LON > GRIDSQUARE > (M3) cty.dat
- **Gotovo kad:** uvoz 10.000 veza traje manje od 10 s, ponovni uvoz istog fajla ne pravi duplikate, loš zapis se preskače i loguje, ne ruši uvoz

### M3. Putanje i geodezija (1 dan)
- `distance_km`, `bearing_deg` za svaku vezu
- Sloj `qso_path` preko `QgsDistanceArea.geodesicLine`
- Podrazumevani stilovi (.qml): boja po bandu
- Opciono: dugme *Azimuthal map* (aeqd projekcija centrirana na moj QTH)
- **Gotovo kad:** veza Beograd-Sidnej ima ispravno rastojanje (±0,5%) i linija preko antimeridijana nije "razvučena" preko cele mape

### M4. DXCC i statistika (2 dana)
- `core/cty.py`: parser cty.dat, najduže poklapanje prefiksa, tačni pozivni znaci (`=`), override zona
- Preuzimanje cty.dat sa country-files.com, keš, ručno osvežavanje
- Dock panel: ukupno veza, DXCC entiteta, po kontinentu, po bandu, po modu, najduža veza
- **Gotovo kad:** test pozivni znaci iz skill-a daju ispravan entitet, panel se osvežava posle uvoza

### M5. WSJT-X live (1,5 dan)
- `core/wsjtx.py`: dekoder UDP poruka (Heartbeat, Status, QSO Logged, Logged ADIF)
- `QUdpSocket` na portu 2237, start/stop dugme
- Na *Logged ADIF* poruku: parsiraj ADIF, upiši u `qso`, osveži slojeve
- Status u panelu: povezan / nije povezan, poslednja frekvencija i mod
- **Gotovo kad:** snimljeni UDP paketi iz test fixtures se ispravno dekodiraju, veza iz pravog WSJT-X-a se pojavi na mapi za manje od 2 s

### M6. Izdanje v0.1.0 (1 dan)
- README sa screenshotovima, CHANGELOG, ikonice
- Prevod na srpski (`i18n/`)
- Paket za plugins.qgis.org, provera `metadata.txt`
- **Gotovo kad:** zip prolazi validaciju repozitorijuma, instalacija iz zip-a radi na čistom profilu

**Ukupno:** oko 9 radnih dana.

## Posle MVP-a (redosled)

1. `rigctld`: prikaz trenutne frekvencije, upis frekvencije u ručno unetu vezu
2. `rotctld`: klik na mapu, izračunaj azimut, okreni antenu
3. Profil trase + Fresnel (VHF/UHF)
4. Grey line
5. QRZ/HamQTH lookup

## Rizici

- **ADIF u praksi odstupa od specifikacije** (UTF-8, pogrešne dužine polja). Parser mora biti tolerantan, sa testovima na pravim fajlovima iz više log programa.
- **Qt5/Qt6 razlike** (enumi, `exec_` vs `exec`). Testirati na obe verzije od M0.
- **Licenca cty.dat**: ne pakovati uz plugin, preuzimati pri prvom pokretanju.
- **UDP port zauzet** (drugi program već sluša 2237). Podržati multicast adresu i jasnu poruku o grešci.
