"""Time-series ingestion (from MQTT) and query (for the mobile app).

Storage is a TimescaleDB hypertable (`tsdata`) — chunked by week and
queried with `time_bucket()` for aggregation. The schema keeps four
typed columns (int/float/bool/str) and the data_type discriminator so
we don't lose precision across heterogeneous params.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")


def _parse_interval(spec: str) -> timedelta:
    m = _INTERVAL_RE.match(spec.strip())
    if not m:
        raise ValueError(f"Invalid interval: {spec!r}")
    n = int(m.group(1))
    unit = m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


def _coerce(dt: str, v: Any) -> dict[str, Any]:
    columns: dict[str, Any] = {
        "data_type": dt,
        "value_int": None,
        "value_float": None,
        "value_bool": None,
        "value_str": None,
    }
    if dt == "int":
        columns["value_int"] = int(v)
    elif dt in {"float", "number"}:
        columns["value_float"] = float(v)
    elif dt == "bool":
        columns["value_bool"] = bool(v)
    else:
        columns["value_str"] = str(v)
    return columns


def _value_column(dt: str) -> str:
    return {
        "int": "value_int",
        "float": "value_float",
        "number": "value_float",
        "bool": "value_bool",
    }.get(dt, "value_str")


async def insert_tsdata(
    db: AsyncSession,
    *,
    node_id: str,
    device_name: str,
    param_name: str,
    data_type: str,
    points: list[tuple[datetime, Any]],
) -> int:
    """Insert ``points`` rows. Returns the number of rows inserted."""
    if not points:
        return 0
    rows = []
    for ts, value in points:
        cols = _coerce(data_type, value)
        rows.append(
            {
                "ts": ts,
                "nid": node_id,
                "d": device_name,
                "p": param_name,
                **{k: cols[k] for k in cols if k != "data_type"},
                "dt": cols["data_type"],
            }
        )
    await db.execute(
        text(
            "INSERT INTO tsdata "
            "(ts, node_id, device_name, param_name, data_type, "
            " value_int, value_float, value_bool, value_str) VALUES "
            "(:ts, :nid, :d, :p, :dt, :value_int, :value_float, :value_bool, :value_str)"
        ),
        rows,
    )
    await db.commit()
    return len(rows)


async def query_tsdata(
    db: AsyncSession,
    *,
    node_id: str,
    param: str,
    start_time: datetime,
    end_time: datetime,
    aggregate: str | None = None,
    aggregate_interval: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if "." not in param:
        raise ValueError("param must be `Device.param`")
    device_name, param_name = param.split(".", 1)

    # Look up the data_type from any matching row, defaulting to int.
    dt_row = (
        await db.execute(
            text(
                "SELECT data_type FROM tsdata "
                "WHERE node_id=:nid AND device_name=:d AND param_name=:p "
                "ORDER BY ts DESC LIMIT 1"
            ),
            {"nid": node_id, "d": device_name, "p": param_name},
        )
    ).first()
    dt = dt_row[0] if dt_row else "int"
    value_col = _value_column(dt)

    params = {
        "nid": node_id,
        "d": device_name,
        "p": param_name,
        "start_ts": start_time,
        "end_ts": end_time,
        "lim": limit,
    }

    if aggregate and aggregate_interval:
        delta = _parse_interval(aggregate_interval)
        agg = {"avg": "AVG", "min": "MIN", "max": "MAX", "sum": "SUM"}.get(aggregate.lower())
        if agg is None:
            raise ValueError(f"Unsupported aggregate {aggregate}")
        # agg and value_col are from internal whitelists; the user-supplied
        # values flow only through bind parameters.
        query = (
            f"SELECT time_bucket(:bucket, ts) AS bucket, "  # noqa: S608
            f"{agg}({value_col})::float AS v "
            f"FROM tsdata "
            f"WHERE node_id=:nid AND device_name=:d AND param_name=:p "
            f"AND ts >= :start_ts AND ts <= :end_ts "
            f"GROUP BY bucket ORDER BY bucket ASC LIMIT :lim"
        )
        params["bucket"] = delta
        rows = (await db.execute(text(query), params)).all()
        return [{"t": int(r.bucket.timestamp()), "v": r.v} for r in rows]

    query = (
        f"SELECT ts, {value_col} AS v FROM tsdata "  # noqa: S608
        f"WHERE node_id=:nid AND device_name=:d AND param_name=:p "
        f"AND ts >= :start_ts AND ts <= :end_ts "
        f"ORDER BY ts ASC LIMIT :lim"
    )
    rows = (await db.execute(text(query), params)).all()
    return [{"t": int(r.ts.timestamp()), "v": r.v} for r in rows]


def _seconds_to_dt(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)
