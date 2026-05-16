from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT_DIR / "sql" / "schema.sql"
ENV_PATH = ROOT_DIR / ".env"


def connect(database: str | None = None):
    try:
        import mysql.connector
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "mysql-connector-python is not installed. Run: pip install -r requirements.txt"
        ) from exc

    _load_env_file()
    config = {
        "host": os.environ.get("STOCK_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("STOCK_DB_PORT", "3306")),
        "user": os.environ.get("STOCK_DB_USER", "root"),
        "password": os.environ.get("STOCK_DB_PASSWORD", ""),
        "charset": "utf8mb4",
        "use_unicode": True,
    }
    selected_database = database if database is not None else os.environ.get("STOCK_DB_NAME", "stock_analysis")
    if selected_database:
        config["database"] = selected_database
    return mysql.connector.connect(**config)


def _load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def initialize_schema(schema_path: Path = SCHEMA_PATH) -> None:
    statements = _split_sql_statements(schema_path.read_text(encoding="utf-8"))
    connection = connect(database="")
    try:
        cursor = connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()


def save_upload_payload(payload: dict) -> dict[str, int]:
    connection = connect()
    try:
        cursor = connection.cursor()
        batch_id = _upsert_upload_batch(cursor, payload)
        kiwoom_count = _upsert_kiwoom_rows(cursor, batch_id, payload)
        analysis_count = _upsert_analysis_rows(cursor, batch_id, payload)
        outcome_count = _upsert_outcome_rows(cursor, batch_id, payload)
        connection.commit()
        return {
            "batch_id": batch_id,
            "kiwoom_count": kiwoom_count,
            "analysis_count": analysis_count,
            "outcome_count": outcome_count,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_verification_rows() -> list[dict]:
    connection = connect()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
              b.file_name AS source_file,
              DATE_FORMAT(a.detected_date, '%Y.%m.%d') AS source_date,
              a.condition_type,
              a.stock_code AS code,
              COALESCE(a.stock_name, o.stock_name, k.stock_name) AS name,
              a.final_score,
              a.final_grade AS grade,
              a.volume_ratio AS vol_ratio,
              a.rsi,
              a.disparity,
              k.day_change_rate AS day_rate,
              o.base_price AS base,
              o.next_price AS after,
              o.price_diff AS diff,
              o.result_status AS status,
              o.return_rate AS rate
            FROM app_analysis_results a
            JOIN next_day_outcomes o
              ON a.detected_date = o.detected_date
             AND a.stock_code = o.stock_code
            LEFT JOIN kiwoom_condition_results k
              ON a.detected_date = k.detected_date
             AND a.stock_code = k.stock_code
            LEFT JOIN upload_batches b
              ON a.upload_batch_id = b.id
            ORDER BY a.detected_date DESC, a.row_order ASC, a.stock_code ASC
            """
        )
        return [_normalize_verification_row(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _normalize_verification_row(row: dict) -> dict:
    numeric_keys = ["final_score", "vol_ratio", "rsi", "disparity", "day_rate", "base", "after", "diff", "rate"]
    normalized = dict(row)
    for key in numeric_keys:
        value = normalized.get(key)
        if value is not None:
            normalized[key] = float(value)
    normalized["final_score"] = int(normalized.get("final_score") or 0)
    return normalized


def _upsert_upload_batch(cursor, payload: dict) -> int:
    cursor.execute(
        """
        INSERT INTO upload_batches (
          file_name, file_hash, detected_date, status, total_rows, valid_rows, duplicate_rows, memo
        ) VALUES (
          %(file_name)s, %(file_hash)s, %(detected_date)s, 'success',
          %(total_rows)s, %(valid_rows)s, %(duplicate_rows)s, %(memo)s
        )
        ON DUPLICATE KEY UPDATE
          id = LAST_INSERT_ID(id),
          detected_date = VALUES(detected_date),
          status = VALUES(status),
          total_rows = VALUES(total_rows),
          valid_rows = VALUES(valid_rows),
          duplicate_rows = VALUES(duplicate_rows),
          memo = VALUES(memo)
        """,
        {
            "file_name": payload["file_name"],
            "file_hash": payload["file_hash"],
            "detected_date": payload["detected_date"],
            "total_rows": payload["total_rows"],
            "valid_rows": payload["valid_rows"],
            "duplicate_rows": payload["duplicate_rows"],
            "memo": payload.get("memo"),
        },
    )
    return int(cursor.lastrowid)


def _upsert_kiwoom_rows(cursor, batch_id: int, payload: dict) -> int:
    rows = [
        {
            "upload_batch_id": batch_id,
            "detected_date": payload["detected_date"],
            **row,
        }
        for row in payload["kiwoom_rows"]
    ]
    if not rows:
        return 0
    cursor.executemany(
        """
        INSERT INTO kiwoom_condition_results (
          upload_batch_id, detected_date, stock_code, stock_name, current_price,
          day_direction, day_change_price, day_change_rate, volume, reference_text, row_order
        ) VALUES (
          %(upload_batch_id)s, %(detected_date)s, %(stock_code)s, %(stock_name)s, %(current_price)s,
          %(day_direction)s, %(day_change_price)s, %(day_change_rate)s, %(volume)s, %(reference_text)s, %(row_order)s
        )
        ON DUPLICATE KEY UPDATE
          upload_batch_id = VALUES(upload_batch_id),
          stock_name = VALUES(stock_name),
          current_price = VALUES(current_price),
          day_direction = VALUES(day_direction),
          day_change_price = VALUES(day_change_price),
          day_change_rate = VALUES(day_change_rate),
          volume = VALUES(volume),
          reference_text = VALUES(reference_text),
          row_order = VALUES(row_order)
        """,
        rows,
    )
    return len(rows)


def _upsert_analysis_rows(cursor, batch_id: int, payload: dict) -> int:
    rows = [
        {
            "upload_batch_id": batch_id,
            "detected_date": payload["detected_date"],
            **row,
        }
        for row in payload["analysis_rows"]
    ]
    if not rows:
        return 0
    cursor.executemany(
        """
        INSERT INTO app_analysis_results (
          upload_batch_id, detected_date, stock_code, stock_name, condition_type,
          close_price, pre_filter_score, final_score, final_grade, volume_ratio, rsi, disparity, row_order
        ) VALUES (
          %(upload_batch_id)s, %(detected_date)s, %(stock_code)s, %(stock_name)s, %(condition_type)s,
          %(close_price)s, %(pre_filter_score)s, %(final_score)s, %(final_grade)s,
          %(volume_ratio)s, %(rsi)s, %(disparity)s, %(row_order)s
        )
        ON DUPLICATE KEY UPDATE
          upload_batch_id = VALUES(upload_batch_id),
          stock_name = VALUES(stock_name),
          close_price = VALUES(close_price),
          pre_filter_score = VALUES(pre_filter_score),
          final_score = VALUES(final_score),
          final_grade = VALUES(final_grade),
          volume_ratio = VALUES(volume_ratio),
          rsi = VALUES(rsi),
          disparity = VALUES(disparity),
          row_order = VALUES(row_order)
        """,
        rows,
    )
    return len(rows)


def _upsert_outcome_rows(cursor, batch_id: int, payload: dict) -> int:
    rows = [
        {
            "upload_batch_id": batch_id,
            "detected_date": payload["detected_date"],
            **row,
        }
        for row in payload["outcome_rows"]
    ]
    if not rows:
        return 0
    cursor.executemany(
        """
        INSERT INTO next_day_outcomes (
          upload_batch_id, detected_date, result_date, stock_code, stock_name,
          base_price, next_price, price_diff, result_status, return_rate, row_order
        ) VALUES (
          %(upload_batch_id)s, %(detected_date)s, %(result_date)s, %(stock_code)s, %(stock_name)s,
          %(base_price)s, %(next_price)s, %(price_diff)s, %(result_status)s, %(return_rate)s, %(row_order)s
        )
        ON DUPLICATE KEY UPDATE
          upload_batch_id = VALUES(upload_batch_id),
          result_date = VALUES(result_date),
          stock_name = VALUES(stock_name),
          base_price = VALUES(base_price),
          next_price = VALUES(next_price),
          price_diff = VALUES(price_diff),
          result_status = VALUES(result_status),
          return_rate = VALUES(return_rate),
          row_order = VALUES(row_order)
        """,
        rows,
    )
    return len(rows)


def _split_sql_statements(sql: str) -> Iterable[str]:
    statement_lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        statement_lines.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(statement_lines).strip()
            statement_lines = []
            if statement:
                yield statement

    trailing_statement = "\n".join(statement_lines).strip()
    if trailing_statement:
        yield trailing_statement


if __name__ == "__main__":
    initialize_schema()
    print("MySQL schema initialized.")
