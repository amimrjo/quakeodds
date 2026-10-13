# QuakeOdds

Earthquake probability for any place on Earth, from live USGS data.

## Run it

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Tests (offline): `pip install pytest && python -m pytest tests`
Production: `gunicorn 'app:create_app()'`

## How it works

1. Autocomplete: as you type, `/api/autocomplete` asks Photon (OpenStreetMap) for matching places, falling back to Nominatim. Results are cached for a day.
2. Live data: `/api/hazard` pulls the last 30 years of M3+ earthquakes within your radius, plus every quake in the last 30 days, from the USGS FDSN event API.
3. Model: finds the magnitude the local network reliably records, fits a Gutenberg–Richter law (Aki–Utsu b-value) to that local history, then turns yearly rates into the chance of at least one quake in 1, 10 and 30 years with a Poisson model, P = 1 − e^(−rate × years).

This is a statistical estimate, not an official forecast.

## Layout

```
app.py                                    EarthquakeProbabilityApp, HazardQuery
quakeodds/config.py                       AppConfig
quakeodds/models.py                       Location, Earthquake, ProbabilityEstimate, HazardReport
quakeodds/services/geocoding.py           GeocodingProvider (ABC), PhotonGeocoder, NominatimGeocoder, GeocodingService
quakeodds/services/usgs.py                USGSEarthquakeClient
quakeodds/analysis/gutenberg_richter.py   GutenbergRichterModel
quakeodds/analysis/hazard.py              ProbabilityModel (ABC), PoissonProbabilityModel, RiskClassifier, SeismicHazardAnalyzer
quakeodds/utils/                          TTLCache, haversine
templates/, static/                       Front end: ApiClient, Autocomplete, Seismogram, QuakeMap, ReportView
```
