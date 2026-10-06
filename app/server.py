"""Joker's eye — local application host, Python standard library only."""
import argparse
import csv
from http.cookies import CookieError, SimpleCookie
import io
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import secrets
import shutil
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
from scraper import Collector, today as source_today
from platform_support import default_data_dir, launch_browser, show_error

APP = "jokers-eye"
ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WEB = ROOT / "web"
START = "2023-04-27"
BASE_SHEET_START = "2024-03-01"
BASE_SHEET_END = "2026-09-30"
BASE_SHEET_COLUMNS = ["日付", "曜日", "台番号", "機種", "ジャグラーかジャグラーじゃないか", "ゲーム数", "BB数", "RB数", "合成", "差枚", "出率"]
JAPANESE_WEEKDAYS = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,filename TEXT NOT NULL,rows INTEGER NOT NULL,source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(day TEXT NOT NULL,seat TEXT NOT NULL,model TEXT NOT NULL,games INTEGER NOT NULL CHECK(games>=0),bb INTEGER NOT NULL CHECK(bb>=0),rb INTEGER NOT NULL CHECK(rb>=0),net INTEGER,rate TEXT NOT NULL CHECK(rate IN ('main','low','unknown')),import_id INTEGER NOT NULL REFERENCES imports(id),PRIMARY KEY(day,seat));
CREATE TABLE IF NOT EXISTS map_versions(id INTEGER PRIMARY KEY,valid_from TEXT NOT NULL,valid_to TEXT,notes TEXT);
CREATE TABLE IF NOT EXISTS seats(map_id INTEGER REFERENCES map_versions(id),seat TEXT,island TEXT,side TEXT,position INTEGER,x REAL,y REAL,left_seat TEXT,right_seat TEXT,PRIMARY KEY(map_id,seat));
CREATE TABLE IF NOT EXISTS installations(seat TEXT,model TEXT,rate TEXT,valid_from TEXT,valid_to TEXT,source TEXT,PRIMARY KEY(seat,valid_from));
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
        self.path = self.folder / "jokers-eye.sqlite3"
        self.map_source = json.loads((WEB / "map-data.json").read_text(encoding="utf-8"))
        self.floor_revision = hashlib.sha256((WEB / "floor-map.webp").read_bytes()).hexdigest()
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
        self.seed_public_map()
        self.collector = Collector(self, WEB / 'report-index.json')
        with self.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(scraped_observations)")}
            if columns and "combined" not in columns:
                db.execute("ALTER TABLE scraped_observations ADD COLUMN combined TEXT")
        self._base_cache_lock = threading.RLock()
        self._base_write_lock = threading.RLock()
        self._base_cache_key = None
        self._base_cache_rows = []

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
            union = "(SELECT day,seat,rate FROM observations UNION ALL SELECT day,seat,rate FROM scraped_observations s WHERE NOT EXISTS (SELECT 1 FROM observations o WHERE o.day=s.day AND o.seat=s.seat))"
            summary = dict(db.execute("SELECT COUNT(*) records,COUNT(DISTINCT day) days,COUNT(DISTINCT seat) seats,MIN(day) first,MAX(day) last FROM " + union).fetchone())
            summary["legacy"] = db.execute("SELECT COUNT(*) FROM observations WHERE day < ?", (START,)).fetchone()[0]
            summary["main"] = db.execute("SELECT COUNT(*) FROM observations WHERE day >= ? AND rate='main'", (START,)).fetchone()[0]
            latest = self.periods[-1] if self.periods else None
            map_count = latest["seatCount"] if latest else 0
            positioned = (WEB / "fixed-floor.json").is_file() or db.execute("SELECT COUNT(*) FROM physical_positions WHERE period=? AND image_revision=?", (latest["id"] if latest else "", self.floor_revision)).fetchone()[0]
            return {"app": APP, "version": VERSION, "summary": summary, "dataPath": str(self.folder), "settings": {r[0]:r[1] for r in db.execute("SELECT key,value FROM settings WHERE key NOT LIKE 'public_map_%'")}, "imports": [dict(r) for r in db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 100")], "mapRegistered": bool(positioned), "mapSeats": map_count, "mapReportDate": self.history_source.get("lastObservedDate"), "mapPeriods": len(self.periods), "mapReports": self.history_source.get("reportCount",0)}

    def base_sheet_path(self):
        return self.folder / "exports" / f"jokers-eye-base-data-{BASE_SHEET_START}_{BASE_SHEET_END}.csv"

    @staticmethod
    def _weekday(day):
        return JAPANESE_WEEKDAYS[date.fromisoformat(day).weekday()]

    @staticmethod
    def _is_juggler(model):
        return "ジャグラー" in unicodedata.normalize("NFKC", model or "")

    @staticmethod
    def _base_number(value):
        if value is None or value == "":
            return ""
        return str(value)

    @staticmethod
    def _base_rate(value):
        if value is None or value == "":
            return ""
        try:
            text = f"{float(value):.1f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(value)
        return text + "%"

    @staticmethod
    def _base_combined_denominator(value):
        match = re.fullmatch(r"1/(\d+(?:\.\d+)?)", str(value or "").strip())
        return float(match.group(1)) if match else None

    def _read_base_sheet(self):
        path = self.base_sheet_path()
        if not path.exists():
            return []
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._base_cache_lock:
            if key == self._base_cache_key:
                return list(self._base_cache_rows)
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames != BASE_SHEET_COLUMNS:
                    raise ValueError("基礎データシートの列構成が不正です。")
                rows = [{column: (row.get(column) or "") for column in BASE_SHEET_COLUMNS} for row in reader]
            self._base_cache_key, self._base_cache_rows = key, rows
            return list(rows)

    @staticmethod
    def _calendar_days(start, end):
        result = []
        current = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while current <= last:
            result.append(current.isoformat())
            current += timedelta(days=1)
        return result

    def _base_rows_from_db(self, days=None, end=BASE_SHEET_END):
        with self.connect() as db:
            params = [BASE_SHEET_START, end]
            where = "day BETWEEN ? AND ?"
            if days is not None:
                if not days:
                    return []
                placeholders = ",".join("?" for _ in days)
                where += f" AND day IN ({placeholders})"
                params.extend(days)
            query = f"""SELECT day,seat,model,games,bb,rb,combined,net,payout_percent
                FROM scraped_observations WHERE {where}
                ORDER BY day,CAST(seat AS INTEGER),seat"""
            source_rows = [dict(row) for row in db.execute(query, params)]
        return [self._base_row(row) for row in source_rows]

    def _base_row(self, row):
        model = row.get("model") or ""
        return {
            "日付": row.get("day", ""),
            "曜日": self._weekday(row["day"]),
            "台番号": self._base_number(row.get("seat")),
            "機種": model,
            "ジャグラーかジャグラーじゃないか": "ジャグラー" if self._is_juggler(model) else "ジャグラーではない",
            "ゲーム数": self._base_number(row.get("games")),
            "BB数": self._base_number(row.get("bb")),
            "RB数": self._base_number(row.get("rb")),
            "合成": self._base_number(row.get("combined")),
            "差枚": self._base_number(row.get("net")),
            "出率": self._base_rate(row.get("payout_percent")),
        }

    def _write_base_sheet(self, rows):
        path = self.base_sheet_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=BASE_SHEET_COLUMNS, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(path)
        with self._base_cache_lock:
            self._base_cache_key = None
            self._base_cache_rows = []
        return path

    def base_sheet_status(self):
        path = self.base_sheet_path()
        rows = self._read_base_sheet()
        dates = sorted({row["日付"] for row in rows})
        calendar_missing = [day for day in self._calendar_days(BASE_SHEET_START, BASE_SHEET_END) if day not in set(dates)]
        with self.connect() as db:
            unpublished = {row[0] for row in db.execute(
                "SELECT day FROM scrape_days WHERE status='not-published' AND day BETWEEN ? AND ?",
                (BASE_SHEET_START, BASE_SHEET_END)
            )}
        actionable_missing = [day for day in calendar_missing if day not in unpublished]
        return {
            "exists": path.exists(), "path": str(path), "columns": BASE_SHEET_COLUMNS,
            "rowCount": len(rows), "dayCount": len(dates), "firstDate": dates[0] if dates else None,
            "lastDate": dates[-1] if dates else None, "startDate": BASE_SHEET_START, "endDate": BASE_SHEET_END,
            "missingDates": calendar_missing, "actionableMissingDates": actionable_missing,
            "unpublishedDates": sorted(day for day in calendar_missing if day in unpublished),
            "missingBonusRows": sum(row['BB数']=='' or row['RB数']=='' for row in rows),
            "missingJugglerBonusRows": sum((row['BB数']=='' or row['RB数']=='') and self._is_juggler(row['機種']) for row in rows),
        }

    def build_base_sheet(self, mode="create"):
        with self._base_write_lock:
            return self._build_base_sheet(mode)

    def _build_base_sheet(self, mode):
        if mode not in ("create", "update", "bonuses"):
            raise ValueError("基礎データシートの処理が不正です。")
        existing = self._read_base_sheet()
        if mode == "create" and existing:
            return {"status": "exists", "addedRows": 0, "addedDates": [], "sheet": self.base_sheet_status()}
        if mode != "create" and not self.base_sheet_path().exists():
            raise ValueError("先に基礎データシートを作成してください。")
        existing_dates = {row["日付"] for row in existing}
        if mode == "create":
            new_rows = self._base_rows_from_db()
            rows = new_rows
        elif mode == "update":
            target_end = source_today()
            target_days = self._calendar_days(BASE_SHEET_START, target_end)
            missing = [day for day in target_days if day not in existing_dates]
            new_rows = self._base_rows_from_db(missing, target_end)
            rows = [dict(row) for row in existing] + new_rows
        else:
            new_rows = []
            rows = [dict(row) for row in existing]
        updated_rows = 0
        if mode != "create":
            source = {(row['日付'], row['台番号']): row for row in self._base_rows_from_db(end=source_today())}
            for row in rows:
                saved = source.get((row['日付'], row['台番号']))
                # Enrichment changes only empty bonus fields of the same record.
                # Preserve games/net/payout/model and any populated CSV counts.
                if not saved or (row['機種'], row['ゲーム数']) != (saved['機種'], saved['ゲーム数']):
                    continue
                if any(row[key] and saved[key] and row[key] != saved[key] for key in ('BB数','RB数')):
                    continue
                changed = False
                for key in ('BB数', 'RB数', '合成'):
                    if row[key] == '' and saved[key] != '':
                        row[key] = saved[key]
                        changed = True
                updated_rows += changed
        rows.sort(key=lambda row: (row["日付"], int(row["台番号"]) if str(row["台番号"]).isdigit() else str(row["台番号"])))
        self._write_base_sheet(rows)
        return {"status": "created" if mode == "create" else "updated", "addedRows": len(new_rows), "updatedRows": updated_rows,
                "addedDates": sorted({row["日付"] for row in new_rows}), "sheet": self.base_sheet_status()}

    def base_sheet_update_plan(self):
        status = self.base_sheet_status()
        if not status["exists"]:
            raise ValueError("先に基礎データシートを作成してください。")
        today_end = source_today()
        present = {row["日付"] for row in self._read_base_sheet()}
        missing = [day for day in self._calendar_days(BASE_SHEET_START, today_end) if day not in present]
        unpublished = set(status.get("unpublishedDates", []))
        actionable = [day for day in missing if day not in unpublished]
        return {"targetEnd": today_end, "missingDates": actionable, "missingDateCount": len(actionable),
                "unpublishedDates": sorted(day for day in missing if day in unpublished),
                "totalMissingDateCount": len(missing), "sheet": status}

    def base_sheet_bonus_plan(self, start=None, end=None):
        if not self.base_sheet_path().exists():
            raise ValueError("先に基礎データシートを作成してください。")
        start, end = start or BASE_SHEET_START, end or source_today()
        if date.fromisoformat(start).isoformat()!=start or date.fromisoformat(end).isoformat()!=end or not BASE_SHEET_START<=start<=end<=source_today():
            raise ValueError("BB/RB補完の日付範囲が不正です。")
        # Local synchronization precedes planning; no source request is needed
        # when counts have already been captured into SQLite.
        self.build_base_sheet('bonuses')
        wanted={(r['日付'],r['台番号']):r for r in self._read_base_sheet() if start<=r['日付']<=end and (r['BB数']=='' or r['RB数']=='')}
        with self.connect() as db:
            missing=[]
            for r in db.execute('SELECT day,seat,model,games FROM scraped_observations WHERE day BETWEEN ? AND ? AND (bb IS NULL OR rb IS NULL) ORDER BY day,CAST(seat AS INTEGER)',(start,end)):
                record=wanted.get((r['day'],r['seat']))
                if record and (record['機種'],record['ゲーム数'])==(r['model'],self._base_number(r['games'])):
                    missing.append(dict(r))
        dates=sorted({r['day'] for r in missing})
        return {'start':start,'targetEnd':end,'missingDates':dates,'missingDateCount':len(dates),'missingRows':len(missing),'unavailableRows':len(wanted)-len(missing)}

    @staticmethod
    def _parse_filter_number(value, label):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as ex:
            raise ValueError(label + "は数値で指定してください。") from ex

    def base_sheet_rows(self, query):
        rows = self._read_base_sheet()
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
            if combined_min is not None and (self._base_combined_denominator(row["合成"]) is None or self._base_combined_denominator(row["合成"]) < combined_min): continue
            if payout_min is not None and (number_or_none("出率") is None or number_or_none("出率") < payout_min): continue
            filtered.append(row)
        total = len(filtered)
        try: offset = max(0, int(query.get("offset", "0")))
        except ValueError: raise ValueError("ページ位置が不正です。")
        try: limit = min(500, max(1, int(query.get("limit", "100"))))
        except ValueError: raise ValueError("表示件数が不正です。")
        return {"rows": filtered[offset:offset + limit], "total": total, "offset": offset, "limit": limit, "status": self.base_sheet_status()}

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
                        installations.append((seat, model, None, period["validFrom"], period["validToExclusive"], period["reportUrl"] + "?num=" + seat))
                db.executemany("INSERT OR IGNORE INTO seats(map_id,seat) VALUES(?,?)", seats)
                db.executemany("""INSERT INTO installations(seat,model,rate,valid_from,valid_to,source) VALUES(?,?,?,?,?,?)
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

    def import_csv(self, text, filename, source):
        if not source.strip():
            raise ValueError("出典を入力してください。")
        reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
        required = {"date", "seat", "model", "games", "bb", "rb", "net", "rate"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("CSVの列が不足しています。テンプレートの列名を使用してください。")
        rows, seen = [], set()
        for line, r in enumerate(reader, 2):
            try:
                day = date.fromisoformat(r["date"].strip()).isoformat()
                seat, model, rate = r["seat"].strip(), r["model"].strip(), r["rate"].strip()
                games, bb, rb = [int(r[k]) for k in ("games", "bb", "rb")]
                net = int(r["net"]) if r["net"].strip() else None
                if not seat or not model or len(seat)>30 or len(model)>150:
                    raise ValueError()
                if min(games,bb,rb)<0 or max(games,bb,rb)>1000000 or bb+rb>games or (net is not None and abs(net)>10000000):
                    raise ValueError()
                if rate not in ("main", "low", "unknown") or day>date.today().isoformat():
                    raise ValueError()
                if (day,seat) in seen:
                    raise ValueError("同じ日付・台番号がCSV内に重複しています。")
                seen.add((day,seat))
                rows.append((day,seat,model,games,bb,rb,net,rate))
            except (ValueError, TypeError, AttributeError) as ex:
                raise ValueError("%s行目の値が不正です。日付・台番号・数値・貸区分を確認してください。 %s" % (line,str(ex))) from ex
        if not rows:
            raise ValueError("CSVにデータ行がありません。")
        with self.connect() as db:
            for r in rows:
                if db.execute("SELECT 1 FROM observations WHERE day=? AND seat=?",r[:2]).fetchone():
                    raise ValueError("%s / %s は登録済みです。既存のデータは変更していません。" % r[:2])
            cur = db.execute("INSERT INTO imports(created_at,filename,rows,source) VALUES(?,?,?,?)", (now(),Path(filename).name,len(rows),source.strip()))
            db.executemany("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)", [r+(cur.lastrowid,) for r in rows])
        return {"rows":len(rows)}

    def observations(self, day):
        with self.connect() as db:
            days=[r[0] for r in db.execute("SELECT day FROM observations UNION SELECT day FROM scraped_observations ORDER BY day DESC")]
            selected=day or (days[0] if days else "")
            rows=[dict(r) for r in db.execute("""SELECT day,seat,model,games,bb,rb,net,rate FROM observations WHERE day=?
                UNION ALL SELECT day,seat,model,games,bb,rb,net,rate FROM scraped_observations s WHERE day=? AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.day=s.day AND o.seat=s.seat)""",(selected,selected))]
            rows.sort(key=lambda r:(int(r['seat']) if r['seat'].isdigit() else 100000,r['seat']))
            return {"days":days,"selected":selected,"rows":rows}

    def settings(self, payload):
        values={k:str(payload[k]) for k in ("notes", "theme") if k in payload}
        if any(len(v)>10000 for v in values.values()):
            raise ValueError("設定値が長すぎます。")
        if "theme" in values and values["theme"] not in ("dark", "light"):
            raise ValueError("テーマが不正です。")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)",values.items())

    def backup(self):
        folder=self.folder/"backups"
        folder.mkdir(exist_ok=True)
        target=folder/("jokers-eye-"+datetime.now().strftime("%Y%m%d-%H%M%S-")+secrets.token_hex(3)+".sqlite3")
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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.info("%s %s",self.command,urlsplit(self.path).path)

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
                return self.reply(self.server.store.state())
            if path=="/api/scrape/status":
                return self.reply(self.server.store.collector.status())
            if path=="/api/scrape/browser-task":
                return self.reply(self.server.store.collector.browser_task())
            if path=="/api/scrape/csv":
                import scraper
                query=parse_qs(urlsplit(self.path).query)
                start=query.get('start',[START])[0]; end=query.get('end',[scraper.today()])[0]
                try:
                    if not START<=date.fromisoformat(start).isoformat()<=date.fromisoformat(end).isoformat()<=scraper.today(): raise ValueError('日付範囲が不正です。')
                    export=self.server.store.collector.export(self.server.store.folder/'exports',start,end)
                    return self.reply(Path(export['path']).read_bytes(),mime='text/csv; charset=utf-8',extra_headers={'Content-Disposition':'attachment; filename="gotham-city.csv"'})
                except ValueError as ex:
                    return self.reply({'error':str(ex)},400)
            if path=="/api/base-sheet/status":
                try:
                    return self.reply(self.server.store.base_sheet_status())
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/base-sheet/rows":
                try:
                    query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
                    return self.reply(self.server.store.base_sheet_rows(query))
                except ValueError as ex:
                    return self.reply({"error":str(ex)},400)
            if path=="/api/base-sheet/download":
                path_to_file = self.server.store.base_sheet_path()
                if not path_to_file.exists():
                    return self.reply({"error":"先に基礎データシートを作成してください。"},404)
                return self.reply(path_to_file.read_bytes(), mime="text/csv; charset=utf-8", extra_headers={"Content-Disposition": "attachment; filename=\"jokers-eye-base-data.csv\""})
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
        files={"/":"index.html","/app.js":"app.js","/base-sheet.js":"base-sheet.js","/map.js":"map.js","/floor-plan.js":"floor-plan.js","/fixed-floor.js":"fixed-floor.js","/fixed-floor.json":"fixed-floor.json","/fixed-floor-display.json":"fixed-floor-display.json","/scrape.js":"scrape.js","/style.css":"style.css","/map.css":"map.css","/juggler.js":"juggler.js","/juggler-math.js":"juggler-math.js","/juggler.css":"juggler.css","/juggler-specs.json":"juggler-specs.json","/icon.png":"icon.png","/icon.ico":"icon.ico","/template.csv":"template.csv","/manifest.json":"manifest.json","/floor-map.webp":"floor-map.webp"}
        if path not in files:
            return self.reply({"error":"Not found"},404)
        file=WEB/files[path]
        types={".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".png":"image/png",".ico":"image/x-icon",".csv":"text/csv; charset=utf-8",".json":"application/json",".webp":"image/webp"}
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
                return self.reply({"error":"ファイルは10MB以下にしてください。"},413)
            payload=json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload,dict):
                raise ValueError("リクエスト形式が不正です。")
            path=urlsplit(self.path).path
            if path=="/api/import":
                result=self.server.store.import_csv(payload["text"],payload["filename"],payload["source"])
            elif path=="/api/settings":
                self.server.store.settings(payload)
                result={"ok":True}
            elif path=="/api/map/position":
                result=self.server.store.save_position(payload)
            elif path=="/api/scrape/start":
                result=self.server.store.collector.start(payload.get('end'),payload.get('start'),payload.get('include_bonus',True),payload.get('only_dates'),payload.get('bonus_only',False))
            elif path=="/api/base-sheet/create":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後に基礎データシートを作成してください。")
                if self.server.store.base_sheet_path().exists():
                    result={"status":"exists","sheetMode":"create","sheetBuildPending":False,"sheet":self.server.store.base_sheet_status()}
                else:
                    result=self.server.store.collector.start(BASE_SHEET_END,BASE_SHEET_START,True,sheet_mode='create')
                    result.update({"sheetMode":"create","sheetBuildPending":True})
            elif path=="/api/base-sheet/update":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後に基礎データシートを更新してください。")
                plan=self.server.store.base_sheet_update_plan()
                if not plan["missingDates"]:
                    result={"status":"up-to-date","sheetMode":"update","sheetBuildPending":False,"plan":plan}
                else:
                    # Delta mode sends only dates absent from the existing CSV;
                    # existing dates are never re-requested by this action.
                    result=self.server.store.collector.start(plan["targetEnd"],min(plan["missingDates"]),True,plan["missingDates"])
                    result.update({"sheetMode":"update","sheetBuildPending":True,"plan":plan})
            elif path=="/api/base-sheet/bonuses":
                if self.server.store.collector.active:
                    raise ValueError("現在スクレイピング中です。完了後にBB/RBを補完してください。")
                plan=self.server.store.base_sheet_bonus_plan(payload.get('start'),payload.get('end'))
                if not plan['missingDates']:
                    result={'status':'up-to-date','sheetMode':'bonuses','sheetBuildPending':False,'plan':plan}
                else:
                    result=self.server.store.collector.start(plan['targetEnd'],min(plan['missingDates']),True,plan['missingDates'],True)
                    result.update({'sheetMode':'bonuses','sheetBuildPending':True,'plan':plan})
            elif path=="/api/base-sheet/build":
                result=self.server.store.build_base_sheet(payload.get("mode","create"))
            elif path=="/api/scrape/stop":
                result=self.server.store.collector.stop()
            elif path=="/api/scrape/browser-result":
                result=self.server.store.collector.browser_result(payload)
            elif path=="/api/backup":
                result={"path":self.server.store.backup()}
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
