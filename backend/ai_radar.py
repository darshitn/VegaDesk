"""AI Radar: a daily, ₹0-recurring-cost research pass over public, documented
sources (GitHub releases, Hugging Face Hub, OpenRouter models API).

Design rules enforced here:
- Remote content is UNTRUSTED text. Titles/summaries are sanitized (HTML and
  control chars stripped, length-capped) and every URL is validated against a
  host allowlist before it is stored or shown. Nothing fetched is ever executed
  or treated as an instruction.
- A new Hugging Face repo is a CANDIDATE (verification_status='unverified'), not
  proof of a major launch. A free hosted model is not a credit/token grant.
- An offer whose expiry has passed is marked 'expired' and never shown as
  currently free. Unknown billing terms stay None ('not confirmed').
- Sources fail in isolation; a failing source keeps prior items and is recorded
  in the run's per_source report. The fetch date is never used as the announce
  date (published_utc comes from the source's own timestamp).

All network access goes through an injectable ``fetch`` callable so tests run
fully offline with mocked responses and a FakeClock.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

try:
    from db import AIRadarItem, AIRadarRun, ScheduledAlert, iso_utc
    import timeutil
except ImportError:  # pragma: no cover - package import path
    from .db import AIRadarItem, AIRadarRun, ScheduledAlert, iso_utc
    from . import timeutil

# ── configuration ────────────────────────────────────────────────────────
DEFAULT_GITHUB_REPOS = ["ollama/ollama", "ggml-org/llama.cpp", "SYSTRAN/faster-whisper"]
TIMEOUT_SECONDS = 15
POLITE_DELAY_SECONDS = 1.0          # between live source calls
VERIFICATION_RANK = {"expired": 0, "unverified": 1, "verified": 2}
ALLOWED_HOSTS = {
    "api.github.com", "github.com",
    "huggingface.co",
    "openrouter.ai",
}
CATEGORIES = {"model_release", "local_model", "free_api_model", "credit_offer", "other"}
# Categories that represent a free-access / credit offer. These get a guaranteed
# share of the read-side page so frequent local-model items can't crowd them out.
OFFER_CATEGORIES = ("free_api_model", "credit_offer")

# Cloudflare fronts openrouter.ai and 403s obvious bot UAs (verified live: a
# conventional browser UA returns 200 with no key, the custom VEGA UA returns
# 403 with an HTML challenge). The models catalog is public — no Bearer needed —
# so we send a normal UA plus OpenRouter's documented OPTIONAL attribution
# headers. A key is honored if the user configures one, but never required.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _user_agent():
    return (os.getenv("VEGA_RADAR_USER_AGENT") or "").strip() or DEFAULT_USER_AGENT


def _openrouter_key():
    """Optional Bearer key for openrouter.ai. Read from env/config only; never
    logged, never required (the public catalog is keyless). Empty -> None."""
    return (os.getenv("VEGA_RADAR_OPENROUTER_KEY") or "").strip() or None


def _attribution_headers():
    """OpenRouter's optional HTTP-Referer / X-Title app-attribution headers."""
    h = {}
    referer = (os.getenv("VEGA_RADAR_REFERER") or "").strip()
    title = (os.getenv("VEGA_RADAR_TITLE") or "").strip() or "VEGA Desktop Dashboard"
    if referer:
        h["HTTP-Referer"] = referer
    if title:
        h["X-Title"] = title
    return h


def _host_of(url):
    try:
        return re.match(r"https?://([^/]+)", url).group(1).split(":")[0].lower()
    except Exception:
        return ""


def _headers_for(url):
    """Build request headers for a URL: normal UA + Accept, plus OpenRouter
    attribution and an optional Bearer key only for openrouter.ai."""
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    if _host_of(url) == "openrouter.ai":
        headers.update(_attribution_headers())
        key = _openrouter_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers



class RadarSourceError(Exception):
    """A single source failed — isolated, recorded, never fatal to the run."""


class RadarSourceUnavailable(RadarSourceError):
    """A source is reachable but refused/blocked (e.g. HTTP 401/403). Isolated
    like any other failure, but flagged so the UI can say 'unavailable' rather
    than implying the whole digest is broken."""


def _github_repos():
    env = (os.getenv("VEGA_RADAR_GITHUB_REPOS") or "").strip()
    if env:
        return [r.strip() for r in env.split(",") if r.strip()][:10]
    return DEFAULT_GITHUB_REPOS


# ── text / URL safety ────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def _sanitize(text, limit=400):
    """Strip HTML/control chars, collapse whitespace, cap length. Remote text
    is data to display, never to act on."""
    if text is None:
        return ""
    s = str(text)
    s = _TAG_RE.sub(" ", s)
    s = _CTRL_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit]


def _validate_url(url):
    """Return the url if it is http(s) and on the allowlist, else None."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not (u.startswith("https://") or u.startswith("http://")):
        return None
    try:
        host = re.match(r"https?://([^/]+)", u).group(1).split(":")[0].lower()
    except Exception:
        return None
    if host not in ALLOWED_HOSTS:
        return None
    return u


def _canonical_url(url):
    """Normalize for dedup: lowercase host, drop fragment + tracking query."""
    u = url.split("#", 1)[0]
    scheme, _, rest = u.partition("://")
    host, _, path_query = rest.partition("/")
    path, _, query = path_query.partition("?")
    keep = []
    for part in query.split("&"):
        if part and not part.lower().startswith("utm_"):
            keep.append(part)
    path = path.rstrip("/")
    out = f"{scheme.lower()}://{host.lower()}/{path}" if path else f"{scheme.lower()}://{host.lower()}"
    return out + ("?" + "&".join(keep) if keep else "")


def _parse_when(value):
    """ISO-8601 string or epoch seconds -> naive UTC datetime, else None."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


# ── live HTTP (conditional requests + in-process cache) ──────────────────
_HTTP_CACHE = {}


def _live_fetch(url, timeout=TIMEOUT_SECONDS):
    """Polite GET with per-host headers, finite timeout, and ETag revalidation.
    Returns {'status', 'json', 'text', 'headers'}. Raises RadarSourceError on
    transport failure so the caller can isolate it. Never logs request headers
    (they may carry an optional Bearer key)."""
    import requests
    headers = _headers_for(url)
    cached = _HTTP_CACHE.get(url)
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    try:
        resp = requests.get(url, timeout=timeout, headers=headers)
    except Exception as e:
        raise RadarSourceError(f"transport error: {type(e).__name__}") from e
    if resp.status_code == 304 and cached:
        return {"status": 200, "json": cached.get("json"), "text": cached.get("text", ""),
                "headers": dict(resp.headers), "revalidated": True}
    try:
        data = resp.json()
    except Exception:
        data = None
    etag = resp.headers.get("ETag")
    _HTTP_CACHE[url] = {"etag": etag, "json": data, "text": resp.text or "", "ts": time.time()}
    return {"status": resp.status_code, "json": data, "text": resp.text or "",
            "headers": dict(resp.headers)}


# ── source adapters ──────────────────────────────────────────────────────
def _adapt_github(fetch, src, timeout):
    repo = src["repo"]
    res = fetch(f"https://api.github.com/repos/{repo}/releases?per_page=5", timeout)
    if res.get("status") != 200 or not isinstance(res.get("json"), list):
        raise RadarSourceError(f"github {repo}: HTTP {res.get('status')}")
    out = []
    for rel in res["json"][:5]:
        published = _parse_when(rel.get("published_at"))
        if published is None:
            continue  # tag-only entry, not a release
        tag = _sanitize(rel.get("tag_name") or rel.get("name") or "", 120)
        html_url = _validate_url(rel.get("html_url")) or f"https://github.com/{repo}/releases"
        name = _sanitize(rel.get("name") or tag, 200)
        out.append({
            "source_id": f"github:{repo}:{tag or html_url}",
            "category": "local_model",
            "title": _sanitize(f"{repo} {tag}".strip(), 300),
            "publisher": repo.split("/")[0],
            "url": html_url,
            "published_utc": published,
            "summary": f"{name or tag} — official GitHub release of {repo}." if name else f"Official GitHub release of {repo}.",
            "verification_status": "verified",
        })
    return out


def _adapt_openrouter(fetch, src, timeout):
    res = fetch("https://openrouter.ai/api/v1/models", timeout)
    status = res.get("status")
    if status in (401, 403):
        # Blocked or key rejected. The catalog is normally keyless; report this
        # source as unavailable (isolated) without implying the digest is broken.
        raise RadarSourceUnavailable(
            "openrouter: source unavailable (HTTP "
            f"{status}; access blocked or key rejected). Other sources still run.")
    if status != 200 or not isinstance(res.get("json"), dict):
        raise RadarSourceError(f"openrouter: HTTP {status}")
    models = res["json"].get("data") or []
    limit = int(src.get("limit", 8))
    out = []
    for m in models:
        if not isinstance(m, dict):
            continue
        pricing = m.get("pricing") or {}
        # Free = both prompt and completion priced at exactly 0.
        if str(pricing.get("prompt")) not in ("0", "0.0") or str(pricing.get("completion")) not in ("0", "0.0"):
            continue
        mid = m.get("id")
        if not mid or not isinstance(mid, str):
            continue
        url = f"https://openrouter.ai/models/{mid}"
        if not _validate_url(url):
            continue
        # OpenRouter publishes an optional expiration_date on some free listings.
        # Record it only when present so read-time expiry can mark it honestly;
        # a zero price is NEVER treated as a promotional token/credit grant.
        expires = _parse_when(m.get("expiration_date"))
        out.append({
            "source_id": f"openrouter:{mid}",
            "category": "free_api_model",
            "title": _sanitize(m.get("name") or mid, 300),
            "publisher": mid.split("/")[0] if "/" in mid else "OpenRouter",
            "url": url,
            "published_utc": _parse_when(m.get("created")),
            "summary": ("Listed as free (prompt+completion priced at 0) in OpenRouter's models API. "
                        "Hosted free model — not a promotional token grant; rate limits may apply."),
            "verification_status": "verified",
            "offer": {
                "kind": "free_model",
                "eligibility": "OpenRouter account",
                "quota": "rate limits may apply",
                "billing_required": None,   # not stated by the API -> not confirmed
                "region": None,
                "expires_utc": expires,     # verified from the source when present
                "terms_status": "confirmed" if expires else "unconfirmed",
            },
        })
        if len(out) >= limit:
            break
    return out


def _adapt_huggingface(fetch, src, timeout):
    res = fetch("https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=10", timeout)
    if res.get("status") != 200 or not isinstance(res.get("json"), list):
        raise RadarSourceError(f"huggingface: HTTP {res.get('status')}")
    limit = int(src.get("limit", 8))
    out = []
    for m in res["json"][:limit]:
        if not isinstance(m, dict):
            continue
        mid = m.get("modelId") or m.get("id")
        if not mid or not isinstance(mid, str):
            continue
        url = f"https://huggingface.co/{mid}"
        if not _validate_url(url):
            continue
        out.append({
            "source_id": f"hf:{mid}",
            "category": "local_model",
            "title": _sanitize(mid, 300),
            "publisher": mid.split("/")[0] if "/" in mid else None,
            "url": url,
            "published_utc": _parse_when(m.get("createdAt")),
            "summary": "Newly created Hugging Face repository — candidate only, not a confirmed major model launch.",
            "verification_status": "unverified",
        })
    return out


_ADAPTERS = {"github": _adapt_github, "openrouter": _adapt_openrouter, "huggingface": _adapt_huggingface}


def default_sources():
    srcs = [{"name": f"github:{repo}", "kind": "github", "repo": repo} for repo in _github_repos()]
    srcs.append({"name": "openrouter", "kind": "openrouter", "limit": 8})
    srcs.append({"name": "huggingface", "kind": "huggingface", "limit": 8})
    return srcs


# ── offer expiry ─────────────────────────────────────────────────────────
def _offer_expired(offer, now):
    if not offer:
        return False
    exp = offer.get("expires_utc")
    return exp is not None and exp <= now


def _apply_offer_expiry(cand, now):
    offer = cand.get("offer")
    if not offer:
        return
    if _offer_expired(offer, now):
        offer["terms_status"] = "expired"
        cand["verification_status"] = "expired"


# ── candidate normalization + dedup ──────────────────────────────────────
def _normalize_candidate(c, now):
    url = _validate_url(c.get("url"))
    if not url:
        return None
    category = c.get("category") if c.get("category") in CATEGORIES else "other"
    norm = {
        "source_id": (_sanitize(c.get("source_id"), 300) or url)[:300],
        "category": category,
        "title": (_sanitize(c.get("title"), 300) or url)[:300],
        "publisher": _sanitize(c.get("publisher"), 200) or None,
        "url": url,
        "canonical_url": _canonical_url(url),
        "published_utc": c.get("published_utc"),
        "summary": _sanitize(c.get("summary"), 1000),
        "verification_status": c.get("verification_status", "unverified"),
        "offer": dict(c["offer"]) if c.get("offer") else None,
    }
    _apply_offer_expiry(norm, now)
    return norm


def _merge_into(target, src):
    """Fold a duplicate same-story candidate into the kept record."""
    ev = list(target.get("evidence_urls") or [])
    for u in [src["url"]] + list(src.get("evidence_urls") or []):
        if u and u not in ev:
            ev.append(u)
    target["evidence_urls"] = ev[:8]
    # Prefer the stronger verification claim.
    if VERIFICATION_RANK.get(src["verification_status"], 0) > VERIFICATION_RANK.get(target["verification_status"], 0):
        target["verification_status"] = src["verification_status"]
    # Prefer the earliest real publication time.
    sp, tp = src.get("published_utc"), target.get("published_utc")
    if sp and (tp is None or sp < tp):
        target["published_utc"] = sp
    for field in ("publisher", "summary"):
        if not target.get(field) and src.get(field):
            target[field] = src[field]
    if src.get("offer") and not target.get("offer"):
        target["offer"] = dict(src["offer"])


def _dedup(candidates, now):
    by_url, by_title, order = {}, {}, []
    for raw in candidates:
        c = _normalize_candidate(raw, now)
        if c is None:
            continue
        title_key = (c["title"].lower().strip(), (c.get("publisher") or "").lower().strip())
        target = by_url.get(c["canonical_url"])
        if target is None and title_key[0]:
            target = by_title.get(title_key)
        if target is None:
            c["evidence_urls"] = [c["url"]]
            by_url[c["canonical_url"]] = c
            if title_key[0]:
                by_title[title_key] = c
            order.append(c)
        else:
            _merge_into(target, c)
    return order


# ── persistence ──────────────────────────────────────────────────────────
def _persist_items(db, clock, candidates, run_id):
    now = clock.now_utc()
    merged = _dedup(candidates, now)
    new_count = 0
    new_verified_count = 0
    for c in merged:
        existing = (db.query(AIRadarItem)
                    .filter((AIRadarItem.source_id == c["source_id"]) |
                            (AIRadarItem.canonical_url == c["canonical_url"]))
                    .first())
        offer = c.get("offer") or {}
        if existing:
            existing.title = c["title"]
            existing.publisher = c["publisher"]
            existing.url = c["url"]
            existing.canonical_url = c["canonical_url"]
            existing.category = c["category"]
            existing.summary = c["summary"]
            existing.verification_status = c["verification_status"]
            if c.get("published_utc"):
                existing.published_utc = c["published_utc"]
            existing.evidence_urls = json.dumps(c.get("evidence_urls") or [c["url"]])
            existing.offer_kind = offer.get("kind")
            existing.offer_eligibility = offer.get("eligibility")
            existing.offer_quota = offer.get("quota")
            existing.offer_billing_required = offer.get("billing_required")
            existing.offer_region = offer.get("region")
            existing.offer_expires_utc = offer.get("expires_utc")
            existing.offer_terms_status = offer.get("terms_status")
            existing.last_checked_utc = now
            existing.last_run_id = run_id
        else:
            item = AIRadarItem(
                source_id=c["source_id"], canonical_url=c["canonical_url"],
                category=c["category"], title=c["title"], publisher=c["publisher"],
                url=c["url"], published_utc=c.get("published_utc"),
                first_seen_utc=now, last_checked_utc=now, summary=c["summary"],
                evidence_urls=json.dumps(c.get("evidence_urls") or [c["url"]]),
                verification_status=c["verification_status"],
                offer_kind=offer.get("kind"), offer_eligibility=offer.get("eligibility"),
                offer_quota=offer.get("quota"), offer_billing_required=offer.get("billing_required"),
                offer_region=offer.get("region"), offer_expires_utc=offer.get("expires_utc"),
                offer_terms_status=offer.get("terms_status"),
                last_run_id=run_id,
            )
            db.add(item)
            new_count += 1
            if c["verification_status"] == "verified":
                new_verified_count += 1
    db.flush()
    return new_count, new_verified_count


# ── quiet hours ──────────────────────────────────────────────────────────
def _quiet_hours():
    def _hour(name):
        v = (os.getenv(name) or "").strip()
        if v.isdigit() and 0 <= int(v) <= 23:
            return int(v)
        return None
    return _hour("VEGA_RADAR_QUIET_START"), _hour("VEGA_RADAR_QUIET_END")


def in_quiet_hours(clock):
    start, end = _quiet_hours()
    if start is None or end is None:
        return False
    hour = timeutil.local_now(clock).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


# ── the research pass ────────────────────────────────────────────────────
def run_radar(db, clock, fetch=None, sources=None, trigger="manual"):
    """Run one research pass. Returns {'run', 'new_items', 'status', 'per_source'}.
    Never raises for a single source failure — those are isolated and recorded."""
    used_live = fetch is None
    fetch = fetch or _live_fetch
    sources = default_sources() if sources is None else sources
    now = clock.now_utc()

    run = AIRadarRun(started_utc=now, status="running", trigger=trigger, per_source="{}")
    db.add(run)
    db.flush()

    per_source, candidates, ok, failed = {}, [], 0, 0
    for i, src in enumerate(sources):
        name = src.get("name", src.get("kind", f"source{i}"))
        adapter = _ADAPTERS.get(src.get("kind"))
        if adapter is None:
            per_source[name] = {"count": 0, "error": "unknown adapter"}
            failed += 1
            continue
        try:
            items = adapter(fetch, src, TIMEOUT_SECONDS)
            per_source[name] = {"count": len(items), "error": None, "unavailable": False}
            candidates.extend(items)
            ok += 1
        except RadarSourceUnavailable as e:  # reachable but refused/blocked
            per_source[name] = {"count": 0, "error": _sanitize(str(e), 300), "unavailable": True}
            failed += 1
        except Exception as e:  # isolate every other source failure
            per_source[name] = {"count": 0, "error": _sanitize(str(e), 300), "unavailable": False}
            failed += 1
        if used_live and i < len(sources) - 1:
            time.sleep(POLITE_DELAY_SECONDS)  # polite spacing between live calls

    status = "success" if (failed == 0 and ok > 0) else ("partial" if ok > 0 else "failed")
    new_items, new_verified = _persist_items(db, clock, candidates, run.id)

    run.status = status
    run.sources_ok = ok
    run.sources_failed = failed
    run.new_items = new_items
    run.per_source = json.dumps(per_source)
    run.finished_utc = clock.now_utc()
    db.commit()

    maybe_notify_digest(db, clock, run, new_verified)

    return {"run": run.to_dict(), "new_items": new_items, "status": status, "per_source": per_source}


def maybe_notify_digest(db, clock, run, new_verified_count):
    """Create at most one digest alert per run, only when new verified items
    appeared and we're outside quiet hours. The digest (items + run) is already
    persisted before this point, so a notification is never the source of truth."""
    if run.digest_notified or new_verified_count <= 0:
        return None
    if in_quiet_hours(clock):
        return None
    now = clock.now_utc()
    alert = ScheduledAlert(
        kind="ai_radar_digest", entity_type="ai_radar_run", entity_id=run.id,
        due_utc=now, status="pending",
        message=f"AI Radar: {new_verified_count} new verified item(s) today.")
    db.add(alert)
    db.flush()
    run.digest_notified = True
    run.digest_alert_id = alert.id
    db.commit()
    return alert.id


# ── read-side: state + deterministic digest ──────────────────────────────
def _effective(item, now):
    """Return item.to_dict() with offer expiry re-evaluated at read time, so an
    offer that expired since the last run is never shown as currently free."""
    d = item.to_dict()
    offer = d.get("offer")
    if offer and offer.get("expires_utc"):
        exp = _parse_when(offer["expires_utc"])
        if exp is not None and exp <= now:
            offer["terms_status"] = "expired"
            d["verification_status"] = "expired"
            d["offer"] = offer
    return d


def get_radar_state(db, clock, limit=25):
    now = clock.now_utc()
    base = db.query(AIRadarItem).filter(AIRadarItem.is_dismissed.is_(False))
    recency = (AIRadarItem.published_utc.is_(None),
               AIRadarItem.published_utc.desc(),
               AIRadarItem.first_seen_utc.desc())

    # Guarantee high-value offer listings (free API models / credit offers) a
    # share of the slots. Without this, a flood of frequent local-model items
    # (every GitHub release + every new HF repo) crowds the offers out of the
    # page entirely, and the free digest would report "nothing" while offers are
    # still stored. Expired offers sort last so live ones win the quota.
    offer_quota = max(4, limit // 3)
    offers = (base.filter(AIRadarItem.category.in_(OFFER_CATEGORIES))
              .order_by((AIRadarItem.offer_expires_utc.isnot(None))
                        & (AIRadarItem.offer_expires_utc <= now), *recency)
              .limit(offer_quota).all())
    offer_ids = [o.id for o in offers]
    # Fill only the remaining slots with the newest non-offer items, so the offer
    # quota is reserved rather than re-truncated away by recency below.
    remaining = max(0, limit - len(offers))
    others = (base.filter(~AIRadarItem.id.in_(offer_ids))
              .order_by(*recency).limit(remaining).all()) if remaining else []

    selected = list(offers) + list(others)
    selected.sort(key=_recency_key, reverse=True)  # one recency order for display

    last_run = db.query(AIRadarRun).order_by(AIRadarRun.id.desc()).first()
    last_success = (db.query(AIRadarRun).filter(AIRadarRun.status.in_(["success", "partial"]))
                    .order_by(AIRadarRun.id.desc()).first())
    freshness = None
    if last_success and last_success.finished_utc:
        age_seconds = int((now - last_success.finished_utc).total_seconds())
        freshness = {"last_success_utc": iso_utc(last_success.finished_utc),
                     "age_seconds": max(0, age_seconds),
                     "status": last_success.status}
    return {
        "items": [_effective(it, now) for it in selected],
        "last_run": last_run.to_dict() if last_run else None,
        "last_success": last_success.to_dict() if last_success else None,
        "freshness": freshness,
        "now_utc": now.isoformat() + "Z",
    }


def _recency_key(item):
    """Python mirror of the SQL recency order: published-first (newest), then
    unpublished by first-seen (newest). Used to re-merge the offer quota with the
    recency page without a second round trip."""
    pub = item.published_utc
    return (1 if pub is not None else 0,
            pub or datetime.min,
            item.first_seen_utc or datetime.min)



def _free_now(item):
    if item["category"] not in OFFER_CATEGORIES:
        return False
    if item.get("verification_status") == "expired":
        return False
    offer = item.get("offer") or {}
    return offer.get("terms_status") != "expired"


def format_digest(state, clock, mode="general", limit=6):
    """Deterministic, offline digest text (no LLM). mode='free' answers the
    free-models/credits question; 'general' answers what's-new-in-AI."""
    items = state.get("items") or []
    fresh = state.get("freshness")
    if mode == "free":
        rows = [it for it in items if _free_now(it)]
        header = "Currently free AI models / API access:"
    else:
        rows = [it for it in items if it.get("verification_status") in ("verified", "unverified")]
        # Verified launches outrank unverified candidates (a new HF repo is a
        # candidate, not a major launch). Stable sort keeps recency within a tier.
        rows.sort(key=lambda it: -VERIFICATION_RANK.get(it.get("verification_status"), 1))
        header = "Latest AI Radar items:"
    lines = []
    for it in rows[:limit]:
        tag = {"verified": "verified", "unverified": "unverified", "expired": "expired"}.get(
            it.get("verification_status"), it.get("verification_status"))
        when = ""
        if it.get("published_utc"):
            when = " (" + timeutil.render_local(_parse_when(it["published_utc"])) + ")"
        lines.append(f"- [{it['category']}|{tag}] {it['title']}{when}")
    if not lines:
        body = "Nothing new is stored yet." if not items else "No items match that filter."
    else:
        body = "\n".join(lines)
    if fresh and fresh.get("age_seconds") is not None:
        age_h = fresh["age_seconds"] // 3600
        stamp = f" Last successful check ~{age_h}h ago."
    elif state.get("last_run"):
        stamp = " Last check did not fully succeed; showing stored items."
    else:
        stamp = " No research run has completed yet."
    return f"{header}\n{body}{stamp}"
