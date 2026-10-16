#!/usr/bin/env python3
"""Synchronize the 'unblocked' label with native GitHub issue dependencies.

Stdlib-only. Intended to run under GitHub Actions with GITHUB_TOKEN,
GITHUB_REPOSITORY, GITHUB_EVENT_NAME and GITHUB_EVENT_PATH.
"""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

LABEL = "unblocked"
LABEL_COLOR = "0e8a16"
LABEL_DESCRIPTION = "Open issue with no open blocking issue dependencies; managed automatically"
API_VERSION = "2022-11-28"


class APIError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo, token, api_url="https://api.github.com"):
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        owner, separator, name = repo.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise ValueError("GITHUB_REPOSITORY must be owner/repo")
        self.api_url = api_url.rstrip("/")
        self.root = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        self.repo_url = f"{self.api_url}{self.root}".lower()
        self.token = token

    def request(self, method, path, data=None, missing_ok=False):
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(
            self.api_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "sync-unblocked-issue-labels",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(req, timeout=30) as res:
                content = res.read()
                return json.loads(content) if content else None
        except HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            details = exc.read(500).decode("utf-8", errors="replace")
            raise APIError(f"GitHub API {method} {path}: HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise APIError(f"GitHub API {method} {path}: {exc.reason}") from exc

    def paginated(self, path, **params):
        page = 1
        while True:
            query = urlencode({**params, "per_page": 100, "page": page})
            result = self.request("GET", f"{path}?{query}")
            if not isinstance(result, list):
                raise APIError(f"Expected API list at {path}, got {type(result).__name__}")
            yield from result
            if len(result) < 100:
                return
            page += 1

    def ensure_label(self):
        label_path = f"{self.root}/labels/{quote(LABEL, safe='')}"
        if self.request("GET", label_path, missing_ok=True) is not None:
            return
        try:
            self.request(
                "POST",
                f"{self.root}/labels",
                {"name": LABEL, "color": LABEL_COLOR, "description": LABEL_DESCRIPTION},
            )
            print(f"Created '{LABEL}' label")
        except APIError:
            # Another workflow may have created it concurrently. Verify first.
            if self.request("GET", label_path, missing_ok=True) is None:
                raise

    def get_issue(self, number):
        return self.request("GET", f"{self.root}/issues/{int(number)}", missing_ok=True)

    def issues(self, state, labels=None):
        params = {"state": state}
        if labels:
            params["labels"] = labels
        for issue in self.paginated(f"{self.root}/issues", **params):
            # GitHub's REST issues endpoint includes pull requests.
            if "pull_request" not in issue:
                yield issue

    def blocked_by(self, number):
        return list(self.paginated(f"{self.root}/issues/{int(number)}/dependencies/blocked_by"))

    def blocking(self, number):
        return list(self.paginated(f"{self.root}/issues/{int(number)}/dependencies/blocking"))

    def add_label(self, number):
        self.request("POST", f"{self.root}/issues/{int(number)}/labels", {"labels": [LABEL]})

    def remove_label(self, number):
        self.request(
            "DELETE",
            f"{self.root}/issues/{int(number)}/labels/{quote(LABEL, safe='')}",
            missing_ok=True,
        )


def has_label(issue):
    return any(
        (label.get("name") if isinstance(label, dict) else label).casefold() == LABEL
        for label in issue.get("labels", [])
    )


def should_be_unblocked(issue, dependencies):
    if issue["state"] != "open":
        if issue["state"] != "closed":
            raise APIError(f"Unknown state {issue['state']!r} on issue #{issue['number']}")
        return False
    for dependency in dependencies:
        state = dependency.get("state")
        if state not in ("open", "closed"):
            # Never label an issue as actionable if dependency visibility is partial.
            raise APIError(f"Missing/unknown dependency state for issue #{issue['number']}")
        if state == "open":
            return False
    return True


def sync_issue(api, issue):
    number = issue["number"]
    if issue["state"] == "closed":
        desired = False
    else:
        desired = should_be_unblocked(issue, api.blocked_by(number))
    current = has_label(issue)
    if desired and not current:
        api.add_label(number)
        print(f"#{number}: added {LABEL}")
        return "added"
    if not desired and current:
        api.remove_label(number)
        print(f"#{number}: removed {LABEL}")
        return "removed"
    return "unchanged"


def sync_full(api):
    counts = {"added": 0, "removed": 0, "unchanged": 0}
    # Only open issues need dependency checks; closed issues are queried by label.
    for issue in api.issues("open"):
        counts[sync_issue(api, issue)] += 1
    # Snapshot the filtered list *before* removing labels. Otherwise the
    # results shrink between pages and page 2 could skip issues.
    for issue in list(api.issues("closed", labels=LABEL)):
        counts[sync_issue(api, issue)] += 1
    print(f"Full sync: {counts}")


def sync_event(api, event):
    issue = event.get("issue")
    if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
        print("Event has no issue number: falling back to full sync")
        return sync_full(api)

    number = issue["number"]
    fresh = api.get_issue(number)
    if fresh is None:
        # E.g., deleted/transferred issue; its dependents may still need updates.
        print(f"Issue #{number} not found: running full sync")
        return sync_full(api)
    sync_issue(api, fresh)

    # When a blocker opens, closes, or reopens, recalculate same-repository issues
    # it blocks immediately. Cross-repository updates are caught by their schedules.
    for dependent in api.blocking(number):
        if str(dependent.get("repository_url", "")).rstrip("/").lower() != api.repo_url:
            continue
        dep_number = dependent["number"]
        if dep_number == number:
            continue
        fresh_dependent = api.get_issue(dep_number)
        if fresh_dependent is not None:
            sync_issue(api, fresh_dependent)


def main():
    api = GitHub(
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    api.ensure_label()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "issues":
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            sync_event(api, json.load(f))
    else:
        sync_full(api)


if __name__ == "__main__":
    try:
        main()
    except (APIError, ValueError, KeyError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
