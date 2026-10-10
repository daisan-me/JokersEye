"""Joker's eye — local application host, Python standard library only."""
import argparse
from http.cookies import CookieError, SimpleCookie
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import secrets
import socket
import sys
import sqlite3
import re
import threading
import time
import unicodedata
import urllib.request
from contextlib import contextmanager, closing
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper import Collector, FILL_KEYS, today as source_today
from platform_support import default_data_dir, launch_browser, show_error
from versions import VersionManager

APP = "jokers-eye"
# The packaged app (tools/build_package.py) carries web/ and VERSION in its bundle folder.
ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WEB = ROOT / "web"
START = "2023-04-27"
# GothamDataBase (GDB): the single SQLite file that holds all seat data. No CSV files are kept.
DB_NAME = "GothamDataBase.sqlite"
LEGACY_DB_NAME = "jokers-eye.sqlite3"  # renamed to DB_NAME on first start
GDB_START = "2024-03-01"
# A record with all 11 columns: 日付・台番号 are the key, 曜日・ジャグラーか are derived, these 7 must all have a value.
COMPLETE_ROW = ("model!='' AND games IS NOT NULL AND bb IS NOT NULL AND rb IS NOT NULL AND combined IS NOT NULL"
                " AND net IS NOT NULL AND payout_percent IS NOT NULL")
GDB_COLUMNS = ["日付", "曜日", "台番号", "機種", "ジャグラーかジャグラーじゃないか", "ゲーム数", "BB数", "RB数", "合成", "差枚", "出率"]
JAPANESE_WEEKDAYS = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS map_versions(id INTEGER PRIMARY KEY,valid_from TEXT NOT NULL,valid_to TEXT,notes TEXT);
CREATE TABLE IF NOT EXISTS seats(map_id INTEGER REFERENCES map_versions(id),seat TEXT,island TEXT,side TEXT,position INTEGER,x REAL,y REAL,left_seat TEXT,right_seat TEXT,PRIMARY KEY(map_id,seat));
CREATE TABLE IF NOT EXISTS installations(seat TEXT,model TEXT,valid_from TEXT,valid_to TEXT,source TEXT,PRIMARY KEY(seat,valid_from));
CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,kind TEXT NOT NULL,parameters TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physical_positions(period TEXT NOT NULL,seat TEXT NOT NULL,image_revision TEXT NOT NULL,x REAL NOT NULL,y REAL NOT NULL,angle REAL NOT NULL,evidence TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(period,seat,image_revision));
PRAGMA user_version=1;
"""

def now():
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / DB_NAME
        self._rename_legacy_database()
        self.map_source = json.loads((WEB / "map-data.json").read_text(encoding="utf-8"))
        # Positions are keyed to the fixed floor drawing (the shop's published floor image is not bundled).
        self.floor_revision = hashlib.sha256((WEB / "fixed-floor.json").read_bytes()).hexdigest()
        history_file = WEB / "map-history.json"
        if history_file.exists():
            history_bytes = history_file.read_bytes()
            self.history_source = json.loads(history_bytes)
        else:
            p = {"id": self.map_source["reportDate"], "validFrom": self.map_source["reportDate"],
                 "observedThrough": self.map_source["reportDate"],
                 "validToExclusive": (date.fromisoformat(self.map_source["reportDate"])+timedelta(days=1)).isoformat(),
                 "reportUrl": self.map_source["reportUrl"], "lastReportUrl": self.map_source["reportUrl"],
                 "publishedAt": self.map_source["reportPublishedAt"], "seatCount": self.map_source["totalSeats"],
                 "reportCount": 1, "seatRanges": self.map_source["seatRanges"], "changes": [],
                 "modelCountChanges": [], "announcements": [], "boundaryStatus": "first-observation"}
            self.history_source = {"periods": [p], "reportCount": 1, "indexedReportCount": 1, "failures": [],
                "firstObservedDate": p["id"], "lastObservedDate": p["id"], "announcements": []}
            history_bytes = json.dumps(self.history_source).encode("utf-8")
        self.history_revision = hashlib.sha256(history_bytes).hexdigest()
        self.periods = self.history_source["periods"]
        self.period_index = {p["id"]: p for p in self.periods}
        with self.connect() as db:
            db.executescript(SCHEMA)
            self._drop_lending_rate(db)
        self.seed_public_map()
        self.collector = Collector(self, WEB / 'report-index.json')
        with self.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(scraped_observations)")}
            if columns and "combined" not in columns:
                db.execute("ALTER TABLE scraped_observations ADD COLUMN combined TEXT")
        self._gdb_cache_lock = threading.RLock()
        self._gdb_cache_key = None
        self._gdb_cache_rows = []

    def _rename_legacy_database(self):
        """Up to 1.3.1 the database was jokers-eye.sqlite3. Rename it (with its WAL files) once."""
        legacy = self.folder / LEGACY_DB_NAME
        if self.path.exists() or not legacy.exists():
            return
        for suffix in ("-wal", "-shm", ""):  # main file last: a crash leaves the old name usable
            part = self.folder / (LEGACY_DB_NAME + suffix)
            if part.exists():
                part.rename(self.folder / (DB_NAME + suffix))

    @staticmethod
    def _drop_lending_rate(db):
        """The lending rate (貸区分) is no longer recorded (user decision, 2026-10-07).
        Drops the old column, and the old CSV-import tables when they are empty."""
        if "rate" in {row[1] for row in db.execute("PRAGMA table_info(installations)")}:
            db.execute("ALTER TABLE installations DROP COLUMN rate")
        for table in ("observations", "imports"):  # observations refers to imports: drop it first
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists and not db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                db.execute(f"DROP TABLE {table}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.connect() as db:
            summary = dict(db.execute("""SELECT COUNT(*) records,COUNT(DISTINCT day) days,COUNT(DISTINCT seat) seats,MIN(day) first,MAX(day) last,
                SUM(bb IS NOT NULL AND rb IS NOT NULL) withBonus,
                IFNULL(SUM(%s),0) complete,
                IFNULL(SUM(bb IS NOT NULL AND rb IS NOT NULL AND NOT (%s)),0) withDash,
                IFNULL(SUM(bb IS NULL OR rb IS NULL),0) notYetRead FROM scraped_observations""" % (COMPLETE_ROW, COMPLETE_ROW)).fetchone())
            summary["lockedRows"] = db.execute("SELECT COUNT(*) FROM gdb_locked_rows").fetchone()[0]
            latest = self.periods[-1] if self.periods else None
            map_count = latest["seatCount"] if latest else 0
            positioned = (WEB / "fixed-floor.json").is_file() or db.execute("SELECT COUNT(*) FROM physical_positions WHERE period=? AND image_revision=?", (latest["id"] if latest else "", self.floor_revision)).fetchone()[0]
            return {"app": APP, "version": VERSION, "summary": summary, "dataPath": str(self.folder), "settings": {r[0]:r[1] for r in db.execute("SELECT key,value FROM settings WHERE key NOT LIKE 'public_map_%'")}, "databaseFile": self.path.name, "runs": [dict(r) for r in db.execute("SELECT state,start_day,end_day,started_at,finished_at,message,stop_reason,stop_code,elapsed_seconds,days_done,seconds_per_day,refill_summary,mode FROM scrape_runs ORDER BY started_at DESC LIMIT 100")], "mapRegistered": bool(positioned), "mapSeats": map_count, "mapReportDate": self.history_source.get("lastObservedDate"), "mapPeriods": len(self.periods), "mapReports": self.history_source.get("reportCount",0)}

    @staticmethod
    def _weekday(day):
        return JAPANESE_WEEKDAYS[date.fromisoformat(day).weekday()]

    @staticmethod
    def _is_juggler(model):
        return "ジャグラー" in unicodedata.normalize("NFKC", model or "")

    @staticmethod
    def _gdb_number(value):
        if value is None or value == "":
            return ""
        return str(value)

    @staticmethod
    def _gdb_rate(value):
        if value is None or value == "":
            return ""
        try:
            text = f"{float(value):.1f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(value)
        return text + "%"

    @staticmethod
    def _gdb_combined_denominator(value):
        match = re.fullmatch(r"1/(\d+(?:\.\d+)?)", str(value or "").strip())
        return float(match.group(1)) if match else None

    @staticmethod
    def _calendar_days(start, end):
        result = []
        current = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while current <= last:
            result.append(current.isoformat())
            current += timedelta(days=1)
        return result

    @staticmethod
    def _gdb_dash(value, read):
        """A value the site showed as "-" is recorded as "-", whatever the cause (hidden or none; user decision,
        2026-10-09). Only a value that was never read stays "" (未取得)."""
        return "-" if value in (None, "") and read else value

    def _gdb_row(self, row):
        model = row.get("model") or ""
        bonus_read = row.get("bb") is not None and row.get("rb") is not None  # 合成 comes with BB/RB
        return {
            "日付": row.get("day", ""),
            "曜日": self._weekday(row["day"]),
            "台番号": self._gdb_number(row.get("seat")),
            "機種": model,
            "ジャグラーかジャグラーじゃないか": "ジャグラー" if self._is_juggler(model) else "ジャグラーではない",
            "ゲーム数": self._gdb_number(row.get("games")),
            "BB数": self._gdb_number(row.get("bb")),
            "RB数": self._gdb_number(row.get("rb")),
            "合成": self._gdb_dash(self._gdb_number(row.get("combined")), bonus_read),
            "差枚": self._gdb_dash(self._gdb_number(row.get("net")), True),        # the row comes from the all-seat table
            "出率": self._gdb_dash(self._gdb_rate(row.get("payout_percent")), True),
        }

    def gdb_rows_all(self):
        """Every GDB record from GDB_START in the 11 columns, by date then seat number.
        Cached until a row is added or its values (fetched_at) change."""
        with self.connect() as db:
            key = tuple(db.execute("SELECT COUNT(*),MAX(fetched_at) FROM scraped_observations WHERE day>=?", (GDB_START,)).fetchone())
            with self._gdb_cache_lock:
                if key != self._gdb_cache_key:
                    rows = db.execute("""SELECT day,seat,model,games,bb,rb,combined,net,payout_percent
                        FROM scraped_observations WHERE day>=? ORDER BY day,CAST(seat AS INTEGER),seat""", (GDB_START,))
                    self._gdb_cache_rows = [self._gdb_row(dict(row)) for row in rows]
                    self._gdb_cache_key = key
                return self._gdb_cache_rows

    def gdb_status(self):
        end = source_today()
        rows = self.gdb_rows_all()
        dates = sorted({row["日付"] for row in rows})
        present = set(dates)
        missing = [day for day in self._calendar_days(GDB_START, end) if day not in present]
        with self.connect() as db:
            unpublished = {row[0] for row in db.execute(
                "SELECT day FROM scrape_days WHERE status='not-published' AND day BETWEEN ? AND ?", (GDB_START, end))}
        return {
            "path": str(self.path), "columns": GDB_COLUMNS,
            "rowCount": len(rows), "dayCount": len(dates), "firstDate": dates[0] if dates else None,
            "lastDate": dates[-1] if dates else None, "startDate": GDB_START, "endDate": end,
            "missingDates": missing, "actionableMissingDates": [day for day in missing if day not in unpublished],
            "unpublishedDates": sorted(day for day in missing if day in unpublished),
            "missingBonusRows": sum(row["BB数"] == "" or row["RB数"] == "" for row in rows),
            "missingJugglerBonusRows": sum((row["BB数"] == "" or row["RB数"] == "") and row["ジャグラーかジャグラーじゃないか"] == "ジャグラー" for row in rows),
            "lockedDates": self.gdb_locked_days(),
            "lockedRows": self.gdb_locked_row_count(),
        }

    def gdb_today_plan(self):
        """「本日までの分を更新」: from the last date recorded in GDB (or GDB_START) to today.
        Dates after the last recorded one are tried even if they were unpublished at the last
        attempt (today's report appears only after the day ends)."""
        with self.connect() as db:
            last = db.execute("SELECT MAX(day) FROM scraped_observations WHERE day>=?", (GDB_START,)).fetchone()[0]
        return dict(self.gdb_range_plan(last or GDB_START, source_today(), retry_unpublished_after=last), lastDate=last)

    def gdb_bonus_plan(self, start=None, end=None):
        """Saved records whose BB or RB is still empty."""
        start, end = start or GDB_START, end or source_today()
        if date.fromisoformat(start).isoformat()!=start or date.fromisoformat(end).isoformat()!=end or not GDB_START<=start<=end<=source_today():
            raise ValueError("BB/RB補完の日付範囲が不正です。")
        with self.connect() as db:
            days = db.execute("""SELECT day,COUNT(*) FROM scraped_observations WHERE day BETWEEN ? AND ? AND (bb IS NULL OR rb IS NULL)
                GROUP BY day ORDER BY day""", (start, end)).fetchall()
        return {"start": start, "targetEnd": end, "missingDates": [row[0] for row in days], "missingDateCount": len(days),
                "missingRows": sum(row[1] for row in days)}

    def gdb_locked_row_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM gdb_locked_rows").fetchone()[0]

    def gdb_lock_complete_rows(self):
        """Lock every record whose 11 columns all have a value: it is then never overwritten or deleted."""
        with self.connect() as db:
            added = db.execute("INSERT OR IGNORE INTO gdb_locked_rows SELECT day,seat,? FROM scraped_observations WHERE " + COMPLETE_ROW,
                               (now(),)).rowcount
        return {"added": added, "lockedRows": self.gdb_locked_row_count()}

    def gdb_unlock_rows(self):
        with self.connect() as db:
            removed = db.execute("DELETE FROM gdb_locked_rows").rowcount
        return {"removed": removed, "lockedRows": 0}

    def gdb_locked_days(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT day FROM gdb_locked_days ORDER BY day")]

    def _gdb_day_range(self, start, end, action):
        try:
            valid = date.fromisoformat(start).isoformat() == start and date.fromisoformat(end).isoformat() == end
        except (TypeError, ValueError):
            valid = False
        if not valid or not GDB_START <= start <= end <= source_today():
            raise ValueError("%sする期間は %s から本日までの範囲で、開始日を終了日以前にしてください。" % (action, GDB_START))
        return self._calendar_days(start, end)

    def gdb_delete(self, start, end):
        """Delete every seat of the days in [start, end] so they are empty (未取得). They are fetched again by
        「期間を指定して取得」, or by 「本日までの分を更新」 when no later day is recorded. Locked days and locked rows are kept."""
        days = self._gdb_day_range(start, end, "削除")
        locked = set(self.gdb_locked_days())
        targets = [day for day in days if day not in locked]
        with self.connect() as db:
            rows = kept_rows = 0
            for day in targets:
                rows += db.execute("DELETE FROM scraped_observations WHERE day=? AND seat NOT IN (SELECT seat FROM gdb_locked_rows WHERE day=?)",
                                   (day, day)).rowcount
                kept_rows += db.execute("SELECT COUNT(*) FROM gdb_locked_rows WHERE day=?", (day,)).fetchone()[0]
                for table in ("scrape_days", "scrape_bonus_days", "scrape_bonus_targets"):
                    db.execute("DELETE FROM %s WHERE day=?" % table, (day,))
        logging.info("GDB delete %s..%s: %d rows, %d locked days kept", start, end, rows, len(days) - len(targets))
        return {"start": start, "end": end, "deletedRows": rows, "deletedDays": len(targets),
                "lockedKept": [day for day in days if day in locked], "lockedRowsKept": kept_rows}

    def gdb_lock(self, start, end, locked):
        """Lock (confirm) or unlock the days in [start, end]. A locked day cannot be deleted, overwritten or fetched."""
        days = self._gdb_day_range(start, end, "ロック" if locked else "ロック解除")
        with self.connect() as db:
            if locked:
                db.executemany("INSERT OR IGNORE INTO gdb_locked_days VALUES(?,?)", [(day, now()) for day in days])
            else:
                db.executemany("DELETE FROM gdb_locked_days WHERE day=?", [(day,) for day in days])
        return {"start": start, "end": end, "days": len(days), "lockedDates": self.gdb_locked_days()}

    def gdb_rescrape_plan(self, start, end):
        """「再スクレイプ」: saved days in [start, end] with an unlocked row that has an empty ("-") cell among the
        cells a re-read fills (FILL_KEYS, as Collector.rescrape_day). Locked days and rows are left out."""
        if not start or not end:
            raise ValueError("再スクレイプする期間の開始日と終了日を指定してください。")
        days = self._gdb_day_range(start, end, "再スクレイプ")
        empty = " OR ".join("o.%s IS NULL" % key for key in FILL_KEYS)
        with self.connect() as db:
            found = db.execute("""SELECT o.day,COUNT(*) FROM scraped_observations o WHERE o.day BETWEEN ? AND ? AND (%s)
                AND o.day NOT IN (SELECT day FROM gdb_locked_days)
                AND NOT EXISTS(SELECT 1 FROM gdb_locked_rows l WHERE l.day=o.day AND l.seat=o.seat)
                GROUP BY o.day ORDER BY o.day""" % empty, (days[0], days[-1])).fetchall()
        return {"start": start, "targetEnd": end, "dates": [row[0] for row in found], "dateCount": len(found),
                "rows": sum(row[1] for row in found)}

    def gdb_unread_days(self, start, end):
        """Saved days whose all-seat table has not been read since: they have rows but no result in scrape_days. A
        delete removes that result and keeps the locked rows, so their other seats are fetched again. A day read
        with fewer seats than expected keeps its 'partial' result and is not requested again on every run."""
        with self.connect() as db:
            return [row[0] for row in db.execute("""SELECT DISTINCT o.day FROM scraped_observations o
                WHERE o.day BETWEEN ? AND ? AND NOT EXISTS(SELECT 1 FROM scrape_days d WHERE d.day=o.day) ORDER BY o.day""", (start, end))]

    def gdb_range_plan(self, start, end, retry_unpublished_after=None):
        """Dates in [start, end] that still need work: no record yet (dates seen as unpublished are left out,
        except those after retry_unpublished_after), a saved day whose all-seat table has not been read since
        (e.g. only locked rows are left after a delete), or a saved record whose BB or RB is empty.
        Complete dates are not requested again."""
        if not start or not end:
            raise ValueError("取得する期間の開始日と終了日を指定してください。")
        if date.fromisoformat(start).isoformat()!=start or date.fromisoformat(end).isoformat()!=end or not GDB_START<=start<=end<=source_today():
            raise ValueError("取得する期間は %s から本日までの範囲で、開始日を終了日以前にしてください。" % GDB_START)
        status = self.gdb_status()
        retry = {day for day in status["unpublishedDates"] if retry_unpublished_after and day > retry_unpublished_after}
        new_days = [day for day in status["missingDates"] if start <= day <= end and (day in status["actionableMissingDates"] or day in retry)]
        locked = set(status["lockedDates"])  # a locked (confirmed) day is never fetched again
        kinds = {"new": new_days, "bonus": self.gdb_bonus_plan(start, end)["missingDates"], "unread": self.gdb_unread_days(start, end)}
        kinds = {name: [day for day in days if day not in locked] for name, days in kinds.items()}
        return {"start": start, "targetEnd": end, "missingDates": sorted(set().union(*kinds.values())),
                "newDateCount": len(kinds["new"]), "bonusDateCount": len(kinds["bonus"]), "unreadDateCount": len(kinds["unread"]),
                "lockedDates": [day for day in status["lockedDates"] if start <= day <= end],
                "unpublishedDates": [day for day in status["unpublishedDates"] if start <= day <= end and day not in retry]}

    @staticmethod
    def _parse_filter_number(value, label):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as ex:
            raise ValueError(label + "は数値で指定してください。") from ex

    def gdb_rows(self, query):
        rows = self.gdb_rows_all()
        from_day, to_day = query.get("from", ""), query.get("to", "")
        if from_day: date.fromisoformat(from_day)
        if to_day: date.fromisoformat(to_day)
        if from_day and to_day and from_day > to_day: raise ValueError("日付の範囲が不正です。")
        month = query.get("month", "")
        if month and not re.fullmatch(r"\d{4}-\d{2}", month): raise ValueError("月はYYYY-MMで指定してください。")
        weekday = query.get("weekday", "")
        if weekday not in ("", "0", "1", "2", "3", "4", "5", "6"): raise ValueError("曜日の指定が不正です。")
        games_min = self._parse_filter_number(query.get("games_min"), "ゲーム数")
        net_min = self._parse_filter_number(query.get("net_min"), "差枚")
        combined_min = self._parse_filter_number(query.get("combined_n_min"), "合成分母")
        payout_min = self._parse_filter_number(query.get("payout_min"), "出率")
        model = unicodedata.normalize("NFKC", query.get("model", "")).casefold()
        juggler = query.get("juggler", "all")
        if juggler not in ("all", "juggler", "non-juggler"): raise ValueError("ジャグラー判定の指定が不正です。")
        filtered = []
        for row in rows:
            day = row["日付"]
            if from_day and day < from_day or to_day and day > to_day or month and not day.startswith(month): continue
            if weekday and date.fromisoformat(day).weekday() != int(weekday): continue
            if model and model not in unicodedata.normalize("NFKC", row["機種"]).casefold(): continue
            is_juggler = row["ジャグラーかジャグラーじゃないか"] == "ジャグラー"
            if juggler == "juggler" and not is_juggler or juggler == "non-juggler" and is_juggler: continue
            def number_or_none(key, percent=False):
                value = row[key]
                if not value: return None
                try: return float(str(value).rstrip("%"))
                except ValueError: return None
            if games_min is not None and (number_or_none("ゲーム数") is None or number_or_none("ゲーム数") < games_min): continue
            if net_min is not None and (number_or_none("差枚") is None or number_or_none("差枚") < net_min): continue
            if combined_min is not None and (self._gdb_combined_denominator(row["合成"]) is None or self._gdb_combined_denominator(row["合成"]) < combined_min): continue
            if payout_min is not None and (number_or_none("出率") is None or number_or_none("出率") < payout_min): continue
            filtered.append(row)
        total = len(filtered)
        try: offset = max(0, int(query.get("offset", "0")))
        except ValueError: raise ValueError("ページ位置が不正です。")
        try: limit = min(500, max(1, int(query.get("limit", "100"))))
        except ValueError: raise ValueError("表示件数が不正です。")
        return {"rows": filtered[offset:offset + limit], "total": total, "offset": offset, "limit": limit, "status": self.gdb_status()}

    def seed_public_map(self):
        with self.connect() as db:
            revision = db.execute("SELECT value FROM settings WHERE key='public_map_revision'").fetchone()
            if revision and revision[0] == self.history_revision:
                return
            for period in self.periods:
                row = db.execute("SELECT id FROM map_versions WHERE valid_from=? ORDER BY id DESC LIMIT 1", (period["validFrom"],)).fetchone()
                if row:
                    map_id = row[0]
                    db.execute("UPDATE map_versions SET valid_to=? WHERE id=?", (period["validToExclusive"], map_id))
                else:
                    cur = db.execute("INSERT INTO map_versions(valid_from,valid_to,notes) VALUES(?,?,?)", (period["validFrom"], period["validToExclusive"], "公開ログによる配置履歴。座標未確定。"))
                    map_id = cur.lastrowid
                seats, installations = [], []
                for start, end, model in period["seatRanges"]:
                    for number in range(start, end + 1):
                        seat = str(number)
                        seats.append((map_id, seat))
                        installations.append((seat, model, period["validFrom"], period["validToExclusive"], period["reportUrl"] + "?num=" + seat))
                db.executemany("INSERT OR IGNORE INTO seats(map_id,seat) VALUES(?,?)", seats)
                db.executemany("""INSERT INTO installations(seat,model,valid_from,valid_to,source) VALUES(?,?,?,?,?)
                    ON CONFLICT(seat,valid_from) DO UPDATE SET valid_to=excluded.valid_to
                    WHERE installations.model=excluded.model AND installations.source=excluded.source""", installations)
            db.execute("INSERT INTO settings(key,value) VALUES('public_map_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.history_revision,))

    def coverage(self):
        gaps = [g for p in self.periods for g in p.get("gaps", [])]
        for left, right in zip(self.periods, self.periods[1:]):
            if left["validToExclusive"] < right["validFrom"]:
                gaps.append({"from": left["validToExclusive"], "toExclusive": right["validFrom"]})
        return {"first": self.history_source.get("firstObservedDate"), "last": self.history_source.get("lastObservedDate"),
                "reportCount": self.history_source.get("reportCount", 0), "periodCount": len(self.periods),
                "missingReports": len(self.history_source.get("failures", [])), "gaps": sorted(gaps, key=lambda g:g["from"])}

    def seat_map(self, period_id="", day=""):
        if period_id and day:
            raise ValueError("期間または日付のどちらかを指定してください。")
        if day:
            day = date.fromisoformat(day).isoformat()
            period = next((p for p in self.periods if p["validFrom"] <= day < p["validToExclusive"]), None)
            if period and period.get("reportRefs") and not any(r["day"] == day for r in period["reportRefs"]):
                period = None
        elif period_id:
            if period_id not in self.period_index:
                raise ValueError("この期間の機種マップは登録されていません。")
            period = self.period_index[period_id]
        else:
            period = self.periods[-1] if self.periods else None
        summaries = [{k:p.get(k) for k in ("id","validFrom","validToExclusive","observedThrough","seatCount","reportCount","boundaryStatus")} | {"changeCount":len(p["changes"]), "hasAnnouncement":bool(p["announcements"])} for p in self.periods]
        response = {"metadata": {k:v for k,v in self.map_source.items() if k != "seatRanges"},
                    "periods": summaries, "coverage": self.coverage(), "requestedDate": day or None,
                    "period": {k:v for k,v in period.items() if k != "seatRanges"} if period else None,
                    "seats": []}
        response["geometry"] = {"imageRevision": self.floor_revision, "status": "reference-only", "positions": [],
            "imageDate": self.map_source["mapImageDate"], "scope": "selected-period", "width": 640, "height": 360}
        if not period:
            return response
        with self.connect() as db:
            version = db.execute("SELECT * FROM map_versions WHERE valid_from=? ORDER BY id DESC LIMIT 1", (period["validFrom"],)).fetchone()
            rows = db.execute("""SELECT s.seat,s.island,s.side,s.position,s.x,s.y,i.model,i.source
                FROM seats s LEFT JOIN installations i ON i.seat=s.seat AND i.valid_from=?
                WHERE s.map_id=? ORDER BY CAST(s.seat AS INTEGER),s.seat""", (period["validFrom"], version["id"])).fetchall()
            response["seats"] = [dict(r) for r in rows]
            response["geometry"]["positions"] = [dict(r) for r in db.execute(
                "SELECT seat,x,y,angle,evidence,updated_at FROM physical_positions WHERE period=? AND image_revision=? ORDER BY CAST(seat AS INTEGER)",
                (period["id"], self.floor_revision))]
            if response["geometry"]["positions"]:
                response["geometry"]["status"] = "user-registered"
            for seat in response["seats"]:
                seat["labelNotes"] = [n for n in period.get("labelNotes", []) if n["seat"] == seat["seat"] and (not day or day in n["days"])]
            response["version"] = dict(version)
            response["metadata"].update(reportDate=period["validFrom"], reportUrl=period["reportUrl"], reportPublishedAt=period["publishedAt"])
            if day:
                report = next((r for r in period.get("reportRefs", []) if r["day"] == day), None)
                if report:
                    response["metadata"].update(reportDate=day, reportUrl=report["url"], reportPublishedAt=report["publishedAt"])
                    for seat in response["seats"]:
                        seat["source"] = report["url"]+"?num="+seat["seat"]
            return response

    def save_position(self, payload):
        period = self.period_index.get(payload.get("period"))
        if not period or payload.get("imageRevision") != self.floor_revision:
            raise ValueError("期間またはフロア図が更新されています。再読み込みしてください。")
        raw_seat = str(payload.get("seat", ""))
        if not raw_seat.isdigit():
            raise ValueError("台番号を指定してください。")
        seat = str(int(raw_seat))
        if not any(a <= int(seat) <= b for a,b,_ in period["seatRanges"]):
            raise ValueError("この期間に存在する台番号を指定してください。")
        with self.connect() as db:
            if payload.get("remove") is True:
                db.execute("DELETE FROM physical_positions WHERE period=? AND seat=? AND image_revision=?", (period["id"], seat, self.floor_revision))
                return {"ok": True}
            coordinates = [payload.get(k) for k in ("x", "y", "angle")]
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in coordinates):
                raise ValueError("有限の座標と向きを指定してください。")
            x,y,angle = coordinates
            if not (0 <= x <= 1 and 0 <= y <= 1 and -180 <= angle <= 180):
                raise ValueError("座標または向きが図の範囲外です。")
            evidence = payload.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000:
                raise ValueError("この期間の位置を確認した根拠を2000文字以内で入力してください。")
            db.execute("""INSERT INTO physical_positions VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(period,seat,image_revision) DO UPDATE SET x=excluded.x,y=excluded.y,angle=excluded.angle,evidence=excluded.evidence,updated_at=excluded.updated_at""",
                (period["id"], seat, self.floor_revision, x,y,angle,evidence.strip(),now()))
        return {"ok": True}

    def seat_history(self, number):
        if not number.isdigit() or not 0 < int(number) < 10000:
            raise ValueError("台番号を数字で指定してください。")
        number, entries = str(int(number)), []
        for p in self.periods:
            model = next((m for start,end,m in p["seatRanges"] if start <= int(number) <= end), None)
            normalized = lambda m: "".join(unicodedata.normalize("NFKC", m or "").split())
            if entries and normalized(entries[-1]["model"]) == normalized(model):
                if entries[-1]["validToExclusive"] < p["validFrom"]:
                    entries[-1]["gaps"].append({"from":entries[-1]["validToExclusive"], "toExclusive":p["validFrom"]})
                entries[-1].update(validToExclusive=p["validToExclusive"], observedThrough=p["observedThrough"], lastReportUrl=p["lastReportUrl"])
                entries[-1]["reportCount"] += p["reportCount"]
                entries[-1]["gaps"].extend(p.get("gaps", []))
                entries[-1]["labelNotes"].extend(n for n in p.get("labelNotes", []) if n["seat"] == number)
            else:
                entries.append({"periodId":p["id"], "validFrom":p["validFrom"], "validToExclusive":p["validToExclusive"],
                                "observedThrough":p["observedThrough"], "model":model, "reportCount":p["reportCount"],
                                "gaps":list(p.get("gaps", [])),
                                "labelNotes":[n for n in p.get("labelNotes", []) if n["seat"] == number],
                                "reportUrl":p["reportUrl"]+"?num="+number, "lastReportUrl":p["lastReportUrl"]+"?num="+number,
                                "boundaryStatus":p["boundaryStatus"]})
        return {"seat":number, "entries":entries, "coverage":self.coverage()}

    def observations(self, day):
        """One saved day in the GDB columns (日付・曜日・台番号・機種・ジャグラーか・ゲーム数・BB数・RB数・合成・差枚・出率)."""
        with self.connect() as db:
            days=[r[0] for r in db.execute("SELECT DISTINCT day FROM scraped_observations ORDER BY day DESC")]
            selected=day or (days[0] if days else "")
            rows=[self._gdb_row(dict(r)) for r in db.execute("""SELECT day,seat,model,games,bb,rb,combined,net,payout_percent
                FROM scraped_observations WHERE day=? ORDER BY CAST(seat AS INTEGER),seat""",(selected,))]
            return {"days":days,"selected":selected,"columns":GDB_COLUMNS,"rows":rows}

    def settings(self, payload):
        values={k:str(payload[k]) for k in ("notes", "theme") if k in payload}
        if any(len(v)>10000 for v in values.values()):
            raise ValueError("設定値が長すぎます。")
        if "theme" in values and values["theme"] not in ("dark", "light"):
            raise ValueError("テーマが不正です。")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)",values.items())

    def copy_into(self, folder):
        """Consistent copy of the database, for running another version on its own data.
        Saved under the legacy name: builds up to 1.3.1 read only that name, newer ones rename it."""
        folder=Path(folder)
        with self.connect() as source, closing(sqlite3.connect(folder/LEGACY_DB_NAME)) as dest:
            source.backup(dest)

    def backup(self):
        folder=self.folder/"backups"
        folder.mkdir(exist_ok=True)
        target=folder/(Path(DB_NAME).stem+"-"+datetime.now().strftime("%Y%m%d-%H%M%S-")+secrets.token_hex(3)+Path(DB_NAME).suffix)
        with self.connect() as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        return str(target)

class Host(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, store):
        super().__init__(address, Handler)
        self.store=store
        self.token=secrets.token_urlsafe(32)
        self.last_seen=time.monotonic()
        self.focus_requested=threading.Event()
        self.versions=VersionManager(store.folder,ROOT,VERSION,store.copy_into)

class Handler(BaseHTTPRequestHandler):
    QUIET_PATHS = {"/api/scrape/browser-task", "/api/scrape/browser-result", "/api/scrape/status", "/api/heartbeat"}

    def log_message(self, fmt, *args):
        # The six source-browser windows poll many times a second; record only other requests.
        path = urlsplit(self.path).path
        if path not in self.QUIET_PATHS:
            logging.info("%s %s",self.command,path)

    def reply(self, data, status=200, mime="application/json; charset=utf-8", extra_headers=None):
        raw=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,(dict,list)) else data
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        if secrets.compare_digest(self.headers.get("X-Joker-Token", ""),self.server.token):
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        session = cookies.get("joker-session")
        return bool(session and secrets.compare_digest(session.value,self.server.token))

    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith("/api/"):
            if not self.allowed():
                return self.reply({"error":"Joker's eyeを起動し直してください。"},403)
            if path=="/api/state":
                return self.reply(dict(self.server.store.state(),build=self.server.versions.build,trial=self.server.versions.trial))
            if path=="/api/versions":
                return self.reply(self.server.versions.overview())
            if path=="/api/versions/status":
                return self.reply(self.server.versions.status())
            if path=="/api/versions/remote":
                try:
                    return self.reply(self.server.versions.remote())
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/scrape/status":
                return self.reply(self.server.store.collector.status())
            if path=="/api/scrape/browser-task":
                try:
                    wait=float(parse_qs(urlsplit(self.path).query).get("wait",["0"])[0])
                except ValueError:
                    wait=0.0
                return self.reply(self.server.store.collector.browser_task(wait))
            if path=="/api/gdb/status":
                try:
                    return self.reply(self.server.store.gdb_status())
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/gdb/rows":
                try:
                    query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
                    return self.reply(self.server.store.gdb_rows(query))
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/map":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    return self.reply(self.server.store.seat_map(query.get("period",[""])[0], query.get("date",[""])[0]))
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/map/seat-history":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    return self.reply(self.server.store.seat_history(query.get("seat",[""])[0]))
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/observations":
                day=parse_qs(urlsplit(self.path).query).get("date",[""])[0]
                return self.reply(self.server.store.observations(day))
            if path=="/api/health":
                return self.reply({"app":APP,"version":VERSION})
            return self.reply({"error":"Not found"},404)
        files={"/":"index.html","/app.js":"app.js","/versions.js":"versions.js","/versions.css":"versions.css","/gdb.js":"gdb.js","/map.js":"map.js","/floor-plan.js":"floor-plan.js","/fixed-floor.js":"fixed-floor.js","/fixed-floor.json":"fixed-floor.json","/fixed-floor-display.json":"fixed-floor-display.json","/scrape.js":"scrape.js","/style.css":"style.css","/map.css":"map.css","/juggler.js":"juggler.js","/juggler-math.js":"juggler-math.js","/juggler.css":"juggler.css","/juggler-specs.json":"juggler-specs.json","/icon.png":"icon.png","/icon.ico":"icon.ico","/manifest.json":"manifest.json"}
        if path not in files:
            return self.reply({"error":"Not found"},404)
        file=WEB/files[path]
        types={".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".png":"image/png",".ico":"image/x-icon",".json":"application/json",".webp":"image/webp"}
        headers = None
        if path == "/":
            headers = {"Set-Cookie": "joker-session=%s; HttpOnly; SameSite=Strict; Path=/" % self.server.token}
        self.reply(file.read_bytes(),mime=types[file.suffix],extra_headers=headers)

    def do_POST(self):
        if not self.allowed():
            return self.reply({"error":"許可されていない操作です。"},403)
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<=size<=10*1024*1024:
                return self.reply({"error":"リクエストが大きすぎます。"},413)
            payload=json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload,dict):
                raise ValueError("リクエスト形式が不正です。")
            path=urlsplit(self.path).path
            if path=="/api/settings":
                self.server.store.settings(payload)
                result={"ok":True}
            elif path=="/api/map/position":
                result=self.server.store.save_position(payload)
            elif path=="/api/gdb/update":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後に更新してください。")
                plan=self.server.store.gdb_today_plan()
                if not plan["missingDates"]:
                    result={"status":"up-to-date","plan":plan}
                else:
                    result=self.server.store.collector.start(plan["targetEnd"],min(plan["missingDates"]),True,plan["missingDates"])
                    result.update({"plan":plan})
            elif path=="/api/gdb/range":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後に期間を指定して取得してください。")
                plan=self.server.store.gdb_range_plan(payload.get('start'),payload.get('end'))
                if not plan['missingDates']:
                    result={'status':'up-to-date','plan':plan}
                else:
                    # New dates get the full table plus BB/RB; saved dates only their missing BB/RB.
                    result=self.server.store.collector.start(plan['targetEnd'],min(plan['missingDates']),True,plan['missingDates'])
                    result.update({'plan':plan})
            elif path=="/api/gdb/rescrape":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後に再スクレイプしてください。")
                plan=self.server.store.gdb_rescrape_plan(payload.get('start'),payload.get('end'))
                if not plan['dates']:
                    result={'status':'up-to-date','plan':plan}
                else:
                    result=self.server.store.collector.start(plan['targetEnd'],plan['dates'][0],True,plan['dates'],fill_dashes=True)
                    result.update({'plan':plan})
            elif path in ("/api/gdb/delete","/api/gdb/lock","/api/gdb/unlock","/api/gdb/lock-complete-rows","/api/gdb/unlock-rows"):
                with self.server.store.collector.idle():  # refused while a run is going; no run starts meanwhile
                    if path=="/api/gdb/delete":
                        result=self.server.store.gdb_delete(payload.get("start"),payload.get("end"))
                    elif path=="/api/gdb/lock-complete-rows":
                        result=self.server.store.gdb_lock_complete_rows()
                    elif path=="/api/gdb/unlock-rows":
                        result=self.server.store.gdb_unlock_rows()
                    else:
                        result=self.server.store.gdb_lock(payload.get("start"),payload.get("end"),path=="/api/gdb/lock")
            elif path=="/api/scrape/stop":
                result=self.server.store.collector.stop(payload.get("reason","button"))
            elif path=="/api/scrape/browser-result":
                result=self.server.store.collector.browser_result(payload)
            elif path=="/api/backup":
                result={"path":self.server.store.backup()}
            elif path=="/api/versions/token":
                result=self.server.versions.set_token(payload.get("token"))
            elif path=="/api/versions/token/delete":
                result=self.server.versions.delete_token()
            elif path=="/api/versions/install":
                result=self.server.versions.install(payload["artifactId"])
            elif path=="/api/versions/launch":
                result=self.server.versions.launch(payload["id"])
            elif path=="/api/versions/remove":
                result=self.server.versions.remove(payload["id"])
            elif path=="/api/focus":
                self.server.focus_requested.set()
                result={"ok":True}
            elif path=="/api/heartbeat":
                self.server.last_seen=time.monotonic()
                result={"ok":True}
            elif path=="/api/shutdown":
                self.reply({"ok":True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            else:
                return self.reply({"error":"Not found"},404)
            self.reply(result)
        except (ValueError,KeyError,TypeError) as ex:
            self.reply({"error":str(ex)},400)
        except Exception:
            logging.exception("Request failed")
            self.reply({"error":"保存できませんでした。データフォルダーの空き容量とログを確認してください。"},500)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=18763)
    parser.add_argument("--data",default=os.environ.get("JOKERS_EYE_DATA",str(default_data_dir(ROOT))))
    parser.add_argument("--open",action="store_true")
    parser.add_argument("--session",default="")
    args=parser.parse_args()
    store=Store(args.data)
    logging.basicConfig(filename=store.folder/"application.log",level=logging.INFO,format="%(asctime)s %(message)s")
    statefile=Path(args.session) if args.session else store.folder/"session.json"
    try:
        host=Host(("127.0.0.1",args.port),store)
    except OSError:
        try:
            if statefile.exists():
                session=json.loads(statefile.read_text(encoding="utf-8"))
                req=urllib.request.Request(session["url"]+"api/health",headers={"X-Joker-Token":session["token"]})
                with urllib.request.urlopen(req,timeout=2) as r:
                    if json.load(r).get("app")==APP:
                        launch_browser(session["url"]+"#"+session["token"])
                        return
        except Exception as ex:
            logging.info("Existing session could not be reused (%s); using an alternate local port.",type(ex).__name__)
        host=Host(("127.0.0.1",0),store)
    url="http://127.0.0.1:%s/" % host.server_port
    session_temp=statefile.with_name("session-%s.tmp" % host.server_port)
    session_temp.write_text(json.dumps({"url":url,"token":host.token}),encoding="utf-8")
    os.replace(session_temp,statefile)
    if args.open:
        launch_browser(url+"#"+host.token)
    def watch():
        while True:
            time.sleep(30)
            if time.monotonic()-host.last_seen>120:
                host.shutdown()
                return
    threading.Thread(target=watch,daemon=True).start()
    try:
        host.serve_forever()
    finally:
        host.server_close()
        try:
            current=json.loads(statefile.read_text(encoding="utf-8"))
            if secrets.compare_digest(current.get("token",""),host.token):
                statefile.unlink(missing_ok=True)
        except (OSError,ValueError,AttributeError):
            pass

if __name__=="__main__":
    try:
        main()
    except Exception as ex:
        logging.exception("Startup failed")
        show_error("Joker's eye — 起動エラー",str(ex))
        raise SystemExit(1)
