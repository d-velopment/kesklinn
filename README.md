# Kesklinn

## Running the map locally

From the project root, start a static server and open
[http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser:

```sh
npm start
```

The page loads the checked-in GeoJSON. Select **Follow my location** to enable
GPS tracking. When opening the app on a phone from another device, serve it
over HTTPS so the phone can grant location access.

## Refreshing parking data

Before the first export, create a Python virtual environment and install the dependency:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt
```

For the first export and every refresh, run this command from the project root:

```sh
npm run import
```

The script fetches the latest data from Tallinn GIS and writes
`data/kesklinn.geojson`. Run `npm run import -- --output path.geojson` to save
a separate export for comparison. If the export fails, the previous file stays
in place.

The `properties.layer` field identifies each GeoJSON feature:

| Value | Contents |
| --- | --- |
| `parking` | Public parking segments in the Kesklinn tariff zone that use the standard municipal payment rules, allow passenger cars, and have no separate restrictions on parking hours or duration. |
| `tariff_zone` | Official boundary of the Kesklinn tariff zone. |
| `resident_zone` | Full boundaries of resident parking zones T5–T12. |

Coordinates use WGS 84. The `parkimiskohad` field counts spaces along the
entire source segment, even if the Kesklinn boundary clips it.
