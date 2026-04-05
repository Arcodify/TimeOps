"""
CSV Exporter — daily, weekly, monthly reports
Exports to exports/ directory with timestamps
"""

import csv
import os
import logging
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


class CSVExporter:
    def __init__(self, db):
        self.db = db
        os.makedirs(EXPORT_DIR, exist_ok=True)

    def _write_csv(self, filename: str, rows: List[Dict], fieldnames: List[str]) -> str:
        path = os.path.join(EXPORT_DIR, filename)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        log.info(f"Exported {len(rows)} rows → {path}")
        return path

    async def export_timesheet(self, guild_id: str, period: str, 
                                start: datetime, end: datetime) -> str:
        """Export detailed time entries for a period."""
        entries = await self.db.get_all_entries_range(guild_id, start, end)
        
        rows = []
        for e in entries:
            rows.append({
                "Date": e["clock_in"][:10],
                "Employee": e["username"],
                "User ID": e["user_id"],
                "Clock In": e["clock_in"].replace("T", " ")[:19],
                "Clock Out": (e["clock_out"] or "").replace("T", " ")[:19] or "In Progress",
                "Duration": fmt_duration(e["duration_minutes"]),
                "Duration (mins)": e["duration_minutes"] or "",
                "Auto Clock-Out": "Yes" if e["auto_out"] else "No",
                "Note": e["note"] or ""
            })
        
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"timesheet_{period}_{start.strftime('%Y%m%d')}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "Date", "Employee", "User ID", "Clock In", "Clock Out",
            "Duration", "Duration (mins)", "Auto Clock-Out", "Note"
        ])

    async def export_summary(self, guild_id: str, period: str,
                              start: datetime, end: datetime) -> str:
        """Export per-employee summary with overtime."""
        entries = await self.db.get_all_entries_range(guild_id, start, end)
        config = await self.db.get_overtime_config(guild_id)
        
        # Group by user
        users: Dict[str, Dict] = {}
        for e in entries:
            uid = e["user_id"]
            if uid not in users:
                users[uid] = {
                    "username": e["username"],
                    "user_id": uid,
                    "total_minutes": 0,
                    "days_worked": set(),
                    "entry_count": 0
                }
            if e["clock_out"]:
                users[uid]["total_minutes"] += e["duration_minutes"] or 0
                users[uid]["days_worked"].add(e["clock_in"][:10])
            users[uid]["entry_count"] += 1
        
        rows = []
        for uid, u in users.items():
            days = len(u["days_worked"])
            total_mins = u["total_minutes"]
            expected_mins = config["daily_hours"] * 60 * days
            overtime_mins = max(0, total_mins - expected_mins)
            
            rows.append({
                "Employee": u["username"],
                "User ID": uid,
                "Days Worked": days,
                "Total Hours": fmt_duration(total_mins),
                "Total Minutes": total_mins,
                "Expected Hours": fmt_duration(int(expected_mins)),
                "Overtime": fmt_duration(overtime_mins),
                "Overtime Minutes": overtime_mins,
                "Sessions": u["entry_count"]
            })
        
        rows.sort(key=lambda x: x["Employee"].lower())
        
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"summary_{period}_{start.strftime('%Y%m%d')}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "Employee", "User ID", "Days Worked", "Total Hours", "Total Minutes",
            "Expected Hours", "Overtime", "Overtime Minutes", "Sessions"
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
            "Reason": r["reason"] or "",
            "Status": r["status"].upper(),
            "Approver": r["approver_name"] or "",
            "Submitted At": r["created_at"][:10]
        } for r in requests]
        
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        label = f"_{status}" if status else ""
        filename = f"leave_requests{label}_{ts}.csv"
        return self._write_csv(filename, rows, [
            "ID", "Employee", "User ID", "Leave Type", "Start Date", "End Date",
            "Reason", "Status", "Approver", "Submitted At"
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
