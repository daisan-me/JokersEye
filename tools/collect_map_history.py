"""Collect public seat/model facts and derive periods from observed changes.

Run explicitly; the application never crawls in the background. Responses are
reduced to factual seat/model rows and source metadata, with a resumable cache.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

TAG = "https://min-repo.com/tag/" + quote("ゴッサムシティ") + "/"
NOTICES = "https://p-town.dmm.com/shops/hokkaido/1295/informations"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find(self, tag=None, cls=None):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get("class", "").split()):
                    found.append(child)
                found.extend(child.find(tag, cls))
        return found

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(c.text() if isinstance(c, Node) else c for c in self.children).strip()


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def compact(text):
    return re.sub(r"\s+", " ", text).strip()


def dated(text):
    match = re.search(r"(20\d\d)[/年.-](\d{1,2})[/月.-](\d{1,2})", text)
    return date(*map(int, match.groups())).isoformat() if match else None


class Fetcher:
    def __init__(self, transport="auto"):
        self.lock = threading.Lock()
        self.next_request = 0
        self.stopped = threading.Event()
        self.transport = "powershell" if transport == "auto" and os.name == "nt" and shutil.which("powershell.exe") else ("urllib" if transport == "auto" else transport)

    def download(self, url):
        if self.transport == "powershell":
            # Use the platform HTTP client, also used to inspect these public pages.
            # URL arguments remain data; only the two source hosts are accepted.
            from urllib.parse import urlsplit
            if urlsplit(url).hostname not in {"min-repo.com", "p-town.dmm.com"}:
                raise ValueError("Unsupported source host")
            quoted = url.replace("'", "''")
            command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); try { $historyResponse = Invoke-WebRequest -UseBasicParsing -Uri '" + quoted + "' -TimeoutSec 30; [System.Text.Encoding]::UTF8.GetString($historyResponse.RawContentStream.ToArray()) } catch { if ($_.Exception.Response) { [Console]::Error.WriteLine('HTTP_STATUS:'+[int]$_.Exception.Response.StatusCode) }; exit 1 }"
            response = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, encoding="utf-8", timeout=45)
            if response.returncode:
                match = re.search(r"HTTP_STATUS:(\d+)", response.stderr)
                if match:
                    raise HTTPError(url, int(match[1]), response.stderr.strip(), None, None)
                raise OSError(response.stderr.strip() or "Platform HTTP request failed")
            return response.stdout
        request = Request(url, headers={"User-Agent": "JokersEye/0.3 public-history-research", "Accept": "text/html"})
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")

    def get(self, url):
        for attempt in range(3):
            if self.stopped.is_set():
                raise RuntimeError("Collection stopped after a server rate/access response")
            with self.lock:
                delay = max(0, self.next_request - time.monotonic())
                self.next_request = max(self.next_request, time.monotonic()) + 1
            if delay:
                time.sleep(delay)
            try:
                return self.download(url)
            except HTTPError as ex:
                if ex.code in (401, 403, 429):
                    self.stopped.set()
                    raise
                if attempt == 2 or ex.code == 404:
                    raise
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 * (attempt + 1))


def index_reports(fetch, start, end):
    pending, visited, reports = [TAG], set(), {}
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        root = Document(fetch.get(url)).root
        for a in root.find("a"):
            href = urljoin(url, a.attrs.get("href", ""))
            day = dated(a.text())
            short_day = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\([^)]*\)\s*", a.text())
            if not day and short_day:
                day = date(date.fromisoformat(end).year, *map(int, short_day.groups())).isoformat()
            if re.fullmatch(r"https://min-repo\.com/\d+/", href) and day and start <= day <= end:
                reports[day] = href
            if href.startswith(TAG + "page/") and href not in visited and href not in pending:
                pending.append(href)
    return [{"day": day, "url": reports[day]} for day in sorted(reports)]


def report_facts(fetch, item, cache):
    target = cache / (item["day"] + ".json")
    if target.exists():
        value = json.loads(target.read_text(encoding="utf-8"))
        if value.get("url") == item["url"] and value.get("seats"):
            return value
    root = Document(fetch.get(item["url"] + "?kishu=all&sort=num")).root
    headings = root.find("h1")
    if not headings:
        fetch.stopped.set()
        raise RuntimeError("The public page did not provide readable content; collection stopped")
    if not any(dated(h.text()) == item["day"] and "ゴッサムシティ" in h.text() for h in headings):
        raise ValueError("Report title/date mismatch: " + item["url"])
    seats = {}
    for table in root.find("table"):
        rows = table.find("tr")
        header = rows[0].text() if rows else ""
        if "機種" not in header or "台番" not in header:
            continue
        for row in rows:
            cells = [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if len(cells) != 5:
                continue
            model, number = compact(cells[0].text()), compact(cells[1].text())
            if number.isdigit():
                if number in seats:
                    raise ValueError("Duplicate seat in report: " + number)
                seats[number] = model
        break
    if len(seats) < 100:
        raise ValueError("Incomplete all-seat table: " + item["url"])
    value = dict(item, publishedAt=next((dated(t.text()) for t in root.find("time") if dated(t.text())), None), seats=seats)
    target.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return value


def notice_facts(fetch, start, cache):
    target = cache / "announcements.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    pending, visited, notices = [NOTICES], set(), []
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        root = Document(fetch.get(url)).root
        lists = root.find("ul", "list-shopinformation")
        days = []
        if lists:
            items = [n for n in lists[0].children if isinstance(n, Node) and n.tag == "li"]
            for i, item in enumerate(items):
                dates, titles = item.find("div", "date"), item.find("h3", "title")
                published = dated(dates[0].text()) if dates else None
                if not published:
                    continue
                days.append(published)
                title = compact(titles[0].text()) if titles else ""
                bodies = item.find("div", "contents")
                body = compact(bodies[0].text()) if bodies else ""
                if published < start or not re.search(r"新台|増台|移動|入替|配置|導入|リニューアル", title + body):
                    continue
                # Keep the notice title and relevant date statements, not the article.
                statements = re.findall(r".{0,22}(?:新台|増台|移動|入替|配置|導入).{0,35}", body)
                images = [{"url": n.attrs.get("src"), "alt": n.attrs.get("alt", "")} for n in item.find("img") if "cdn.p-town" in n.attrs.get("src", "")]
                text = title + " " + " ".join(statements[:3])
                month_day = re.search(r"(\d{1,2})\s*[/月]\s*(\d{1,2})\s*日?", text)
                effective = None
                if month_day:
                    pub = date.fromisoformat(published)
                    for year in (pub.year, pub.year + 1, pub.year - 1):
                        try:
                            candidate = date(year, *map(int, month_day.groups()))
                            if 0 <= (candidate - pub).days <= 14:
                                effective = candidate.isoformat()
                                break
                        except ValueError:
                            pass
                elif "本日" in title:
                    effective = published
                elif "明日" in title:
                    effective = (date.fromisoformat(published) + timedelta(days=1)).isoformat()
                notices.append({"publishedAt": published, "eventDate": effective, "title": title, "url": url, "images": images, "dateBasis": "告知本文の実施日" if effective else "告知日だけ確認"})
        if days and min(days) < start:
            break
        for a in root.find("a"):
            href = urljoin(url, a.attrs.get("href", ""))
            # Follow the immediate next page, rather than jumping to the tail.
            current = int(re.search(r"page=(\d+)", url).group(1)) if "page=" in url else 1
            if href == NOTICES + "?page=" + str(current + 1) and href not in visited and href not in pending:
                pending.append(href)
    target.write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8")
    return notices


def signature(seats):
    # Normalize typography only; preserve distinctions between machine models.
    import unicodedata
    return tuple((n, re.sub(r"\s+", "", unicodedata.normalize("NFKC", m))) for n, m in sorted(seats.items(), key=lambda s: int(s[0])))


def ranges(seats):
    result = []
    for number, model in sorted(seats.items(), key=lambda s: int(s[0])):
        n = int(number)
        if result and result[-1][2] == model and result[-1][1] + 1 == n:
            result[-1][1] = n
        else:
            result.append([n, n, model])
    return result


def reviewed_labels(report):
    """Keep raw facts; apply only explicit, auditable comparison rules."""
    seats, notes = dict(report["seats"]), []
    for seat, raw in list(seats.items()):
        model, status, basis, evidence = raw, None, None, None
        key = signature({seat: raw})[0][1]
        if key == "LBヱヴァンゲリヲン~約束の扉~":
            model, status = "LBパチスロ ヱヴァンゲリヲン～約束の扉～", "name-variant"
            basis, evidence = "同じLB・約束の扉の名称省略。メーカー販売名と照合。", "https://www.sankyo-fever.jp/collection/983/"
        elif key == "Lエウレカセブン4HI-EVOKX":
            model, status = "スマスロ交響詩篇エウレカセブン4 HI-EVOLUTION", "name-variant"
            basis, evidence = "販売名と型式名の表記差。", "https://opt.p-world.co.jp/machine/database/10011"
        elif report["day"] >= "2024-04-26" and seat in ("93", "94", "95") and key == "ジャグラーガールズ":
            model, status = "ジャグラーガールズSS", "inferred-name-variant"
            basis = "同じ93–95番台で旧名とSSが反復して記載されるため、表記差候補として比較上統合。SSへの同定は推定であり、筐体移動の確定根拠ではない。"
            evidence = "https://www.kitadenshi.co.jp/products/2024/jgss/"
        if status:
            seats[seat] = model
            notes.append({"seat": seat, "rawModel": raw, "model": model, "status": status,
                          "basis": basis, "evidenceUrl": evidence, "days": [report["day"]],
                          "reportUrl": report["url"], "lastReportUrl": report["url"]})
    return dict(report, seats=seats, labelNotes=notes)


def add_label_notes(period, notes):
    for note in notes:
        found = next((n for n in period["labelNotes"] if (n["seat"], n["rawModel"], n["model"]) == (note["seat"], note["rawModel"], note["model"])), None)
        if found:
            found["days"].extend(note["days"])
            found["lastReportUrl"] = note["lastReportUrl"]
        else:
            period["labelNotes"].append(dict(note, days=list(note["days"])))


def derive_periods(reports, notices):
    periods, prev = [], None
    for raw_report in sorted(reports, key=lambda r: r["day"]):
        report = reviewed_labels(raw_report)
        day, seats = report["day"], report["seats"]
        continuous = prev is not None and (date.fromisoformat(day) - date.fromisoformat(prev["day"])).days == 1
        same = prev is not None and signature(seats) == signature(prev["seats"])
        if same:
            if not continuous:
                periods[-1]["gaps"].append({"from": (date.fromisoformat(prev["day"])+timedelta(days=1)).isoformat(), "toExclusive": day})
            periods[-1]["observedThrough"] = day
            periods[-1]["lastReportUrl"] = report["url"]
            periods[-1]["reportCount"] += 1
            periods[-1]["reportRefs"].append({k:report.get(k) for k in ("day","url","publishedAt")})
            add_label_notes(periods[-1], report["labelNotes"])
            prev = report
            continue
        old = prev["seats"] if prev else {}
        # Use the same normalization when calculating changes as when grouping.
        old_norm, new_norm = dict(signature(old)), dict(signature(seats))
        changed = [{"seat": n, "before": old.get(n), "after": seats.get(n)} for n in sorted(set(old) | set(seats), key=int) if old_norm.get(n) != new_norm.get(n)] if prev else []
        from collections import Counter
        old_counts, new_counts = Counter(old_norm.values()), Counter(new_norm.values())
        models = {new_norm[n]: seats[n] for n in seats} | {old_norm[n]: old[n] for n in old}
        count_changes = [{"model": models[m], "before": old_counts[m], "after": new_counts[m], "delta": new_counts[m]-old_counts[m]} for m in sorted(set(old_counts) | set(new_counts)) if old_counts[m] != new_counts[m]] if prev else []
        related = []
        for notice in notices:
            event_day = notice.get("eventDate")
            if event_day == day:
                related.append(dict(notice, matchKind="same-day"))
            elif prev and not continuous and event_day and prev["day"] < event_day < day:
                related.append(dict(notice, matchKind="within-gap"))
            elif prev and not continuous and not event_day and prev["day"] < notice["publishedAt"] <= day:
                related.append(dict(notice, matchKind="published-within-gap"))
        period = {"id": day, "validFrom": day, "observedThrough": day, "validToExclusive": None,
                  "reportUrl": report["url"], "lastReportUrl": report["url"], "publishedAt": report["publishedAt"],
                  "reportCount": 1, "seatCount": len(seats), "seatRanges": ranges(seats), "changes": changed,
                  "reportRefs": [{k:report.get(k) for k in ("day","url","publishedAt")}],
                  "gaps": [], "labelNotes": report["labelNotes"],
                  "modelCountChanges": count_changes, "announcements": related,
                  "boundaryStatus": "first-observation" if not prev else ("daily-confirmed" if continuous else "after-gap"),
                  "previousObservedDate": prev["day"] if prev else None, "previousReportUrl": prev["url"] if prev else None,
                  "changeWindow": {"after": prev["day"], "through": day} if prev and not continuous and not same else None,
                  "countsUnchanged": bool(changed) and old_counts == new_counts}
        periods.append(period)
        prev = report
    for p in periods:
        p["validToExclusive"] = (date.fromisoformat(p["observedThrough"]) + timedelta(days=1)).isoformat()
    return periods


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-04-27")
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Derive periods using already collected factual rows only")
    parser.add_argument("--collected-at", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--transport", choices=("auto", "powershell", "urllib"), default="auto")
    args = parser.parse_args()
    date.fromisoformat(args.start)
    date.fromisoformat(args.end)
    date.fromisoformat(args.collected_at)
    args.cache.mkdir(parents=True, exist_ok=True)
    fetch = Fetcher(args.transport)
    index_path = args.cache / "index.json"
    cached_index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    cache_valid = isinstance(cached_index, dict) and cached_index.get("schemaVersion") == 2
    if args.offline and not cache_valid:
        raise ValueError("Offline generation needs a previously collected index.json")
    if cache_valid and (args.offline or (cached_index.get("from", "9999-12-31") <= args.start and cached_index.get("through", "0001-01-01") >= args.end)):
        index = cached_index["reports"]
        index = [i for i in index if args.start <= i["day"] <= args.end]
    else:
        index = index_reports(fetch, args.start, args.end)
        index_path.write_text(json.dumps({"schemaVersion": 2, "from":args.start, "through":args.end, "reports": index}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not index:
        raise ValueError("No public report index was readable for this date range")
    print(json.dumps({"reports": len(index), "first": index[0] if index else None, "last": index[-1] if index else None}), flush=True)
    if args.index_only:
        return
    reports, failures = [], []
    if args.offline:
        for item in index:
            target = args.cache / (item["day"] + ".json")
            if target.exists():
                reports.append(json.loads(target.read_text(encoding="utf-8")))
            else:
                failures.append(dict(item, error="全台表未取得"))
        notices_path = args.cache / "announcements.json"
        notices = json.loads(notices_path.read_text(encoding="utf-8")) if notices_path.exists() else []
        write_result(args, index, reports, failures, notices)
        return
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(report_facts, fetch, item, args.cache): item for item in index}
        for future in as_completed(tasks):
            item = tasks[future]
            try:
                reports.append(future.result())
            except Exception as ex:
                failures.append(dict(item, error=str(ex)))
                print("FAILED " + item["day"] + " " + str(ex), flush=True)
            if (len(reports) + len(failures)) % 25 == 0:
                print("PROGRESS " + str(len(reports)) + "/" + str(len(index)) + " failures=" + str(len(failures)), flush=True)
    if fetch.stopped.is_set():
        notices_path = args.cache / "announcements.json"
        notices = json.loads(notices_path.read_text(encoding="utf-8")) if notices_path.exists() else []
    else:
        notices = notice_facts(fetch, args.start, args.cache)
    write_result(args, index, reports, failures, notices)


def write_result(args, index, reports, failures, notices):
    complete = []
    for report in reports:
        # Gotham's complete numbered baseline has 1–310. A missing/unlabelled
        # row is not evidence that a machine was removed or moved that day.
        seats = report.get("seats", {})
        if set(seats) != {str(n) for n in range(1, 311)} or not all(str(m).strip() for m in seats.values()):
            failures.append({"day": report["day"], "url": report["url"], "error": "台番号1–310の機種対応が不完全。撤去・移動とは見なさず未確認扱い。"})
        else:
            complete.append(report)
    reports = complete
    if not reports:
        raise ValueError("No factual seat/model tables were readable; output was not replaced")
    periods = derive_periods(reports, notices)
    result = {"schemaVersion": 1, "collectedAt": args.collected_at, "requestedFrom": args.start,
              "firstObservedDate": min((r["day"] for r in reports), default=None),
              "lastObservedDate": max((r["day"] for r in reports), default=None),
              "reportCount": len(reports), "indexedReportCount": len(index), "failures": failures,
              "announcements": notices, "periods": periods,
              "method": "台番ごとの機種名を日別に照合し、配置が変わった時だけ期間を分ける。明示した表記差ルールだけ統合し、推定と原表記をlabelNotesに保持する。同じ配置の期間内に欠落がある場合は欠落日を保存し、その日の配置は未確認とする。前後の日別表が揃う割当変更日は確定、欠落を跨ぐ変更日は候補範囲として保存。筐体そのものの移動日は未同定。終了日は排他的。"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "periods": len(periods), "reports": len(reports), "announcements": len(notices), "failures": len(failures)}), flush=True)


if __name__ == "__main__":
    main()
