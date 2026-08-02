from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin

import requests
from gitsource import GithubRepositoryDataReader

from faq_assistant.matching import matches_any
from faq_assistant.models import SourceDocument


FAQ_BASE_URL = "https://datatalks.club/faq/"


def load_source_documents(config: dict[str, Any]) -> list[SourceDocument]:
    documents: list[SourceDocument] = load_course_status_documents(config)

    if config["sources"]["faq"]["enabled"]:
        documents.extend(load_faq_documents(config))

    if config["sources"]["docs"]["enabled"]:
        documents.extend(load_general_docs_documents(config))

    if config["sources"]["course_markdown"]["enabled"]:
        documents.extend(load_course_markdown_documents(config))

    if config["sources"].get("shared_course_docs", {}).get("enabled", False):
        documents.extend(load_shared_course_docs_documents(config))

    if config["sources"]["course_repositories"]["enabled"]:
        documents.extend(load_course_repository_documents(config))

    return documents


def load_faq_documents(config: dict[str, Any]) -> list[SourceDocument]:
    source_config = config["sources"]["faq"]
    response = requests.get(source_config["courses_url"], timeout=60)
    response.raise_for_status()
    courses_index = response.json()

    configured_courses = config["courses"]
    documents: list[SourceDocument] = []

    for item in courses_index:
        course = item["course"]
        course_config = configured_courses.get(course)
        if not course_config or not course_config.get("faq_enabled", False):
            continue

        url = urljoin(FAQ_BASE_URL, item["path"].lstrip("/"))
        course_response = requests.get(url, timeout=60)
        course_response.raise_for_status()

        for faq in course_response.json():
            question = clean_text(faq.get("question", ""))
            answer = clean_text(faq.get("answer", ""))
            section = clean_text(faq.get("section", "FAQ"))
            faq_id = str(faq.get("id") or "")
            source_id = faq_id or stable_hash(f"{course}:{section}:{question}")
            # Deep link to the specific question on the rendered FAQ page, e.g.
            # https://datatalks.club/faq/data-engineering-zoomcamp.html#9e508f2212
            url = f"{FAQ_BASE_URL}{course}.html#{faq_id}" if faq_id else FAQ_BASE_URL
            text = f"section: {section}\nquestion: {question}\nanswer: {answer}".strip()
            documents.append(
                SourceDocument(
                    source_type="faq",
                    scope="course",
                    course=course,
                    course_name=course_config["name"],
                    section=section,
                    title=question,
                    text=text,
                    url=url,
                    repo=None,
                    path=None,
                    source_id=source_id,
                    authority="reference",
                )
            )

    return documents


def load_general_docs_documents(config: dict[str, Any]) -> list[SourceDocument]:
    source_config = config["sources"]["docs"]
    github_config = source_config["github"]
    files = read_github_files(github_config)

    documents: list[SourceDocument] = []
    for file in files:
        title = extract_title(file.content) or file.filename
        documents.append(
            SourceDocument(
                source_type="docs",
                scope="docs",
                course=None,
                course_name=None,
                section=section_from_path(file.filename),
                title=title,
                text=clean_text(file.content),
                url=docs_site_url(file.filename),
                repo=github_config["repo"],
                path=file.filename,
                source_id=file.filename,
                authority="canonical",
            )
        )
    return documents


def load_course_markdown_documents(config: dict[str, Any]) -> list[SourceDocument]:
    source_config = config["sources"]["course_markdown"]
    github_config = source_config["github"]
    documents: list[SourceDocument] = []

    for course, course_config in config["courses"].items():
        prefix = str(course_config.get("docs_prefix") or "")
        if not prefix:
            continue

        files = read_github_files(github_config, required_prefix=prefix)
        for file in files:
            title = extract_title(file.content) or file.filename
            documents.append(
                SourceDocument(
                    source_type="course_docs",
                    scope="course",
                    course=course,
                    course_name=course_config["name"],
                    section=section_from_path(file.filename),
                    title=title,
                    text=clean_text(file.content),
                    url=docs_site_url(file.filename),
                    repo=github_config["repo"],
                    path=file.filename,
                    source_id=file.filename,
                    authority="canonical",
                )
            )

    return documents


def load_course_repository_documents(config: dict[str, Any]) -> list[SourceDocument]:
    source_config = config["sources"]["course_repositories"]
    github_defaults = source_config["github"]
    documents: list[SourceDocument] = []

    for course, course_config in config["courses"].items():
        for repo_config in course_config.get("github_repositories", []):
            github_config = {
                "repo": repo_config["repo"],
                "ref": repo_config.get("ref", github_defaults.get("ref", "main")),
                "include": github_defaults.get("include", []),
                "exclude": github_defaults.get("exclude", []),
            }
            try:
                files = read_github_files(github_config)
            except Exception as e:
                print(f"warning: failed to fetch {github_config['repo']}: {e}")
                continue

            for file in files:
                title = extract_title(file.content) or file.filename
                cohort = cohort_from_locator(file.filename)
                current_cohort = str(course_config.get("current_cohort") or "")
                is_current = bool(cohort and cohort == current_cohort)
                documents.append(
                    SourceDocument(
                        source_type="github",
                        scope="course",
                        course=course,
                        course_name=course_config["name"],
                        section=section_from_path(file.filename),
                        title=title,
                        text=clean_text(file.content),
                        url=github_url(github_config["repo"], github_config["ref"], file.filename),
                        repo=github_config["repo"],
                        path=file.filename,
                        source_id=f"{github_config['repo']}:{file.filename}",
                        cohort=cohort,
                        current=is_current,
                        authority=(
                            "canonical" if is_current else "historical" if cohort else "reference"
                        ),
                    )
                )

    return documents


def load_shared_course_docs_documents(config: dict[str, Any]) -> list[SourceDocument]:
    """Load course-agnostic logistics pages shared by every Zoomcamp."""
    source_config = config["sources"]["shared_course_docs"]
    github_config = source_config["github"]
    documents: list[SourceDocument] = []
    seen: set[str] = set()

    for prefix in source_config.get("prefixes", []):
        for file in read_github_files(github_config, required_prefix=str(prefix)):
            if file.filename in seen:
                continue
            seen.add(file.filename)
            documents.append(
                SourceDocument(
                    source_type="docs",
                    scope=str(source_config.get("scope", "docs")),
                    course=None,
                    course_name=None,
                    section=section_from_path(file.filename),
                    title=extract_title(file.content) or file.filename,
                    text=clean_text(file.content),
                    url=docs_site_url(file.filename),
                    repo=github_config["repo"],
                    path=file.filename,
                    source_id=file.filename,
                    authority="canonical",
                )
            )
    return documents


def load_course_status_documents(config: dict[str, Any]) -> list[SourceDocument]:
    """Build one canonical, searchable current-course record per configured course."""
    documents: list[SourceDocument] = []
    for course, course_config in config.get("courses", {}).items():
        cohort = str(course_config.get("current_cohort") or "")
        platform_url = str(course_config.get("platform_url") or "")
        if not cohort or not platform_url:
            raise ValueError(f"course {course!r} needs current_cohort and platform_url")
        name = str(course_config.get("name") or course)
        text = (
            f"{name} current cohort: {cohort}.\n"
            f"Current course management platform: {platform_url}\n"
            "Use the course management platform for current enrollment, homework and project "
            "deadlines, project submission, peer-review assignments, leaderboard, dashboard, "
            "certificate details, and other live cohort status. Dates can change; check the "
            "platform for the current value instead of relying on historical cohort pages."
        )
        documents.append(
            SourceDocument(
                source_type="course_status",
                scope="course",
                course=course,
                course_name=name,
                section="Current course information",
                title=f"{name} {cohort} course management platform",
                text=text,
                url=platform_url,
                repo=None,
                path="",
                source_id=f"course-status:{course}:{cohort}",
                cohort=cohort,
                current=True,
                authority="canonical",
            )
        )
    return documents


_COHORT_RE = re.compile(r"(?:^|/)cohorts/(20\d{2})(?:/|$)")
_PLATFORM_COHORT_RE = re.compile(r"(?:^|[-/])(20\d{2})(?:/|$)")


def cohort_from_locator(path: str | None = None, url: str | None = None) -> str:
    """Extract a four-digit cohort from a repository path or platform URL."""
    path_match = _COHORT_RE.search(str(path or ""))
    if path_match:
        return path_match.group(1)
    url_match = _PLATFORM_COHORT_RE.search(str(url or ""))
    return url_match.group(1) if url_match else ""


def read_github_files(github_config: dict[str, Any], required_prefix: str | None = None):
    owner, repo_name = github_config["repo"].split("/", 1)
    include = list(github_config.get("include", []))
    exclude = list(github_config.get("exclude", []))
    required_prefix = (required_prefix or "").strip("/")

    def filename_filter(path: str) -> bool:
        normalized = path.strip("/")
        if required_prefix and not normalized.startswith(required_prefix + "/"):
            return False
        if exclude and matches_any(normalized, exclude):
            return False
        return matches_any(normalized, include)

    reader = GithubRepositoryDataReader(
        repo_owner=owner,
        repo_name=repo_name,
        branch=github_config.get("ref", "main"),
        allowed_extensions={"md"},
        filename_filter=filename_filter,
        skip_hidden=True,
    )
    # The reader's filename_filter is not reliably applied to every path (e.g.
    # exact root filenames slip through), so re-apply it to the returned files.
    return [file for file in reader.read() if filename_filter(file.filename)]


def github_url(repo: str, ref: str, path: str) -> str:
    return f"https://github.com/{repo}/blob/{ref}/{path}"


DOCS_SITE_BASE = "https://datatalks.club/docs/"


def docs_site_url(path: str) -> str:
    """Map a DataTalksClub/docs repo path to its rendered site URL.

    The site uses Jekyll pretty permalinks under /docs/, e.g.
    ``general/slack.md`` -> ``https://datatalks.club/docs/general/slack/`` and
    ``courses/de/getting-started.md`` -> ``.../docs/courses/de/getting-started/``.
    """
    slug = path[:-3] if path.endswith(".md") else path
    if slug == "index":
        slug = ""
    elif slug.endswith("/index"):
        slug = slug[: -len("/index")]
    url = DOCS_SITE_BASE + slug
    return url if url.endswith("/") else url + "/"


def section_from_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) <= 1:
        return "General"
    return " / ".join(parts[:-1])


def extract_title(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", str(text or ""))
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]
