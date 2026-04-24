"""
CSV Exporter — daily, weekly, monthly reports
Exports to exports/ directory with timestamps
"""

import csv
import os
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict

log = logging.getLogger("CSVExporter")
EXPORT_DIR = "exports"


def fmt_duration(minutes: int) -> str:
    if minutes is None:
        return "in progress"
    h = minutes // 60
    m = minutes % 60
    return f"{h}h {m:02d}m"


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    return label.strip("_") or "report"


def _split_work_update_content(content: str) -> tuple[str, str, str]:
    current_work = ""
    next_work = ""
    blockers = ""
    if not content:
        return current_work, next_work, blockers

    parts = content.split("\n\n")
    for part in parts:
        lines = part.split("\n", 1)
        header = lines[0].strip().lower()
        body = lines[1].strip() if len(lines) > 1 else ""
        if header.startswith("what are you working on?"):
            current_work = body
        elif header.startswith("what will you work on now?"):
            next_work = body
        elif header.startswith("any blockers or notes?"):
            blockers = body
    return current_work, next_work, blockers


class CSVExporter:
    def __init__(self, db):
        self.db = db
        os.makedirs(EXPORT_DIR, exist_ok=True)

    def _write_csv(
        self,
        filename: str,
        rows: List[Dict],
        fieldnames: List[str],
        note_lines: List[str] | None = None,
    ) -> str:
        path = os.path.join(EXPORT_DIR, filename)
        with open(path, "w", newline="", encoding="utf-8") as f:
            raw_writer = csv.writer(f)
            for note in note_lines or []:
                raw_writer.writerow([f"NOTE: {note}"])
            if note_lines:
                raw_writer.writerow([])
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        log.info(f"Exported {len(rows)} rows → {path}")
        return path

    async def export_timesheet(
        self,
        guild_id: str,
        period: str,
        start: datetime,
        end: datetime,
        user_id: str = None,
    ) -> str:
        """Export detailed time entries for a period."""
        if user_id:
            entries = await self.db.get_entries_range(guild_id, user_id, start, end)
        else:
            entries = await self.db.get_all_entries_range(guild_id, start, end)
        
        rows = []
        for e in entries:
            actual_break_minutes = await self.db.get_break_minutes_for_entry(e["id"])
            applied_break_minutes = (
                await self.db.get_applied_break_minutes_for_entry(guild_id, e["id"])
                if e["clock_out"]
                else actual_break_minutes
            )
            gross_minutes = e["duration_minutes"]
            net_minutes = (
                max(0, gross_minutes - applied_break_minutes)
                if gross_minutes is not None
                else None
            )
            rows.append({
                "Date": e["clock_in"][:10],
                "Employee": e["username"],
                "User ID": e["user_id"],
                "Clock In": e["clock_in"].replace("T", " ")[:19],
                "Clock Out": (e["clock_out"] or "").replace("T", " ")[:19] or "In Progress",
                "Status": "In Progress" if not e["clock_out"] else ("Auto Clock-Out" if e["auto_out"] else "Completed"),
                "Gross Duration": fmt_duration(gross_minutes),
                "Gross Minutes": gross_minutes if gross_minutes is not None else "",
                "Break Minutes": applied_break_minutes if e["clock_out"] else actual_break_minutes,
                "Net Duration": fmt_duration(net_minutes),
                "Net Minutes": net_minutes if net_minutes is not None else "",
                "Auto Clock-Out": "Yes" if e["auto_out"] else "No",
                "Early Clock-Out": "Yes" if e.get("early_clock_out") else "No",
                "Early Clock-Out Reason": e.get("early_clock_out_reason") or "",
                "Scheduled Clock-Out Time": e.get("scheduled_clock_out_time") or "",
                "Note": e["note"] or ""
            })

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"timesheet_{_safe_label(period)}_{start.strftime('%Y%m%d')}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "Date", "Employee", "User ID", "Clock In", "Clock Out",
            "Status", "Gross Duration", "Gross Minutes", "Break Minutes",
            "Net Duration", "Net Minutes", "Auto Clock-Out",
            "Early Clock-Out", "Early Clock-Out Reason", "Scheduled Clock-Out Time", "Note"
        ], note_lines=[
            "Date = UTC calendar date of the clock-in.",
            "Employee = display name stored at the time of the session.",
            "User ID = Discord user ID for the employee.",
            "Clock In / Clock Out = UTC timestamps for the session boundaries.",
            "Status = completed state of the session, including auto clock-out when applied.",
            "Gross Duration / Gross Minutes = raw session time before break deductions.",
            "Break Minutes = applied deducted break minutes for the session.",
            "Net Duration / Net Minutes = worked time after break deductions.",
            "Auto Clock-Out = whether the bot ended the session automatically.",
            "Early Clock-Out / Early Clock-Out Reason = whether the session ended before the final 5-minute shift window and the stored reason.",
            "Scheduled Clock-Out Time = configured shift end time used for time-shift comparisons.",
            "Note = optional note entered at clock-in.",
        ])

    async def export_summary(self, guild_id: str, period: str,
                              start: datetime, end: datetime) -> str:
        """Export per-employee summary with overtime."""
        entries = await self.db.get_all_entries_range(guild_id, start, end)
        config = await self.db.get_overtime_config(guild_id)

        users: Dict[str, Dict] = {}
        for e in entries:
            uid = e["user_id"]
            if uid not in users:
                users[uid] = {
                    "username": e["username"],
                    "user_id": uid,
                    "entry_count": 0
                }
            users[uid]["entry_count"] += 1
        
        rows = []
        for uid, u in users.items():
            summary = await self.db.get_user_summary(guild_id, uid, start, end)
            days = summary["days_worked"]
            total_mins = summary["total_minutes"]
            expected_mins = config["daily_hours"] * 60 * days if (config.get("mode") or "overtime") == "overtime" else 0
            overtime_mins = max(0, total_mins - expected_mins) if expected_mins > 0 else 0
            balance_minutes = int(total_mins - expected_mins) if expected_mins > 0 else 0
            
            rows.append({
                "Employee": u["username"],
                "User ID": uid,
                "Days Worked": days,
                "Gross Hours": fmt_duration(summary["gross_minutes"]),
                "Gross Minutes": summary["gross_minutes"],
                "Break Duration": fmt_duration(summary["break_minutes"]),
                "Total Hours": fmt_duration(total_mins),
                "Total Minutes": total_mins,
                "Expected Hours": fmt_duration(int(expected_mins)),
                "Overtime": fmt_duration(overtime_mins),
                "Overtime Minutes": overtime_mins,
                "Balance": (
                    f"+{fmt_duration(balance_minutes)}"
                    if balance_minutes > 0
                    else (f"-{fmt_duration(abs(balance_minutes))}" if balance_minutes < 0 else "0h 00m")
                ),
                "Balance Minutes": balance_minutes,
                "Sessions": u["entry_count"],
                "Break Minutes": summary["break_minutes"]
            })
        
        rows.sort(key=lambda x: x["Employee"].lower())
        
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"summary_{_safe_label(period)}_{start.strftime('%Y%m%d')}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "Employee", "User ID", "Days Worked", "Gross Hours", "Gross Minutes",
            "Break Duration", "Break Minutes", "Total Hours", "Total Minutes",
            "Expected Hours", "Overtime", "Overtime Minutes", "Balance",
            "Balance Minutes", "Sessions"
        ], note_lines=[
            "Employee / User ID = the person included in the summary.",
            "Days Worked = distinct UTC dates with at least one session in the selected range.",
            "Gross Hours / Gross Minutes = raw time logged before break deductions.",
            "Break Duration / Break Minutes = total applied break deductions for the range.",
            "Total Hours / Total Minutes = net worked time after break deductions.",
            "Expected Hours = baseline hours expected from overtime settings for the days worked.",
            "Overtime / Overtime Minutes = net time above expected hours when overtime mode is enabled.",
            "Balance / Balance Minutes = net time minus expected time for the period.",
            "Sessions = number of sessions included for that employee.",
        ])

    async def export_leave(self, guild_id: str, status: str = None) -> str:
        """Export all leave requests."""
        requests = await self.db.get_leave_requests(guild_id, status=status)
        
        rows = [{
            "ID": r["id"],
            "Employee": r["username"],
            "User ID": r["user_id"],
            "Leave Type": r["leave_type"],
            "Start Date": r["start_date"],
            "End Date": r["end_date"],
            "Duration Days": (
                (datetime.fromisoformat(r["end_date"]) - datetime.fromisoformat(r["start_date"])).days + 1
            ),
            "Reason": r["reason"] or "",
            "Status": r["status"].upper(),
            "Approver": r["approver_name"] or "",
            "Submitted At": (r["created_at"] or "").replace("T", " ")[:19]
        } for r in requests]
        
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        label = f"_{status}" if status else ""
        filename = f"leave_requests{label}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "ID", "Employee", "User ID", "Leave Type", "Start Date", "End Date",
            "Duration Days", "Reason", "Status", "Approver", "Submitted At"
        ], note_lines=[
            "ID = internal leave request ID.",
            "Employee / User ID = employee identity for the leave request.",
            "Leave Type = category submitted by the employee.",
            "Start Date / End Date = requested leave range in YYYY-MM-DD.",
            "Duration Days = inclusive length of the leave request.",
            "Reason = employee-provided explanation, if any.",
            "Status = current approval state of the request.",
            "Approver = manager/admin who approved or denied the request.",
            "Submitted At = UTC timestamp when the request was created.",
        ])

    async def export_work_updates(
        self,
        guild_id: str,
        period: str,
        start: datetime,
        end: datetime,
        user_id: str = None,
        label: str = "all",
    ) -> str:
        """Export submitted work updates for a period."""
        updates = await self.db.get_work_updates(guild_id, start, end, user_id=user_id)

        rows = []
        for item in updates:
            current_work, next_work, blockers = _split_work_update_content(item.get("content") or "")
            rows.append({
                "Date": item["prompted_at"][:10],
                "Employee": item["username"],
                "User ID": item["user_id"],
                "Prompt Slot": item["prompt_slot"],
                "Prompted At": item["prompted_at"].replace("T", " ")[:19],
                "Submitted At": (item.get("submitted_at") or "").replace("T", " ")[:19],
                "Instruction": item.get("question_text") or "",
                "Current Work": current_work,
                "Next Work": next_work,
                "Blockers / Notes": blockers,
                "Answer": item.get("content") or "",
                "Time Entry ID": item["time_entry_id"],
            })

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"work_updates_{_safe_label(label)}_{_safe_label(period)}_{start.strftime('%Y%m%d')}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "Date",
            "Employee",
            "User ID",
            "Prompt Slot",
            "Prompted At",
            "Submitted At",
            "Instruction",
            "Current Work",
            "Next Work",
            "Blockers / Notes",
            "Answer",
            "Time Entry ID",
        ], note_lines=[
            "Date = UTC calendar date of the prompt.",
            "Employee / User ID = employee identity for the work update.",
            "Prompt Slot = sequence key for the prompt within the session.",
            "Prompted At / Submitted At = UTC timestamps for the prompt and response.",
            "Instruction = prompt text shown to the employee.",
            "Current Work / Next Work / Blockers / Notes = parsed sections from the submitted answer.",
            "Answer = full stored response body.",
            "Time Entry ID = linked session ID for the update.",
        ])

    async def export_daily(self, guild_id: str = None) -> List[str]:
        """Export yesterday's timesheet for all guilds."""
        yesterday = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        today = yesterday + timedelta(days=1)
        
        if guild_id:
            guilds = [guild_id]
        else:
            # Export for all guilds that have data
            guilds = []  # In production: query distinct guild_ids from DB
        
        paths = []
        for gid in guilds:
            path = await self.export_timesheet(gid, "daily", yesterday, today)
            paths.append(path)
        return paths

    def get_period_dates(self, period: str) -> tuple:
        """Return (start, end) for today/week/month/last_week/last_month."""
        now = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if period == "today":
            return now, now + timedelta(days=1)
        elif period == "yesterday":
            return now - timedelta(days=1), now
        elif period == "week":
            start = now - timedelta(days=now.weekday())
            return start, start + timedelta(days=7)
        elif period == "last_week":
            start = now - timedelta(days=now.weekday() + 7)
            return start, start + timedelta(days=7)
        elif period == "month":
            start = now.replace(day=1)
            if now.month == 12:
                end = now.replace(year=now.year + 1, month=1, day=1)
            else:
                end = now.replace(month=now.month + 1, day=1)
            return start, end
        elif period == "last_month":
            first_this_month = now.replace(day=1)
            last_month_end = first_this_month
            last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
            return last_month_start, last_month_end
        else:
            return now, now + timedelta(days=1)
