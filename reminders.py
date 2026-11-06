# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reminders, daily briefs, and follow-up tracking for Nova.

Two tables in memory.sqlite3:
- reminders: one-time reminders plus the recurring daily-brief row (kind='brief').
- followups: action items extracted from conversation summaries, tracked until done.

Times are stored as local 'YYYY-MM-DD HH:MM' strings.
"""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_BRIEF_TIME = "09:00"


@contextmanager
def _connect(path=None):
    db = sqlite3.connect(str(path or ROOT / "memory.sqlite3"), timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, created TEXT NOT NULL, due TEXT NOT NULL, message TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'reminder', done INTEGER NOT NULL DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS followups(id INTEGER PRIMARY KEY, created TEXT NOT NULL, task TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', asked INTEGER NOT NULL DEFAULT 0)")
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


# dateparser handles Spanish poorly for bare clock times; normalize common words first.
_ES_NORMALIZE = [
    ("pasado mañana", "in 2 days"), ("de la madrugada", "am"), ("de la mañana", "am"),
    ("de la tarde", "pm"), ("de la noche", "pm"), ("mañana", "tomorrow"), ("hoy", "today"),
    ("el lunes", "monday"), ("el martes", "tuesday"), ("el miércoles", "wednesday"),
    ("el miercoles", "wednesday"), ("el jueves", "thursday"), ("el viernes", "friday"),
    ("el sábado", "saturday"), ("el sabado", "saturday"), ("el domingo", "sunday"),
    ("a las", "at"),
]


def parse_when(text, now=None):
    """Parse a spoken/typed time expression into a local datetime. Raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Say when to remind you, for example 'in 20 minutes' or 'tomorrow at 3 pm'.")
    now = now or datetime.now()
    low = " " + text.lower() + " "
    for es, en in _ES_NORMALIZE:
        low = low.replace(" " + es + " ", " " + en + " ")
    text = low.strip()
    # dateparser ignores bare hours ("tomorrow at 3"); add a meridiem by convention:
    # 1-7 -> pm, 8-11 -> am, 12 -> pm. 24-hour times are left alone.
    def _meridiem(m):
        h = int(m.group(1))
        if h == 12 or 1 <= h <= 7:
            return m.group(0) + " pm"
        if 8 <= h <= 11:
            return m.group(0) + " am"
        return m.group(0)
    text = re.sub(r"\bat\s+(\d{1,2})(?::\d{2})?(?![:\d]|\s*[ap]m)", _meridiem, text)
    try:
        import dateparser
        dt = dateparser.parse(text, settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False, "RELATIVE_BASE": now})
    except Exception:
        dt = None
    if dt is None:
        # Plain clock time like "15:30" -> today (or tomorrow if already past).
        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?", text.lower())
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            if m.group(3) == "pm" and hour < 12:
                hour += 12
            if m.group(3) == "am" and hour == 12:
                hour = 0
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
        else:
            raise ValueError("I couldn't understand the time '" + text + "'. Try 'in 20 minutes', 'tomorrow at 3 pm', or 'Friday at 9 am'.")
    dt = dt.replace(second=0, microsecond=0)
    if dt <= now - timedelta(minutes=1):
        raise ValueError("That time is in the past. Give me a future time.")
    return dt


def add(due_dt, message, kind="reminder", path=None):
    due_str = due_dt.strftime("%Y-%m-%d %H:%M")
    with _connect(path) as db:
        cur = db.execute("INSERT INTO reminders(created,due,message,kind) VALUES(?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), due_str, message, kind))
        return cur.lastrowid, due_str


def due(now=None, path=None):
    """All undelivered reminders whose time has come (oldest first)."""
    now = now or datetime.now()
    with _connect(path) as db:
        return db.execute("SELECT id,due,message,kind FROM reminders WHERE done=0 AND due<=? ORDER BY due",(now.strftime("%Y-%m-%d %H:%M"),)).fetchall()


def mark_done(ids, path=None):
    if not ids:
        return
    with _connect(path) as db:
        db.execute("UPDATE reminders SET done=1 WHERE id IN (" + ",".join("?" * len(ids)) + ")", tuple(ids))


def clear_pending(path=None):
    with _connect(path) as db:
        n = db.execute("SELECT COUNT(*) FROM reminders WHERE done=0 AND kind='reminder'").fetchone()[0]
        db.execute("DELETE FROM reminders WHERE done=0 AND kind='reminder'")
        return n


def list_pending(path=None):
    with _connect(path) as db:
        return db.execute("SELECT id,due,message,kind FROM reminders WHERE done=0 AND kind IN ('reminder','brief') ORDER BY due").fetchall()


def delete(rid, path=None):
    with _connect(path) as db:
        db.execute("DELETE FROM reminders WHERE id=?", (rid,))


def nice(due_str, now=None):
    """'YYYY-MM-DD HH:MM' -> 'today 3:00 PM' / 'tomorrow 9:00 AM' / 'Fri 30 Oct 9:00 AM'."""
    now = now or datetime.now()
    try:
        dt = datetime.strptime(due_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return due_str
    day = "today" if dt.date() == now.date() else \
          "tomorrow" if dt.date() == (now + timedelta(days=1)).date() else \
          dt.strftime("%a %d %b")
    h12, ap = (dt.hour % 12 or 12), ("AM" if dt.hour < 12 else "PM")
    return f"{day} {h12}:{dt.minute:02d} {ap}"


def describe_pending(path=None):
    rows = list_pending(path)
    if not rows:
        return "You have no pending reminders."
    out = []
    for _, due_str, message, kind in rows:
        if kind == "brief":
            out.append(f"daily morning brief at {nice(due_str).split(' ', 1)[1] if ' ' in nice(due_str) else due_str}")
        else:
            out.append(f"{message} ({nice(due_str)})")
    return "Your reminders: " + "; ".join(out) + "."


def describe_followups(path=None):
    rows = list_followups(path)
    if not rows:
        return "You have no open follow-ups."
    items = [((owner + ": ") if owner else "") + task.rstrip(".") for _, task, owner, _, _ in rows]
    return "Open follow-ups: " + "; ".join(items) + ". Say 'mark <keywords> as done' when one is finished."


# ---------- daily brief ----------

def brief_time(path=None):
    """'HH:MM' for the morning brief (default 09:00)."""
    with _connect(path) as db:
        row = db.execute("SELECT message FROM reminders WHERE kind='brief_time' ORDER BY id DESC LIMIT 1").fetchone()
    if row and re.fullmatch(r"\d{2}:\d{2}", row[0]):
        return row[0]
    return DEFAULT_BRIEF_TIME


def set_brief_time(hhmm, path=None):
    with _connect(path) as db:
        db.execute("DELETE FROM reminders WHERE kind='brief_time'")
        db.execute("INSERT INTO reminders(created,due,message,kind) VALUES(?,?,?,'brief_time')",
                    (datetime.now().isoformat(timespec="seconds"), "2099-01-01 00:00", hhmm))
        # Drop any previously scheduled briefs so only the new time is pending.
        db.execute("DELETE FROM reminders WHERE kind='brief' AND done=0")


def _next_brief_due(hhmm, now=None):
    now = now or datetime.now()
    hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt <= now:
        dt += timedelta(days=1)
    return dt


def ensure_brief(path=None):
    """Create the recurring brief row if none is scheduled."""
    with _connect(path) as db:
        row = db.execute("SELECT id FROM reminders WHERE kind='brief' AND done=0").fetchone()
        if row:
            return
    bt = brief_time(path)
    add(_next_brief_due(bt), "daily brief", kind="brief", path=path)


def reschedule_brief(path=None):
    """After a brief fires (or its time changes), schedule the next one."""
    with _connect(path) as db:
        db.execute("DELETE FROM reminders WHERE kind='brief' AND done=0")
    bt = brief_time(path)
    add(_next_brief_due(bt), "daily brief", kind="brief", path=path)


def brief_content(now=None, path=None):
    """Text for the morning brief, or None when there is nothing to say."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    with _connect(path) as db:
        rem_today = db.execute("SELECT due,message FROM reminders WHERE done=0 AND kind='reminder' AND due LIKE ? ORDER BY due", (today + "%",)).fetchall()
        overdue = db.execute("SELECT due,message FROM reminders WHERE done=0 AND kind='reminder' AND due<? ORDER BY due", (today + " 00:00",)).fetchall()
        fups = db.execute("SELECT id,task,owner,created FROM followups WHERE status='pending' ORDER BY id").fetchall()
    parts = ["Good morning. Here's your brief."]
    if rem_today:
        parts.append("Reminders today: " + "; ".join(f"{m} at {d[11:]}" for d, m in rem_today) + ".")
    if overdue:
        parts.append("You have %d overdue reminder%s: %s." % (len(overdue), "s" if len(overdue) > 1 else "",
                        "; ".join(m for _, m in overdue[:3])))
    if fups:
        fdesc = []
        for _, task, owner, created in fups[:5]:
            who = (owner + ": ") if owner else ""
            try:
                age = (now - datetime.fromisoformat(created)).days
            except Exception:
                age = 0
            fdesc.append(f"{who}{task.rstrip('.')}" + (f" ({age} day{'s' if age != 1 else ''} ago)" if age else ""))
        parts.append("Open follow-ups: " + "; ".join(fdesc) + ". Say 'mark <keywords> as done' when one is finished.")
    if len(parts) == 1:
        parts.append("No reminders or open follow-ups. Have a good day.")
    return " ".join(parts), [f[0] for f in fups[:5]]


def mark_asked(fup_ids, path=None):
    if not fup_ids:
        return
    with _connect(path) as db:
        db.execute("UPDATE followups SET asked=1 WHERE id IN (" + ",".join("?" * len(fup_ids)) + ")", tuple(fup_ids))


# ---------- follow-ups from summaries ----------

_ITEM = re.compile(r"^\s*-\s*(?:(?P<who>.+?)\s*(?:\((?P<role>[^)]+)\)\s*:?|:\s*))?(?P<task>.+?)\s*(?:\[\d+\])?\s*\.?\s*$")


def capture_followups(summary_text, path=None):
    """Extract action items from a conversation summary into the followups table.
    Returns the number of new items."""
    if not summary_text:
        return 0
    # Accept ordinary Markdown and either UI language, independent of LLM styling.
    lines=[];in_section=False
    for line in summary_text.splitlines():
        heading=re.sub(r"^[\s#*]+|[\s*:]+$","",line).strip().casefold()
        if heading in ("action items","acciones pendientes","tareas pendientes","acciones","tareas"):
            in_section=True;continue
        if in_section and heading in ("open questions","preguntas abiertas","preguntas pendientes",
                                      "summary","resumen","explicit decisions","decisiones explícitas"):
            break
        if in_section:lines.append(line)
    section="\n".join(lines)
    added = 0
    with _connect(path) as db:
        for line in section.splitlines():
            line=re.sub(r"^\s*(?:\d+[.)]|[+*•])\s+","- ",line)
            m = _ITEM.match(line)
            if not m:
                continue
            owner = (m.group("who") or "").strip()
            task = (m.group("task") or "").strip()
            if not task or len(task) < 4 or task.lower().startswith(("no action", "none", "ningun", "ningún", "sin acciones", "no hay")):
                continue
            exists = db.execute("SELECT id FROM followups WHERE task=? AND status='pending'", (task,)).fetchone()
            if exists:
                continue
            db.execute("INSERT INTO followups(created,task,owner) VALUES(?,?,?)",
                       (datetime.now().isoformat(timespec="seconds"), task, owner))
            added += 1
    return added


def list_followups(path=None):
    with _connect(path) as db:
        return db.execute("SELECT id,task,owner,created,status FROM followups WHERE status='pending' ORDER BY id").fetchall()


def complete_followup(match, path=None):
    """Mark a pending follow-up done by matching key words against its task."""
    q = (match or "").strip().lower()
    if len(q) < 3:
        raise ValueError("Say a few words from the follow-up, like 'mark the chiller quote as done'.")
    words = [w for w in re.split(r"\s+", q) if len(w) > 2 and w not in ("the", "done", "mark", "como", "listo")]
    with _connect(path) as db:
        rows = db.execute("SELECT id,task,owner FROM followups WHERE status='pending'").fetchall()
    best = None
    for rid, task, owner in rows:
        tl = task.lower()
        hits = sum(1 for w in words if w in tl or w in owner.lower())
        if hits and (best is None or hits > best[1]):
            best = (rid, hits, task)
    if not best:
        raise ValueError("No open follow-up matches '" + match + "'.")
    with _connect(path) as db:
        db.execute("UPDATE followups SET status='done' WHERE id=?", (best[0],))
    return best[2]
