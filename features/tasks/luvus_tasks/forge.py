"""Repository-scoped PR/CI reads and explicitly reviewed publication."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .core import TaskError, clean, now
from .handover import git
from .providers import Azure, GitHub, ProviderError, github_repositories, segment

LOG_LIMIT = 2 * 1024 * 1024


def remote_identity(path):
    remote = git(path, "remote", "get-url", "origin").strip()
    if re.match(r"git@github\.com:", remote, re.I):
        remote = "https://github.com/" + remote.split(":", 1)[1]
    if remote.startswith("git@ssh.dev.azure.com:v3/"):
        org, project, repo = remote.split(":v3/", 1)[1].split("/", 2)
        return {"kind": "azure", "organization": org, "project": project, "repository": repo}
    u = urllib.parse.urlsplit(remote)
    parts = [urllib.parse.unquote(x) for x in u.path.strip("/").split("/")]
    if u.hostname == "github.com" and len(parts) == 2:
        return {"kind": "github", "repository": "/".join(parts).removesuffix(".git")}
    if u.hostname == "dev.azure.com" and len(parts) == 4 and parts[2] == "_git":
        return {"kind": "azure", "organization": parts[0], "project": parts[1], "repository": parts[3].removesuffix(".git")}
    if (u.hostname or "").endswith(".visualstudio.com") and len(parts) == 3 and parts[1] == "_git":
        return {"kind": "azure", "organization": u.hostname.removesuffix(".visualstudio.com"), "project": parts[0], "repository": parts[2].removesuffix(".git")}
    raise TaskError("Origin is not a supported GitHub.com or Azure Repos remote.")


def choices(store, record):
    identity = remote_identity(record["target"]["path"])
    result = []
    for c in store.config()["connections"]:
        if c["provider"] != identity["kind"]:
            continue
        if identity["kind"] == "github":
            try:
                allowed = github_repositories(c)
            except TaskError:
                continue
            if identity["repository"].casefold() not in [x.casefold() for x in allowed]:
                continue
        elif any(str(c.get(k, "")).casefold() != identity[k].casefold() for k in ("organization", "project")):
            continue
        result.append({**identity, "connection": c["id"]})
    return result


def preference_key(record):
    identity = remote_identity(record['target']['path'])
    return 'pr-connection:' + json.dumps(identity, sort_keys=True).casefold()


def preferred(store, record):
    links = choices(store, record)
    saved = record.get('forge')
    if saved:
        c = next((c for c in store.config()['connections'] if c['id'] == saved.get('connection')), {})
        return saved if saved in links and record.get('forge_account', c.get('account', '')) == c.get('account', '') else None
    default = store.preference(preference_key(record))
    if default:
        # Identity includes account so an account change requires fresh selection.
        c = next((c for c in store.config()['connections'] if c['id'] == default.get('connection')), {})
        if c.get('account', '') != default.get('account', ''):
            return None
        return next((x for x in links if x['connection'] == default.get('connection')), None)
    return links[0] if len(links) == 1 else None


def remember(store, record, link):
    if link not in choices(store, record):
        raise TaskError('Connection scope changed during repair. Refresh and choose again.')
    c = next(c for c in store.config()['connections'] if c['id'] == link['connection'])
    store.set_preference(preference_key(record), {'connection': link['connection'], 'account': c.get('account', '')})


def client(store, record):
    link = record.get("forge")
    if not link or not any(x == link for x in choices(store, record)):
        raise TaskError("Choose an authorized PR connection for this repository; origin or connection scope may have changed.")
    if git(record["target"]["path"], "branch", "--show-current") != record["target"]["branch"]:
        raise TaskError("Checkout branch changed. Reconcile the handover before accessing its PR.")
    c = next(c for c in store.config()["connections"] if c["id"] == link["connection"])
    if record.get("forge_account", c.get("account", "")) != c.get("account", ""):
        raise TaskError("PR connection account changed. Repair the connection before continuing.")
    return GitHubPR(c, link["repository"]) if link["kind"] == "github" else AzurePR(c, link["repository"])


def result_state(value):
    if value in ("success", "succeeded"):
        return "passed"
    if value in ("failure", "failed", "timed_out", "action_required", "error"):
        return "failed"
    if value in ("pending", "in_progress", "queued", "notStarted", "inProgress"):
        return "running"
    return "unknown"  # Skipped/cancelled/neutral are not successful validation.



def feedback_item(ident, thread, comments, resolved=False, outdated=False, resolvable=True, path=None, line=None):
    # Our own durable reply markers do not invalidate the reviewed input snapshot.
    comments = [c for c in comments if '<!-- luvus-workflow:' not in c.get('text', '')]
    payload = {'comments': comments, 'path': path, 'line': line}
    return {'id': ident, 'thread': thread, 'comments': comments, 'resolved': bool(resolved),
            'outdated': bool(outdated), 'resolvable': resolvable, 'path': path, 'line': line,
            'signature': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}


class GitHubPR:
    def __init__(self, connection, repo):
        self.api_client = GitHub(connection).scoped(repo)
        self.repo = repo
        self.prefix = "repos/" + repo
        if connection.get("account") and self.api_client.api("user")["login"] != connection["account"]:
            raise TaskError("GitHub account changed. Review the connection first.")

    def api(self, path, **kwargs):
        return self.api_client.api(self.prefix + path, **kwargs)

    def list(self, branch):
        q = urllib.parse.urlencode({"head": self.repo.split("/")[0] + ":" + branch, "state": "open", "per_page": 100})
        return [self.normalize(p) for p in self.api("/pulls?" + q, paginate=True)]

    def normalize(self, p):
        return {"id": str(p["number"]), "title": clean(p["title"]), "body": clean(p.get("body", "")),
                "url": p["html_url"], "state": "merged" if p.get("merged_at") or p.get("merged") else p["state"], "draft": p.get("draft", False),
                "head": p["head"]["sha"], "base": p["base"]["sha"], "head_branch": p["head"]["ref"],
                "base_branch": p["base"]["ref"], "repository": self.repo, "kind": "github"}

    def get(self, ident):
        return self.normalize(self.api("/pulls/" + str(int(ident))))

    def branch(self, branch):
        ref = self.api("/git/ref/heads/" + segment(branch), optional=True)
        if ref is None:
            self.api("")  # Distinguish an unpublished branch from inaccessible repository credentials.
            return None
        return ref["object"]["sha"]

    def workflow_feedback(self, ident):
        groups = {}
        for c in self.review_comments(ident):
            groups.setdefault(c['thread'], []).append(c)
        result = []
        for thread, comments in groups.items():
            root = next(c for c in comments if not c.get('in_reply_to_id'))
            result.append(feedback_item('thread:' + thread, thread,
                [{'id': str(c['id']), 'text': c.get('body', ''), 'author': c.get('user', {}).get('login'), 'at': c.get('updated_at')} for c in comments],
                root.get('state') == 'resolved', root.get('outdated'), path=root.get('path'), line=root.get('line') or root.get('original_line')))
            result[-1]['reply_to'] = str(root['id'])
        for c in self.api(f'/issues/{int(ident)}/comments?per_page=100', paginate=True):
            if '<!-- luvus-workflow:' in c.get('body', ''):
                continue
            result.append(feedback_item('discussion:' + str(c['id']), None,
                [{'id': str(c['id']), 'text': c.get('body', ''), 'author': c.get('user', {}).get('login'), 'at': c.get('updated_at')}], resolvable=False))
        return result

    def workflow_find_reply(self, ident, item, marker):
        route = f'/pulls/{int(ident)}/comments?per_page=100' if item['resolvable'] else f'/issues/{int(ident)}/comments?per_page=100'
        matches = [c for c in self.api(route, paginate=True) if marker in c.get('body', '') and
                   (not item['resolvable'] or str(c.get('in_reply_to_id')) == item['reply_to'])]
        if len(matches) > 1:
            raise TaskError('Duplicate workflow replies require inspection.')
        return str(matches[0]['id']) if matches else None

    def workflow_reply(self, ident, item, body):
        route = f"/pulls/{int(ident)}/comments/{int(item['reply_to'])}/replies" if item['resolvable'] else f'/issues/{int(ident)}/comments'
        return self.api(route, method='POST', data={'body': body})

    def workflow_resolve(self, ident, item):
        if not item['resolvable']:
            raise TaskError('Discussion comments are not resolvable review threads.')
        data = self.api_client.api('graphql', method='POST', data={
            'query': 'mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}',
            'variables': {'id': item['thread']}})
        if data.get('errors'):
            raise TaskError('GitHub thread resolution failed; inspect before retrying.')
        return data

    def review_comments(self, ident):
        comments = self.api(f"/pulls/{int(ident)}/comments?per_page=100", paginate=True)
        if not comments:
            return []
        # REST replies identify their parent; only the thread root is needed from GraphQL.
        query = """query($owner:String!,$repo:String!,$number:Int!,$after:String){
          repository(owner:$owner,name:$repo){pullRequest(number:$number){
            reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
              nodes{id isResolved isOutdated comments(first:1){nodes{id}}}}}}} """
        owner, repo = self.repo.split("/")
        states, after = {}, None
        for _ in range(100):
            data = self.api_client.api("graphql", method="POST", data={"query": query,
                "variables": {"owner": owner, "repo": repo, "number": int(ident), "after": after}})
            try:
                if data.get("errors"):
                    raise ValueError()
                page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
                for thread in page["nodes"]:
                    states[thread["comments"]["nodes"][0]["id"]] = {
                        "thread": thread["id"], "state": "resolved" if thread["isResolved"] else "comment",
                        "outdated": thread["isOutdated"]}
                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]
                if not cursor or cursor == after:
                    raise ValueError()
                after = cursor
            except (KeyError, TypeError, IndexError, ValueError) as exc:
                raise TaskError("GitHub review thread state is incomplete; refresh again.") from exc
        else:
            raise TaskError("GitHub review thread pagination limit reached; partial results are not current.")
        by_id = {x["id"]: x for x in comments}
        for comment in comments:
            root, seen = comment, set()
            while root.get("in_reply_to_id"):
                parent = root["in_reply_to_id"]
                if parent in seen or parent not in by_id:
                    raise TaskError("GitHub review comment ancestry is incomplete; refresh again.")
                seen.add(parent)
                root = by_id[parent]
            state = states.get(root.get("node_id"))
            if state is None:
                raise TaskError("GitHub review thread changed during refresh; refresh again.")
            comment.update(state)
        return comments

    def snapshot(self, ident=None, branch=None):
        p = self.get(ident) if ident is not None else {"id": "", "title": branch, "state": "branch", "head": self.branch(branch),
            "head_branch": branch, "repository": self.repo, "url": "https://github.com/" + self.repo + "/tree/" + segment(branch)}
        if ident is None and not p["head"]:
            return {**p, "state": "unpublished", "checks": [], "feedback": [], "runs": [], "errors": []}
        checks, feedback, runs, errors = [], [], [], []
        def read(label, fn):
            try:
                return fn()
            except TaskError as exc:
                errors.append(label + ": " + str(exc))
                return []
        for x in read("checks", lambda: self.pages("/commits/" + p["head"] + "/check-runs", "check_runs")):
            checks.append({"id": "check:" + str(x["id"]), "title": x["name"], "state": result_state(x.get("conclusion") or x["status"]),
                           "revision": x["head_sha"], "url": x.get("details_url"), "text": clean((x.get("output") or {}).get("summary", ""))})
        statuses = read("statuses", lambda: self.api("/commits/" + p["head"] + "/statuses?per_page=100", paginate=True))
        seen = set()
        for x in statuses:
            if x["context"] in seen:
                continue
            seen.add(x["context"])
            checks.append({"id": "status:" + x["context"], "title": x["context"], "state": result_state(x["state"]),
                           "revision": p["head"], "url": x.get("target_url"), "text": clean(x.get("description", ""))})
        for route, family in ((("reviews", "pulls"), ("comments", "pulls"), ("discussion", "issues")) if ident is not None else ()):
            for x in read(route, lambda route=route, family=family: self.review_comments(ident) if route == "comments" else self.api(f"/{family}/{int(ident)}/{'comments' if route == 'discussion' else route}?per_page=100", paginate=True)):
                feedback.append({"id": route + ":" + str(x["id"]), "text": clean(x.get("body", "")) or ("Review: " + x["state"] if x.get("state") == "CHANGES_REQUESTED" else ""), "state": x.get("state", "comment"),
                                 "thread": x.get("thread"), "outdated": x.get("outdated", False),
                                 "author": x.get("user", {}).get("login"), "revision": x.get("commit_id"), "url": x["html_url"], "at": x.get("submitted_at", x.get("updated_at"))})
        for x in read("runs", lambda: self.pages("/actions/runs?" + urllib.parse.urlencode({"head_sha": p["head"]}), "workflow_runs")):
            runs.append({"id": str(x["id"]), "group": str(x.get("workflow_id", x["id"])), "title": x.get("name", "Workflow"), "revision": x["head_sha"],
                         "state": result_state(x.get("conclusion") or x["status"]), "url": x["html_url"]})
        return {**p, "checks": checks, "feedback": feedback, "runs": runs, "errors": errors}

    def pages(self, route, key):
        out = []
        for page in range(1, 101):
            data = self.api(route + ("&" if "?" in route else "?") + f"per_page=100&page={page}")
            out.extend(data[key])
            if len(data[key]) < 100:
                return out
        raise TaskError("Provider pagination limit reached; narrow the result. Partial results are not current.")

    def jobs(self, run):
        return [{"id": str(x["id"]), "title": x["name"], "state": result_state(x.get("conclusion") or x["status"]),
                 "url": x["html_url"]} for x in self.pages(f"/actions/runs/{int(run)}/jobs?filter=latest", "jobs")]

    def log(self, run, job):
        endpoint = self.prefix + f"/actions/jobs/{int(job)}/logs"
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                r = subprocess.run([self.api_client.binary, "api", "--hostname", "github.com", endpoint], stdout=output, stderr=errors, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TaskError("GitHub log download failed or timed out.") from exc
            if r.returncode:
                raise TaskError("GitHub log unavailable; check Actions read permission and log retention.")
            size = output.tell()
            output.seek(0)
            raw = output.read(LOG_LIMIT)
        return {"text": clean(raw.decode("utf-8", "replace")), "omitted_bytes": max(0, size - len(raw))}

    def create(self, plan):
        return self.normalize(self.api("/pulls", method="POST", data={k: plan[k] for k in ("title", "body", "head", "base", "draft")}))


class AzurePR:
    def __init__(self, connection, repo):
        self.azure = Azure(connection)
        self.prefix = "/" + segment(connection["project"]) + "/_apis/"
        self.repository = self.request("git/repositories/" + segment(repo))
        self.repo = self.repository["id"]
        self.git = "git/repositories/" + segment(self.repo)

    def request(self, path, method="GET", data=None, query=None, write=False):
        return self.azure.http.request(self.prefix + path, method, data, query={"api-version": "7.1", **(query or {})}, write=write)

    def pages(self, path, query=None):
        # Git PR/thread lists use skip/top; build lists use continuation headers.
        values, token = [], None
        for _ in range(100):
            q = {"api-version": "7.1", "$top": 100, **(query or {})}
            if token:
                q["continuationToken"] = token
            data, headers = self.response(path, q)
            values.extend(data.get("value", []))
            next_token = headers.get("x-ms-continuationtoken")
            if not next_token:
                return values
            if next_token == token:
                raise TaskError("Azure repeated a continuation token.")
            token = next_token
        raise TaskError("Azure pagination limit reached; results are incomplete.")

    def response(self, path, query, text=False):
        http = self.azure.http
        req = urllib.request.Request(http.base + self.prefix + path + "?" + urllib.parse.urlencode(query), headers={
            "Authorization": "Basic " + http.auth, "Accept": "text/plain" if text else "application/json"})
        try:
            with http.opener.open(req, timeout=30) as r:
                raw = r.read(LOG_LIMIT + 1 if text else 8 * LOG_LIMIT + 1)
                if not text and len(raw) > 8 * LOG_LIMIT:
                    raise TaskError("Azure response exceeded the bounded response limit.")
                return (raw if text else json.loads(raw)), dict((k.lower(), v) for k, v in r.headers.items())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TaskError("Azure read failed; check Code/Build read permissions, authentication, and network.") from exc

    def workflow_feedback(self, ident):
        data = self.request(self.git + f'/pullrequests/{int(ident)}/threads')
        if not isinstance(data.get('value'), list) or len(data['value']) >= 10000:
            raise TaskError('Azure feedback is incomplete or exceeds its bound.')
        result = []
        for t in data['value']:
            if t.get('isDeleted'):
                continue
            comments = [{'id': str(c['id']), 'text': c.get('content', ''),
                         'author': c.get('author', {}).get('id'), 'at': c.get('lastUpdatedDate')}
                        for c in t.get('comments', []) if not c.get('isDeleted') and c.get('commentType') not in ('system', 3)]
            if not comments:
                continue
            context = t.get('threadContext') or {}
            resolved = t.get('status') in ('fixed', 'closed', 'wontFix', 'byDesign', 2, 3, 4, 5)
            result.append(feedback_item('thread:' + str(t['id']), str(t['id']), comments, resolved,
                path=context.get('filePath'), line=(context.get('rightFileStart') or context.get('leftFileStart') or {}).get('line')))
        return result

    def workflow_find_reply(self, ident, item, marker):
        t = self.request(self.git + f"/pullrequests/{int(ident)}/threads/{int(item['thread'])}")
        matches = [c for c in t.get('comments', []) if not c.get('isDeleted') and marker in c.get('content', '')]
        if len(matches) > 1:
            raise TaskError('Duplicate workflow replies require inspection.')
        return str(matches[0]['id']) if matches else None

    def workflow_reply(self, ident, item, body):
        return self.request(self.git + f"/pullrequests/{int(ident)}/threads/{int(item['thread'])}/comments",
                            method='POST', data={'content': body, 'commentType': 1}, write=True)

    def workflow_resolve(self, ident, item):
        return self.request(self.git + f"/pullrequests/{int(ident)}/threads/{int(item['thread'])}",
                            method='PATCH', data={'status': 'fixed'}, write=True)

    def normalize(self, p):
        return {"id": str(p["pullRequestId"]), "title": clean(p["title"]), "body": clean(p.get("description", "")),
                "url": self.repository["webUrl"] + "/pullrequest/" + str(p["pullRequestId"]), "state": p["status"], "draft": p.get("isDraft", False),
                "head": (p.get("lastMergeSourceCommit") or {}).get("commitId"), "base": (p.get("lastMergeTargetCommit") or {}).get("commitId"),
                "merge": (p.get("lastMergeCommit") or {}).get("commitId"), "reviewers": p.get("reviewers", []),
                "head_branch": p["sourceRefName"].removeprefix("refs/heads/"), "base_branch": p["targetRefName"].removeprefix("refs/heads/"),
                "repository": self.repo, "kind": "azure"}

    def list(self, branch):
        result = []
        for skip in range(0, 10000, 100):
            data = self.request(self.git + "/pullrequests", query={"searchCriteria.sourceRefName": "refs/heads/" + branch,
                                  "searchCriteria.status": "active", "$top": 100, "$skip": skip})["value"]
            result.extend(self.normalize(x) for x in data)
            if len(data) < 100:
                return result
        raise TaskError("Too many Azure PRs; results are incomplete.")

    def get(self, ident):
        return self.normalize(self.request(self.git + "/pullrequests/" + str(int(ident))))

    def branch(self, branch):
        refs = self.pages(self.git + "/refs", {"filter": "heads/" + branch})
        match = [x for x in refs if x["name"] == "refs/heads/" + branch]
        if not match:
            return None
        if len(match) != 1:
            raise TaskError("Remote branch is ambiguous; inspect it in Azure Repos.")
        return match[0]["objectId"]

    def snapshot(self, ident=None, branch=None):
        p = self.get(ident) if ident is not None else {"id": "", "title": branch, "state": "branch", "head": self.branch(branch),
            "head_branch": branch, "repository": self.repo, "url": self.repository["webUrl"], "reviewers": []}
        if ident is None and not p["head"]:
            return {**p, "state": "unpublished", "checks": [], "feedback": [], "runs": [], "errors": []}
        checks, feedback, runs, errors = [], [], [], []
        for x in p.pop("reviewers"):
            feedback.append({"id": "reviewer:" + x["id"], "text": clean(x.get("displayName", "Reviewer")) + " vote: " + str(x.get("vote", 0)),
                             "state": "CHANGES_REQUESTED" if x.get("vote", 0) < 0 else "comment", "revision": None, "url": p["url"]})
        if ident is not None:
            try:
                for x in self.pages(self.git + f"/pullrequests/{int(ident)}/threads"):
                    if x.get("isDeleted"):
                        continue
                    feedback.append({"id": "thread:" + str(x["id"]), "text": clean("\n".join(c.get("content", "") for c in x.get("comments", []) if not c.get("isDeleted"))),
                                     "state": "resolved" if x.get("status") in ("fixed", "closed", "wontFix", "byDesign") else "active",
                                     "revision": None, "at": x.get("lastUpdatedDate"), "url": p["url"]})
            except TaskError as exc:
                errors.append(str(exc))
            try:
                project_id = self.repository["project"]["id"]
                artifact = f"vstfs:///CodeReview/CodeReviewId/{project_id}/{int(ident)}"
                for x in self.pages("policy/evaluations", {"artifactId": artifact}):
                    checks.append({"id": "policy:" + x["evaluationId"], "title": x["configuration"]["type"]["displayName"],
                                   "state": "failed" if x["status"] == "rejected" else "unknown", "revision": None,
                                   "text": "Policy: " + x["status"] + " (revision not independently established)", "url": p["url"]})
            except TaskError as exc:
                errors.append(str(exc))
        try:
            for branch in ["refs/heads/" + p["head_branch"]] + ([f"refs/pull/{int(ident)}/merge"] if ident is not None else []):
                for x in self.pages("build/builds", {"repositoryId": self.repo, "repositoryType": "TfsGit", "branchName": branch}):
                    sha = x.get("sourceVersion")
                    runs.append({"id": str(x["id"]), "group": str(x.get("definition", {}).get("id", x["id"])), "title": x.get("buildNumber", "Build"), "revision": sha,
                                 "association": "head" if sha and sha == p["head"] else "merge" if sha and sha == p.get("merge") else "unknown",
                                 "state": result_state(x.get("result") or x["status"]), "url": x.get("_links", {}).get("web", {}).get("href", p["url"])})
        except TaskError as exc:
            errors.append(str(exc))
        return {**p, "checks": checks, "feedback": feedback, "runs": runs, "errors": errors}

    def jobs(self, run):
        return [{"id": str(x["log"]["id"]), "title": x.get("name", "Job"), "state": result_state(x.get("result") or x.get("state")),
                 "url": self.repository["webUrl"].split("/_git/")[0] + "/_build/results?buildId=" + str(int(run))}
                for x in self.request(f"build/builds/{int(run)}/timeline").get("records", []) if x.get("log")]

    def log(self, run, job):
        raw, headers = self.response(f"build/builds/{int(run)}/logs/{int(job)}", {"api-version": "7.1"}, text=True)
        omitted = max(0, int(headers.get("content-length", len(raw))) - LOG_LIMIT)
        return {"text": clean(raw[:LOG_LIMIT].decode("utf-8", "replace")), "omitted_bytes": omitted,
                "omission_is_minimum": len(raw) > LOG_LIMIT and "content-length" not in headers}

    def create(self, plan):
        return self.normalize(self.request(self.git + "/pullrequests", "POST", {"title": plan["title"], "description": plan["body"],
                              "sourceRefName": "refs/heads/" + plan["head"], "targetRefName": "refs/heads/" + plan["base"], "isDraft": plan["draft"]}, write=True))


def publication_plan(store, record, title, body, base, draft=True):
    if not title.strip() or not base.strip() or base.startswith("-"):
        raise TaskError("A title and remote base branch are required.")
    p = client(store, record)
    branch = record["target"]["branch"]
    head = p.branch(branch)
    if head != git(record["target"]["path"], "rev-parse", "HEAD"):
        raise TaskError("Remote head differs from the local head. Publish/reconcile using Git Sidebar first.")
    if base == branch:
        raise TaskError("PR source and base branches must differ.")
    return {"title": title, "body": body, "base": base, "head": branch, "draft": bool(draft), "head_sha": head,
            "base_sha": p.branch(base), "destination": record["forge"]}


def publish(store, record, plan):
    p = client(store, record)
    if plan["destination"] != record["forge"] or plan["head"] != record["target"]["branch"]:
        raise TaskError("PR destination changed after review.")
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    ident = "pr-write:" + digest
    ticket = record["ticket"]["connection"] + ":" + record["ticket"]["id"]
    with store.lock("pr-publication"):
        writes = store.records("writes")
        previous = next((x for x in writes if x["id"] == ident), None)
        existing = p.list(plan["head"])
        if previous and previous.get("result"):
            verified = p.get(previous["result"]["id"])
            if any(verified[k] != plan[v] for k, v in (("head_branch", "head"), ("base_branch", "base"), ("title", "title"), ("body", "body"), ("draft", "draft"))):
                raise TaskError("Published PR readback differs. Inspect the provider before continuing.")
            store.put("writes", ident, {**previous, "state": "published"}, ticket)
            return verified
        if existing:
            raise TaskError("A PR already exists for this branch. Select it instead of publishing another.")
        if any(x.get("kind") == "pr" and x["state"] == "pending" and x["plan"]["destination"] == plan["destination"] and x["plan"]["head"] == plan["head"] for x in writes):
            raise TaskError("Previous PR publication is uncertain. Inspect the provider; automatic retry is disabled.")
        if p.branch(plan["head"]) != plan["head_sha"] or p.branch(plan["base"]) != plan["base_sha"]:
            raise TaskError("Remote branches changed after review. Review publication again.")
        entry = {"id": ident, "kind": "pr", "state": "pending", "at": now(), "plan": plan}
        store.put("writes", ident, entry, ticket)
        try:
            result = p.create(plan)
        except ProviderError as exc:
            if not exc.ambiguous:
                entry["state"] = "rejected"
                store.put("writes", ident, entry, ticket)
            raise
        entry["result"] = result
        store.put("writes", ident, entry, ticket)
        verified = p.get(result["id"])
        if any(verified[k] != plan[v] for k, v in (("head_branch", "head"), ("base_branch", "base"), ("title", "title"), ("body", "body"), ("draft", "draft"))):
            raise TaskError("Published PR readback differs. Inspect the provider before continuing.")
        entry["state"] = "published"
        store.put("writes", ident, entry, ticket)
        return verified


def reconcile(store, record, selected):
    """An explicitly selected provider PR can settle an uncertain publication."""
    verified = client(store, record).get(selected)
    ticket = record["ticket"]["connection"] + ":" + record["ticket"]["id"]
    with store.lock("pr-publication"):
        for entry in store.records("writes"):
            if entry.get("kind") != "pr" or entry["state"] != "pending":
                continue
            plan = entry["plan"]
            if plan["destination"] == record["forge"] and all(verified[k] == plan[v] for k, v in
                (("head_branch", "head"), ("base_branch", "base"), ("title", "title"), ("body", "body"), ("draft", "draft"))):
                store.put("writes", entry["id"], {**entry, "state": "published", "result": verified}, ticket)
    return verified
