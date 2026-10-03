import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter

from . import __version__

log = logging.getLogger(__name__)

API_BASE = "https://api.miro.com"

# https://developers.miro.com/reference/rate-limiting
# Each user+app pair gets a credit budget (X-RateLimit-Limit, 100k) per fixed 60s window that
# starts with the first request. Every call costs credits by its level; the item listing is
# Level 2 and downloading an image/document file costs 500 (Level 3), measured on real boards.
DEFAULT_LIMIT = 100_000
LEVEL_COST = {1: 50, 2: 100, 3: 500, 4: 2000}

# Boards are surveyed in parallel, each with its own worker threads: keep a connection per thread.
POOL_SIZE = 64


def pooled_session():
	session = requests.Session()
	adapter = HTTPAdapter(pool_connections=4, pool_maxsize=POOL_SIZE)
	session.mount("https://", adapter)
	session.mount("http://", adapter)
	return session


class MiroAPIError(Exception):
	def __init__(self, method, url, status, body):
		self.method = method
		self.url = url
		self.status = status
		self.body = body
		super().__init__(f"{method} {url} -> {status}: {(body or '')[:300]}")


class Cancelled(Exception):
	"""Raised in worker threads after Ctrl-C so they stop at their next request instead of finishing their board."""


class CreditBucket:
	"""Spends credits at a steady rate just under Miro's budget, shared across threads.

	Bursting through the whole minute's budget gets 429s, and Miro answers a 429 with a 60s
	penalty. A steady rate (plus a small burst allowance) stays under the limit in any window.
	"""

	def __init__(self, limit=DEFAULT_LIMIT, *, fraction=0.9, burst_seconds=2.0, clock=time.monotonic, sleep=time.sleep):
		self.fraction = fraction
		self.burst_seconds = burst_seconds
		self.clock = clock
		self.sleep = sleep
		self.lock = threading.Lock()
		self._configure(limit)
		self.tokens = self.capacity
		self.updated = clock()
		self.blocked_until = 0.0
		self.cancelled = lambda: False  # MiroClient wires this to its cancel event

	def _configure(self, limit):
		self.limit = limit
		self.rate = limit * self.fraction / 60.0
		self.capacity = max(self.rate * self.burst_seconds, max(LEVEL_COST.values()))

	def _refill(self, now):
		self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
		self.updated = now

	def set_limit(self, limit):
		"""Follow the budget the server reports, in case an account has a different one."""
		with self.lock:
			if limit != self.limit:
				self._configure(limit)
				self.tokens = min(self.tokens, self.capacity)

	def take(self, cost):
		while True:
			if self.cancelled():
				raise Cancelled()
			with self.lock:
				now = self.clock()
				self._refill(now)
				wait = self.blocked_until - now
				if wait <= 0:
					if self.tokens >= cost:
						self.tokens -= cost
						return
					wait = (cost - self.tokens) / self.rate
			self.sleep(min(wait, 1.0))  # short naps so a cancel is noticed during long rate-limit pauses

	def block_for(self, seconds):
		"""Pause every thread. Returns False if an existing pause already covers it (log once, not per thread)."""
		with self.lock:
			now = self.clock()
			until = now + seconds
			if until <= self.blocked_until + 1:
				return False
			self.blocked_until = until
			self._refill(now)
			self.tokens = 0.0
			return True

	def seconds_for(self, credits):
		return credits / self.rate


class MiroClient:
	def __init__(
		self,
		token,
		*,
		base_url=API_BASE,
		session=None,
		bucket=None,
		max_retries=5,
		max_rate_limit_waits=30,
		timeout=60,
		sleep=time.sleep,
	):
		self.base_url = base_url.rstrip("/")
		self.session = session or pooled_session()
		self.session.headers.update({
			"Authorization": f"Bearer {token}",
			"Accept": "application/json",
			"User-Agent": f"miro-exporter/{__version__}",
		})
		self.bucket = bucket or CreditBucket(sleep=sleep)
		self.cancel = threading.Event()  # set on Ctrl-C (see cli.main) to stop every worker thread
		self.bucket.cancelled = self.cancel.is_set
		self.max_retries = max_retries
		self.max_rate_limit_waits = max_rate_limit_waits
		self.timeout = timeout
		self.sleep = sleep
		self._stats_lock = threading.Lock()
		self.stats = {"calls": 0, "credits": 0, "retries": 0, "rate_limit_waits": 0}

	def _bump(self, **counts):
		with self._stats_lock:
			for key, value in counts.items():
				self.stats[key] += value

	def stats_snapshot(self):
		with self._stats_lock:
			return dict(self.stats)

	def url_for(self, path):
		return path if path.startswith("http") else f"{self.base_url}{path}"

	def request(self, method, path, *, params=None, json_body=None, level=1, stream=False):
		url = self.url_for(path)
		cost = LEVEL_COST[level]
		retries = 0
		waits = 0
		while True:
			if self.cancel.is_set():
				raise Cancelled()
			self.bucket.take(cost)
			self._bump(calls=1, credits=cost)
			try:
				resp = self.session.request(method, url, params=params, json=json_body, timeout=self.timeout, stream=stream)
			except (requests.ConnectionError, requests.Timeout) as e:
				if retries >= self.max_retries:
					raise MiroAPIError(method, url, None, str(e)) from e
				retries += 1
				self._bump(retries=1)
				self._backoff(retries)
				continue

			if resp.status_code == 429:
				if waits >= self.max_rate_limit_waits:
					raise MiroAPIError(method, url, 429, resp.text)
				waits += 1
				wait = self._seconds_until_reset(resp) or min(60, 2 ** waits)
				self._bump(rate_limit_waits=1)
				if self.bucket.block_for(wait):
					log.warning("Miro rate limit hit, pausing %.0fs", wait)
				continue

			self._watch_headers(resp)

			if resp.status_code >= 500 and retries < self.max_retries:
				retries += 1
				self._bump(retries=1)
				self._backoff(retries)
				continue

			if resp.status_code >= 400:
				raise MiroAPIError(method, url, resp.status_code, resp.text)
			return resp

	def get_json(self, path, params=None, level=1):
		return self.request("GET", path, params=params, level=level).json()

	def paginate_cursor(self, path, params=None, *, limit=50, level=1, on_page=None):
		"""Yield every element of a cursor-paged collection (items, connectors, groups, ...)."""
		base = dict(params or {})
		if limit:
			base["limit"] = limit
		cursor = None
		seen = set()
		page_no = 0
		while True:
			query = dict(base)
			if cursor:
				query["cursor"] = cursor
			page = self.get_json(path, query, level)
			page_no += 1
			if on_page:
				on_page(page_no, page)
			data = page.get("data") or []
			yield from data
			cursor = page.get("cursor")
			if not cursor or not data or cursor in seen:
				return
			seen.add(cursor)

	def paginate_offset(self, path, params=None, *, limit=50, level=1, on_page=None):
		"""Yield every element of an offset-paged collection (boards, tags, members)."""
		offset = 0
		page_no = 0
		while True:
			query = dict(params or {})
			query["limit"] = limit
			query["offset"] = offset
			page = self.get_json(path, query, level)
			page_no += 1
			if on_page:
				on_page(page_no, page)
			data = page.get("data") or []
			yield from data
			offset += len(data)
			total = page.get("total")
			if not data or (total is not None and offset >= total) or (total is None and len(data) < limit):
				return

	def _backoff(self, attempt):
		remaining = min(30.0, 2 ** attempt) + random.random()
		while remaining > 0 and not self.cancel.is_set():
			self.sleep(min(remaining, 1.0))
			remaining -= 1.0

	def _watch_headers(self, resp):
		limit = _int_header(resp, "X-RateLimit-Limit")
		if limit:
			self.bucket.set_limit(limit)
		# Backstop for budget spent elsewhere (another run with the same token): wait out the window.
		remaining = _int_header(resp, "X-RateLimit-Remaining")
		if remaining is not None and remaining < max(LEVEL_COST.values()):
			wait = self._seconds_until_reset(resp)
			if wait and self.bucket.block_for(wait):
				log.info("Miro credit budget nearly spent (%s left), pausing %.0fs until it resets", remaining, wait)

	@staticmethod
	def _seconds_until_reset(resp):
		retry_after = _int_header(resp, "Retry-After")
		if retry_after is not None:
			return max(1, retry_after)
		reset = _int_header(resp, "X-RateLimit-Reset")
		if reset is None:
			return None
		if reset > 1_000_000_000:
			# Epoch seconds: measure against the server's clock so local clock skew doesn't matter.
			reset -= _server_time(resp)
		# +1s: the window flips on whole seconds and differs by a second between Miro's servers.
		return max(1, min(120, reset + 1))


def _int_header(resp, name):
	value = resp.headers.get(name)
	if value is None:
		return None
	try:
		return int(float(value))
	except ValueError:
		return None


def _server_time(resp):
	try:
		return parsedate_to_datetime(resp.headers["Date"]).timestamp()
	except (KeyError, TypeError, ValueError):
		return time.time()
