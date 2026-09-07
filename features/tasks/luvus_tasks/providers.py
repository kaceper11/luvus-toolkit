from __future__ import annotations

import base64
import html
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .core import GITHUB_DEFAULT_QUERY, TaskError, clean, credentials


class ProviderError(TaskError):
    def __init__(self, message, ambiguous=False):
        super().__init__(message)
        self.ambiguous = ambiguous


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "text":
            return clean(value.get("text", ""))
        if kind == "hardBreak":
            return "\n"
        if kind in ("inlineCard", "mention", "emoji"):
            attrs = value.get("attrs", {})
            return clean(attrs.get("url") or attrs.get("text") or attrs.get("shortName") or "")
        text = "".join(plain(x) for x in value.get("content", []))
        return text + ("\n" if kind in ("paragraph", "heading", "listItem", "codeBlock", "tableRow") else "")
    parser = TextHTML()
    parser.feed(str(value or ""))
    return clean("".join(parser.parts)).strip()


def adf(text):
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} if line else
        {"type": "paragraph", "content": []} for line in text.split("\n")]}


def segment(value):
    return urllib.parse.quote(str(value), safe="")


class HTTP:
    def __init__(self, base, user, token):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TaskError("API base must be an HTTPS URL without credentials, query, or fragment.")
        self.base = base.rstrip("/")
        self.token = token
        self.auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path, method="GET", data=None, content_type="application/json", query=None, write=False):
        if not path.startswith("/") or path.startswith("//"):
            raise TaskError("Invalid provider API path.")
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(url, data=payload, method=method, headers={
            "Authorization": "Basic " + self.auth, "Accept": "application/json", "Content-Type": content_type})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                try:
                    return json.loads(raw) if raw else {}
                except ValueError as exc:
                    raise ProviderError("Provider returned invalid JSON.", ambiguous=write) from exc
        except urllib.error.HTTPError as exc:
            detail = clean(exc.read().decode("utf-8", "replace")).replace(self.token, "[redacted]").replace(self.auth, "[redacted]")
            if exc.code == 429:
                detail = "Rate limited. Retry after " + exc.headers.get("Retry-After", "the provider's cooldown")
            raise ProviderError(f"HTTP {exc.code}: {detail}", ambiguous=write and exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError("Provider connection failed or timed out. Refresh before retrying.", ambiguous=write) from exc


class Jira:
    def __init__(self, connection, http=None):
        self.c = connection
        site = connection["url"].rstrip("/")
        parsed = urllib.parse.urlsplit(site)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".atlassian.net") or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise TaskError("Jira Cloud site must be https://your-site.atlassian.net.")
        self.site = site
        cloud = connection.get("cloud_id")
        base = f"https://api.atlassian.com/ex/jira/{segment(cloud)}" if cloud else site
        self.http = http or HTTP(base, connection["email"], credentials(connection))

    def normalize(self, issue):
        f = issue["fields"]
        acceptance = f.get(self.c.get("acceptance_field", ""), "")
        return {"provider": "Jira", "connection": self.c["id"], "id": str(issue["id"]),
                "key": issue["key"], "title": clean(f.get("summary")), "status": clean(f.get("status", {}).get("name")),
                "project": f.get("project", {}).get("key", ""), "description": plain(f.get("description")),
                "acceptance": plain(acceptance), "url": self.site + "/browse/" + segment(issue["key"])}

    def query(self, expression=""):
        expression = expression or "assignee = currentUser() ORDER BY updated DESC"
        if self.c.get('_scope_project'):
            project = self.c['_scope_project']
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', project):
                raise TaskError('Invalid Jira project scope.')
            # Split ORDER BY only outside quoted JQL values.
            pattern = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\bORDER\s+BY\b"
            order = next((m.start() for m in re.finditer(pattern, expression, re.I) if m.group()[0] not in ('"', "'")), len(expression))
            expression = '(' + expression[:order].strip() + ') AND project = "' + project + '" ' + expression[order:]
        result, token, seen = [], None, set()
        while True:
            data = {"jql": expression, "maxResults": 100, "fields": ["summary", "status", "project", "description"]}
            if self.c.get("acceptance_field"):
                data["fields"].append(self.c["acceptance_field"])
            if token:
                data["nextPageToken"] = token
            page = self.http.request("/rest/api/3/search/jql", "POST", data)
            result.extend(self.normalize(i) for i in page.get("issues", []))
            token = page.get("nextPageToken")
            if not token or page.get("isLast"):
                return result
            if token in seen:
                raise ProviderError("Jira repeated a pagination token; results were not cached as complete.")
            seen.add(token)

    def get(self, identifier):
        if not re.fullmatch(r"(?:[A-Za-z][A-Za-z0-9_]*-)?[0-9]+", str(identifier)):
            raise TaskError("Enter a Jira key such as TEAM-123 or a numeric issue ID.")
        return self.normalize(self.http.request("/rest/api/3/issue/" + segment(identifier)))

    def actions(self, ticket):
        data = self.http.request(f"/rest/api/3/issue/{segment(ticket['id'])}/transitions", query={"expand": "transitions.fields"})
        return [{"id": t["id"], "name": t["name"] + " → " + t["to"]["name"], "state": t["to"]["name"],
                 "fields": t.get("fields", {})} for t in data["transitions"]]

    def transition(self, ticket, action, fields):
        self.http.request(f"/rest/api/3/issue/{segment(ticket['id'])}/transitions", "POST",
                          {"transition": {"id": action["id"]}, "fields": fields}, write=True)
        updated = self.get(ticket["id"])
        if updated["status"] != action["state"]:
            raise ProviderError("Transition sent, but refreshed state differs; inspect the ticket before retrying.", True)
        return updated

    def comments(self, ticket):
        items, start = [], 0
        while True:
            page = self.http.request(f"/rest/api/3/issue/{segment(ticket['id'])}/comment", query={"startAt": start, "maxResults": 100})
            batch = page.get("comments", [])
            items.extend({"id": str(c["id"]), "label": "Comment " + str(c["id"]), "text": plain(c["body"])} for c in batch)
            start += len(batch)
            if start >= page.get("total", start):
                return items
            if not batch:
                raise ProviderError("Incomplete Jira comment pagination.")

    def related(self, ticket):
        f = self.http.request(f"/rest/api/3/issue/{segment(ticket['id'])}", query={"fields": "parent,subtasks,issuelinks"})["fields"]
        issues = ([f["parent"]] if f.get("parent") else []) + f.get("subtasks", [])
        for link in f.get("issuelinks", []):
            issues.extend(link[k] for k in ("inwardIssue", "outwardIssue") if k in link)
        return [{"id": i["key"], "label": i["key"] + " " + clean(i.get("fields", {}).get("summary"))} for i in issues]

    def comment(self, ticket, text):
        return self.http.request(f"/rest/api/3/issue/{segment(ticket['id'])}/comment", "POST", {"body": adf(text)}, write=True)


class Azure:
    def __init__(self, connection, http=None):
        self.c = connection
        org = connection["organization"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", org):
            raise TaskError("Azure organization must be its name, not a URL.")
        self.site = "https://dev.azure.com/" + org
        self.prefix = "/" + segment(connection["project"]) + "/_apis/wit/"
        self.http = http or HTTP(self.site, "", credentials(connection))

    def request(self, path, method="GET", data=None, query=None, write=False, patch=False):
        return self.http.request(self.prefix + path, method, data,
                                 "application/json-patch+json" if patch else "application/json",
                                 {"api-version": "7.1", **(query or {})}, write)

    def normalize(self, item):
        f = item["fields"]
        return {"provider": "Azure DevOps", "connection": self.c["id"], "id": str(item["id"]), "key": "AB#" + str(item["id"]),
                "title": clean(f.get("System.Title")), "status": clean(f.get("System.State")),
                "project": f.get("System.TeamProject", self.c["project"]), "description": plain(f.get("System.Description")),
                "acceptance": plain(f.get(self.c.get("acceptance_field") or "Microsoft.VSTS.Common.AcceptanceCriteria")),
                "rev": item["rev"], "work_type": f["System.WorkItemType"],
                "url": self.site + "/" + segment(f.get("System.TeamProject", self.c["project"])) + "/_workitems/edit/" + str(item["id"])}

    def query(self, expression=""):
        expression = expression or "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND [System.TeamProject] = @Project ORDER BY [System.ChangedDate] DESC"
        page = self.request("wiql", "POST", {"query": expression})
        if page.get("queryType", "flat") != "flat":
            raise TaskError("Only flat WIQL queries are supported.")
        ids = [i["id"] for i in page.get("workItems", [])]
        result = []
        for offset in range(0, len(ids), 200):
            batch = self.request("workitemsbatch", "POST", {"ids": ids[offset:offset + 200], "errorPolicy": "Fail"})
            result.extend(self.normalize(i) for i in batch["value"])
        by_id = {r["id"]: r for r in result}
        if len(by_id) != len(set(ids)):
            raise ProviderError("Azure returned incomplete work-item results.")
        return [by_id[str(i)] for i in ids]

    def get(self, identifier):
        identifier = str(identifier).removeprefix("AB#")
        if not identifier.isdigit():
            raise TaskError("Enter a numeric Azure work-item ID.")
        return self.normalize(self.request("workitems/" + identifier))

    def actions(self, ticket):
        states = self.request("workitemtypes/" + segment(ticket["work_type"]) + "/states")["value"]
        return [{"id": s["name"], "name": s["name"], "state": s["name"], "fields": {}} for s in states if s["name"] != ticket["status"]]

    def transition(self, ticket, action, fields):
        self.request("workitems/" + ticket["id"], "PATCH", [
            {"op": "test", "path": "/rev", "value": ticket["rev"]},
            {"op": "add", "path": "/fields/System.State", "value": action["state"]}], write=True, patch=True)
        updated = self.get(ticket["id"])
        if updated["status"] != action["state"]:
            raise ProviderError("Update sent, but refreshed state differs. Inspect before retrying.", True)
        return updated

    def comments(self, ticket):
        result, token, seen = [], None, set()
        while True:
            q = {"api-version": "7.1-preview.4", "$top": 200}
            if token:
                q["continuationToken"] = token
            page = self.request("workitems/" + ticket["id"] + "/comments", query=q)
            result.extend({"id": str(c["id"]), "label": "Comment " + str(c["id"]), "text": plain(c["text"])} for c in page.get("comments", []))
            token = page.get("continuationToken")
            if not token:
                return result
            if token in seen:
                raise ProviderError("Azure repeated a comment pagination token.")
            seen.add(token)

    def related(self, ticket):
        item = self.request("workitems/" + ticket["id"], query={"$expand": "relations"})
        return [{"id": r["url"].rsplit("/", 1)[-1], "label": r["rel"] + " " + r["url"].rsplit("/", 1)[-1]}
                for r in item.get("relations", []) if r["rel"].startswith("System.LinkTypes.") and r["url"].rsplit("/", 1)[-1].isdigit()]

    def comment(self, ticket, text):
        return self.request("workitems/" + ticket["id"] + "/comments", "POST", {"text": html.escape(text).replace("\n", "<br>")},
                            query={"api-version": "7.1-preview.4"}, write=True)


class GitHub:
    """GitHub Issues through gh's existing login; no token is read or stored here."""
    def __init__(self, connection):
        self.c = connection
        self.repos = github_repositories(connection)
        self.repo = connection.get("_scope_repository", self.repos[0])
        self.binary = shutil.which("gh")
        if not self.binary:
            raise TaskError("Install gh and run 'gh auth login --hostname github.com' before using GitHub Issues.")
        self.prefix = "repos/" + self.repo + "/issues"

    def scoped(self, repo):
        if repo not in self.repos:
            raise TaskError("Repository is not selected in this connection. Repair its repository selection in Configuration.")
        import copy
        result = copy.copy(self)
        result.repo, result.prefix = repo, "repos/" + repo + "/issues"
        result.c = {**self.c, "_scope_repository": repo}
        return result

    def issue_path(self, ticket):
        scope = self.scoped(ticket["project"])
        number = ticket["id"].rsplit("#", 1)[-1]
        if not number.isdigit():
            raise TaskError("GitHub issue number must be numeric.")
        return scope.prefix + "/" + number

    def accounts(self):
        user = self.api("user")["login"]
        return user, [user] + [x["login"] for x in self.api("user/orgs?per_page=100", paginate=True)]

    def repositories(self, owner, login):
        if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
            raise TaskError("Invalid GitHub owner.")
        endpoint = "user/repos?affiliation=owner&per_page=100" if owner == login else "orgs/" + owner + "/repos?per_page=100"
        return sorted(x["full_name"] for x in self.api(endpoint, paginate=True) if x["owner"]["login"].lower() == owner.lower())

    def api(self, endpoint, method="GET", data=None, paginate=False, optional=False):
        if method != "GET" and self.c.get("account") and self.api("user")["login"] != self.c["account"]:
            raise TaskError("The local gh account changed. Review the connection before writing.")
        argv = [self.binary, "api", "--hostname", "github.com", endpoint, "--method", method,
                "-H", "Accept: application/vnd.github+json"]
        payload = None
        if data is not None:
            argv += ["--input", "-"]
            payload = json.dumps(data)
        if paginate:
            argv += ["--paginate", "--slurp"]
        try:
            result = subprocess.run(argv, input=payload, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError("gh could not complete the request. Check authentication/network and inspect before retrying writes.", method != "GET") from exc
        if result.returncode:
            if optional and "HTTP 404" in result.stderr:
                return None
            # gh errors may contain request details; only expose its HTTP status.
            code = re.search(r"HTTP (\d+)", result.stderr)
            status = code[1] if code else "unknown"
            raise ProviderError(f"gh request failed (HTTP {status}). Check gh auth status, repository permissions, query syntax, and rate limits.",
                                method != "GET" and (not code or int(code[1]) >= 500))
        try:
            parsed = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError as exc:
            raise ProviderError("gh returned invalid JSON.", method != "GET") from exc
        return [item for page in parsed for item in page] if paginate else parsed

    def normalize(self, issue):
        if "pull_request" in issue:
            raise TaskError("This URL/number identifies a pull request, not an issue.")
        reason = issue.get("state_reason")
        state = issue["state"] + (f" ({reason})" if issue["state"] == "closed" and reason else "")
        # Preserve the original repository's IDs, including durable write journals.
        ident = str(issue["number"]) if self.repo == self.c.get("repository") else self.repo + "#" + str(issue["number"])
        return {"provider": "GitHub", "connection": self.c["id"], "id": ident,
                "key": self.repo + "#" + str(issue["number"]), "title": clean(issue["title"]),
                "status": state, "state": issue["state"], "project": self.repo,
                "description": clean(issue.get("body")), "acceptance": "", "updated_at": issue["updated_at"],
                "url": "https://github.com/" + self.repo + "/issues/" + str(issue["number"])}

    def query(self, expression=""):
        if len(self.repos) > 1 and not self.c.get("_scope_repository"):
            return [issue for repo in self.repos for issue in self.scoped(repo).query(expression)]
        if self.c.get("account") and self.api("user")["login"] != self.c["account"]:
            raise TaskError("The local gh account changed. Review and save this connection with the intended account before refreshing.")
        if not expression:
            expression = GITHUB_DEFAULT_QUERY
        elif "@me" in expression:
            expression = expression.replace("@me", self.api("user")["login"])
        # Connection scope is fixed; additional repo qualifiers must not widen it.
        if re.search(r"\brepo:", expression, re.I):
            raise TaskError("Select a connection to choose the repository; omit repo: from its saved filter.")
        query = "repo:" + self.repo + " is:issue " + expression
        items, page = [], 1
        while True:
            response = self.api("search/issues?" + urllib.parse.urlencode({"q": query, "per_page": 100, "page": page}))
            if response.get("incomplete_results") or response["total_count"] > 1000:
                raise ProviderError("GitHub search is incomplete or exceeds 1,000 matches. Narrow the saved filter; results were not cached as complete.")
            batch = response.get("items", [])
            if any(i.get("repository_url", "").lower() != "https://api.github.com/repos/" + self.repo.lower() for i in batch):
                raise ProviderError("Search returned an issue outside this connection's repository. Narrow the filter.")
            items.extend(self.normalize(i) for i in batch)
            if len(items) >= response["total_count"]:
                return items
            if not batch:
                raise ProviderError("GitHub search ended before all matching issues were retrieved.")
            page += 1
            if page > 10:
                raise ProviderError("Search changed during pagination. Refresh or narrow the filter.")

    def get(self, identifier):
        repo = str(identifier).rsplit("#", 1)[0] if "#" in str(identifier).lstrip("#") else self.c.get("repository", self.repo)
        scope = self.scoped(repo)
        number = str(identifier).rsplit("#", 1)[-1]
        if not number.isdigit():
            raise TaskError("GitHub issue number must be numeric.")
        return scope.normalize(self.api(scope.prefix + "/" + number))

    def actions(self, ticket):
        if ticket["state"] == "closed":
            return [{"id": "open", "name": "Reopen", "state": "open", "reason": "reopened", "fields": {}}]
        return [{"id": r, "name": "Close as " + r.replace("_", " "), "state": "closed", "reason": r, "fields": {}}
                for r in ("completed", "not_planned")]

    def transition(self, ticket, action, fields):
        if self.get(ticket["id"])["updated_at"] != ticket["updated_at"]:
            raise ProviderError("Issue changed since review. Refresh and choose the action again.")
        self.api(self.issue_path(ticket), "PATCH", {"state": action["state"], "state_reason": action["reason"]})
        updated = self.get(ticket["id"])
        if updated["state"] != action["state"]:
            raise ProviderError("State update submitted but refreshed issue differs. Inspect before retrying.", True)
        return updated

    def comments(self, ticket):
        return [{"id": str(c["id"]), "label": "Comment by " + c.get("user", {}).get("login", "unknown"), "text": clean(c["body"])}
                for c in self.api(self.issue_path(ticket) + "/comments?per_page=100", paginate=True)]

    def related(self, ticket):
        parent = self.api(self.issue_path(ticket) + "/parent", optional=True)
        children = self.api(self.issue_path(ticket) + "/sub_issues?per_page=100", paginate=True)
        return [{"id": str(i["number"]), "label": i["html_url"] + " " + clean(i["title"]), "url": i["html_url"]}
                for i in ([parent] if parent else []) + children]

    def comment(self, ticket, text):
        return self.api(self.issue_path(ticket) + "/comments", "POST", {"body": text})


def github_repositories(connection):
    repos = connection.get("selected_repositories", [connection.get("repository", "")])
    if not isinstance(repos, list) or not repos or any(not isinstance(r, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) or any(p in (".", "..") for p in r.split("/")) for r in repos):
        raise TaskError("GitHub repository selection is incomplete. Open Configuration → Repair invalid connection to choose repositories; a username or organisation alone is not a repository.")
    if len({r.split("/")[0].lower() for r in repos}) != 1:
        raise TaskError("Choose repositories from one owner per connection.")
    return list(dict.fromkeys(repos))


def provider(connection):
    kinds = {"jira": Jira, "azure": Azure, "github": GitHub}
    if connection["provider"] not in kinds:
        raise TaskError("Unsupported provider.")
    return kinds[connection["provider"]](connection)


def lookup_candidates(config, value):
    if "://" not in value:
        result = [(c, value) for c in config["connections"] if
                (c["provider"] == "jira" and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", value)) or
                (c["provider"] == "azure" and value.removeprefix("AB#").isdigit())]
        for c in config["connections"]:
            if c["provider"] != "github":
                continue
            for repo in github_repositories(c):
                if value.removeprefix("#").isdigit():
                    result.append((c, value if repo == c.get("repository") else repo + "#" + value.lstrip("#")))
                elif value.startswith(repo + "#") and value.rsplit("#", 1)[-1].isdigit():
                    result.append((c, value))
        return result
    u = urllib.parse.urlsplit(value)
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        raise TaskError("Ticket URL must use HTTPS and contain no credentials or custom port.")
    result = []
    for c in config["connections"]:
        if c["provider"] == "jira":
            host = urllib.parse.urlsplit(c["url"]).hostname
            m = re.fullmatch(r"/browse/([A-Za-z][A-Za-z0-9_]*-\d+)/?", u.path)
            if u.hostname == host and m:
                result.append((c, m[1]))
        elif c["provider"] == "azure":
            parts = [urllib.parse.unquote(p) for p in u.path.strip("/").split("/")]
            if u.hostname == "dev.azure.com" and len(parts) == 5 and parts[:2] == [c["organization"], c["project"]] and parts[2:4] == ["_workitems", "edit"] and parts[4].isdigit():
                result.append((c, parts[4]))
        elif c["provider"] == "github":
            match = re.fullmatch(r"/([^/]+/[^/]+)/issues/(\d+)/?", u.path)
            if u.hostname == "github.com" and match and match[1].lower() in [r.lower() for r in github_repositories(c)]:
                repo = next(r for r in github_repositories(c) if r.lower() == match[1].lower())
                result.append((c, match[2] if repo == c.get("repository") else repo + "#" + match[2]))
    return result
