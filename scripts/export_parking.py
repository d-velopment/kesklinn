#!/usr/bin/env python3
"""Refresh the Kesklinn map's GeoJSON from Tallinn's public ArcGIS layers.

The export combines eligible street parking lines, the tariff boundary and
resident zones T5–T12. It replaces the previous snapshot only after a complete
download and geometry processing.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import MultiLineString, mapping, shape
from shapely.ops import unary_union

SERVICES = "https://gis.tallinn.ee/arcgis/rest/services"
PARKING_LAYER = f"{SERVICES}/parkimine/MapServer/2"
TARIFF_LAYER = f"{SERVICES}/Andmed_Tallinn/MapServer/3"
RESIDENT_LAYER = f"{SERVICES}/parkimine/MapServer/5"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "kesklinn.geojson"
TARIFF_NAME = "Kesklinna parkimisala"
RESIDENT_ZONES = tuple(f"T{number}" for number in range(5, 13))
BATCH_SIZE = 100

# Public access, the ordinary paid regime, passenger cars allowed, and no
# separate restrictions on the time of day or length of stay.
PARKING_FILTER = (
    "piirang_kasutaja = 1 AND tasustamine = 1 "
    "AND piirang_s6iduk IN (1, 7, 8, 9) AND piirang__aeg = 4"
)

PARKING_FIELDS = (
    "objectid,parkimiskohad,piirang_kasutaja,tasustamine,"
    "piirang_s6iduk,piirang__aeg,maaomand"
)

def query(layer, **parameters):
    """Read one ArcGIS query response and surface API errors with the layer URL."""
    url = f"{layer}/query?{urlencode(parameters)}"

    with urlopen(url, timeout=30) as response:
        payload = json.load(response)

    if "error" in payload:
        raise ValueError(f"{layer}: {payload['error']}")

    return payload

def load_features(layer, where, fields):
    """Fetch all matching objects in bounded batches and check for omissions."""
    inventory = query(layer, where=where, returnIdsOnly="true", f="json")
    object_ids = sorted(inventory["objectIds"] or [])
    features = []

    for offset in range(0, len(object_ids), BATCH_SIZE):
        batch = object_ids[offset : offset + BATCH_SIZE]

        response = query(
            layer,
            where=where,
            objectIds=",".join(map(str, batch)),
            outFields=fields,
            outSR=4326,
            returnGeometry="true",
            returnZ="false",
            f="geojson",
        )

        page = response["features"]
        received = [feature_id(feature) for feature in page]

        if response.get("type") != "FeatureCollection" or sorted(received) != batch:
            raise ValueError(f"{layer}: incomplete or unexpected batch at offset {offset}")

        features.extend(sorted(page, key=feature_id))

    return features

def feature_id(feature):
    """Return the source object ID for stable ordering within a layer."""
    return feature["properties"]["objectid"]

def geometry_parts(geometry, kind):
    """Yield components of the requested type, ignoring empty intersections."""
    if geometry.is_empty:
        return

    if geometry.geom_type == kind:
        yield geometry
    elif geometry.geom_type in ("GeometryCollection", f"Multi{kind}"):
        for part in geometry.geoms:
            yield from geometry_parts(part, kind)

def tag(feature, layer, geometry):
    """Build a GeoJSON feature with a map layer tag and a layer-scoped ID."""

    return {
        "type": "Feature",
        "id": f"{layer}-{feature_id(feature)}",
        "geometry": geometry,
        "properties": {**feature["properties"], "layer": layer},
    }

def tariff_boundary():
    """Load and merge the official tariff polygons into a clipping boundary."""
    features = load_features(TARIFF_LAYER, "1=1", "objectid,nimetus")
    polygons = []

    for feature in features:
        if (feature["properties"]["nimetus"] or "").strip() != TARIFF_NAME:
            continue

        repaired = make_valid(shape(feature["geometry"]))
        parts = list(geometry_parts(repaired, "Polygon"))

        if not parts:
            raise ValueError(f"Missing tariff polygon for object {feature_id(feature)}")

        polygons.extend(parts)

    if not polygons:
        raise ValueError("No Kesklinn tariff polygons found")

    return unary_union(polygons)

def parking_lines(boundary):
    """Clip public parking lines to the tariff boundary and keep linear pieces."""
    result = []

    for feature in load_features(PARKING_LAYER, PARKING_FILTER, PARKING_FIELDS):
        source = shape(feature["geometry"])

        if source.is_empty or source.geom_type not in ("LineString", "MultiLineString"):
            raise ValueError(f"Missing parking line for object {feature_id(feature)}")

        pieces = list(geometry_parts(source.intersection(boundary), "LineString"))

        if pieces:
            geometry = pieces[0] if len(pieces) == 1 else MultiLineString(pieces)
            result.append(tag(feature, "parking", mapping(geometry)))

    if not result:
        raise ValueError("No eligible parking lines found inside Kesklinn")

    return result

def resident_zones():
    """Keep the full resident polygons as an independent reference layer."""
    names = ", ".join(f"'{zone}'" for zone in RESIDENT_ZONES)
    features = load_features(RESIDENT_LAYER, f"nimetus IN ({names})", "objectid,nimetus")
    found = set()
    result = []

    for feature in features:
        geometry = feature["geometry"]

        if not geometry or geometry["type"] not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"Missing resident polygon for object {feature_id(feature)}")

        found.add(feature["properties"]["nimetus"])
        result.append(tag(feature, "resident_zone", geometry))

    missing = set(RESIDENT_ZONES) - found

    if missing:
        raise ValueError(f"Missing resident zones: {', '.join(sorted(missing))}")

    return result

def write_snapshot(output, snapshot):
    """Write beside the destination and atomically replace it when complete."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=".kesklinn-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.write("\n")

        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def main():
    """Export one snapshot and report the result or an actionable error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="GeoJSON destination")
    args = parser.parse_args()

    try:
        boundary = tariff_boundary()
        parking = parking_lines(boundary)
        residents = resident_zones()

        tariff = {
            "type": "Feature",
            "id": "tariff-zone-kesklinn",
            "geometry": mapping(boundary),
            "properties": {"layer": "tariff_zone", "nimetus": TARIFF_NAME},
        }

        snapshot = {
            "type": "FeatureCollection",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sources": [PARKING_LAYER, TARIFF_LAYER, RESIDENT_LAYER],
            "features": [tariff, *residents, *parking],
        }

        write_snapshot(args.output, snapshot)
    except (URLError, OSError, GEOSException, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Kesklinn export failed: {error}\n")

    print(f"Exported {len(parking)} parking lines and {len(residents)} resident polygons.")
    print(args.output.resolve())

if __name__ == "__main__":
    main()
