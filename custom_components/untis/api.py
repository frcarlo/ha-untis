"""WebUntis client (JSON-RPC + REST for parent accounts).

Port of the WebUntis connector from frcarlo/landing-page (backend/src/routes/integrations.ts).
The URL is the one from the browser, e.g. https://xyz.webuntis.com/WebUntis/?school=name
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import aiohttp

ERRORS: dict[int, str] = {
    -8504: "Wrong username or password",
    -8509: "No permission to view this timetable",
    -8520: "Not logged in",
}

# person type 5 = student
PERSON_TYPE_STUDENT = 5


class UntisError(Exception):
    """Generic WebUntis error."""


class UntisAuthError(UntisError):
    """Login rejected."""


class UntisUrlError(UntisError):
    """URL does not point to a school."""


@dataclass(frozen=True)
class Student:
    id: int
    name: str


@dataclass(frozen=True)
class Lesson:
    id: int
    day: date
    start: datetime
    end: datetime
    subject: str | None
    subject_long: str | None
    room: str | None
    room_changed: bool
    teacher: str | None
    teacher_changed: bool
    # "cancelled" | "irregular" | None
    code: str | None
    text: str | None

    @property
    def cancelled(self) -> bool:
        return self.code == "cancelled"

    @property
    def changed(self) -> bool:
        return self.code == "irregular"

    @property
    def title(self) -> str:
        return self.subject_long or self.subject or self.text or "—"

    @property
    def key(self) -> str:
        """Identity of a change: a new room/teacher on the same lesson counts as new."""
        return f"{self.day:%Y%m%d}:{self.id}:{self.code or ''}:{self.room or ''}:{self.teacher or ''}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.day.isoformat(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "subject": self.subject,
            "subject_long": self.subject_long,
            "room": self.room,
            "room_changed": self.room_changed,
            "teacher": self.teacher,
            "teacher_changed": self.teacher_changed,
            "code": self.code,
            "text": self.text,
        }


@dataclass
class _Session:
    cookie: str
    person_id: int
    person_type: int
    students: list[Student] = field(default_factory=list)


def parse_url(url: str) -> tuple[str, str]:
    """Return (origin, school) from the browser URL."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UntisUrlError("Invalid URL")
    school = parse_qs(parts.query).get("school", [None])[0]
    if not school:
        raise UntisUrlError("Add the school to the URL, e.g. …/WebUntis/?school=name")
    return f"{parts.scheme}://{parts.netloc}", school


def _hm(value: int) -> tuple[int, int]:
    return value // 100, value % 100


def _ymd(value: int) -> date:
    return date(value // 10000, value // 100 % 100, value % 100)


def _name(element: Any) -> str | None:
    if isinstance(element, dict):
        name = element.get("name")
        if name and name != "---":
            return str(name)
    return None


class UntisClient:
    """One client per config entry. Every fetch logs in and out again (like the web widget)."""

    def __init__(self, session: aiohttp.ClientSession, url: str, username: str, password: str, verify_ssl: bool = True) -> None:
        self._http = session
        self.origin, self.school = parse_url(url)
        self._username = username
        self._password = password
        self._ssl = None if verify_ssl else False

    async def _rpc(self, method: str, params: dict[str, Any], cookie: str | None = None) -> tuple[Any, list[str]]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        url = f"{self.origin}/WebUntis/jsonrpc.do?school={quote(self.school)}"
        payload = {"id": str(int(time.time() * 1000)), "method": method, "params": params, "jsonrpc": "2.0"}
        for attempt in range(2):
            try:
                async with self._http.post(url, json=payload, headers=headers, ssl=self._ssl, timeout=aiohttp.ClientTimeout(total=30)) as res:
                    if res.status != 200:
                        raise UntisError(f"HTTP {res.status}")
                    try:
                        body = await res.json(content_type=None)
                    except ValueError as err:
                        raise UntisError("Not a WebUntis server — check the URL") from err
                    set_cookies = res.headers.getall("Set-Cookie", [])
                break
            except (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError) as err:
                # A pooled keep-alive connection the server already closed: retry once on a fresh one
                if attempt:
                    raise UntisError(f"Connection failed: {err}") from err
            except aiohttp.ClientError as err:
                raise UntisError(f"Connection failed: {err}") from err
        if not isinstance(body, dict):
            raise UntisError("Not a WebUntis server — check the URL")
        if body.get("error"):
            code = body["error"].get("code")
            message = ERRORS.get(code) or body["error"].get("message") or f"WebUntis error {code}"
            if code == -8504:
                raise UntisAuthError(message)
            raise UntisError(message)
        return body.get("result"), set_cookies

    async def _login(self) -> _Session:
        result, set_cookies = await self._rpc(
            "authenticate", {"user": self._username, "password": self._password, "client": "home-assistant"}
        )
        if not isinstance(result, dict) or not result.get("sessionId"):
            raise UntisAuthError("Login failed")
        # Keep whatever the server sets (some instances add tenant cookies) plus the session and school
        jar: dict[str, str] = {}
        for raw in set_cookies:
            pair = raw.split(";", 1)[0]
            if "=" in pair:
                k, v = pair.split("=", 1)
                jar[k.strip()] = v.strip()
        jar["JSESSIONID"] = str(result["sessionId"])
        jar["schoolname"] = quote("_" + base64.b64encode(self.school.encode()).decode(), safe="")
        return _Session(
            cookie="; ".join(f"{k}={v}" for k, v in jar.items()),
            person_id=int(result.get("personId") or 0),
            person_type=int(result.get("personType") or 0),
        )

    async def _logout(self, s: _Session) -> None:
        try:
            await self._rpc("logout", {}, s.cookie)
        except UntisError:
            pass

    async def _students(self, s: _Session) -> list[Student]:
        """Parent accounts list their children in the app data (REST, bearer token from the session)."""
        students: list[Student] = []
        try:
            async with self._http.get(
                f"{self.origin}/WebUntis/api/token/new", headers={"Cookie": s.cookie}, ssl=self._ssl
            ) as res:
                token = (await res.text()).strip() if res.status == 200 else None
            if token:
                async with self._http.get(
                    f"{self.origin}/WebUntis/api/rest/view/v1/app/data",
                    headers={"Cookie": s.cookie, "Authorization": f"Bearer {token}", "Accept": "application/json"},
                    ssl=self._ssl,
                ) as res:
                    app = await res.json(content_type=None) if res.status == 200 else None
                for st in ((app or {}).get("user") or {}).get("students") or []:
                    if isinstance(st, dict) and st.get("id"):
                        students.append(Student(int(st["id"]), str(st.get("displayName") or st.get("name") or st["id"])))
        except (aiohttp.ClientError, ValueError):
            pass  # fall back to the account's own timetable below
        # A student account sees its own timetable
        if not students and s.person_type == PERSON_TYPE_STUDENT and s.person_id:
            students.append(Student(s.person_id, self._username))
        return students

    async def _lessons(self, s: _Session, student: int, start: date, days: int, tz: tzinfo) -> list[Lesson]:
        end = start + timedelta(days=days - 1)
        result, _ = await self._rpc(
            "getTimetable",
            {
                "options": {
                    "id": int(time.time() * 1000),
                    "element": {"id": student, "type": PERSON_TYPE_STUDENT},
                    "startDate": int(start.strftime("%Y%m%d")),
                    "endDate": int(end.strftime("%Y%m%d")),
                    "showLsText": True,
                    "showSubstText": True,
                    "showInfo": True,
                    "subjectFields": ["id", "name", "longname"],
                    "roomFields": ["id", "name", "longname"],
                    "teacherFields": ["id", "name", "longname"],
                    "klasseFields": ["id", "name"],
                }
            },
            s.cookie,
        )
        lessons: list[Lesson] = []
        for item in result if isinstance(result, list) else []:
            try:
                day = _ymd(int(item["date"]))
                sh, sm = _hm(int(item["startTime"]))
                eh, em = _hm(int(item["endTime"]))
            except (KeyError, TypeError, ValueError):
                continue
            su = (item.get("su") or [None])[0]
            ro = (item.get("ro") or [None])[0]
            te = (item.get("te") or [None])[0]
            text = " · ".join(str(x) for x in (item.get("substText"), item.get("lstext"), item.get("info")) if x) or None
            lessons.append(
                Lesson(
                    id=int(item.get("id") or 0),
                    day=day,
                    start=datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz),
                    end=datetime(day.year, day.month, day.day, eh, em, tzinfo=tz),
                    subject=_name(su),
                    subject_long=str(su["longname"]) if isinstance(su, dict) and su.get("longname") else None,
                    room=_name(ro),
                    # orgname = the originally planned teacher/room before a substitution
                    room_changed=bool(isinstance(ro, dict) and ro.get("orgname")),
                    teacher=_name(te),
                    teacher_changed=bool(isinstance(te, dict) and te.get("orgname")),
                    code=str(item["code"]) if item.get("code") else None,
                    text=text,
                )
            )
        lessons.sort(key=lambda l: (l.start, l.cancelled))
        return lessons

    async def students(self) -> list[Student]:
        """The account's students (config flow / connection test)."""
        s = await self._login()
        try:
            return await self._students(s)
        finally:
            await self._logout(s)

    async def timetable(self, student: int, start: date, days: int, tz: tzinfo) -> list[Lesson]:
        """One student's lessons for an arbitrary range (service call)."""
        s = await self._login()
        try:
            return await self._lessons(s, student, start, days, tz)
        finally:
            await self._logout(s)

    async def timetables(self, start: date, days: int, tz: tzinfo, only: set[int] | None = None) -> dict[Student, list[Lesson]]:
        """One login for all (or the selected) students."""
        s = await self._login()
        try:
            out: dict[Student, list[Lesson]] = {}
            for student in await self._students(s):
                if only is not None and student.id not in only:
                    continue
                out[student] = await self._lessons(s, student.id, start, days, tz)
            return out
        finally:
            await self._logout(s)
